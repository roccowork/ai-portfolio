"""第二批：工单分派、会议纪要、一句话查数据。同样用假的模型服务，不需要密钥。"""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import chatbi
from app import triage as tr
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app

CASES = Path(__file__).resolve().parents[2] / "src" / "content" / "cases"
TRANSCRIPT = "周一例会。小王负责周五前把发布方案发出来。测试环境谁来搭还没定。"


def reply_for(body: dict, sql_queue: list[str]) -> dict:
    msgs = body["messages"]
    first = msgs[0]["content"]
    if isinstance(first, list) and first[0]["type"] == "input_audio":
        return {"choices": [{"message": {"content": TRANSCRIPT}}], "usage": {"seconds": 30}}
    system = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
    if "工单分派" in system:
        content = {"category": "故障报修", "urgency": "P1", "sentiment": "着急", "summary": "官网打不开", "entities": [], "reply": "已收到"}
    elif "会议秘书" in system:
        content = {
            "title": "周一例会",
            "summary": "讨论发布",
            "decisions": [],
            "todos": [{"task": "发布方案", "owner": "小王", "due": "周五"}, {"task": "搭测试环境", "owner": None, "due": None}],
            "open_questions": ["测试环境谁来搭"],
        }
    elif "把用户的问题写成" in system:
        content = {"sql": sql_queue.pop(0), "chart": "bar", "x": "区域", "y": "销售额", "title": "各区域销售额"}
    else:
        return {"choices": [{"message": {"content": "华东最高。"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}], "usage": {"prompt_tokens": 500, "completion_tokens": 80}}


@pytest.fixture
def env(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "meeting.mp3").write_bytes(b"ID3fake-mp3")
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=samples, daily_budget=1.0)
    log: list = []
    sql_queue: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        log.append(body.get("model"))
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(500)  # 这里不测问答，让索引一直建不成
        return httpx.Response(200, json=reply_for(body, sql_queue))

    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=httpx.MockTransport(handler))
    return s, gw, log, sql_queue


def test_triage_rules_override_ai():
    doc, reasons = tr.check({"category": "瞎编的类别", "urgency": "P9", "sentiment": "愤怒"}, "再不处理我就找律师")
    assert doc["category"] == "其他" and doc["team"] == "人工分派" and doc["urgency"] == "P3"
    assert doc["need_human"] and any("律师" in r for r in reasons) and "用户情绪激动" in reasons
    doc, reasons = tr.check({"category": "发票财务", "urgency": "P3", "sentiment": "平静"}, "请重开发票")
    assert doc["team"] == "财务组" and not doc["need_human"] and reasons == []


def test_triage_endpoint(env):
    s, gw, _, _ = env
    with TestClient(create_app(s, gw)) as client:
        r = client.post("/api/ai/triage", json={"text": "官网打不开了，所有客户都在问！"})
        assert r.status_code == 200
        body = r.json()
        assert body["ticket"]["team"] == "运维组" and body["ticket"]["need_human"]
        assert "P1 紧急工单" in body["reasons"]


def test_minutes_from_audio_is_cached(env):
    s, gw, log, _ = env
    with TestClient(create_app(s, gw)) as client:
        r1 = client.post("/api/ai/minutes", data={"sample": "meeting"})
        assert r1.status_code == 200
        b = r1.json()
        assert b["transcript"] == TRANSCRIPT and not b["cached"]
        assert "qwen3-asr-flash" in b["meta"]["model"]
        assert b["issues"] == ["第 2 条待办「搭测试环境」没有负责人和截止时间"]
        r2 = client.post("/api/ai/minutes", data={"sample": "meeting"})
        assert r2.json()["cached"]
        assert log.count("qwen3-asr-flash") == 1
        assert client.post("/api/ai/minutes", data={"text": "太短"}).status_code == 400
        assert client.post("/api/ai/minutes", data={"sample": "../ai"}).status_code == 400


def test_chatbi_data_is_isolated_by_role(tmp_path):
    paths = chatbi.ensure(tmp_path)
    _, rows = chatbi.run_sql(paths["east"], "SELECT DISTINCT region FROM orders")
    assert rows == [["华东"]]
    _, rows = chatbi.run_sql(paths["hq"], "SELECT COUNT(DISTINCT region) FROM orders")
    assert rows == [[4]]
    # 剧情：华南 9 月比 8 月明显下滑
    sql = "SELECT substr(order_date,1,7) m, SUM(amount) FROM orders WHERE region='华南' AND m IN ('2026-08','2026-09') GROUP BY m"
    _, rows = chatbi.run_sql(paths["hq"], sql)
    assert rows[1][1] < rows[0][1] * 0.8


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
        "PRAGMA table_info(orders)",
        "SELECT * FROM orders; SELECT 1",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "WITH x AS (SELECT 1) DELETE FROM orders",
    ],
)
def test_chatbi_blocks_non_select(tmp_path, sql):
    paths = chatbi.ensure(tmp_path)
    with pytest.raises(sqlite3.Error):
        chatbi.run_sql(paths["hq"], sql)
    assert chatbi.run_sql(paths["hq"], "SELECT COUNT(*) FROM orders")[1][0][0] > 1000


def test_chatbi_endpoint_retries_bad_sql(env):
    s, gw, _, sql_queue = env
    sql_queue += ["SELECT region AS 区域, SUM(amout) AS 销售额 FROM orders GROUP BY region", "SELECT region AS 区域, ROUND(SUM(amount),2) AS 销售额 FROM orders GROUP BY region"]
    with TestClient(create_app(s, gw)) as client:
        r = client.post("/api/ai/chatbi", json={"question": "各区域销售额", "role": "east"})
        assert r.status_code == 200
        body = r.json()
        assert body["retried"] and body["columns"] == ["区域", "销售额"]
        assert [row[0] for row in body["rows"]] == ["华东"]
        assert body["answer"] == "华东最高。"
        assert client.post("/api/ai/chatbi", json={"question": "各区域", "role": "admin"}).status_code == 422
