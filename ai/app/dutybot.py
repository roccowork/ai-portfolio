"""网站值班台机器人的菜单内容：服务器状态、告警、自愈记录、访客留言、网站 AI 用量。

都只读，不调用模型、不花钱；直接打字提问才交给运维 Agent（会调用模型）。
只有主人（TELEGRAM_CHAT_ID）能用，所以这里可以显示访客留下的联系方式和完整的诊断。
"""

import html
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from . import autoheal, localtime
from . import opsagent as ops
from . import schedules
from .gateway import PUBLIC_FEATURES, Gateway
from .ledger import TZ, Ledger
from .telegram import to_html

e = html.escape
TOOL_ZH = {"search_runbook": "运维手册", "list_alerts": "告警", "cluster_overview": "服务器概况", "list_pods": "Pod 状态", "pod_events": "事件", "promql": "监控指标", "pod_logs": "日志"}

HELP = """<b>我是 OPS 值班台</b>，负责网站和服务器这边的事：

🖥 <b>服务器状态</b>：CPU、内存、磁盘，哪些 Pod 不正常，Argo CD 同步和外部拨测，系统盘几天写满，Prometheus 序列数和内存
🌐 <b>外部服务</b>：自己的 11 个网站和 5 个外部依赖（百炼、GitHub、GHCR、Cloudflare、Telegram）通不通、延迟、证书天数
📦 <b>Pod 资源</b>：内存、CPU 占用前 5 的 Pod（快到上限的标红），24 小时内重启过的，数据盘用量
🚨 <b>当前告警</b>：正在触发和等待中的告警
🩹 <b>自愈记录</b>：运维 Agent 最近处理的故障
📊 <b>平台实况</b>：最近发布、7 天可用率、备份、证书、7 天告警（和网页值班大屏同一份数据），加上 DORA 四个指标（部署频率、提交到上线、变更失败率、故障恢复）
🗓 <b>定时任务</b>：服务器上的备份、周报等任务，下次几点、上次成没成功
💬 <b>最近留言</b>：网站访客在 AI 助手里给你的留言
💰 <b>网站 AI 用量</b>：网站 AI 今天的调用次数、费用和剩余预算，本月各模型用量（阿里云余额和账单在私人助理）

按钮都不调用模型、不花钱。两个评测会花钱，不放在菜单里，要跑就手动输入：/eval（RAG 评测，2～3 分钟，一两毛钱）、/agenteval（Agent 故障演练，3～5 分钟，几毛钱，只读不动集群）。也可以直接打字问，运维 Agent 会自己查（每次花几分钱）：问状态，比如「为什么内存这么高」，它查实时数据；问怎么做，比如「证书快过期怎么办」「怎么回滚」，它查本站的运维手册，按手册回答并写明出处。

会主动推给你的：告警和运维 Agent 的诊断（需要确认的操作带「执行 / 忽略」按钮）、网站周报、R2 用量报告、访客新留言、客服 Agent 演示的预约。
吃药、提醒、简报在另一个机器人（私人助理）。"""


async def status_text(k8s: ops.Cluster, kuma: Callable[[], Awaitable[str | None]] | None = None) -> str:
    if not k8s.enabled:
        return "这里查不到服务器状态（没有集群权限）"
    st = await ops.run_tool(k8s, "cluster_overview", {})
    alerts = [a for a in await ops.run_tool(k8s, "list_alerts", {}) if a.get("state") == "firing" and a.get("severity") not in ("info", "none")]  # type: ignore[union-attr]
    bad = st.get("unhealthy_pods_by_namespace") or {}  # type: ignore[union-attr]
    # 不同类别之间空一行，手机上好读
    blocks = [
        "<b>🖥 服务器状态</b>" + ("  ✅ 正常" if not alerts else f"  ⚠️ {len(alerts)} 条告警"),
        f"⚙️ CPU {st.get('cpu_used_pct')}% · 内存 {st.get('mem_used_pct')}% · 磁盘 {st.get('disk_used_pct')}%\n"  # type: ignore[union-attr]
        + ("不正常的 Pod：" + "、".join(f"{e(k)} {v} 个" for k, v in bad.items()) if bad else "Pod 全部正常"),
        await _argo_line(k8s),
        await kuma() if kuma else None,
        "\n".join(x for x in (await _disk_forecast(k8s), await _prom_line(k8s)) if x),
        "<i>细看每个 Pod：点「📦 Pod 资源」</i>",
    ]
    return "\n\n".join(b for b in blocks if b)


