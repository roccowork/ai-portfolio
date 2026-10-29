"""RAG 评测本身的测试：假的模型服务，检查指标算得对、结果能存能查、评测集格式正确。"""

import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.evals import rag as ev
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app
from app.rag import CaseQA, load_chunks

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"

ANSWERS = {
    "回滚要多久？": "用 git revert 回滚，实测 89 秒生效。[1]",
    "ChatBI 一次最多返回多少行？": "最多返回 100 行。[1]",  # 故意答错：资料里是 200
    "作者期望薪资多少？": "资料里没有写，可以给作者留言问问。",
    "作者今年多大了？": "作者今年 35 岁。",  # 故意编造
}


def fake_llm(judged: list) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.path.endswith("/embeddings"):
            data = []
            for i, t in enumerate(body["input"]):
                v = [0.1] * 8
                if "回滚" in t or "revert" in t:
                    v[0] = 10.0
                if "200 行" in t or "返回多少行" in t:
                    v[1] = 10.0
                data.append({"index": i, "embedding": v})
            return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 10}})
        last = body["messages"][-1]["content"]
        if "【问题】" in last:
            judged.append(last)
            wrong = "100 行" in last or "35 岁" in last
            content = json.dumps({"verdict": "wrong" if wrong else "correct", "reason": "和资料不符" if wrong else "有依据"})
        else:
            content = ANSWERS[last]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 500, "completion_tokens": 50}})

    return httpx.MockTransport(handler)


def setup(tmp_path):
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=KNOWLEDGE, samples_dir=tmp_path, daily_budget=5.0)
    judged: list = []
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_llm(judged))
    qa = CaseQA(gw, load_chunks(KNOWLEDGE))
    asyncio.run(qa.build())
    return s, gw, qa, judged


def test_metrics(tmp_path):
    _, gw, qa, judged = setup(tmp_path)
    cases = [
        {"id": "g3", "question": "回滚要多久？", "docs": ["30-gitops-platform"], "keywords": ["89"]},
        {"id": "b5", "question": "ChatBI 一次最多返回多少行？", "docs": ["20-chatbi"], "keywords": ["200"]},
        {"id": "n1", "question": "作者期望薪资多少？", "docs": [], "keywords": [], "unanswerable": True},
        {"id": "n2", "question": "作者今年多大了？", "docs": [], "keywords": [], "unanswerable": True},
    ]
    out = asyncio.run(ev.run(qa, gw, cases))
    s = out["summary"]
    assert s["cases"] == 4 and s["answerable"] == 2
    assert s["hit_at_1"] == 100.0 and s["mrr"] == 1.0  # 两道题的资料都排第一
    assert s["keyword_acc"] == 50.0 and s["judge_correct"] == 50.0 and s["judge_wrong"] == 1
    assert s["refusal_acc"] == 50.0 and s["hallucinated"] == ["n2"]
    assert s["citation_acc"] == 100.0 and s["cost_yuan"] > 0
    assert len(judged) == 4 and all("《" in j for j in judged)  # 裁判拿到了资料
    assert any("【必须答到的要点】89" in j for j in judged)  # 和这道题必须答到的要点
    text = ev.summary_text(out)
    assert "命中@1 100.0%" in text and "编了：n2" in text and "b5" in text


def test_stored_and_served(tmp_path):
    s, gw, qa, _ = setup(tmp_path)
    out = asyncio.run(ev.run(qa, gw, [{"id": "g3", "question": "回滚要多久？", "docs": ["30-gitops-platform"], "keywords": ["89"]}]))
    with TestClient(create_app(s, gw)) as c:
        assert c.get("/api/ai/eval/latest").status_code == 404
        gw.ledger.add_eval("rag", out)
        body = c.get("/api/ai/eval/latest").json()
    assert body["rows"][0]["answer"].startswith("用 git revert") and body["history"][0]["keyword_acc"] == 100.0


def test_case_file_is_valid():
    cases = ev.load_cases()
    docs = {p.stem for p in KNOWLEDGE.glob("*.md")}
    assert len(cases) >= 30 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        if c.get("unanswerable"):
            assert not c["docs"] and not c["keywords"], c["id"]
        else:
            assert c["docs"] and set(c["docs"]) <= docs and c["keywords"], c["id"]


def test_keyword_and_refusal_rules():
    assert ev.keywords_ok("回滚用 Git Revert，89 秒", ["git revert|revert", "89"])
    assert not ev.keywords_ok("回滚很快", ["89"])
    assert ev.keywords_ok("用的是 ArgoCD", ["Argo CD|ArgoCD"])
    assert ev.refused("资料里没有写这个") and not ev.refused("作者 35 岁")
