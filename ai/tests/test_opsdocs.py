"""运维知识库：手册切段、检索、给运维 Agent 用的 search_runbook 工具、评测集格式。假的模型服务，不花钱。"""

import asyncio
import json
from pathlib import Path

import httpx

from app import opsagent as ops
from app import opsdocs as od
from app.config import Settings
from app.evals import rag as ev
from app.gateway import Gateway
from app.ledger import Ledger

DOCS = Path(__file__).resolve().parents[2] / "docs"

SAMPLE = """# 运维手册

开头的说明。

## 2. 回滚

回滚就是改 Git，不要用 rollout undo。

```bash
git revert --no-edit <deploy 提交>

git push
```

## 5. 常见故障

| 现象      | 处理                  |
| --------- | --------------------- |
""" + "".join(f"| 现象{i}    | 处理办法{i}" + "很长" * 40 + " |\n" for i in range(30))


def fake_llm(seen: list | None = None, script: list | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.path.endswith("/embeddings"):
            data = [{"index": i, "embedding": [10.0 if "回滚" in t or "revert" in t else 0.1, 1.0]} for i, t in enumerate(body["input"])]
            return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 10}})
        if seen is not None:
            seen.append(body)
        step = script.pop(0) if script else "回滚就是改 Git：git revert 后推送。[1]\n出处：运维手册 › 2. 回滚"
        msg = {"content": step} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 300, "completion_tokens": 30}})

    return httpx.MockTransport(handler)


def make(tmp_path, docs_dir: Path, **kw) -> od.OpsDocs:
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=tmp_path, samples_dir=tmp_path, daily_budget=5.0)
    return od.OpsDocs(Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_llm(**kw)), od.load_doc_chunks(docs_dir))


def test_chunks_keep_headings_code_and_table_headers(tmp_path):
    (tmp_path / "runbook.md").write_text(SAMPLE, encoding="utf-8")
    chunks = od.load_doc_chunks(tmp_path)
    sections = [c.section for c in chunks]
    assert "运维手册 › 2. 回滚" in sections
    rollback = next(c for c in chunks if c.section.endswith("回滚"))
    assert "git revert" in rollback.text and "git push" in rollback.text  # 代码块里的空行不会把它切开
    table = [c for c in chunks if c.section.endswith("常见故障")]
    assert len(table) > 1 and all(c.text.startswith("| 现象 | 处理 |") for c in table)  # 每份都带表头，对齐空格已去掉
    assert all(len(c.text) <= od.MAX_CHUNK for c in chunks)


def test_lookup_returns_sources(tmp_path):
    (tmp_path / "runbook.md").write_text(SAMPLE, encoding="utf-8")
    docs = make(tmp_path, tmp_path)
    assert docs.ready is False and "还没加载" in asyncio.run(docs.lookup("回滚"))[0]["error"]
    asyncio.run(docs.build())
    hits = asyncio.run(docs.lookup("怎么回滚"))
    assert hits[0]["出处"] == "运维手册 › 2. 回滚" and "git revert" in hits[0]["内容"]


def test_agent_uses_runbook_tool(tmp_path):
    (tmp_path / "runbook.md").write_text(SAMPLE, encoding="utf-8")
    seen: list = []
    call = [{"id": "c1", "type": "function", "function": {"name": "search_runbook", "arguments": json.dumps({"query": "怎么回滚"})}}]
    docs = make(tmp_path, tmp_path, seen=seen, script=[call, "按手册：git revert 后推送。出处：运维手册 › 2. 回滚"])
    asyncio.run(docs.build())
    c = ops.Cluster("http://prom", sa_dir=tmp_path / "none")
    answer, steps, _ = asyncio.run(ops.investigate(docs.gw, c, "网站怎么回滚？", public=True, extra=od.tool(docs)))
    assert "git revert" in answer and steps[0]["tool"] == "search_runbook"
    assert "search_runbook" in [t["function"]["name"] for t in seen[0]["tools"]]
    tool_msg = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert "2. 回滚" in tool_msg["content"]  # 手册原文和出处交给了模型


def test_real_docs_and_ops_cases():
    chunks = od.load_doc_chunks(DOCS)
    assert {c.case_id for c in chunks} == set(od.DOCS) and len(chunks) > 30
    cases = ev.load_cases(ev.OPS_CASES)
    assert len(cases) >= 20 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        if c.get("unanswerable"):
            assert not c["sections"] and not c["keywords"], c["id"]
            continue
        # 每道题的章节都存在，而且要点确实写在这些章节里：手册改了、题目过时会在这里报出来
        mine = [ch for ch in chunks if ev.expected(ch, c)]
        assert mine, c["id"]
        for k in c["keywords"]:
            assert any(a.lower() in ch.text.lower() for ch in mine for a in k.split("|")), (c["id"], k)


def test_ops_eval_matches_by_section(tmp_path):
    (tmp_path / "runbook.md").write_text(SAMPLE, encoding="utf-8")
    docs = make(tmp_path, tmp_path, script=["回滚就是改 Git：git revert。[1]", json.dumps({"verdict": "correct", "reason": "有依据"})])
    asyncio.run(docs.build())
    out = asyncio.run(ev.run(docs, docs.gw, [{"id": "r1", "question": "怎么回滚？", "sections": ["2. 回滚"], "keywords": ["git revert"]}], title="运维知识库评测"))
    s = out["summary"]
    assert s["hit_at_1"] == 100.0 and s["keyword_acc"] == 100.0 and out["rows"][0]["retrieved"][0].endswith("2. 回滚")
    assert ev.summary_text(out).startswith("<b>📏 运维知识库评测</b>")


def test_hybrid_finds_exact_terms_vectors_miss(tmp_path):
    """向量对英文专有名词不敏感：假向量全都一样时，只有关键词那一路能把 rateLimited 那一节排到第一"""
    (tmp_path / "runbook.md").write_text(
        "# 运维手册\n\n## 4. 证书问题\n\n报 rateLimited：失败验证每小时最多 5 次，等一小时。\n\n## 5. 常见故障\n\n滚动更新时偶发 504，已加 preStop。\n\n## 6. 监控告警\n\n告警推到值班台。\n",
        encoding="utf-8",
    )
    docs = make(tmp_path, tmp_path)
    asyncio.run(docs.build())
    flat = docs.matrix[0] * 0 + 1.0  # 和每一段的相似度都一样
    assert docs.retrieve("签证书报 rateLimited 怎么处理？", flat)[0].section.endswith("4. 证书问题")
    assert docs.retrieve("504 是怎么回事", flat)[0].section.endswith("5. 常见故障")
    assert od.tokens("PodRestartingOften 告警") == ["podrestartingoften", "告警"]
