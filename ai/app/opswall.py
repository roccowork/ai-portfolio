"""运维大屏（/ops-agent 页面）的实时数据：直接查 Prometheus、K8s 和平台实况，不调用模型、不花钱。

结果缓存一小会儿（概况 15 秒、趋势 60 秒）：访客再多，Prometheus 也只被查一次。
每一块单独取，某一块查不到（比如 Prometheus 重启中）就是 null，其他照常显示。
公开页面用，只返回指标和状态：没有日志、IP、Secret。
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from . import opsagent as ops

log = logging.getLogger("ai")

SITE = 'service="ops-desk-ops-desk-http@kubernetes"'
NS = "|".join(ops.NAMESPACES)
SCALARS = {
    "cpu_cores": 'count(node_cpu_seconds_total{mode="idle"})',
    "mem_total_gb": "sum(node_memory_MemTotal_bytes) / 1024^3",
    "disk_total_gb": 'max(node_filesystem_size_bytes{mountpoint="/"}) / 1024^3',
    "uptime_days": "(time() - max(node_boot_time_seconds)) / 86400",
    "req_per_min": f"sum(rate(traefik_service_requests_total{{{SITE}}}[5m])) * 60",
    # 没有 5xx 时分子那一组是空的，整个表达式也会是空：用 or vector(0) 补成 0
    "err_5xx_pct": f'100 * (sum(rate(traefik_service_requests_total{{{SITE},code=~"5.."}}[5m])) or vector(0)) / clamp_min(sum(rate(traefik_service_requests_total{{{SITE}}}[5m])), 1e-9)',
}
# 跑完的 Job（备份、周报等 CronJob 留下的 Pod）是 Succeeded，不算「没在运行」
PODS = f'sum by (namespace, phase) (kube_pod_status_phase{{namespace=~"{NS}",phase!="Succeeded"}}) > 0'
TREND = {
    "cpu": '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "mem": "100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))",
    "req": f"sum(rate(traefik_service_requests_total{{{SITE}}}[5m])) * 60",
}


class Cache:
    """同一时间只有一个请求去查，其他的等着用它的结果"""

    def __init__(self, ttl: float):
        self.ttl, self.at, self.value = ttl, 0.0, None
        self.lock = asyncio.Lock()

    async def get(self, fn: Callable[[], Awaitable[dict]]) -> dict:
        async with self.lock:
            if self.value is None or time.monotonic() - self.at > self.ttl:
                self.value, self.at = await fn(), time.monotonic()
            return self.value


async def _try(coro, what: str):
    try:
        return await coro
    except Exception as e:  # noqa: BLE001  一块查不到不影响其他块
        log.info("大屏数据 %s 查不到：%s", what, str(e)[:120])
        return None


class OpsWall:
    def __init__(self, c: ops.Cluster, status: Callable[[], Awaitable[dict]], extras: Callable[[], dict]):
        """status：平台实况 /status.json；extras：账本里的评测分数、自愈统计（同步函数）"""
        self.c, self.status, self.extras = c, status, extras
        self._overview, self._trend = Cache(15), Cache(60)

    async def scalar(self, q: str) -> float | None:
        res = await self.c.query(q)
        return round(float(res[0]["value"][1]), 2) if res else None

    async def pods(self) -> dict:
        out: dict[str, dict[str, int]] = {}
        for r in await self.c.query(PODS):
            ns, phase = r["metric"]["namespace"], r["metric"]["phase"]
            d = out.setdefault(ns, {"running": 0, "total": 0})
            d["total"] += int(float(r["value"][1]))
            if phase == "Running":
                d["running"] += int(float(r["value"][1]))
        return out

    async def _build_overview(self) -> dict:
        if not self.c.enabled:
            return {"enabled": False}
        names = list(SCALARS)
        node, alerts, pods, status, *vals = await asyncio.gather(
            _try(ops.run_tool(self.c, "cluster_overview", {}), "概况"),
            _try(ops.run_tool(self.c, "list_alerts", {}), "告警"),
            _try(self.pods(), "Pod"),
            _try(self.status(), "平台实况"),
            *(_try(self.scalar(SCALARS[n]), n) for n in names),
        )
        st = status or {}
        deploy = (st.get("deploys") or [None])[0]
        certs = st.get("certs") or []
        return {
            "enabled": True,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "node": node,
            **dict(zip(names, vals, strict=True)),
            "pods": pods,
            "alerts": [{k: a.get(k) for k in ("name", "severity", "state", "namespace", "since", "summary")} for a in alerts] if alerts is not None else None,
            "availability7d": st.get("availability7d"),
            "p95_ms": st.get("p95Ms"),
            "deploy": {"sha": deploy.get("sha"), "time": deploy.get("time")} if deploy else None,
            "cert_days_left": min((x["daysLeft"] for x in certs), default=None),
            "backups": st.get("backups"),
            **self.extras(),
        }

    async def overview(self) -> dict:
        return await self._overview.get(self._build_overview)

    async def _build_trend(self) -> dict:
        if not self.c.enabled:
            return {"enabled": False}
        end = time.time()

        async def series(q: str) -> list[list[float]]:
            r = await self.c.prom.get("/api/v1/query_range", params={"query": q, "start": end - 3600, "end": end, "step": 60})
            r.raise_for_status()
            res = r.json()["data"]["result"]
            return [[float(t), round(float(v), 2)] for t, v in res[0]["values"]] if res else []

        names = list(TREND)
        vals = await asyncio.gather(*(_try(series(TREND[n]), f"趋势 {n}") for n in names))
        return {"enabled": True, **{n: v or [] for n, v in zip(names, vals, strict=True)}}

    async def trend(self) -> dict:
        return await self._trend.get(self._build_trend)
