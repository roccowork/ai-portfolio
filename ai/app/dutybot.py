"""网站值班台机器人的菜单内容：服务器状态、告警、自愈记录、访客留言、今日用量。

都只读，不调用模型、不花钱；直接打字提问才交给运维 Agent（会调用模型）。
只有主人（TELEGRAM_CHAT_ID）能用，所以这里可以显示访客留下的联系方式和完整的诊断。
"""

import html
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from . import autoheal
from . import opsagent as ops
from . import schedules
from .gateway import Gateway
from .ledger import Ledger
from .telegram import to_html

e = html.escape
TOOL_ZH = {"search_runbook": "运维手册", "list_alerts": "告警", "cluster_overview": "服务器概况", "list_pods": "Pod 状态", "pod_events": "事件", "promql": "监控指标", "pod_logs": "日志"}

HELP = """<b>我是 OPS 值班台</b>，负责网站和服务器这边的事：

🖥 <b>服务器状态</b>：CPU、内存、磁盘，哪些 Pod 不正常
🚨 <b>当前告警</b>：正在触发和等待中的告警
🩹 <b>自愈记录</b>：运维 Agent 最近处理的故障
📊 <b>平台实况</b>：最近发布、7 天可用率、备份、证书、7 天告警（和网页 /live 同一份数据）
🗓 <b>定时任务</b>：服务器上的备份、周报等任务，下次几点、上次成没成功
💬 <b>最近留言</b>：网站访客在 AI 助手里给你的留言
📏 <b>RAG 评测</b>：用评测集把作品集问答跑一遍（1～2 分钟，花几分钱），结果公开在 /api/ai/eval/latest
💰 <b>今日用量</b>：AI 演示今天的调用次数、费用和剩余预算

除了「RAG 评测」，按钮都不调用模型、不花钱。也可以直接打字问，运维 Agent 会自己查（每次花几分钱）：问状态，比如「为什么内存这么高」，它查实时数据；问怎么做，比如「证书快过期怎么办」「怎么回滚」，它查本站的运维手册，按手册回答并写明出处。

会主动推给你的：告警和运维 Agent 的诊断（需要确认的操作带「执行 / 忽略」按钮）、网站周报、R2 用量报告、访客新留言、客服 Agent 演示的预约。
吃药、提醒、简报在另一个机器人（私人助理）。"""


async def status_text(k8s: ops.Cluster) -> str:
    if not k8s.enabled:
        return "这里查不到服务器状态（没有集群权限）"
    st = await ops.run_tool(k8s, "cluster_overview", {})
    alerts = [a for a in await ops.run_tool(k8s, "list_alerts", {}) if a.get("state") == "firing" and a.get("severity") not in ("info", "none")]  # type: ignore[union-attr]
    lines = [
        "<b>🖥 服务器状态</b>" + ("  ✅ 正常" if not alerts else f"  ⚠️ {len(alerts)} 条告警"),
        f"CPU {st.get('cpu_used_pct')}% · 内存 {st.get('mem_used_pct')}% · 磁盘 {st.get('disk_used_pct')}%",  # type: ignore[union-attr]
    ]
    bad = st.get("unhealthy_pods_by_namespace") or {}  # type: ignore[union-attr]
    lines.append("不正常的 Pod：" + "、".join(f"{e(k)} {v} 个" for k, v in bad.items()) if bad else "Pod 全部正常")
    return "\n".join(lines)


async def alerts_text(k8s: ops.Cluster) -> str:
    if not k8s.enabled:
        return "这里查不到告警（没有集群权限）"
    alerts = [a for a in await ops.run_tool(k8s, "list_alerts", {}) if a.get("severity") not in ("none",)]  # type: ignore[union-attr]
    if not alerts:
        return "<b>🚨 当前告警</b>\n没有告警 ✅"
    icon = {"critical": "🔴", "warning": "🟡"}
    lines = ["<b>🚨 当前告警</b>"]
    for a in alerts[:15]:
        where = " / ".join(x for x in (a.get("namespace"), a.get("pod")) if x)
        state = "" if a.get("state") == "firing" else "（等待中）"
        lines.append(f"{icon.get(a.get('severity') or '', '⚪')} <b>{e(str(a.get('name')))}</b>{state}" + (f"  {e(where)}" if where else ""))
        if a.get("summary"):
            lines.append(f"　{e(str(a['summary']))[:120]}")
    return "\n".join(lines)


def heal_text(store: autoheal.IncidentStore, limit: int = 5) -> str:
    items = autoheal.public_records(store, limit)
    if not items:
        return "<b>🩹 自愈记录</b>\n最近没有需要处理的故障 ✅"
    lines = ["<b>🩹 自愈记录</b>（最近 5 条）"]
    for r in items:
        lines.append(f"\n{e(r['time'])} <b>{e(r['alert'])}</b>  {e(r['status'])}")
        if r["action"]:
            lines.append(f"　处理：{e(r['action'])}" + (f"（{e(r['mode'])}）" if r["mode"] else ""))
        if r["diagnosis"]:
            lines.append(f"　{e(r['diagnosis'][:150])}")
        if r["result"]:
            lines.append(f"　结果：{e(r['result'][:100])}")
    return "\n".join(lines)


def leads_text(ledger: Ledger, limit: int = 5) -> str:
    items = ledger.leads(limit)
    if not items:
        return "<b>💬 最近留言</b>\n还没有访客留言"
    lines = [f"<b>💬 最近留言</b>（最近 {len(items)} 条）"]
    for x in items:
        who = " · ".join(e(v) for v in (x["name"], x["company"]) if v) or "访客"
        lines.append(f"\n#{x['id']} {e(str(x['ts'])[:16].replace('T', ' '))}  {who}\n　联系方式：<code>{e(x['contact'])}</code>\n　{e(x['need'][:200])}")
    return "\n".join(lines)


