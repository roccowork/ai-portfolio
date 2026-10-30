"""网站的 Telegram 机器人（值班台）：只理主人、自愈按钮、菜单、提问交给运维 Agent。"""

import asyncio
import json

import httpx

from app import opsagent as ops
from app.ledger import Ledger
from app import autoheal, dutybot
from app.telegram import OpsBot, Telegram


def fake_tg(sent: list) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.url.path.rsplit("/", 1)[-1], json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return httpx.MockTransport(handler)


def button(data: str, chat: int = 42) -> dict:
    return {"update_id": 1, "callback_query": {"id": "c", "data": data, "message": {"message_id": 7, "chat": {"id": chat}}}}


def test_ops_bot_only_handles_owner_buttons():
    sent: list = []
    pressed: list = []

    async def on_button(data: str) -> str:
        pressed.append(data)
        return "✅ 已执行"

    bot = OpsBot(Telegram("T", "42", transport=fake_tg(sent)), on_button)
    asyncio.run(bot.handle(button("heal:ok:1", chat=999)))  # 陌生人
    asyncio.run(bot.handle({"update_id": 2, "message": {"chat": {"id": 999}, "text": "/start"}}))
    assert pressed == [] and sent == []
    asyncio.run(bot.handle(button("heal:ok:1")))
    assert pressed == ["heal:ok:1"]
    assert [m for m, _ in sent] == ["answerCallbackQuery", "editMessageText"]
    assert sent[1][1]["text"] == "✅ 已执行" and sent[1][1]["message_id"] == 7


def msg(text: str, chat: int = 42) -> dict:
    return {"update_id": 3, "message": {"chat": {"id": chat}, "text": text}}


def test_menu_commands_and_questions():
    sent: list = []
    asked: list = []

    async def status() -> str:
        return "<b>🖥 服务器状态</b>  ✅ 正常"

    async def ask(q: str) -> str:
        asked.append(q)
        return "内存高是因为 Prometheus 缓存"

    async def on_button(_: str) -> str:
        return ""

    bot = OpsBot(Telegram("T", "42", transport=fake_tg(sent)), on_button, [("🖥 服务器状态", "status", "服务器状态", status)], ask, "帮助说明")
    asyncio.run(bot.handle(msg("/start")))
    method, body = sent[-1]
    assert body["text"] == "帮助说明" and body["reply_markup"]["keyboard"] == [[{"text": "🖥 服务器状态"}, {"text": "❓ 帮助"}]]
    asyncio.run(bot.handle(msg("🖥 服务器状态")))
    asyncio.run(bot.handle(msg("/status@opsdesk_duty_bot")))
    assert [b["text"] for _, b in sent[-2:]] == ["<b>🖥 服务器状态</b>  ✅ 正常"] * 2
    assert asked == []  # 按钮不走模型
    asyncio.run(bot.handle(msg("为什么内存这么高")))
    assert asked == ["为什么内存这么高"] and sent[-1][1]["text"] == "内存高是因为 Prometheus 缓存"
    assert "正在查" in sent[-2][1]["text"]


def test_menu_error_is_reported_not_raised():
    sent: list = []

    async def broken() -> str:
        raise RuntimeError("prometheus down")

    async def on_button(_: str) -> str:
        return ""

    bot = OpsBot(Telegram("T", "42", transport=fake_tg(sent)), on_button, [("🚨 当前告警", "alerts", "告警", broken)])
    asyncio.run(bot.handle(msg("🚨 当前告警")))
    assert "查询失败" in sent[-1][1]["text"] and "prometheus down" in sent[-1][1]["text"]


