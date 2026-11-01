"""总控台（/console/）：我所有的项目和资源一张图看全。

每个自动化项目是一名「数字员工」（有名字、岗位、今天干了多少活），服务器、知识库、外部平台是「资源」；
连线表示谁把数据交给谁，最近真有数据在这条线上走过（发布、备份、调用模型、告警……），连线就标成「流动中」。
所有状态都来自真实数据：外部拨测、Argo CD、平台实况（/status.json）、账本、自愈记录、私人助理的运行记录。
只读，不调用模型。页面要私人密钥（ME_TOKEN）：里面有私人项目的状态。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx

from . import opsagent as ops
from . import probes

log = logging.getLogger("ai")

DAY = timedelta(hours=26)  # 每天跑一次的东西，26 小时内成功过就算正常

# 列：入口 → 数字员工 → 数据和知识 → 平台和外部
COLUMNS = [
    {"key": "entry", "name": "入口和渠道"},
    {"key": "staff", "name": "数字员工"},
    {"key": "data", "name": "数据和知识"},
    {"key": "platform", "name": "平台和外部服务"},
]

# 节点：kind 是 staff 的有岗位（role）；link 是点进去看详情的地址（{k} 换成私人密钥）
NODES = [
    {"id": "visitors", "col": "entry", "name": "网站访客", "desc": "作品集 ops.liuxd.dev", "link": "https://ops.liuxd.dev/"},
    {"id": "sites", "col": "entry", "name": "其他网站", "desc": "名片、简历、两个路线图、IT 外包官网", "link": "https://liuxd.dev/"},
    {"id": "me", "col": "entry", "name": "我（Telegram）", "desc": "两个机器人：OPS 值班台、私人助理"},
    {"id": "media", "col": "entry", "name": "自媒体", "desc": "抖音（手动发布）；B 站、小红书、视频号、YouTube、TikTok、X 待开通"},
    {"id": "xiaoxiao", "col": "staff", "kind": "staff", "name": "小晓", "role": "作品集接待员", "desc": "在网站右下角回答访客的问题，按作品资料作答，留言转给我", "link": "https://ops.liuxd.dev/rag/"},
    {"id": "oncall", "col": "staff", "kind": "staff", "name": "值班员", "role": "运维 Agent", "desc": "告警一响自己查原因，低风险问题自动修复，关键操作推给我确认", "link": "https://ops.liuxd.dev/ops-agent/"},
    {"id": "editor", "col": "staff", "kind": "staff", "name": "编辑", "role": "AI 前沿流水线", "desc": "每天收集 AI 新闻，挑 3 条写口播稿，推给我把关", "link": "https://ops.liuxd.dev/me/pipeline/#k={k}"},
    {"id": "butler", "col": "staff", "kind": "staff", "name": "管家", "role": "私人助理", "desc": "提醒、早安简报、GitHub 趋势、VPS 状态"},
    {"id": "deployer", "col": "staff", "kind": "staff", "name": "发布员", "role": "Argo CD", "desc": "代码推到 GitHub，自动同步上线；有人手动改了集群就改回来", "link": "https://argocd.liuxd.dev/"},
    {"id": "watcher", "col": "staff", "kind": "staff", "name": "监控员", "role": "Prometheus · 拨测", "desc": "采集指标、从外面拨测网站和依赖，出问题发告警", "link": "https://grafana.liuxd.dev/"},
    {"id": "archivist", "col": "staff", "kind": "staff", "name": "档案员", "role": "备份", "desc": "每天凌晨把数据库和全部 GitHub 仓库加密备份到 R2"},
    {"id": "analyst", "col": "staff", "kind": "staff", "name": "统计员", "role": "Umami", "desc": "统计网站访问，不用 Cookie", "link": "https://stats.liuxd.dev/"},
    {"id": "gateway", "col": "data", "name": "模型网关", "desc": "所有模型调用都从这里走：选模型、记费用、每日上限", "link": "https://ops.liuxd.dev/gateway/"},
    {"id": "kb", "col": "data", "name": "知识库", "desc": "作品集资料、运维手册（RAG，带评测）；Dify 里的示例知识库", "link": "https://ops.liuxd.dev/rag/"},
    {"id": "dbs", "col": "data", "name": "业务数据", "desc": "账本和自愈记录、私人助理、访问统计、Dify"},
    {"id": "repos", "col": "data", "name": "GitHub 仓库", "desc": "全部项目的代码和文档", "link": "https://github.com/roccowork"},
    {"id": "k3s", "col": "platform", "name": "香港服务器", "desc": "单节点 K3s：网站、AI 服务、监控都跑在这里", "link": "https://ops.liuxd.dev/gitops/"},
    {"id": "models", "col": "platform", "name": "大模型", "desc": "阿里云百炼（默认）、OpenRouter（私人助理）"},
    {"id": "cloudflare", "col": "platform", "name": "Cloudflare", "desc": "DNS、静态网站（Pages）、备份存储（R2）", "link": "https://dash.cloudflare.com/"},
    {"id": "telegram", "col": "platform", "name": "Telegram", "desc": "告警、确认按钮、每日推送"},
    {"id": "news", "col": "platform", "name": "新闻源", "desc": "OpenAI、Google AI、Hugging Face、TechCrunch、The Verge、量子位、Hacker News"},
    {"id": "dify", "col": "platform", "name": "Dify", "desc": "AI 应用搭建平台，客服 Agent 演示", "link": "https://dify.liuxd.dev/"},
]

# 连线：flow 是判断「流动中」用的键，见 build() 里的 flows
EDGES = [
    ("visitors", "xiaoxiao", "提问", "asks"),
    ("visitors", "analyst", "访问记录", "traffic"),
    ("xiaoxiao", "kb", "检索资料", "asks"),
    ("xiaoxiao", "gateway", "生成回答", "asks"),
    ("editor", "gateway", "写口播稿", "frontier"),
    ("oncall", "gateway", "分析原因", "heal"),
    ("gateway", "models", "调用 API", "model_calls"),
    ("news", "editor", "新闻", "frontier"),
    ("editor", "butler", "选题和稿子", "frontier"),
    ("butler", "telegram", "每日推送", "butler"),
    ("watcher", "oncall", "告警", "alerts"),
    ("oncall", "k3s", "排查 / 自愈", "heal"),
    ("watcher", "k3s", "采集指标", "metrics"),
    ("watcher", "sites", "外部拨测", "metrics"),
    ("oncall", "telegram", "汇报、等我确认", "heal"),
    ("telegram", "me", "消息", "telegram"),
    ("me", "media", "发布视频", None),
    ("repos", "deployer", "代码", "deploy"),
    ("deployer", "k3s", "上线", "deploy"),
    ("repos", "cloudflare", "静态网站", None),
    ("archivist", "dbs", "导出", "backup"),
    ("archivist", "repos", "镜像", "backup_git"),
    ("archivist", "cloudflare", "加密备份到 R2", "backup"),
    ("analyst", "dbs", "写入", "traffic"),
    ("dify", "dbs", "写入", None),
]

SITE_PROBES = ["liuxd.dev", "cv.liuxd.dev", "roadmap.liuxd.dev", "ai-roadmap.liuxd.dev", "it.liuxd.dev"]
# Argo CD 应用 → 它管着的节点
ARGO_NODES = {"ops-desk": ["xiaoxiao", "oncall", "gateway"], "personal": ["butler", "editor"], "umami": ["analyst"], "git-backup": ["archivist"],
              "monitoring-extras": ["watcher"], "platform": ["k3s", "watcher"], "dify": ["dify"]}


def _ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return "没有记录"
    m = int((now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() // 60)
    return "刚刚" if m < 1 else f"{m} 分钟前" if m < 60 else f"{m // 60} 小时前" if m < 2880 else f"{m // 1440} 天前"


def _recent(iso: str | None, now: datetime, within: timedelta = DAY) -> bool:
    return bool(iso) and now - datetime.fromisoformat(iso.replace("Z", "+00:00")) <= within  # type: ignore[union-attr]


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
        probe, st, argo, asst, req, calls_1h, alerts = await asyncio.gather(
            _try(probes.fetch(self.c), "拨测"),
            _try(self.status(), "平台实况"),
            _try(self.argo(), "Argo CD"),
            _try(self.assistant(), "私人助理"),
            _try(self.scalar('sum(rate(traefik_service_requests_total{service="ops-desk-ops-desk-http@kubernetes"}[15m])) * 60'), "访问量"),
            _try(self.scalar("sum(increase(ai_calls_total[1h]))"), "模型调用"),
            _try(ops.run_tool(self.c, "list_alerts", {}), "告警"),
        )
        loc = self.local()
        pr = {p["name"]: p for p in probe or []}
        firing = [a for a in alerts or [] if a.get("state") == "firing" and a.get("severity") not in ("info", "none")]
        nodes = {n["id"]: {**n, "status": "unknown", "lines": []} for n in NODES}

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

        # 入口
        s, line = probe_line("ops.liuxd.dev")
        set_("visitors", s, line, f"最近 15 分钟 {req:.1f} 次请求 / 分钟" if req is not None else "")
        sites = [probe_line(n) for n in SITE_PROBES if n in pr]
        bad_sites = [n for n in SITE_PROBES if n in pr and not pr[n]["ok"]]
        if sites:
            set_("sites", "bad" if bad_sites else "ok", f"{len(sites) - len(bad_sites)}/{len(sites)} 个能打开", "打不开：" + "、".join(bad_sites) if bad_sites else "")
        set_("me", "ok" if pr.get("Telegram", {}).get("ok", True) else "warn", f"现在 {len(firing)} 条告警" if alerts is not None else "")
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
            if fr:
                ok_run = fr["status"] == "ok"
                set_("editor", "ok" if ok_run and _recent(fr["ts"], now) else ("bad" if fr["status"] == "error" else "warn"),
                     f"上次 {_ago(fr['ts'], now)}：{fr['picks']} 条选题", f"新闻源 {fr['sources_ok']}/{fr['sources']} 连通")
            else:
                set_("editor", "idle", "还没有运行过")
        apps = argo or []
        bad_apps = [a["name"] for a in apps if not (a["sync"] == "Synced" and a["health"] == "Healthy")]
        deploys = (st or {}).get("deploys") or []
        last_deploy = deploys[0].get("time") if deploys else None
        if argo is not None:
            set_("deployer", "warn" if bad_apps else "ok", f"{len(apps) - len(bad_apps)}/{len(apps)} 个应用已同步、健康", "有问题：" + "、".join(bad_apps) if bad_apps else "",
                 f"上次发布 {_ago(last_deploy, now)}" if last_deploy else "")
        own = [p for p in probe or [] if p["group"] == "own"]
        if probe is not None:
            set_("watcher", "warn" if firing else "ok", f"拨测 {sum(p['ok'] for p in probe)}/{len(probe)} 正常", f"现在 {len(firing)} 条告警" + ("：" + "、".join(str(a.get("name")) for a in firing[:3]) if firing else ""))
        backups = (st or {}).get("backups") or []
        stale = [b["name"] for b in backups if not _recent(b.get("lastSuccess"), now)]
        if backups:
            set_("archivist", "warn" if stale else "ok", f"{len(backups) - len(stale)}/{len(backups)} 个备份 26 小时内成功", "过期：" + "、".join(stale) if stale else "")
        s, line = probe_line("stats.liuxd.dev")
        if nodes["analyst"]["status"] == "unknown" and s != "unknown":
            nodes["analyst"]["status"] = s

        # 数据和知识
        set_("gateway", "ok" if today["calls"] or loc["enabled"] else "warn", f"今天 {today['calls']} 次调用，约 ¥{today['cost']:.2f}", f"最近 1 小时 {calls_1h:.0f} 次" if calls_1h is not None else "")
        kb = loc["kb"]
        set_("kb", "ok" if kb["ready"] else "warn", f"作品集资料 {kb['chunks']} 段" + ("" if kb["ready"] else "（索引准备中）"),
             f"问答评测：命中率 {kb['hit']}%" if kb.get("hit") is not None else "")
        db_backups = [b for b in backups if b["name"] != "backup-github"]
        if db_backups:
            set_("dbs", "warn" if any(b["name"] in stale for b in db_backups) else "ok", f"{len(db_backups)} 个数据库每天备份", f"最近一次 {_ago(max(b.get('lastSuccess') or '' for b in db_backups) or None, now)}")
        gh = next((b for b in backups if b["name"] == "backup-github"), None)
        s, _ = probe_line("GitHub")
        set_("repos", s if s != "unknown" else "ok", f"镜像备份 {_ago(gh.get('lastSuccess'), now)}" if gh else "")

        # 平台和外部
        set_("k3s", "warn" if firing else "ok", f"{len(own)} 个网站和后台在拨测" if own else "", f"7 天可用率 {st['availability7d']}%" if st and st.get("availability7d") is not None else "")
        s, line = probe_line("阿里云百炼")
        set_("models", s, line)
        s, line = probe_line("Cloudflare")
        set_("cloudflare", s, line)
        s, line = probe_line("Telegram")
        set_("telegram", s, line)
        if fr:
            set_("news", "ok" if fr["sources_ok"] == fr["sources"] else "warn", f"{fr['sources_ok']}/{fr['sources']} 个能读", f"上次 {fr['candidates']} 条候选")
        s, line = probe_line("dify.liuxd.dev")
        if nodes["dify"]["status"] in ("unknown", "ok"):
            nodes["dify"]["status"] = s if s != "unknown" else nodes["dify"]["status"]
        set_("dify", nodes["dify"]["status"], line)

        # 最近真有数据走过的连线
        flows = {
            "asks": ask > 0,
            "traffic": bool(req and req > 0),
            "frontier": bool(fr and fr["status"] == "ok" and _recent(fr["ts"], now)),
            "heal": bool(heal),
            "model_calls": bool(calls_1h and calls_1h > 0),
            "butler": bool(asst and asst.get("bot")),
            "alerts": bool(firing or heal),
            "metrics": probe is not None,
            "telegram": bool(asst and asst.get("bot")) or bool(heal),
            "deploy": _recent(last_deploy, now),
            "backup": bool(backups) and not stale,
            "backup_git": bool(gh and _recent(gh.get("lastSuccess"), now)),
        }
        edges = []
        for a, b, label, key in EDGES:
            broken = nodes[a]["status"] == "bad" or nodes[b]["status"] == "bad"
            edges.append({"from": a, "to": b, "label": label, "state": "bad" if broken else ("flow" if key and flows.get(key) else "idle")})
        staff = [n for n in nodes.values() if n.get("kind") == "staff"]
        return {
            "generated": now.isoformat(),
            "columns": COLUMNS,
            "nodes": list(nodes.values()),
            "edges": edges,
            "summary": {
                "staff": len(staff),
                "staff_ok": sum(1 for n in staff if n["status"] == "ok"),
                "problems": [n["name"] for n in nodes.values() if n["status"] in ("bad", "warn")],
                "flows": sum(1 for e in edges if e["state"] == "flow"),
                "alerts": len(firing),
            },
        }