async def _argo_line(k8s: ops.Cluster) -> str | None:
    """Argo CD 各应用是否已同步、健康：推送后有没有真正上线、有没有卡住"""
    try:
        apps = (await k8s.k8s("/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"))["items"]
    except Exception:  # noqa: BLE001 —— 没权限或 Argo CD 不在，就不显示这一行
        return None
    bad = []
    for a in apps:
        st = a.get("status") or {}
        sync, health = (st.get("sync") or {}).get("status"), (st.get("health") or {}).get("status")
        op = (st.get("operationState") or {}).get("phase")
        if sync != "Synced" or health != "Healthy" or op in ("Failed", "Error"):
            bad.append(f"{e(a['metadata']['name'])}（{SYNC_ZH.get(sync or '', sync or '?')}、{HEALTH_ZH.get(health or '', health or '?')}" + ("、上次同步失败" if op in ("Failed", "Error") else "") + "）")
    if not bad:
        return f"🔄 Argo CD：{len(apps)} 个应用全部已同步、健康 ✅"
    return f"⚠️ Argo CD：{len(apps) - len(bad)}/{len(apps)} 正常，" + "、".join(bad)


SYNC_ZH = {"Synced": "已同步", "OutOfSync": "没同步", "Unknown": "状态未知"}
HEALTH_ZH = {"Healthy": "健康", "Progressing": "更新中", "Degraded": "异常", "Missing": "缺失", "Suspended": "已暂停", "Unknown": "未知"}


def kuma_line(metrics: str) -> str | None:
    """Uptime Kuma /metrics 里每个检测的 monitor_status（1 在线、0 掉线、2 等待、3 维护）和 monitor_response_time（毫秒），
    每个检测一行。名字以 VPS 开头的不算"""
    status, ms = {}, {}
    for metric, labels, value in re.findall(r'^(monitor_status|monitor_response_time)\{([^}]*)\}\s+(\S+)', metrics, re.M):
        name = dict(re.findall(r'(\w+)="([^"]*)"', labels)).get("monitor_name", "?")
        if name.upper().startswith("VPS"):  # 翻墙 VPS 归私人助理管，这里只看本站的服务
            continue
        if metric == "monitor_status":
            status[name] = int(float(value))
        else:
            ms[name] = float(value)
    if not status:
        return None
    down = [n for n, v in status.items() if v == 0]
    head = f"外部拨测（Uptime Kuma，从服务器外面定时访问）：{len(status) - len(down)}/{len(status)} 在线" + (" ✅" if not down else "")
    icon = {0: "❌", 1: "✅", 2: "⏳", 3: "🔧"}
    rows = [f"　{icon.get(v, '?')} {e(n)}" + (f" · {ms[n]:.0f} ms" if v == 1 and ms.get(n, -1) >= 0 else "") for n, v in status.items()]
    return ("⚠️ " if down else "🌐 ") + head + "\n" + "\n".join(rows)


async def _q(k8s: ops.Cluster, q: str) -> list[dict]:
    """查询失败（指标不存在、Prometheus 暂时不可用）就当没有数据，按钮照样能出结果"""
    try:
        return await k8s.query(q)
    except Exception:  # noqa: BLE001
        return []


def _v(r: dict) -> float:
    return float(r["value"][1])


def _mib(b: float) -> str:
    return f"{b / 1024**3:.1f} GiB" if b >= 1024**3 else f"{b / 1024**2:.0f} MiB"


