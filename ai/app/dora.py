"""DORA 四个指标：用本站自己的真实数据算，衡量交付的速度和稳定性。

- 部署频率：GitHub 上 CI 写回镜像标签的提交（「deploy: ops-desk <sha>」），一条就是一次发布
- 变更前置时间：被发布的那个提交 → 新版本开始上线。上线时间优先用集群里新 ReplicaSet 的创建时间（/status.json），
  没有就用写回镜像标签的时间（之后 Argo CD 一两分钟内同步）。一次推送有多个提交时按最后一个算，偏乐观
- 变更失败率：发布后 1 小时内 ops-desk 命名空间出了告警，或者之后被 revert 的发布，占全部发布的比例
- 恢复时间：告警从开始到恢复的中位数（自愈记录里有恢复时间的那些）

只读 GitHub 公开 API（不带令牌，每小时 60 次，结果缓存 1 小时）和本地数据，不调用模型。"""

import statistics
from datetime import datetime, timedelta, timezone

import httpx

REPO = "roccowork/ai-portfolio"
API = f"https://api.github.com/repos/{REPO}"


def _t(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


async def fetch_commits(since: datetime, transport: httpx.AsyncBaseTransport | None = None) -> list[dict]:
    """[{sha, time, message}]，最新的在前，最多 300 条"""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=15, transport=transport, headers={"Accept": "application/vnd.github+json"}) as c:
        for page in (1, 2, 3):
            r = await c.get(f"{API}/commits", params={"since": since.isoformat(), "per_page": 100, "page": page})
            r.raise_for_status()
            rows = r.json()
            out += [{"sha": x["sha"], "time": x["commit"]["committer"]["date"], "message": x["commit"]["message"]} for x in rows]
            if len(rows) < 100:
                break
    return out


def level(name: str, v: float | None) -> str | None:
    """按 DORA 报告的分档（精英 / 高 / 中 / 低），v 的单位见各指标"""
    if v is None:
        return None
    bands = {
        "deploys_per_day": [(1, "精英"), (1 / 7, "高"), (1 / 30, "中")],  # 每天至少一次 / 每周 / 每月
        "lead_hours": [(-24, "精英"), (-24 * 7, "高"), (-24 * 30, "中")],  # 一天内 / 一周内 / 一个月内（取负数统一成「越大越好」）
        "failure_pct": [(-5, "精英"), (-10, "高"), (-15, "中")],
        "restore_hours": [(-1, "精英"), (-24, "高"), (-24 * 7, "中")],
    }[name]
    x = v if name == "deploys_per_day" else -v
    return next((lv for th, lv in bands if x >= th), "低")


def compute(commits: list[dict], rollouts: list[dict], incidents: list[dict], days: int, now: datetime | None = None) -> dict:
    """commits：fetch_commits 的结果；rollouts：/status.json 的 deploys（sha、time）；incidents：自愈记录"""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    by_sha = {c["sha"][:7]: c for c in commits}
    rollout_at = {d["sha"][:7]: _t(d["time"]) for d in rollouts if d.get("time")}
    reverted = {w.strip(".")[:7] for c in commits if c["message"].startswith("Revert") for w in c["message"].split() if len(w.strip(".")) >= 7}

    deploys = []
    for c in commits:
        msg = c["message"]
        if not msg.startswith("deploy: ") or _t(c["time"]) < since:
            continue
        src = msg.split()[2][:7]
        live = rollout_at.get(src, _t(c["time"]))
        source = by_sha.get(src)
        lead = (live - _t(source["time"])).total_seconds() / 3600 if source else None
        deploys.append({"sha": src, "time": live.isoformat(), "lead_hours": round(lead, 2) if lead is not None and lead >= 0 else None})
    deploys.sort(key=lambda d: d["time"])

    site_alerts = [_t(i["started"] or i["ts"]) for i in incidents if (i.get("labels") or {}).get("namespace") == "ops-desk"]
    for d in deploys:
        t = _t(d["time"])
        d["failed"] = d["sha"] in reverted or any(t <= a <= t + timedelta(hours=1) for a in site_alerts)

    restores = [(_t(i["resolved_ts"]) - _t(i["started"] or i["ts"])).total_seconds() / 3600 for i in incidents if i.get("resolved_ts")]
    restores = [h for h in restores if h >= 0]
    leads = [d["lead_hours"] for d in deploys if d["lead_hours"] is not None]
    first = min((_t(c["time"]) for c in commits), default=now)
    span = max(1.0, (now - max(since, first)).total_seconds() / 86400)  # 仓库 2026-10-29 才拆出来，不满 days 天按实际天数算

    m = {
        "deploys_per_day": round(len(deploys) / span, 2),
        "lead_hours": round(statistics.median(leads), 2) if leads else None,
        "failure_pct": round(sum(d["failed"] for d in deploys) / len(deploys) * 100, 1) if deploys else None,
        "restore_hours": round(statistics.median(restores), 2) if restores else None,
    }
    return {
        "days": round(span, 1),
        "deploys": len(deploys),
        "incidents": len(incidents),
        "restored": len(restores),
        **m,
        "levels": {k: level(k, v) for k, v in m.items()},
        "recent": deploys[-10:][::-1],
        "ts": now.isoformat(timespec="seconds"),
    }
