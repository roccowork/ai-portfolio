"""外部拨测结果（Blackbox Exporter，见 deploy/monitoring/blackbox.yaml）：自己的网站和外部依赖通不通、多快、证书还剩几天。
值班台「🌐 外部服务」和值班大屏共用。只查 Prometheus，不调用模型。"""

import html

from . import opsagent as ops

GROUP_ZH = {"own": "自己的网站", "dependency": "外部依赖"}


async def fetch(c: ops.Cluster) -> list[dict]:
    """[{name, group, ok, ms, cert_days}]，自己的网站在前，各组内按名字排"""
    sel = '{job=~"probe-.*"}'
    out: dict[tuple[str, str], dict] = {}

    def key(r: dict) -> tuple[str, str]:
        m = r["metric"]
        return m.get("group", ""), m.get("name") or m.get("instance", "?")

    for r in await c.query(f"probe_success{sel}"):
        g, n = key(r)
        out[(g, n)] = {"name": n, "group": g, "ok": float(r["value"][1]) == 1, "ms": None, "cert_days": None}
    for r in await c.query(f"probe_duration_seconds{sel}"):
        if (item := out.get(key(r))) is not None:
            item["ms"] = round(float(r["value"][1]) * 1000)
    for r in await c.query(f"(probe_ssl_earliest_cert_expiry{sel} - time()) / 86400"):
        if (item := out.get(key(r))) is not None:
            item["cert_days"] = round(float(r["value"][1]))
    return sorted(out.values(), key=lambda x: (x["group"] != "own", x["name"]))


def text(items: list[dict]) -> str:
    if not items:
        return "<b>🌐 外部服务</b>\n还没有拨测数据（Blackbox Exporter 刚部署时要等一两分钟）"
    items = [x for x in items if x["group"] != "vps"]  # VPS 在私人助理那边看（资源中心的 VPS 卡片）
    bad = [x for x in items if not x["ok"]]
    lines = ["<b>🌐 外部服务</b>" + ("  ✅ 全部正常" if not bad else f"  ⚠️ {len(bad)} 个不通")]
    for g in ("own", "dependency"):
        group = [x for x in items if x["group"] == g]
        if not group:
            continue
        lines += ["", f"<b>{GROUP_ZH[g]}</b>"]
        for x in group:
            ms = f"{x['ms']} ms" if x["ms"] is not None else "—"
            cert = ""
            if g == "own" and x["cert_days"] is not None:
                cert = f" · 证书 {x['cert_days']} 天" + (" ⚠️" if x["cert_days"] < 14 else "")
            lines.append(f"{'🟢' if x['ok'] else '🔴'} {html.escape(x['name'])}  {ms}{cert}")
    lines += ["", "<i>每分钟从服务器探测一次；外部依赖返回 401/403 也算在线（没带密钥）</i>"]
    return "\n".join(lines)


# 每个拨测目标是干什么的（总控台卡片和详情里显示一句）
ABOUT = {
    "ops.liuxd.dev": "作品集网站：项目案例、值班大屏、AI 问答，总控台也在这里",
    "liuxd.dev": "个人名片页",
    "cv.liuxd.dev": "简历网站",
    "roadmap.liuxd.dev": "运维自动化职业路线图",
    "ai-roadmap.liuxd.dev": "AI 应用开发学习路线图",
    "it.liuxd.dev": "IT 外包服务官网",
    "grafana.liuxd.dev": "监控仪表盘（Grafana）",
    "argocd.liuxd.dev": "发布系统（Argo CD）：仓库推送后自动同步上线",
    "push.liuxd.dev": "LabPager App 的推送中转",
    "stats.liuxd.dev": "网站访问统计（Umami）",
    "prometheus.liuxd.dev": "监控数据接口，给 LabPager App 用（要密码）",
    "阿里云百炼": "大模型接口：网站 AI 问答、运维 Agent",
    "GitHub": "全部项目的代码、文档和 CI，每天镜像备份到 R2",
    "GHCR 镜像仓库": "容器镜像仓库：上线时从这里拉镜像",
    "Cloudflare": "DNS、Pages 网站托管、R2 备份",
    "Telegram": "值班台和私人助理机器人：告警推送",
}

# 托管在 Cloudflare Pages 的（挂了集群内部看不出来，证书由 Cloudflare 续期）；其余自己的网站在这台服务器上，证书由 cert-manager 续期
CF_PAGES = {"liuxd.dev", "cv.liuxd.dev", "roadmap.liuxd.dev", "ai-roadmap.liuxd.dev", "it.liuxd.dev"}


async def site_text(c: ops.Cluster, name: str) -> str:
    """总控台点开单个网站 / 外部依赖：现在的状态、24 小时可用率和延迟、证书到期日、在哪托管、证书谁续期"""
    sel = f'{{job=~"probe-.*", name="{name}"}}'  # name 已在调用处校验过字符

    async def one(q: str) -> float | None:
        r = await c.query(q)
        return float(r[0]["value"][1]) if r else None

    ok = await one(f"probe_success{sel}")
    if ok is None:
        return f"<b>{html.escape(name)}</b>\n\n没有这个拨测目标"
    ms = await one(f"probe_duration_seconds{sel}")
    up = await one(f"avg_over_time(probe_success{sel}[24h]) * 100")
    avg = await one(f"avg_over_time(probe_duration_seconds{sel}[24h]) * 1000")
    worst = await one(f"max_over_time(probe_duration_seconds{sel}[24h]) * 1000")
    cert = await one(f"probe_ssl_earliest_cert_expiry{sel}")
    group = (await c.query(f"probe_success{sel}"))[0]["metric"].get("group", "")
    lines = [f"<b>{html.escape(name)}</b>  " + ("🟢 正常" if ok == 1 else "🔴 打不开")]
    if name in ABOUT:
        lines.append(html.escape(ABOUT[name]))
    lines.append("")
    if ms is not None:
        lines.append(f"现在 {ms * 1000:.0f} ms")
    if up is not None:
        lines.append(f"24 小时可用率 {up:.2f}%" + (f" · 平均 {avg:.0f} ms · 最慢 {worst:.0f} ms" if avg is not None and worst is not None else ""))
    if cert is not None:
        from datetime import datetime, timezone

        from .localtime import TZ

        left = (cert - datetime.now(timezone.utc).timestamp()) / 86400
        lines.append(f"证书 {datetime.fromtimestamp(cert, TZ):%Y-%m-%d} 到期（还剩 {left:.0f} 天）")
    lines.append("")
    if group == "own":
        if name in CF_PAGES:
            lines.append("托管在 Cloudflare Pages，证书由 Cloudflare 自动续期")
        else:
            lines.append("在这台服务器上（K3s），证书由 cert-manager 到期前 30 天自动续期；剩不到 21 天还没续上会告警")
        lines.append("证书剩不到 14 天、或连续打不开会告警（值班台推送）")
    else:
        lines.append("外部依赖：不带密钥访问，返回 401 / 403 也算在线")
    lines += ["", "<i>延迟是从香港服务器每分钟探测一次（Blackbox Exporter）测的：一次完整的 HTTPS 请求（DNS、连接、TLS 握手、等响应），不是你在本地打开的速度</i>"]
    return "\n".join(lines)