async def _disk_forecast(k8s: ops.Cluster) -> str | None:
    """系统盘：剩余多少，按最近 7 天的平均速度几天写满。
    窗口不能太短：一次性的大写入（比如第一次克隆全部 GitHub 仓库）会被当成持续增长，算出来「几十天写满」"""
    root = '{mountpoint="/", fstype!~"tmpfs|overlay"}'
    avail = await _q(k8s, f"max(node_filesystem_avail_bytes{root})")
    if not avail:
        return None
    left = _v(avail[0])
    slope = await _q(k8s, f"max(deriv(node_filesystem_avail_bytes{root}[7d]))")  # 每秒变化的字节数，负数表示在减少
    if slope and _v(slope[0]) < 0:
        days = left / -_v(slope[0]) / 86400
        when = f"按最近 7 天的速度约 {days:.0f} 天写满" if days < 365 else "增长很慢，一年内写不满"
    else:
        when = "最近 7 天没有增长"
    return f"💽 系统盘剩余 {_mib(left)}，{when}"


async def _prom_line(k8s: ops.Cluster) -> str | None:
    series = await _q(k8s, "max(prometheus_tsdb_head_series)")
    mem = await _q(k8s, 'max(container_memory_working_set_bytes{namespace="monitoring", pod=~"prometheus-.*", container="prometheus"})')
    lim = await _q(k8s, 'max(kube_pod_container_resource_limits{namespace="monitoring", pod=~"prometheus-.*", container="prometheus", resource="memory"})')
    if not series:
        return None
    line = f"📈 Prometheus：{_v(series[0]) / 1e4:.1f} 万条序列"
    if mem:
        line += f"，内存 {_mib(_v(mem[0]))}" + (f" / {_mib(_v(lim[0]))}" if lim else "")
    return line


def _who(r: dict) -> str:
    m = r["metric"]
    return f"{m.get('namespace', '?')}/{m.get('pod', '?')}"


async def pods_text(k8s: ops.Cluster) -> str:
    """Pod 资源：内存前 5（快到上限的标红）、CPU 前 5、24 小时内重启过的，加上各数据盘用量"""
    if not k8s.enabled:
        return "这里查不到 Pod（没有集群权限）"
    sel = '{container!="", image!=""}'
    by = "sum by (namespace, pod)"
    mem = await _q(k8s, f"topk(5, {by} (container_memory_working_set_bytes{sel}))")
    mem_lim = {_who(r): _v(r) for r in await _q(k8s, f'{by} (kube_pod_container_resource_limits{{resource="memory"}})')}
    cpu = await _q(k8s, f"topk(5, {by} (rate(container_cpu_usage_seconds_total{sel}[5m])))")
    cpu_lim = {_who(r): _v(r) for r in await _q(k8s, f'{by} (kube_pod_container_resource_limits{{resource="cpu"}})')}
    near = await _q(k8s, f'{by} (container_memory_working_set_bytes{sel}) / {by} (kube_pod_container_resource_limits{{resource="memory"}}) > 0.8')
    restarts = await _q(k8s, f"{by} (increase(kube_pod_container_status_restarts_total[24h])) >= 1")
    reasons = {_who(r): r["metric"].get("reason", "") for r in await _q(k8s, "kube_pod_container_status_last_terminated_reason == 1")}
    vols = await _q(k8s, "kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes")
    vol_used = {(r["metric"].get("namespace"), r["metric"].get("persistentvolumeclaim")): _v(r) for r in await _q(k8s, "kubelet_volume_stats_used_bytes")}

    lines = ["<b>📦 Pod 资源</b>", "", "<b>内存前 5</b>（用量 / 上限）"]
    for r in mem:
        who, used = _who(r), _v(r)
        lim = mem_lim.get(who)
        pct = f"（{used / lim * 100:.0f}%）" if lim else "（没设上限）"
        warn = "🔴 " if lim and used / lim > 0.8 else ""
        lines.append(f"{warn}{e(who)}  {_mib(used)}" + (f" / {_mib(lim)}" if lim else "") + pct)
    if not mem:
        lines.append("查不到")
    lines += ["", "<b>CPU 前 5</b>（核，最近 5 分钟平均）"]
    for r in cpu:
        who, used = _who(r), _v(r)
        lim = cpu_lim.get(who)
        lines.append(f"{e(who)}  {used:.2f}" + (f" / {lim:g}" if lim else " / 没设上限"))
    if not cpu:
        lines.append("查不到")
    lines += ["", "<b>快到内存上限的</b>（超过 80%，容易被 OOM 杀掉）"]
    lines += [f"🔴 {e(_who(r))}  {_v(r) * 100:.0f}%" for r in near] or ["没有 ✅"]
    lines += ["", "<b>24 小时内重启过的</b>"]
    lines += [f"{e(_who(r))}  {_v(r):.0f} 次" + (f"，上次退出原因 {e(reasons[_who(r)])}" if reasons.get(_who(r)) else "") for r in sorted(restarts, key=_v, reverse=True)[:8]] or ["没有 ✅"]
    if vols:
        lines += ["", "<b>数据盘</b>"]
        for r in sorted(vols, key=_v, reverse=True)[:6]:
            m = r["metric"]
            used = vol_used.get((m.get("namespace"), m.get("persistentvolumeclaim")))
            lines.append(f"{e(m.get('namespace', '?'))}/{e(m.get('persistentvolumeclaim', '?'))}  {_v(r) * 100:.0f}%" + (f"（{_mib(used)}）" if used else ""))
    return "\n".join(lines)


