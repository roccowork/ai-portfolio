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