def test_duty_texts(tmp_path):
    ledger = Ledger(tmp_path / "ai.db")
    assert "还没有访客留言" in dutybot.leads_text(ledger)
    ledger.add_lead("1.2.3.4", "王女士", "wx: wang<1>", "某公司", "想约面试", "")
    text = dutybot.leads_text(ledger)
    assert "王女士 · 某公司" in text and "wang&lt;1&gt;" in text and "想约面试" in text
    ledger.record("ask", "qwen-plus-2025-12-01", 1000, 100, 0.2)
    ledger.record("dutybot", "qwen-plus-2025-12-01", 1000, 100, 0.3)
    text = dutybot.usage_text(ledger, 5, 1)
    assert "其中访客 ¥0.200，访客每日上限 ¥1.00" in text and "还剩 ¥4.50" in text
    store = autoheal.IncidentStore(ledger._db, ledger._lock)
    assert "没有需要处理的故障" in dutybot.heal_text(store)
    assert "查不到" in asyncio.run(dutybot.status_text(ops.Cluster("http://prom", sa_dir=tmp_path / "none")))


def test_alerts_text_lists_firing_alerts(tmp_path):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("t")

    def handler(req: httpx.Request) -> httpx.Response:
        alerts = [
            {"labels": {"alertname": "KubePodCrashLooping", "severity": "warning", "namespace": "ops-desk", "pod": "ai-api-1"}, "state": "firing", "annotations": {"summary": "crash <x>"}},
            {"labels": {"alertname": "Watchdog", "severity": "none"}, "state": "firing", "annotations": {}},
        ]
        return httpx.Response(200, json={"data": {"alerts": alerts}})

    c = ops.Cluster("http://prom", "http://kube", sa_dir=sa, transport=httpx.MockTransport(handler))
    text = asyncio.run(dutybot.alerts_text(c))
    assert "🟡 <b>KubePodCrashLooping</b>  ops-desk / ai-api-1" in text and "crash &lt;x&gt;" in text and "Watchdog" not in text


def test_platform_text_matches_live_page():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 29, 13, 30, tzinfo=timezone.utc)
    st = {
        "generatedAt": "2026-10-29T13:20:00Z",
        "deploys": [{"time": "2026-10-29T13:22:00Z", "sha": "8bf110c", "state": "current"}, {"time": "2026-10-29T12:31:00Z", "sha": "4329d94", "state": "replaced"}],
        "availability7d": 99.97,
        "p95Ms": 80,
        "requests7d": 15234,
        "backups": [{"name": "backup-ai-api", "lastSuccess": "2026-10-29T05:12:00Z"}, {"name": "backup-old", "lastSuccess": "2026-10-20T05:00:00Z"}],
        "certs": [{"name": "ops-desk-tls", "daysLeft": 12}],
        "alerts7d": None,
    }
    text = dutybot.platform_text(st, now)
    assert "数据更新于 10 分钟前" in text and "7 天可用率 99.97% · p95 < 100 ms · 请求 15,234" in text
    assert "<code>8bf110c</code> 8 分钟前 · 当前版本" in text
    assert "✅ backup-ai-api：最近成功 8 小时前" in text and "⚠️ backup-old" in text
    assert "⚠️ ops-desk-tls：剩余 12 天" in text and "7 天告警</b>：查询失败" in text


def test_jobs_text_groups_cronjobs(tmp_path):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("t")

    def handler(req: httpx.Request) -> httpx.Response:
        items = [
            {"metadata": {"name": "weekly-report"}, "spec": {"schedule": "0 9 * * 1", "timeZone": "Asia/Singapore"}, "status": {"lastScheduleTime": "2026-10-26T01:00:00Z", "lastSuccessfulTime": "2026-10-26T01:00:05Z"}},
            {"metadata": {"name": "backup-ai-api"}, "spec": {"schedule": "10 5 * * *", "timeZone": "Asia/Singapore"}, "status": {"lastScheduleTime": "2026-10-28T21:10:00Z"}},
        ]
        return httpx.Response(200, json={"items": items})

    c = ops.Cluster("http://prom", "http://kube", sa_dir=sa, transport=httpx.MockTransport(handler))
    text = asyncio.run(dutybot.jobs_text(c))
    assert "📮 会发消息的" in text and "📈 网站周报 · 每周一 09:00" in text and "上次 ✅" in text
    assert "🔧 后台维护" in text and "💾 备份网站 AI 服务数据" in text and "上次 ❌ 失败" in text
