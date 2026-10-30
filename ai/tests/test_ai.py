"""用一个假的模型服务（httpx.MockTransport）测试：不需要密钥，也不花钱。"""

import io
import json
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import extract as ex
from app.config import Settings
from app.gateway import AIUnavailable, Gateway
from app.ledger import Ledger
from app.main import create_app
from app.rag import load_chunks

CASES = Path(__file__).resolve().parents[2] / "ai" / "knowledge"

DOC = {
    "doc_type": "送货单",
    "number": "SH-001",
    "date": "2026-10-20",
    "seller": "示例公司",
    "buyer": "演示工厂",
    "items": [{"name": "螺丝", "qty": 10, "unit_price": 2.5, "amount": 25}, {"name": "垫片", "qty": 4, "unit_price": 1, "amount": 4}],
    "total": 29,
    "remarks": None,
}


def fake_transport(log: list, fail_models: set[str] = frozenset()) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        log.append((req.url.path, body.get("model")))
        if body.get("model") in fail_models:
            return httpx.Response(500, json={"error": "boom"})
        if req.url.path.endswith("/embeddings"):
            data = []
            for i, t in enumerate(body["input"]):
                rng = np.random.default_rng(abs(hash(t)) % 2**32)
                v = rng.normal(size=16).tolist()
                if "备份" in t:  # 让「备份」相关的文本彼此相似
                    v[0] = 50.0
                data.append({"index": i, "embedding": v})
            return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 10 * len(body["input"])}})
        is_vision = isinstance(body["messages"][-1]["content"], list)
        content = json.dumps(DOC, ensure_ascii=False) if is_vision else "可以交给自动化。[1] 每天加密备份到异地。[2]"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    return httpx.MockTransport(handler)


@pytest.fixture
def setup(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    Image.new("RGB", (40, 30), "white").save(samples / "demo.png")
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=samples, daily_budget=1.0)
    log: list = []
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_transport(log))
    return s, gw, log


def test_chunks_cover_all_knowledge_docs():
    chunks = load_chunks(CASES)
    ids = {c.case_id for c in chunks}
    assert len(ids) == len(list(CASES.glob("*.md"))) >= 5
    assert all(len(c.text) <= 900 for c in chunks)
    # 每篇的第一段是标题加摘要，访客问「你们做什么」时最有用
    assert all(c.text.strip() for c in chunks if c.section == "概述")


