"""营销内容：规则检查，以及「检查出问题 → 让模型改一次」的流程。"""

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import content as ct
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app

CASES = Path(__file__).resolve().parents[2] / "src" / "content" / "cases"
SOURCE = "社区咖啡店秋季新品：桂花拿铁，售价 28 元，10 月 1 日起供应，用本地桂花。"


def test_check_rules():
    versions = {
        "moments": {"title": "", "body": "全网最低价！桂花拿铁只要 18 元，喝了还能减肥，100%好评"},
        "xiaohongshu": {"title": "秋天的第一杯桂花拿铁真的太好喝了吧姐妹们快冲", "body": "28 元一杯，10 月 1 日开售。"},
    }
    issues = ct.check(versions, SOURCE)
    m = " ".join(issues["moments"])
    assert "全网最" in m and "100%" in m and "减肥" in m and "18" in m
    x = issues["xiaohongshu"]
    assert any("标题" in i for i in x) and not any("数字" in i for i in x)


def test_generate_fixes_once(tmp_path):
    replies = [
        {"versions": {"moments": {"title": "", "body": "全网最好喝的桂花拿铁，28 元", "tags": []}}},
        {"versions": {"moments": {"title": "", "body": "秋天的桂花拿铁上新了，28 元，10 月 1 日起供应", "tags": []}}},
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(500)
        content = json.dumps(replies.pop(0), ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 500, "completion_tokens": 200}})

    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, daily_budget=1.0)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=httpx.MockTransport(handler))
    with TestClient(create_app(s, gw)) as client:
        r = client.post("/api/ai/content", json={"source": SOURCE, "platforms": ["moments", "unknown"]})
        assert r.status_code == 200
        body = r.json()
        assert [v["code"] for v in body["versions"]] == ["moments"]
        assert body["versions"][0]["issues"] == []
        assert any("全网最" in f for f in body["fixed"])
        assert client.post("/api/ai/content", json={"source": SOURCE, "platforms": ["x"]}).status_code == 400
