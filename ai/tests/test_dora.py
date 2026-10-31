"""DORA：发布从「deploy:」提交里认，上线时间优先用 ReplicaSet，发布后 1 小时内站点告警或被 revert 算失败，恢复时间取中位数"""

from datetime import datetime, timezone

from app import dora

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
COMMITS = [  # 最新的在前，和 GitHub API 一致
    {"sha": "e" * 40, "time": "2026-10-31T10:00:00Z", "message": 'Revert "加了个 bug" This reverts commit ccccccc1234.'},
    {"sha": "d" * 40, "time": "2026-10-31T09:05:00Z", "message": "deploy: ops-desk ccccccc [skip ci]"},
    {"sha": "c" * 40, "time": "2026-10-31T09:00:00Z", "message": "加了个 bug"},
    {"sha": "b" * 40, "time": "2026-10-30T08:04:00Z", "message": "deploy: ops-desk aaaaaaa [skip ci]"},
    {"sha": "a" * 40, "time": "2026-10-30T08:00:00Z", "message": "改首页"},
]


def test_compute():
    rollouts = [{"sha": "aaaaaaa", "time": "2026-10-30T08:06:00Z"}]
    incidents = [
        {"ts": "2026-10-30T08:30:00Z", "started": "2026-10-30T08:30:00Z", "resolved_ts": "2026-10-30T08:50:00Z", "labels": {"namespace": "ops-desk"}},
        {"ts": "2026-10-31T01:00:00Z", "started": "2026-10-31T01:00:00Z", "resolved_ts": "2026-10-31T03:00:00Z", "labels": {"namespace": "monitoring"}},
    ]
    r = dora.compute(COMMITS, rollouts, incidents, 30, now=NOW)
    assert r["deploys"] == 2 and r["days"] == 1.2  # 第一个提交到现在 1.17 天
    first, second = r["recent"][1], r["recent"][0]
    assert first["time"] == "2026-10-30T08:06:00+00:00" and first["lead_hours"] == 0.1  # 用 ReplicaSet 时间：08:00 → 08:06
    assert second["lead_hours"] == 0.08  # 没有 ReplicaSet 记录，用写回标签的时间：09:00 → 09:05
    assert first["failed"] and second["failed"]  # 前者 24 分钟后站点告警，后者被 revert
    assert r["failure_pct"] == 100.0 and r["restore_hours"] == 1.17  # 20 分钟和 2 小时的中位数
    assert r["levels"]["deploys_per_day"] == "精英" and r["levels"]["failure_pct"] == "低"


def test_levels():
    assert dora.level("lead_hours", 30) == "高" and dora.level("restore_hours", 0.5) == "精英"
    assert dora.level("deploys_per_day", 0.01) == "低" and dora.level("failure_pct", None) is None


def test_dora_text():
    from app import dutybot

    r = dora.compute(COMMITS, [], [], 30, now=NOW)
    text = dutybot.dora_text(r)
    assert "每天 1.71 次（精英）" in text and "提交到上线：5 分钟（精英）" in text and "还没有恢复记录" in text
