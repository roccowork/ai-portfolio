"""网站的 Telegram 机器人：只处理主人点的自愈按钮；给私人助理用的两个集群内接口。"""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app import opsagent as ops
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app
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
    asyncio.run(bot.handle({"update_id": 2, "message": {"chat": {"id": 42}, "text": "你好"}}))  # 普通消息不理
    assert pressed == [] and sent == []
    asyncio.run(bot.handle(button("heal:ok:1")))
    assert pressed == ["heal:ok:1"]
    assert [m for m, _ in sent] == ["answerCallbackQuery", "editMessageText"]
    assert sent[1][1]["text"] == "✅ 已执行" and sent[1][1]["message_id"] == 7


def fake_k8s() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/apis/batch/v1/cronjobs":
            item = {
                "metadata": {"name": "weekly-report", "namespace": "monitoring", "uid": "x", "annotations": {"a": "b"}},
                "spec": {"schedule": "0 9 * * 1", "timeZone": "Asia/Singapore", "jobTemplate": {"spec": {}}},
                "status": {"lastSuccessfulTime": "2026-10-26T01:00:05Z"},
            }
            return httpx.Response(200, json={"items": [item]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_internal_endpoints_for_personal_assistant(tmp_path):
    s = Settings(api_key="", data_dir=tmp_path, cases_dir=tmp_path, samples_dir=tmp_path)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"))
    # 没有集群权限（本地运行）时明确返回 503
    with TestClient(create_app(s, gw, cluster=ops.Cluster("http://prom", sa_dir=tmp_path / "none"))) as c:
        assert c.get("/internal/ops/status").status_code == 503
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("t")
    cluster = ops.Cluster("http://prom", "http://kube", sa_dir=sa, transport=fake_k8s())
    with TestClient(create_app(s, gw, cluster=cluster)) as c:
        jobs = c.get("/internal/ops/cronjobs").json()
    # 只给名字、时间表和运行状态，不带 jobTemplate、注解这些
    assert jobs == [
        {
            "metadata": {"name": "weekly-report"},
            "spec": {"schedule": "0 9 * * 1", "timeZone": "Asia/Singapore"},
            "status": {"lastSuccessfulTime": "2026-10-26T01:00:05Z"},
        }
    ]