def test_ask_flow_with_citations_and_cache(setup):
    s, gw, log = setup
    with TestClient(create_app(s, gw)) as client:
        client.app.state.qa.matrix = None
        import asyncio

        asyncio.run(client.app.state.qa.build())
        embed_calls = sum(1 for p, _ in log if p.endswith("/embeddings"))
        history = [{"role": "user", "content": "你们做运维托管吗"}, {"role": "assistant", "content": "做的。"}]
        r = client.post("/api/ai/ask", json={"question": "备份能自动化吗", "history": history}, headers={"x-real-ip": "1.2.3.4"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "[1]" in body["answer"]
        assert body["sources"][0]["cited"] is True
        assert body["meta"]["tokens_in"] == 1000
        # 第二次建索引全部命中缓存，不再调用向量接口
        client.app.state.qa.matrix = None
        asyncio.run(client.app.state.qa.build())
        assert sum(1 for p, _ in log if p.endswith("/embeddings")) == embed_calls + 1  # 只多了提问那一次
        stats = client.get("/api/ai/stats").json()
        assert stats["by_feature"]["ask"]["saved_min"] == 5


def test_quota_per_visitor(setup):
    s, gw, _ = setup
    s.quota["extract"] = 2
    with TestClient(create_app(s, gw)) as client:
        for _ in range(2):
            assert client.post("/api/ai/extract", data={"sample": "demo"}, headers={"x-real-ip": "9.9.9.9"}).status_code == 200
        r = client.post("/api/ai/extract", data={"sample": "demo"}, headers={"x-real-ip": "9.9.9.9"})
        assert r.status_code == 429
        assert client.post("/api/ai/extract", data={"sample": "demo"}, headers={"x-real-ip": "8.8.8.8"}).status_code == 200


def test_extract_rejects_bad_input(setup):
    s, gw, _ = setup
    with TestClient(create_app(s, gw)) as client:
        assert client.post("/api/ai/extract", data={"sample": "../etc/passwd"}).status_code == 400
        assert client.post("/api/ai/extract", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 400
        big = io.BytesIO(b"0" * (2 * 1024 * 1024 + 10))
        assert client.post("/api/ai/extract", files={"file": ("a.png", big, "image/png")}).status_code == 400


def test_check_finds_amount_mismatch():
    assert ex.check(DOC) == []
    bad = json.loads(json.dumps(DOC))
    bad["items"][0]["amount"] = 30
    issues = ex.check(bad)
    assert any("第 1 行" in i for i in issues)
    assert any("合计" in i for i in issues)
    assert ex.parse_json('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.anyio
async def test_fallback_and_budget(tmp_path):
    s = Settings(api_key="k", data_dir=tmp_path, cases_dir=CASES, daily_budget=0.0002, chat_models=["qwen-plus", "qwen-flash"])
    log: list = []
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_transport(log, fail_models={"qwen-plus"}))
    r = await gw.chat("ask", [{"role": "user", "content": "hi"}])
    assert r.model == "qwen-flash"  # 主模型失败后降级
    assert [m for _, m in log].count("qwen-plus") == 2  # 主模型重试过一次
    # 第一次调用（约 0.0003 元）就超过了 0.0002 元的上限，之后的调用被拦下
    with pytest.raises(AIUnavailable, match="额度"):
        await gw.chat("ask", [{"role": "user", "content": "hi"}])


@pytest.mark.anyio
async def test_disabled_without_key(tmp_path):
    s = Settings(api_key="", data_dir=tmp_path, cases_dir=CASES)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"))
    with pytest.raises(AIUnavailable, match="没有启用"):
        await gw.chat("ask", [{"role": "user", "content": "hi"}])


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_lead_is_saved_and_pushed(setup):
    s, gw, _ = setup
    sent = []

    class FakeTg:
        async def send(self, text, *a, **k):
            sent.append(text)

        async def close(self):
            pass

    with TestClient(create_app(s, gw, telegram=FakeTg())) as client:
        bad = client.post("/api/ai/lead", json={"contact": "", "need": "x"})
        assert bad.status_code == 422
        r = client.post(
            "/api/ai/lead",
            json={"name": "王总", "contact": "wx: wang<1>", "company": "贸易公司", "need": "想做 AI 客服", "chat": "访客：多少钱"},
            headers={"x-real-ip": "5.6.7.8"},
        )
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert "作品集新留言" in sent[0] and "wang&lt;1&gt;" in sent[0] and "多少钱" in sent[0]
        assert gw.ledger.leads()[0]["need"] == "想做 AI 客服"


def test_contact_in_chat_becomes_lead(setup):
    from app.main import find_contact

    assert find_contact("我电话13899999999") == "13899999999"
    assert find_contact("微信：wang_123abc") == "wang_123abc"
    assert find_contact("邮箱 a.b@corp.cn 联系") == "a.b@corp.cn"
    assert find_contact("预算 10000 元") is None

    s, gw, _ = setup
    sent = []

    class FakeTg:
        async def send(self, text, *a, **k):
            sent.append(text)

        async def close(self):
            pass

    with TestClient(create_app(s, gw, telegram=FakeTg())) as client:
        import asyncio

        asyncio.run(client.app.state.qa.build())
        history = [{"role": "user", "content": "想做个 AI 客服"}, {"role": "assistant", "content": "好的"}]
        r = client.post("/api/ai/ask", json={"question": "13899999999", "history": history})
        assert r.status_code == 200, r.text
        assert "13899999999" in sent[0] and "想做个 AI 客服" in sent[0]
        assert gw.ledger.leads()[0]["contact"] == "13899999999"


def test_demo_shop_tools_need_token(setup):
    s, gw, _ = setup
    sent = []

    class FakeTg:
        async def send(self, text, *a, **k):
            sent.append(text)

        async def close(self):
            pass

    s.demo_booking_daily = 1
    with TestClient(create_app(s, gw, telegram=FakeTg())) as client:
        assert client.get("/api/ai/demo/order", params={"order_id": "QY1001"}).status_code == 503  # 没配令牌就关闭
        s.demo_tool_token = "t0ken"
        assert client.get("/api/ai/demo/order", params={"order_id": "QY1001"}).status_code == 401
        h = {"x-demo-token": "t0ken"}
        r = client.get("/api/ai/demo/order", params={"order_id": " qy1001 "}, headers=h).json()
        assert r["found"] and r["status"] == "已发货"
        assert client.get("/api/ai/demo/order", params={"order_id": "X9"}, headers=h).json()["found"] is False
        b = {"name": "王先生", "phone": "13800000000", "address": "某市某区演示路 1 号", "time": "明天下午", "service": "上门安装"}
        r = client.post("/api/ai/demo/booking", json=b, headers=h)
        assert r.status_code == 200 and r.json()["booking_id"].startswith("YY")
        assert "新预约" in sent[0] and "明天下午" in sent[0]
        assert client.post("/api/ai/demo/booking", json=b, headers=h).status_code == 429  # 全站每天有上限
