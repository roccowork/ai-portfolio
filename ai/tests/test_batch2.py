"""第二批：工单分派、会议纪要、一句话查数据（本站真实运行数据的快照）。同样用假的模型服务，不需要密钥。"""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import chatbi
from app import triage as tr
from app.autoheal import IncidentStore
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
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=samples, daily_budget=1.0, status_url="http://127.0.0.1:9/status.json")
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


def make_snapshot(tmp_path) -> Path:
    """一个有真实结构的 ai.db：费用账本、留言、自愈记录、评测记录，再生成 ChatBI 快照"""
    ledger = Ledger(tmp_path / "ai.db")
    ledger.record("ask", "qwen-plus", 1000, 200, 0.002)
    ledger.record("ask", "qwen-plus", 1000, 200, 0.002)
    ledger.record("chatbi", "qwen-max", 3000, 300, 0.01)
    ledger.record("assistant", "qwen-plus", 500, 50, 0.001)  # 私人助理，不能进快照
    ledger.add_lead("1.2.3.4", "张三", "zhang@example.com", "某公司", "想做 RAG", "")
    store = IncidentStore(ledger._db, ledger._lock)
    iid = store.add("fp1", "CronJobFailed", "warning", {}, "2026-10-28T02:00:00Z", "备份任务失败")
    store.update(iid, status="fixed", action=json.dumps({"action": "rerun_cronjob"}), risk="auto", resolved_ts="2026-10-28T02:12:30Z")
    store.add("fp2", "PodRestartingOften", "critical", {}, "2026-10-28T05:00:00Z")
    ledger.add_eval("ops-rag", {"summary": {"cases": 25, "hit_at_1": 85.7, "judge_correct": 95.2}})
    dest = tmp_path / "chatbi" / "ops.db"
    chatbi.build(ledger._db, dest, [("2026-10-27", 1200, 3), ("2026-10-28", 1500, 0)], [{"time": "2026-10-29T07:35:26Z", "sha": "da20259", "state": "current"}])
    return dest


def test_chatbi_snapshot_has_real_stats_only(tmp_path):
    db = make_snapshot(tmp_path)
    _, rows = chatbi.run_sql(db, "SELECT feature, SUM(calls), ROUND(SUM(cost), 4) FROM ai_usage GROUP BY feature ORDER BY feature")
    assert rows == [["ChatBI 查数据", 1, 0.01], ["作品集问答", 2, 0.004]]  # 私人助理的调用不在里面
    _, rows = chatbi.run_sql(db, "SELECT alert, status, action, handled_by, fixed, minutes_to_resolve, started_at FROM incidents ORDER BY id")
    assert rows[0] == ["CronJobFailed", "已恢复", "补跑任务", "自动执行", 1, 12.5, "2026-10-28 10:00"]  # 新加坡时间
    assert rows[1][1:5] == ["分析中", "无", "没有处理", 0]
    assert chatbi.run_sql(db, "SELECT kind, hit_at_1 FROM eval_runs")[1] == [["运维知识库", 85.7]]
    assert chatbi.run_sql(db, "SELECT SUM(requests) FROM site_traffic")[1] == [[2700]]
    assert chatbi.run_sql(db, "SELECT deployed_at, state FROM deploys")[1] == [["2026-10-29 15:35", "当前版本"]]
    # 留言、联系方式根本不在快照里
    tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")}
    assert tables == {"ai_usage", "incidents", "eval_runs", "site_traffic", "deploys"}
    with pytest.raises(sqlite3.Error):
        chatbi.run_sql(db, "SELECT * FROM leads")
    text = chatbi.schema(db, datetime(2026, 10, 29, 4, tzinfo=timezone.utc))
    assert "今天是 2026-10-29（星期四）" in text and "「最近 7 天」= 2026-10-23 到 2026-10-29" in text and "「本周」= 2026-10-26" in text


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ai_usage",
        "SELECT 1; DROP TABLE ai_usage",
        "PRAGMA table_info(ai_usage)",
        "SELECT * FROM ai_usage; SELECT 1",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "WITH x AS (SELECT 1) DELETE FROM ai_usage",
    ],
)
def test_chatbi_blocks_non_select(tmp_path, sql):
    db = make_snapshot(tmp_path)
    with pytest.raises(sqlite3.Error):
        chatbi.run_sql(db, sql)
    assert chatbi.run_sql(db, "SELECT COUNT(*) FROM ai_usage")[1][0][0] == 2


def test_chatbi_snapshot_refreshes_and_survives_missing_sources(tmp_path):
    ledger = Ledger(tmp_path / "ai.db")
    (tmp_path / "chatbi").mkdir()
    (tmp_path / "chatbi" / "hq.db").write_bytes(b"old")

    async def broken():
        raise httpx.ConnectError("prometheus down")

    bi = chatbi.OpsData(ledger._db, ledger._lock, tmp_path / "chatbi" / "ops.db", broken, broken)
    assert not (tmp_path / "chatbi" / "hq.db").exists()  # 以前的虚构销售库清掉
    db = asyncio.run(bi.ensure())  # 没有 incidents 表、访问量和发布都查不到，也能生成
    assert chatbi.run_sql(db, "SELECT COUNT(*) FROM site_traffic")[1] == [[0]]
    ledger.record("ask", "qwen-plus", 10, 10, 0.001)
    asyncio.run(bi.ensure())
    assert chatbi.run_sql(db, "SELECT COUNT(*) FROM ai_usage")[1] == [[0]]  # 10 分钟内用旧快照
    bi.built_at = 0
    asyncio.run(bi.ensure())
    assert chatbi.run_sql(db, "SELECT COUNT(*) FROM ai_usage")[1] == [[1]]


def test_chatbi_endpoint_retries_bad_sql(env):
    s, gw, _, sql_queue = env
    gw.ledger.record("ask", "qwen-plus", 1000, 200, 0.002)
    sql_queue += ["SELECT feature AS 功能, SUM(cals) AS 调用次数 FROM ai_usage GROUP BY feature", "SELECT feature AS 功能, SUM(calls) AS 调用次数 FROM ai_usage GROUP BY feature"]
    with TestClient(create_app(s, gw)) as client:
        r = client.post("/api/ai/chatbi", json={"question": "各功能调用次数"})
        assert r.status_code == 200
        body = r.json()
        assert body["retried"] and body["columns"] == ["功能", "调用次数"]
        assert body["rows"] == [["作品集问答", 1]]
        assert body["answer"] == "华东最高。"
        assert (s.data_dir / "chatbi" / "ops.db").is_file()
