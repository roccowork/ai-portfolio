"""办公 Agent：假的模型（按脚本返回工具调用）+ 假的 Odoo（JSON-RPC）。不需要密钥，也不连真实 Odoo。"""

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent as ag
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app
from app.odoo import Odoo

CASES = Path(__file__).resolve().parents[2] / "src" / "content" / "cases"
NEXT_TUE = (date.today() + timedelta(days=7)).isoformat()


def fake_odoo(created: list) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        p = json.loads(req.content)["params"]
        if p["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        model, method, args, kw = p["args"][3], p["args"][4], p["args"][5], p["args"][6]
        result: object = []
        if method == "search_read":
            result = {
                "res.partner": [{"id": 11, "name": "Deco Addict", "city": "SF", "email": "d@x", "is_company": True}],
                "product.product": [{"id": 21, "name": "Office Chair", "default_code": "FURN_0001", "list_price": 70.0}],
                "mail.activity.type": [{"id": 2}],
                "ir.model": [{"id": 99}],
                "mail.activity": [{"id": 1, "res_model_id": [98, "Lead"]}],
                "crm.tag": [],
                "sale.order": [{"id": 31, "name": "S00031", "amount_total": 1400.0}],
            }.get(model, [])
        elif method == "create":
            created.append((model, args[0]))
            result = {"crm.lead": 41, "crm.tag": 5, "sale.order": 31, "mail.activity": 51}[model]
        return httpx.Response(200, json={"result": result})

    return httpx.MockTransport(handler)


def tool_call(name: str, args: dict, i: int) -> dict:
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def fake_llm(script: list) -> httpx.MockTransport:
    """script 每一项是一轮的回复：工具调用列表，或者最终文本。"""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(500)  # 后台建索引也会调模型，这里不测问答
        step = script.pop(0)
        msg = {"content": step, "tool_calls": None} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 800, "completion_tokens": 60}})

    return httpx.MockTransport(handler)


@pytest.fixture
def env(tmp_path):
    def make(script: list):
        s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, daily_budget=1.0, odoo_password="x")
        created: list = []
        gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_llm(script))
        odoo = Odoo("http://odoo", "odoo", "ai-agent", "x", transport=fake_odoo(created))
        return create_app(s, gw, odoo), created

    return make


def test_write_waits_for_confirmation(env):
    script = [
        [tool_call("find_customers", {"query": "Deco"}, 1)],
        [tool_call("create_opportunity", {"customer_id": 11, "title": "采购 20 把办公椅", "follow_up": {"type": "call", "date": NEXT_TUE, "summary": "回访"}}, 2)],
        "已为 Deco Addict 准备好商机，请确认。",
    ]
    app, created = env(script)
    with TestClient(app) as client:
        r = client.post("/api/ai/agent", json={"instruction": "给 Deco Addict 建商机，下周二电话回访"})
        assert r.status_code == 200
        body = r.json()
        assert body["plan_id"] and body["actions"][0]["title"] == "新建商机"
        assert [s["tool"] for s in body["steps"]] == ["find_customers", "create_opportunity"]
        assert created == []  # 还没确认，Odoo 里什么都没写
        r2 = client.post("/api/ai/agent/confirm", json={"plan_id": body["plan_id"]})
        assert r2.status_code == 200 and r2.json()["results"][0]["ok"]
        models = [m for m, _ in created]
        assert models == ["crm.tag", "crm.lead", "mail.activity"]
        assert created[2][1]["res_model_id"] == 98  # 从已有活动上取，不读 ir.model
        assert created[1][1]["partner_id"] == 11
        # 同一组操作不能确认两次
        assert client.post("/api/ai/agent/confirm", json={"plan_id": body["plan_id"]}).status_code == 404


def test_invented_ids_are_rejected(env):
    script = [
        [tool_call("create_quotation", {"customer_id": 999, "lines": [{"product_id": 21, "quantity": 5}]}, 1)],
        "没找到这个客户。",
    ]
    app, created = env(script)
    with TestClient(app) as client:
        body = client.post("/api/ai/agent", json={"instruction": "给某公司报价"}).json()
        assert body["plan_id"] is None and body["actions"] == []
        assert body["steps"][0]["summary"].startswith("被程序拦下")


def test_propose_validation():
    sess = ag.Session(customers={11: "Deco Addict"}, products={21: {"name": "Office Chair", "list_price": 70.0}})
    today = date.today()
    a = ag.propose("create_quotation", {"customer_id": 11, "lines": [{"product_id": 21, "quantity": 20}]}, sess, today)
    assert "1,400.00" in a["details"][-1]
    with pytest.raises(ag.PlanError):
        ag.propose("create_quotation", {"customer_id": 11, "lines": [{"product_id": 21, "quantity": 0}]}, sess, today)
    with pytest.raises(ag.PlanError):
        ag.propose("create_opportunity", {"customer_id": 11, "title": "x", "follow_up": {"type": "call", "date": "2020-01-01"}}, sess, today)
    with pytest.raises(ag.PlanError):
        ag.propose("delete_everything", {}, sess, today)


def test_agent_disabled_without_odoo(tmp_path):
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, odoo_password="")
    with TestClient(create_app(s, Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_llm([])))) as client:
        assert client.post("/api/ai/agent", json={"instruction": "hello"}).status_code == 503