async def alerts_text(k8s: ops.Cluster) -> str:
    if not k8s.enabled:
        return "这里查不到告警（没有集群权限）"
    alerts = [a for a in await ops.run_tool(k8s, "list_alerts", {}) if a.get("severity") not in ("none",)]  # type: ignore[union-attr]
    if not alerts:
        return "<b>🚨 当前告警</b>\n\n没有告警 ✅"
    icon = {"critical": "🔴", "warning": "🟡"}
    lines = ["<b>🚨 当前告警</b>"]
    for a in alerts[:15]:
        where = " / ".join(x for x in (a.get("namespace"), a.get("pod")) if x)
        state = "" if a.get("state") == "firing" else "（等待中）"
        lines.append("")
        lines.append(f"{icon.get(a.get('severity') or '', '⚪')} <b>{e(str(a.get('name')))}</b>{state}" + (f"  {e(where)}" if where else ""))
        if a.get("summary"):
            lines.append(f"　{e(str(a['summary']))[:120]}")
    return "\n".join(lines)


def heal_text(store: autoheal.IncidentStore, limit: int = 5) -> str:
    items = autoheal.public_records(store, limit)
    if not items:
        return "<b>🩹 自愈记录</b>\n\n最近没有需要处理的故障 ✅"
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
        return "<b>💬 最近留言</b>\n\n还没有访客留言"
    lines = [f"<b>💬 最近留言</b>（最近 {len(items)} 条）"]
    for x in items:
        who = " · ".join(e(v) for v in (x["name"], x["company"]) if v) or "访客"
        lines.append(f"\n#{x['id']} {e(str(x['ts'])[:16].replace('T', ' '))}  {who}\n　联系方式：<code>{e(x['contact'])}</code>\n　{e(x['need'][:200])}")
    return "\n".join(lines)