def usage_text(ledger: Ledger, daily_budget: float) -> str:
    s = ledger.summary()
    t, total = s["today"], s["total"]
    left = max(0.0, daily_budget - ledger.spent_today())
    return "\n".join(
        [
            "<b>💰 今日用量</b>（网站的 AI 演示）",
            f"今天：{t['calls']} 次调用，约 ¥{t['cost']:.3f}",
            f"每日预算 ¥{daily_budget:.2f}，还剩 ¥{left:.2f}" + ("（已用完，演示暂停到明天）" if left <= 0 else ""),
            f"累计：{total['calls']} 次调用，约 ¥{total['cost']:.2f}",
        ]
    )


def ago(iso: str | None, now: datetime | None = None) -> str:
    """「3 分钟前」「5 小时前」「2 天前」，和网页 /live 的写法一样"""
    if not iso:
        return "未知"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "未知"
    minutes = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds() // 60))
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟前"
    return f"{minutes // 60} 小时前" if minutes < 48 * 60 else f"{minutes // 1440} 天前"


def platform_text(st: dict, now: datetime | None = None) -> str:
    """平台实况：/status.json（集群里的 CronJob 每 10 分钟生成）。任何一项都可能是 null（那一项查询失败）"""
    now = now or datetime.now(timezone.utc)
    lines = [f"<b>📊 平台实况</b>（数据更新于 {ago(st.get('generatedAt'), now)}）"]
    nums = []
    if st.get("availability7d") is not None:
        nums.append(f"7 天可用率 {st['availability7d']}%")
    if st.get("p95Ms") is not None:
        nums.append("p95 < 100 ms" if st["p95Ms"] <= 100 else f"p95 {st['p95Ms']} ms")  # Traefik 最小的分桶是 0.1s
    if st.get("requests7d") is not None:
        nums.append(f"请求 {st['requests7d']:,}")
    if nums:
        lines.append(" · ".join(nums))
    state_zh = {"current": "当前版本", "not-ready": "⚠️ 未就绪（旧版本仍在服务）", "replaced": "已替换"}
    deploys = st.get("deploys")
    lines.append("\n🚀 <b>最近发布</b>" + ("：查询失败" if deploys is None else ("：暂无记录" if not deploys else "")))
    for d in (deploys or [])[:3]:
        lines.append(f"　<code>{e(d['sha'])}</code> {ago(d.get('time'), now)} · {state_zh.get(d['state'], d['state'])}")
    backups = st.get("backups")
    lines.append("💾 <b>备份</b>" + ("：查询失败" if backups is None else ("：没有备份任务" if not backups else "")))
    for b in backups or []:
        t = b.get("lastSuccess")
        stale = not t or (now - datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() > 26 * 3600
        lines.append(f"　{'⚠️' if stale else '✅'} {e(b['name'])}：最近成功 {ago(t, now)}")
    certs = st.get("certs")
    lines.append("🔐 <b>证书</b>" + ("：查询失败" if certs is None else ("：没有证书" if not certs else "")))
    for c in certs or []:
        low = c.get("daysLeft") is not None and c["daysLeft"] < 21
        lines.append(f"　{'⚠️' if low else '✅'} {e(c['name'])}：剩余 {c.get('daysLeft', '?')} 天")
    alerts = st.get("alerts7d")
    lines.append("🚨 <b>7 天告警</b>" + ("：查询失败" if alerts is None else ("：没有 ✅" if not alerts else "")))
    for a in alerts or []:
        lines.append(f"　{'🔴' if a.get('severity') == 'critical' else '🟡'} {e(a['name'])}：累计约 {a['minutes']} 分钟")
    lines.append("\n网页版：https://ops.liuxd.dev/live/")
    return "\n".join(lines)


async def jobs_text(k8s: ops.Cluster, tz: str = "Asia/Singapore") -> str:
    if not k8s.enabled:
        return "这里查不到定时任务（没有集群权限）"
    items = (await k8s.k8s("/apis/batch/v1/cronjobs"))["items"]  # type: ignore[index]
    jobs = schedules.summarize(items, tz)
    lines = ["<b>🗓 定时任务</b>（新加坡时间）"]
    for group, head in (("push", "📮 会发消息的"), ("ops", "🔧 后台维护（不发消息，出问题才告警）")):
        rows = [j for j in jobs if j["group"] == group]
        if rows:
            lines += ["", f"<b>{head}</b>"]
        for j in rows:
            last = {True: "上次 ✅", False: "上次 ❌ 失败", None: "还没跑过", "running": "⏳ 正在跑"}[j["last_ok"]]
            nxt = "已暂停" if j["suspended"] else (f"下次 {j['next']:%m-%d %H:%M}" if j["next"] else "")
            lines.append(f"{e(j['title'])} · {e(j['when'])}\n　{' · '.join(x for x in (nxt, last) if x)}")
    return "\n".join(lines)


def make_ask(gw: Gateway, k8s: ops.Cluster, extra: dict | None = None) -> Callable[[str], Awaitable[str]]:
    """直接打字的问题交给运维 Agent。主人自己用，可以查日志（日志先脱敏）；extra 里是「查运维手册」等额外工具。"""

    async def ask(question: str) -> str:
        if not k8s.enabled:
            return "运维 Agent 只能在集群里运行（没有集群权限）"
        gw.check_budget("opsagent")
        answer, steps, _ = await ops.investigate(gw, k8s, question[:500], public=False, extra=extra)
        used = "、".join(dict.fromkeys(TOOL_ZH.get(s["tool"], s["tool"]) for s in steps if s.get("tool")))
        return to_html(answer) + (f"\n\n<i>查了：{e(used)}</i>" if used else "")

    return ask
