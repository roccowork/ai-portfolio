"""总控台（/console/）：我所有的项目和资源都在这一个地方看（查看类内容只在这里，Telegram 只推送和操作）。

页面分标签页：概览、告警中心（所有告警集中一张表）、资源中心（服务器、网站、云服务、大模型……按类型分组）、
数字员工。原来的「数据流」关系图 2026-11-22 删了（和数字员工的信息重复，用不上）。

每个自动化项目是一名「数字员工」（有名字、岗位、今天干了多少活），服务器、知识库、外部平台是「资源」。
所有状态都来自真实数据：外部拨测、Argo CD、平台实况（/status.json）、账本、自愈记录、私人助理的运行记录。
私人项目的数字员工由私人助理自己报上来（见 private_staff），名字和说明不写在这个公开仓库里。
只读，不调用模型。只能从值班台机器人的 Telegram 小程序打开，只认主人本人（tgauth.py）：里面有私人项目的状态。
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx

from . import autoheal
from . import opsagent as ops
from . import probes
from .localtime import TZ

log = logging.getLogger("ai")

DAY = timedelta(hours=26)  # 每天跑一次的东西，26 小时内成功过就算正常

# 节点：kind 是 staff 的有岗位（role）；link 是它用的工具的地址
NODES = [
    {"id": "visitors", "col": "entry", "name": "网站访客", "desc": "作品集 ops.liuxd.dev", "link": "https://ops.liuxd.dev/"},
    {"id": "sites", "col": "entry", "name": "其他网站", "desc": "名片、简历、两个路线图、IT 外包官网", "link": "https://liuxd.dev/"},
    {"id": "me", "col": "entry", "name": "我（Telegram）", "desc": "两个机器人：OPS 值班台、私人助理；告警、确认按钮、每日推送都发到这里"},
    {"id": "media", "col": "entry", "name": "自媒体", "desc": "抖音（手动发布）；B 站、小红书、视频号、YouTube、TikTok、X 待开通"},
    {"id": "xiaoxiao", "col": "staff", "kind": "staff", "name": "小晓", "role": "作品集接待员", "desc": "在网站右下角回答访客的问题，按作品资料作答，留言转给我"},
    {"id": "oncall", "col": "staff", "kind": "staff", "name": "值班员", "role": "运维 Agent", "desc": "告警一响自己查原因，低风险问题自动修复，关键操作推给我确认", "link": "https://ops.liuxd.dev/ops-agent/"},
    {"id": "editor", "col": "staff", "kind": "staff", "name": "编辑", "role": "AI 前沿流水线", "desc": "每天收集 AI 新闻，挑 3 条写口播稿，推给我把关"},
    {"id": "butler", "col": "staff", "kind": "staff", "name": "管家", "role": "私人助理", "desc": "提醒、早安简报、GitHub 趋势、VPS 状态"},
    {"id": "coach", "col": "staff", "kind": "staff", "name": "求职顾问", "role": "LangGraph 求职助手", "desc": "我转发岗位，它对照简历打分、改简历、写打招呼和面试题，记录投递进度"},
    {"id": "deployer", "col": "staff", "kind": "staff", "name": "发布员", "role": "Argo CD", "desc": "代码推到 GitHub，自动同步上线；有人手动改了集群就改回来", "link": "https://argocd.liuxd.dev/"},
    {"id": "watcher", "col": "staff", "kind": "staff", "name": "监控员", "role": "Prometheus · 拨测", "desc": "采集指标、从外面拨测网站和依赖，出问题发告警", "link": "https://grafana.liuxd.dev/"},
    {"id": "archivist", "col": "staff", "kind": "staff", "name": "档案员", "role": "备份", "desc": "每天凌晨把数据库、笔记、全部 GitHub 仓库和集群密钥加密备份到 R2"},
    {"id": "analyst", "col": "staff", "kind": "staff", "name": "统计员", "role": "Umami", "desc": "统计网站访问，不用 Cookie", "link": "https://stats.liuxd.dev/"},
    {"id": "gateway", "col": "data", "name": "模型网关", "desc": "所有模型调用都从这里走：选模型、记费用、每日上限", "link": "https://ops.liuxd.dev/gateway/"},
    {"id": "kb", "col": "data", "name": "知识库", "desc": "作品集资料、运维手册（RAG，带评测）", "link": "https://ops.liuxd.dev/rag/"},
    {"id": "dbs", "col": "data", "name": "业务数据", "desc": "账本和自愈记录、私人助理、访问统计"},
    {"id": "repos", "col": "data", "name": "GitHub 仓库", "desc": "全部项目的代码和文档", "link": "https://github.com/roccowork"},
    {"id": "k3s", "col": "platform", "name": "香港服务器", "desc": "单节点 K3s：网站、AI 服务、监控都跑在这里", "link": "https://ops.liuxd.dev/gitops/"},
    {"id": "models", "col": "platform", "name": "大模型", "desc": "阿里云百炼（默认）、OpenRouter（私人助理）"},
    {"id": "cloudflare", "col": "platform", "name": "Cloudflare", "desc": "DNS、静态网站（Pages）、备份存储（R2）", "link": "https://dash.cloudflare.com/"},
    {"id": "news", "col": "platform", "name": "新闻源", "desc": "OpenAI、Google AI、Hugging Face、TechCrunch、The Verge、量子位、Hacker News"},
]

# 数字员工点开后看的详情（/api/ai/console/detail 的 section）
STAFF_DETAIL = {"xiaoxiao": "leads", "oncall": "heal"}
SITE_PROBES = ["liuxd.dev", "cv.liuxd.dev", "roadmap.liuxd.dev", "ai-roadmap.liuxd.dev", "it.liuxd.dev"]
# Argo CD 应用 → 它管着的节点
ARGO_NODES = {"ops-desk": ["xiaoxiao", "oncall", "gateway"], "personal": ["butler", "editor", "coach"], "umami": ["analyst"], "git-backup": ["archivist"], "secret-backup": ["archivist"],
              "monitoring-extras": ["watcher"], "platform": ["k3s", "watcher"]}


# 资源中心的分组（顺序就是页面上的顺序）。私人助理报上来的资源（VPS、余额等）用同样的 group 键
RESOURCE_GROUPS = [
    {"key": "server", "name": "服务器"},
    {"key": "site", "name": "网站"},
    {"key": "dependency", "name": "外部依赖"},
    {"key": "model", "name": "大模型和用量"},
    {"key": "release", "name": "发布、备份和证书"},
]
LEVELS = {"bad": 0, "warn": 1}  # 告警排序：异常在前


def private_list(asst: dict | None, field: str, limit: int) -> list[dict]:
    """私人助理报上来的告警或资源（字段见 private_alerts / private_resources），只取对象"""
    raw = (asst or {}).get(field)
    return [x for x in raw[:limit] if isinstance(x, dict)] if isinstance(raw, list) else []


def private_pages(asst: dict | None) -> list[dict]:
    """私人助理的网页做成总控台的标签页（iframe 打开，同一个域名，页面用总控台的 Telegram 身份）：[{key, name, path}]。
    页面名字和内容只在私有仓库，这里只校验格式：path 只能是 /me/ 下面的"""
    out = []
    for x in private_list(asst, "pages", 8):
        key, path = str(x.get("key") or ""), str(x.get("path") or "")
        if re.fullmatch(r"[a-z]{1,20}", key) and re.fullmatch(r"/me/[a-z-]{1,30}/", path) and x.get("name"):
            out.append({"key": "p-" + key, "name": str(x["name"])[:10], "path": path})
    return out


def private_alerts(asst: dict | None) -> list[dict]:
    """私人助理的告警：[{level: bad|warn, title, detail, since}]，比如 VPS 掉线、余额快用完"""
    out = []
    for x in private_list(asst, "alerts", 20):
        if x.get("level") in LEVELS and x.get("title"):
            out.append({"level": x["level"], "source": "私人助理", "title": str(x["title"])[:60], "detail": str(x.get("detail") or "")[:200],
                        "since": str(x.get("since") or "")[:40]})
    return out


def private_resources(asst: dict | None) -> list[dict]:
    """私人助理的资源：[{id, group, name, status, lines, detail}]，detail 是私人助理 /internal/detail/{detail} 的名字，
    这边加 p: 前缀，点开时由 /api/ai/console/detail 转给私人助理"""
    keys = {g["key"] for g in RESOURCE_GROUPS}
    out = []
    for x in private_list(asst, "resources", 20):
        if not str(x.get("id") or "").strip() or not x.get("name") or x.get("group") not in keys:
            continue
        out.append({"id": "p-" + str(x["id"]).strip()[:30], "group": x["group"], "name": str(x["name"])[:30],
                    "status": x.get("status") if x.get("status") in PRIVATE_STATUS else "unknown",
                    "lines": [str(line)[:100] for line in (x.get("lines") or [])[:4] if line],
                    **({"detail": "p:" + str(x["detail"])} if re.fullmatch(r"[a-z]{1,20}", str(x.get("detail") or "")) else {})})
    return out


# 私人助理自己报的私人数字员工（私人项目的名字和说明只在私有仓库，公开代码里不写）：
# 助理的 /internal/summary 返回 staff: [{id, name, role, desc, link, status, lines}]（还带的 edges 是原来「数据流」页用的，2026-11-22 删了页面，不再读）。
# 节点 id 统一加 p- 前缀；状态只认下面这几种
PRIVATE_STATUS = {"ok", "warn", "bad", "idle", "unknown", "manual", "paused"}


def private_staff(asst: dict | None) -> list[dict]:
    nodes: list[dict] = []
    raw = (asst or {}).get("staff")
    if not isinstance(raw, list):
        return nodes
    for x in raw[:8]:
        if not isinstance(x, dict) or not str(x.get("id") or "").strip() or not x.get("name"):
            continue
        nid = "p-" + str(x["id"]).strip()[:30]
        if any(n["id"] == nid for n in nodes):
            continue
        link = str(x.get("link") or "")
        nodes.append({
            "id": nid, "col": "staff", "kind": "staff", "name": str(x["name"])[:20], "role": str(x.get("role") or "")[:30],
            "desc": str(x.get("desc") or "")[:120], **({"link": link} if link.startswith("https://") else {}),
            "status": x.get("status") if x.get("status") in PRIVATE_STATUS else "unknown",
            "lines": [str(line)[:80] for line in (x.get("lines") or [])[:3] if line],
            # 点开的详情在私人助理那边（/internal/detail/<名字>），总控台按 p: 前缀转过去
            **({"detail": "p:" + str(x["detail"])} if re.fullmatch(r"[a-z]{1,18}", str(x.get("detail") or "")) else {}),
        })
    return nodes


def _ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return "没有记录"
    m = int((now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() // 60)
    return "刚刚" if m < 1 else f"{m} 分钟前" if m < 60 else f"{m // 60} 小时前" if m < 2880 else f"{m // 1440} 天前"


def _recent(iso: str | None, now: datetime, within: timedelta = DAY) -> bool:
    return bool(iso) and now - datetime.fromisoformat(iso.replace("Z", "+00:00")) <= within  # type: ignore[union-attr]


STEP = 60  # 已恢复告警：按分钟取样


def recovered_alerts(series: list[dict], now: float, step: int = STEP, limit: int = 30) -> list[dict]:
    """最近 24 小时出现过、现在已经消失的告警（Prometheus 自己存的 ALERTS 历史，不另建数据库）。
    含「等待中」（pending）的：问题出现了但没持续到规则的 for 时长就好了，不推送；2026-11-22 用户要看到这些一闪而过的故障。
    同一条告警（pending 和 firing 合并）断开超过 2 个取样点算两次；最后一个点还在的是没恢复的，不列（在上面的「所有告警」里）"""
    groups: dict[tuple, dict] = {}
    for x in series:
        m = x.get("metric") or {}
        key = tuple(sorted((k, v) for k, v in m.items() if k != "alertstate"))
        g = groups.setdefault(key, {"metric": m, "ts": set(), "firing": set()})
        for t, _ in x.get("values") or []:
            g["ts"].add(float(t))
            if m.get("alertstate") == "firing":
                g["firing"].add(float(t))
    out = []
    fmt = lambda t: datetime.fromtimestamp(t, TZ).strftime("%m-%d %H:%M")  # noqa: E731
    for g in groups.values():
        m, ts = g["metric"], sorted(g["ts"])
        if not ts:
            continue
        spans, start, prev = [], ts[0], ts[0]
        for t in ts[1:]:
            if t - prev > 2 * step:
                spans.append((start, prev))
                start = t
            prev = t
        spans.append((start, prev))
        for a, b in spans:
            if now - b <= 2 * step:
                continue  # 还在
            fired = any(a <= t <= b for t in g["firing"])
            out.append({"title": str(m.get("alertname") or "?"),
                        "detail": " · ".join(str(v) for v in (m.get("namespace"), m.get("name") or m.get("pod") or m.get("deployment") or m.get("job")) if v),
                        "level": ("bad" if m.get("severity") == "critical" else "warn") if fired else "flash",
                        "pushed": fired,
                        "start": fmt(a), "end": fmt(b + step), "minutes": max(1, round((b + step - a) / 60)), "_end": b})
    out.sort(key=lambda r: r["_end"], reverse=True)
    for r in out:
        r.pop("_end")
    return out[:limit]


async def _try(coro, what: str):
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 —— 某一块查不到就是 None，其他照常显示
        log.info("总控台：%s 查不到：%s", what, e)
        return None


class Console:
    def __init__(self, c: ops.Cluster, status: Callable[[], Awaitable[dict]], local: Callable[[], dict], assistant_url: str):
        self.c, self.status, self.local, self.assistant_url = c, status, local, assistant_url

    async def scalar(self, q: str) -> float | None:
        r = await self.c.query(q)
        return float(r[0]["value"][1]) if r else None

    async def assistant(self) -> dict:
        async with httpx.AsyncClient(timeout=5) as cl:
            r = await cl.get(self.assistant_url)
            r.raise_for_status()
            return r.json()

    async def argo(self) -> list[dict]:
        apps = (await self.c.k8s("/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"))["items"]
        return [{"name": a["metadata"]["name"], "sync": ((a.get("status") or {}).get("sync") or {}).get("status"),
                 "health": ((a.get("status") or {}).get("health") or {}).get("status")} for a in apps]

    async def build(self) -> dict:
        now = datetime.now(timezone.utc)
        probe, st, argo, asst, req, calls_1h, alerts, pods, mem, cpu, history = await asyncio.gather(
            _try(probes.fetch(self.c), "拨测"),
            _try(self.status(), "平台实况"),
            _try(self.argo(), "Argo CD"),
            _try(self.assistant(), "私人助理"),
            _try(self.scalar('sum(rate(traefik_service_requests_total{service="ops-desk-ops-desk-http@kubernetes"}[15m])) * 60'), "访问量"),
            _try(self.scalar("sum(increase(ai_calls_total[1h]))"), "模型调用"),
            _try(ops.run_tool(self.c, "list_alerts", {}), "告警"),
            _try(self.scalar('sum(kube_pod_status_phase{phase="Running"})'), "Pod 数"),
            _try(self.scalar("(1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)) * 100"), "内存"),
            _try(self.scalar('(1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))) * 100'), "CPU"),
            _try(self.c.query_range('ALERTS{severity!~"info|none", alertname!~"Watchdog|InfoInhibitor"}',
                                    now.timestamp() - 86400, now.timestamp(), STEP), "已恢复告警"),
        )
        loc = self.local()
        pr = {p["name"]: p for p in probe or []}
        firing = [a for a in alerts or [] if a.get("state") == "firing" and a.get("severity") not in ("info", "none")]
        nodes = {n["id"]: {**n, "status": "unknown", "lines": [], **({"detail": STAFF_DETAIL[n["id"]]} if n["id"] in STAFF_DETAIL else {})} for n in NODES}

        def set_(nid: str, status: str, *lines: str) -> None:
            nodes[nid]["status"] = status
            nodes[nid]["lines"] += [x for x in lines if x]

        def probe_line(name: str) -> tuple[str, str]:
            p = pr.get(name)
            if not p:
                return "unknown", ""
            if not p["ok"]:
                return "bad", f"{name} 打不开"
            return "ok", f"{name} 正常" + (f"，{p['ms']} ms" if p["ms"] is not None else "")

        # 入口：拨测的数字只在监控员卡片上显示；这里按拨测结果定状态，打不开时才写出来
        s, line = probe_line("ops.liuxd.dev")
        set_("visitors", s, line if s == "bad" else "", f"最近 15 分钟 {req:.1f} 次请求 / 分钟" if req is not None else "")
        bad_sites = [n for n in SITE_PROBES if n in pr and not pr[n]["ok"]]
        if any(n in pr for n in SITE_PROBES):
            set_("sites", "bad" if bad_sites else "ok", "打不开：" + "、".join(bad_sites) if bad_sites else "")
        s, line = probe_line("Telegram")
        set_("me", s, line)
        set_("media", "manual", "抖音手动发布，其他平台待开通")

        # 数字员工：先按 Argo CD 应用的健康状况定在岗 / 异常，再补上今天的工作量
        for a in argo or []:
            for nid in ARGO_NODES.get(a["name"], []):
                good = a["sync"] == "Synced" and a["health"] == "Healthy"
                if nodes[nid]["status"] in ("unknown", "ok"):
                    nodes[nid]["status"] = "ok" if good else ("warn" if a["health"] in ("Progressing", "Healthy") else "bad")
        today = loc["usage_today"]
        ask = loc["by_feature_today"].get("ask", 0)
        set_("xiaoxiao", nodes["xiaoxiao"]["status"], f"今天回答 {ask} 次")
        heal = loc["heal_24h"]
        set_("oncall", nodes["oncall"]["status"], f"24 小时处理 {len(heal)} 次告警，修好 {sum(1 for h in heal if h['status'] == 'fixed')} 次", f"累计修好 {loc['heal_fixed']} 次")
        fr = (asst or {}).get("frontier")
        if asst is None:
            set_("butler", "bad", "连不上私人助理")
            set_("editor", "unknown", "")
        else:
            set_("butler", "ok" if asst.get("bot") else "warn", "机器人在线" if asst.get("bot") else "没配 Telegram")
            if fr and fr.get("paused"):
                # 栏目暂停（私人助理报的）：照常推新闻标题，不算异常，也不算在岗人数里
                set_("editor", "paused", "栏目暂停中，每天只推新闻标题" + (f"（上次 {_ago(fr.get('ts'), now)}）" if fr.get("ts") else ""))
            elif fr:
                ok_run = fr["status"] == "ok"
                set_("editor", "ok" if ok_run and _recent(fr["ts"], now) else ("bad" if fr["status"] == "error" else "warn"),
                     f"上次 {_ago(fr['ts'], now)}：{fr['picks']} 条选题", f"新闻源 {fr['sources_ok']}/{fr['sources']} 连通")
            else:
                set_("editor", "idle", "还没有运行过")
        cr = (asst or {}).get("career")
        if cr is not None:
            set_("coach", nodes["coach"]["status"] if nodes["coach"]["status"] != "unknown" else "ok",
                 f"本周分析 {cr['week']} 个岗位，共 {cr['total']} 个", f"已投 {cr['applied']} · 面试中 {cr['interview']} · offer {cr['offer']}")
        elif asst is None:
            set_("coach", "bad", "连不上私人助理")
        apps = argo or []
        bad_apps = [a["name"] for a in apps if not (a["sync"] == "Synced" and a["health"] == "Healthy")]
        deploys = (st or {}).get("deploys") or []
        last_deploy = deploys[0].get("time") if deploys else None
        if argo is not None:
            set_("deployer", "warn" if bad_apps else "ok", f"{len(apps) - len(bad_apps)}/{len(apps)} 个应用已同步、健康", "有问题：" + "、".join(bad_apps) if bad_apps else "",
                 f"上次发布 {_ago(last_deploy, now)}" if last_deploy else "")
        if probe is not None:
            set_("watcher", "warn" if firing else "ok", f"拨测 {sum(p['ok'] for p in probe)}/{len(probe)} 正常", f"现在 {len(firing)} 条告警" + ("：" + "、".join(str(a.get("name")) for a in firing[:3]) if firing else ""),
                 f"7 天可用率 {st['availability7d']}%" if st and st.get("availability7d") is not None else "")
        backups = (st or {}).get("backups") or []
        stale = [b["name"] for b in backups if not _recent(b.get("lastSuccess"), now)]
        if backups:
            set_("archivist", "warn" if stale else "ok", f"{len(backups) - len(stale)}/{len(backups)} 个备份 26 小时内成功", "过期：" + "、".join(stale) if stale else "",
                 f"最近一次 {_ago(max(b.get('lastSuccess') or '' for b in backups) or None, now)}")
        s, line = probe_line("stats.liuxd.dev")
        if nodes["analyst"]["status"] == "unknown" and s != "unknown":
            nodes["analyst"]["status"] = s

        # 数据和知识
        set_("gateway", "ok" if today["calls"] or loc["enabled"] else "warn", f"今天 {today['calls']} 次调用，约 ¥{today['cost']:.2f}", f"最近 1 小时 {calls_1h:.0f} 次" if calls_1h is not None else "")
        kb = loc["kb"]
        set_("kb", "ok" if kb["ready"] else "warn", f"作品集资料 {kb['chunks']} 段" + ("" if kb["ready"] else "（索引准备中）"),
             f"问答评测：命中率 {kb['hit']}%" if kb.get("hit") is not None else "")
        # 业务数据、GitHub 仓库只显示自身情况；备份情况在档案员卡片上
        db = loc.get("db") or {}
        if db:
            set_("dbs", "ok", f"AI 账本 {db['mb']:.1f} MB", f"自愈记录 {db['incidents']} 条，评测 {db['evals']} 次")
        s, line = probe_line("GitHub")
        set_("repos", s if s != "unknown" else "ok", line)

        # 平台和外部
        set_("k3s", "warn" if firing else "ok", f"{pods:.0f} 个 Pod 运行中" if pods is not None else "",
             " · ".join(x for x in (f"内存 {mem:.0f}%" if mem is not None else "", f"CPU {cpu:.0f}%" if cpu is not None else "") if x))
        s, line = probe_line("阿里云百炼")
        set_("models", s, line)
        s, line = probe_line("Cloudflare")
        set_("cloudflare", s, line)
        if fr:
            set_("news", "ok" if fr["sources_ok"] == fr["sources"] else "warn", f"{fr['sources_ok']}/{fr['sources']} 个能读", f"上次 {fr['candidates']} 条候选")

        # 私人助理报上来的私人数字员工：自己带状态；助理所在的 Argo CD 应用不健康时一起标出来
        extra_nodes = private_staff(asst)
        personal = next((a for a in argo or [] if a["name"] == "personal"), None)
        for n in extra_nodes:
            if personal and not (personal["sync"] == "Synced" and personal["health"] == "Healthy") and n["status"] == "ok":
                n["status"] = "warn"
            nodes[n["id"]] = n

        staff = [n for n in nodes.values() if n.get("kind") == "staff" and n["status"] != "paused"]  # 暂停的项目不算在岗人数
        alert_list = collect_alerts(alerts, probe, argo, st, heal, now) + private_alerts(asst)
        alert_list.sort(key=lambda a: LEVELS[a["level"]])
        resources = collect_resources(probe, argo, st, loc, pods, mem, cpu, now) + private_resources(asst)
        return {
            "generated": now.isoformat(),
            "nodes": list(nodes.values()),
            "alerts": alert_list,
            "recovered": recovered_alerts(history or [], now.timestamp()),
            "resource_groups": RESOURCE_GROUPS,
            "resources": resources,
            "pages": private_pages(asst),
            "summary": {
                "staff": len(staff),
                "staff_ok": sum(1 for n in staff if n["status"] == "ok"),
                "problems": [n["name"] for n in nodes.values() if n["status"] in ("bad", "warn")],
                "alerts": len(alert_list),
                "alerts_bad": sum(1 for a in alert_list if a["level"] == "bad"),
                "resources": len(resources),
                "resources_bad": sum(1 for r in resources if r["status"] in ("bad", "warn")),
                "cost_today": round(float(today["cost"]), 2),
            },
        }


def collect_alerts(alerts: list[dict] | None, probe: list[dict] | None, argo: list[dict] | None, st: dict | None, heal: list[dict], now: datetime) -> list[dict]:
    """告警中心：所有来源的告警排成一张表 [{level, source, title, detail, since, heal}]。
    heal 是值班员对这条告警的处理（最近 24 小时同名告警的最后一条自愈记录）"""
    out: list[dict] = []
    if alerts is None:
        out.append({"level": "warn", "source": "Prometheus", "title": "告警查不到", "detail": "Prometheus 暂时连不上，下面的列表可能不全", "since": ""})
    handled = {}
    for h in heal or []:
        if h.get("alert"):
            handled[h["alert"]] = h
    for a in alerts or []:
        if a.get("severity") in ("info", "none"):
            continue
        where = " / ".join(x for x in (a.get("namespace"), a.get("pod")) if x)
        firing = a.get("state") == "firing"
        h = handled.get(a.get("name"))
        out.append({
            "level": "bad" if firing and a.get("severity") == "critical" else "warn",
            "source": "Prometheus" + ("" if firing else "（等待中）"),
            "title": str(a.get("name") or "?"),
            "detail": " · ".join(x for x in (where, str(a.get("summary") or "")[:160]) if x),
            "since": str(a.get("since") or ""),
            **({"heal": _heal_line(h)} if h else {}),
        })
    for p in probe or []:
        if p["group"] == "vps":
            continue  # VPS 端口连不上由 Prometheus 的 VPSDown 报，这里再报一遍就重复了
        if not p["ok"]:
            out.append({"level": "bad", "source": "外部拨测", "title": f"{p['name']} 打不开", "detail": "自己的网站" if p["group"] == "own" else "外部依赖", "since": ""})
        elif p["group"] == "own" and p.get("cert_days") is not None and p["cert_days"] < 14:
            out.append({"level": "warn", "source": "外部拨测", "title": f"{p['name']} 证书快过期", "detail": f"还剩 {p['cert_days']} 天", "since": ""})
    for a in argo or []:
        if not (a["sync"] == "Synced" and a["health"] == "Healthy"):
            out.append({"level": "bad" if a["health"] in ("Degraded", "Missing") else "warn", "source": "Argo CD", "title": f"{a['name']} 没同步或不健康",
                        "detail": f"同步：{a['sync'] or '?'}，健康：{a['health'] or '?'}", "since": ""})
    for b in (st or {}).get("backups") or []:
        if not _recent(b.get("lastSuccess"), now):
            out.append({"level": "warn", "source": "备份", "title": f"{b['name']} 超过 26 小时没成功", "detail": f"最近成功：{_ago(b.get('lastSuccess'), now)}", "since": ""})
    # 自愈记录里还没修好、Prometheus 那边已经不报的（比如等我确认的操作）
    for name, h in handled.items():
        if h.get("status") in ("waiting", "failed") and not any(a.get("name") == name for a in alerts or []):
            out.append({"level": "warn", "source": "值班员", "title": name, "detail": "告警已经消失，但处理还没完成", "since": "", "heal": _heal_line(h)})
    return out


def _heal_line(h: dict) -> str:
    a = h.get("action") or {}
    act = f"{autoheal.ACTION_ZH.get(a.get('action'), a.get('action'))} {a.get('target')}" if a.get("action") else ""
    return " · ".join(x for x in (autoheal.STATUS_ZH.get(h.get("status") or "", h.get("status") or ""), act, (h.get("result") or "")[:80]) if x)


def collect_resources(probe, argo, st, loc, pods, mem, cpu, now: datetime) -> list[dict]:
    """资源中心：每个资源 {id, group, name, status, lines, detail}；detail 是点开后看的详情（/api/ai/console/detail 的 section）"""
    out: list[dict] = []

    def add(rid: str, group: str, name: str, status: str, lines: list[str], detail: str = "") -> None:
        out.append({"id": rid, "group": group, "name": name, "status": status, "lines": [x for x in lines if x], **({"detail": detail} if detail else {})})

    nums = " · ".join(x for x in (f"内存 {mem:.0f}%" if mem is not None else "", f"CPU {cpu:.0f}%" if cpu is not None else "") if x)
    add("k3s", "server", "香港服务器（K3s）", "unknown" if pods is None and mem is None else ("warn" if (mem or 0) > 85 or (cpu or 0) > 85 else "ok"),
        [f"{pods:.0f} 个 Pod 运行中" if pods is not None else "", nums], "status")
    add("pods", "server", "Pod 和数据盘", "ok" if pods is not None else "unknown", ["内存、CPU 占用前 5，重启过的，数据盘用量"], "pods")
    add("jobs", "server", "后台定时任务", "ok", ["备份、状态采集：下次几点、上次成没成功（会发消息的在「推送」页）"], "jobs")
    for p in probe or []:
        if p["group"] == "vps":
            continue  # VPS 在私人助理报上来的资源里（每台一项，含延迟），不重复列
        line = f"{p['ms']} ms" if p["ms"] is not None else ""
        if p["group"] == "own" and p.get("cert_days") is not None:
            line += f" · 证书 {p['cert_days']} 天"
        add("probe-" + p["name"], "site" if p["group"] == "own" else "dependency", p["name"],
            "bad" if not p["ok"] else ("warn" if p["group"] == "own" and p.get("cert_days") is not None and p["cert_days"] < 14 else "ok"), [probes.ABOUT.get(p["name"], ""), line if p["ok"] else "打不开"], "s:" + p["name"])
    today = loc["usage_today"]
    add("ai-usage", "model", "网站 AI 用量", "ok" if loc["enabled"] else "warn", [f"今天 {today['calls']} 次调用，约 ¥{today['cost']:.2f}"], "usage")
    deploys = (st or {}).get("deploys") or []
    apps = argo or []
    bad_apps = [a["name"] for a in apps if not (a["sync"] == "Synced" and a["health"] == "Healthy")]
    add("argo", "release", "发布（Argo CD）", "unknown" if argo is None else ("warn" if bad_apps else "ok"),
        [f"{len(apps) - len(bad_apps)}/{len(apps)} 个应用已同步、健康" if argo is not None else "", f"上次发布 {_ago(deploys[0].get('time'), now)}" if deploys else ""], "live")
    backups = (st or {}).get("backups") or []
    stale = [b["name"] for b in backups if not _recent(b.get("lastSuccess"), now)]
    add("backups", "release", "备份", "unknown" if not backups else ("warn" if stale else "ok"),
        [f"{len(backups) - len(stale)}/{len(backups)} 个 26 小时内成功" if backups else "", "过期：" + "、".join(stale) if stale else ""], "backups")
    certs = (st or {}).get("certs") or []
    low = [c["name"] for c in certs if c.get("daysLeft") is not None and c["daysLeft"] < 21]
    if certs:
        add("certs", "release", "证书", "warn" if low else "ok", [f"{len(certs)} 张", "快过期：" + "、".join(low) if low else ""], "certs")
    # Cloudflare、GitHub 不再单独列（2026-11-22：和「外部依赖」的拨测卡片重复，那边有真实状态和详情）
    return out