def balance_line(b: dict | None, error: str = "") -> str:
    """阿里云账户余额（百炼从这里扣钱）。值班台已不显示（钱归私人助理），留着给私人助理复用"""
    if error:
        return f"🏦 阿里云余额：查不到（{e(error)}）"
    if not b:
        return ""
    sym = {"CNY": "¥", "USD": "$"}.get(b["currency"], b["currency"] + " ")
    lines = [f"🏦 阿里云账户还剩 {sym}{b['amount']:.2f}" + ("　⚠️ 快用完了" if b["amount"] < 5 else "")]
    if (r := b.get("recharge")) and b.get("spent") is not None:
        lines.append(f"　{r['time'][:10]} 充值 {sym}{r['amount']:.2f}（充值后余额 {sym}{r['balance']:.2f}），之后一共花了 {sym}{b['spent']:.2f}")
    elif b.get("error"):
        lines.append(f"　充值后花了多少：查不到（{e(b['error'])}）")
    if b.get("per_day") is not None:
        left = b.get("days_left")
        lines.append(f"　平均每天 {sym}{b['per_day']:.2f}" + (f"，照这个速度还能用约 {left} 天" if left is not None and left < 3650 else "，几乎不花钱"))
    if m := b.get("month"):
        top = "、".join(f"{e(n)} {sym}{v:.2f}" for n, v in m["products"][:4])
        lines.append(f"　本月（{m['cycle']}）账单 {sym}{m['total']:.2f}" + (f"：{top}" if top else ""))
        if lm := b.get("last_month"):
            lines.append(f"　上月（{lm['cycle']}）账单 {sym}{lm['total']:.2f}")
    elif b.get("bill_error"):
        lines.append(f"　月账单：查不到（{e(b['bill_error'])}）")
    if (models := b.get("models")) is not None:
        lines.append("　本月各模型（阿里云账单，实际扣费）：" + ("、".join(f"{e(n)} {sym}{v:.2f}" for n, v in models[:6]) or "还没有扣费（可能都在免费额度里）"))
    elif b.get("models_error"):
        lines.append(f"　各模型扣费：查不到（{e(b['models_error'])}）")
    return "\n".join(lines)


def _model_lines(ledger: Ledger, n: int = 6) -> list[str]:
    """本月每个模型用了多少（本站账本，按价格表估算；私人助理的调用不在这本账里）"""
    month = datetime.now(TZ).strftime("%Y-%m-01")
    rows = [r for r in ledger.model_usage(month) if r["calls"]]
    if not rows:
        return []
    out = ["", f"<b>本月各模型调用</b>（{month[:7]}，本站账本：次数和 token 准，费用是估算，以阿里云扣费为准）"]
    for r in rows[:n]:
        out.append(f"　{e(r['model'].split('/')[-1])}：{r['calls']} 次，{r['tokens'] / 10000:.1f} 万 token，约 ¥{r['cost']:.2f}")
    if len(rows) > n:
        rest = rows[n:]
        out.append(f"　其他 {len(rest)} 个：{sum(r['calls'] for r in rest)} 次，约 ¥{sum(r['cost'] for r in rest):.2f}")
    return out


def usage_text(ledger: Ledger, daily_budget: float, public_budget: float) -> str:
    s = ledger.summary()
    t, total = s["today"], s["total"]
    left = max(0.0, daily_budget - ledger.spent_today())
    public = ledger.spent_today(PUBLIC_FEATURES)
    return "\n".join(
        [
            "<b>💰 网站 AI 用量</b>",
            "",
            f"今天：{t['calls']} 次调用，约 ¥{t['cost']:.3f}",
            f"其中访客 ¥{public:.3f}，访客每日上限 ¥{public_budget:.2f}" + ("（已用完，访客的功能暂停到明天）" if public >= public_budget else ""),
            f"全站每日上限 ¥{daily_budget:.2f}（含值班台、自愈、评测），还剩 ¥{left:.2f}" + ("（已用完，全部暂停到明天）" if left <= 0 else ""),
            "",
            f"累计：{total['calls']} 次调用，约 ¥{total['cost']:.2f}",
            *_model_lines(ledger),
        ]
    )


