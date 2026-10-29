"""定时任务一览：cron 下次时间按任务自己的时区算，再换成我的时区；常见写法翻成中文。"""

from datetime import datetime, timezone

from app.schedules import describe, next_run, summarize

NOW = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)  # 洛杉矶 10-25 周日 06:00


def test_next_run_respects_job_timezone():
    la = next_run("0 9 * * *", "America/Los_Angeles", NOW)
    assert la == datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)  # 当天 09:00 PDT
    sh = next_run("10 5 * * *", "Asia/Shanghai", NOW)
    assert sh == datetime(2026, 10, 25, 21, 10, tzinfo=timezone.utc)  # 上海 10-26 05:10
    assert next_run("5 9 * * 1", "America/Los_Angeles", NOW) == datetime(2026, 10, 26, 16, 5, tzinfo=timezone.utc)
    assert next_run("5 9 1 * *", "America/Los_Angeles", NOW) == datetime(2026, 11, 1, 17, 5, tzinfo=timezone.utc)  # 11-01 已是冬令时
    assert next_run("*/10 * * * *", "UTC", NOW) == datetime(2026, 10, 25, 13, 10, tzinfo=timezone.utc)


def test_describe():
    assert describe("0 9 * * *") == "每天 09:00"
    assert describe("5 9 * * 1") == "每周一 09:05"
    assert describe("5 9 1 * *") == "每月 1 号 09:05"
    assert describe("*/10 * * * *") == "每 10 分钟"


def test_summarize_groups_and_status():
    items = [
        {"metadata": {"name": "backup-n8n"}, "spec": {"schedule": "50 4 * * *", "timeZone": "Asia/Shanghai"}, "status": {}},
        {
            "metadata": {"name": "weekly-report"},
            "spec": {"schedule": "0 9 * * *", "timeZone": "America/Los_Angeles"},
            "status": {"lastScheduleTime": "2026-10-24T16:00:00Z", "lastSuccessfulTime": "2026-10-24T16:00:05Z"},
        },
        {"metadata": {"name": "ops-desk-status"}, "spec": {"schedule": "*/10 * * * *"}, "status": {"active": [{"name": "j"}], "lastScheduleTime": "2026-10-25T13:00:00Z", "lastSuccessfulTime": "2026-10-25T12:50:20Z"}},
        {"metadata": {"name": "daily-english"}, "spec": {"schedule": "30 7 * * *", "timeZone": "America/Los_Angeles", "suspend": True}, "status": {"lastScheduleTime": "2026-10-24T14:30:00Z"}},
    ]
    out = summarize(items, "America/Los_Angeles", NOW)
    assert [j["title"] for j in out][:2] == ["📈 网站周报", "📘 每日运维英语"]
    weekly, english, status, backup = out
    assert status["last_ok"] == "running" and status["when"] == "每 10 分钟"
    assert weekly["last_ok"] is True and weekly["next"].strftime("%m-%d %H:%M") == "10-25 09:00"
    assert weekly["when"] == "每天 00:00"
    assert english["suspended"] and english["next"] is None and english["last_ok"] is False
    assert backup["group"] == "ops" and backup["last_ok"] is None and backup["when"] == "每天 04:50"