def ago(iso: str | None, now: datetime | None = None) -> str:
    """「3 分钟前」「5 小时前」「2 天前」，和网页上的写法一样"""
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
        nums.append("p95 &lt; 100 ms" if st["p95Ms"] <= 100 else f"p95 {st['p95Ms']} ms")  # Traefik 最小的分桶是 0.1s
    if st.get("requests7d") is not None:
        nums.append(f"请求 {st['requests7d']:,}")
    if nums:
        lines.append("\n" + " · ".join(nums))
    state_zh = {"current": "当前版本", "not-ready": "⚠️ 未就绪（旧版本仍在服务）", "replaced": "已替换"}
    deploys = st.get("deploys")
    lines.append("\n🚀 <b>最近发布</b>" + ("：查询失败" if deploys is None else ("：暂无记录" if not deploys else "")))
    for d in (deploys or [])[:3]:
        lines.append(f"　<code>{e(d['sha'])}</code> {ago(d.get('time'), now)} · {state_zh.get(d['state'], d['state'])}")
    backups = st.get("backups")
    lines.append("\n💾 <b>备份</b>" + ("：查询失败" if backups is None else ("：没有备份任务" if not backups else "")))
    for b in backups or []:
        t = b.get("lastSuccess")
        stale = not t or (now - datetime.fromisoformat(t.replace("Z", "+00:00"))).total_seconds() > 26 * 3600
        lines.append(f"　{'⚠️' if stale else '✅'} {e(b['name'])}：最近成功 {ago(t, now)}")
    certs = st.get("certs")
    lines.append("\n🔐 <b>证书</b>" + ("：查询失败" if certs is None else ("：没有证书" if not certs else "")))
    for c in certs or []:
        low = c.get("daysLeft") is not None and c["daysLeft"] < 21
        lines.append(f"　{'⚠️' if low else '✅'} {e(c['name'])}：剩余 {c.get('daysLeft', '?')} 天")
    alerts = st.get("alerts7d")
    lines.append("\n🚨 <b>7 天告警</b>" + ("：查询失败" if alerts is None else ("：没有 ✅" if not alerts else "")))
    for a in alerts or []:
        lines.append(f"　{'🔴' if a.get('severity') == 'critical' else '🟡'} {e(a['name'])}：累计约 {a['minutes']} 分钟")
    lines.append("\n网页版（值班大屏）：https://ops.liuxd.dev/ops-agent/")
    return "\n".join(lines)


async def jobs_text(k8s: ops.Cluster, tz: str = localtime.TZ_KEY) -> str:
    if not k8s.enabled:
        return "这里查不到定时任务（没有集群权限）"
    items = (await k8s.k8s("/apis/batch/v1/cronjobs"))["items"]  # type: ignore[index]
    jobs = schedules.summarize(items, tz)
    lines = [f"<b>🗓 定时任务</b>（{localtime.LABEL}）"]
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
        gw.check_budget("dutybot")
        answer, steps, _ = await ops.investigate(gw, k8s, question[:500], public=False, extra=extra, feature="dutybot")
        used = "、".join(dict.fromkeys(TOOL_ZH.get(s["tool"], s["tool"]) for s in steps if s.get("tool")))
        return to_html(answer) + (f"\n\n<i>查了：{e(used)}</i>" if used else "")

    return ask


def _dur(hours: float | None) -> str:
    if hours is None:
        return "—"
    return f"{hours * 60:.0f} 分钟" if hours < 1 else f"{hours:.1f} 小时" if hours < 48 else f"{hours / 24:.1f} 天"


def dora_text(d: dict) -> str:
    """DORA 四个指标，接在「📊 平台实况」后面"""
    lv = d.get("levels") or {}
    tag = lambda k: f"（{lv[k]}）" if lv.get(k) else ""  # noqa: E731
    fail = "—" if d.get("failure_pct") is None else f"{d['failure_pct']:.0f}%"
    return "\n".join([
        f"<b>📐 DORA 指标</b>（最近 {d['days']:g} 天，{d['deploys']} 次发布）",
        "",
        f"部署频率：每天 {d['deploys_per_day']:g} 次{tag('deploys_per_day')}",
        f"提交到上线：{_dur(d.get('lead_hours'))}{tag('lead_hours')}",
        f"变更失败率：{fail}{tag('failure_pct')}",
        f"故障恢复：{_dur(d.get('restore_hours'))}{tag('restore_hours')}" + ("" if d.get("restore_hours") is not None else "（还没有恢复记录）"),
    ])

