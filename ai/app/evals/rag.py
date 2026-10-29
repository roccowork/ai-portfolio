"""作品集问答（RAG）的效果评测：用 rag_cases.json 里的题目真实调用一遍问答，统计四类指标。

1. 检索：应该找到的资料排在第几（命中@1、命中@3、MRR）。不需要模型判断，最客观
2. 要点：答案里有没有出现必须提到的关键词（全部命中才算），规则判断、可复现
3. 裁判：另一次模型调用按资料判断答案是 correct / partial / wrong，包括有没有编造资料里没有的事实
4. 拒答：资料里没有答案的问题，应该说「资料里没写」而不是编一个

关键词只是粗略的代理指标，裁判模型也可能有偏差（默认和回答用同一个模型，可用 EVAL_JUDGE_MODELS 换），
所以报告里两种都给，并保留每道题的回答和理由，方便人工抽查。
"""

import asyncio
import json
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from ..gateway import AIUnavailable, Gateway
from ..rag import CaseQA

CASES = Path(__file__).with_name("rag_cases.json")  # 作品集问答
OPS_CASES = Path(__file__).with_name("ops_cases.json")  # 运维知识库（docs/ 的运维手册）
# 资料全文不超过这么多字时整份交给裁判；超过了（运维手册有四万多字）只给这道题应该用到的章节和检索到的段落，省钱
FULL_MATERIAL = 15000
# 回答里出现这些说法，就算「说了资料里没有」
REFUSAL = ("资料里没有", "没有写", "没写", "没有提到", "没有相关", "未提及", "没有说明", "没有透露", "没有记录", "未记录", "无法确定", "不清楚", "暂无")

JUDGE = """你是评测员，判断一个作品集问答助手的回答是否正确。只能依据下面给的【资料】，不要用你自己的知识。
- correct：回答了问题，关键内容都能在资料里找到依据
- partial：方向对，但漏了问题要的关键信息
- wrong：答错、答非所问，或者给出了资料里没有的具体事实（编造）
如果给了【必须答到的要点】：回答漏了任何一个要点，最多算 partial。
如果资料里根本没有这个问题的答案：回答明确说资料里没有 / 建议给作者留言，算 correct；给出了具体答案，算 wrong。
回答里夹带资料里没有的具体说法（哪怕后面又说「资料里没有写」），算 wrong；尤其是资料里没有的命令、接口地址、网址，出现一条就算 wrong。
只输出 JSON：{"verdict": "correct" | "partial" | "wrong", "reason": "一句话理由"}"""


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def keywords_ok(answer: str, keywords: list[str]) -> bool:
    """每个要点用 | 分隔同义写法，全部要点都出现才算；忽略大小写和空格"""
    text = re.sub(r"\s+", "", answer).lower()
    return all(any(re.sub(r"\s+", "", alt).lower() in text for alt in k.split("|")) for k in keywords)


def refused(answer: str) -> bool:
    return any(p in answer for p in REFUSAL)


async def judge(gw: Gateway, question: str, answer: str, material: str, points: list[str] | None = None) -> tuple[str, str, float]:
    """points：这道题必须答到的要点（评测集里的 keywords）。2026-10-29 第一次评测时裁判没有它，漏答要点也判了 correct"""
    must = f"\n\n【必须答到的要点】{'；'.join(p.replace('|', ' 或 ') for p in points)}" if points else ""
    try:
        r = await gw.chat(
            "eval",
            [{"role": "system", "content": JUDGE}, {"role": "user", "content": f"【问题】{question}\n\n【回答】{answer}{must}\n\n【资料】\n{material}"}],
            json_mode=True,
            max_tokens=200,
        )
        out = json.loads(r.text)
        verdict = out.get("verdict") if out.get("verdict") in ("correct", "partial", "wrong") else "wrong"
        return verdict, str(out.get("reason") or "")[:200], r.cost
    except (json.JSONDecodeError, AttributeError):
        return "error", "裁判输出不是 JSON", 0.0


def expected(chunk, case: dict) -> bool:
    """这段是不是这道题应该检索到的：作品集按文件名（docs），运维手册按章节路径里的文字（sections）"""
    if case.get("sections"):
        return any(sec in chunk.section for sec in case["sections"])
    return chunk.case_id in set(case.get("docs") or [])


def material_for(qa: CaseQA, case: dict, refs: list) -> str:
    fmt = lambda c: f"《{c.title}》· {c.section}\n{c.text}"  # noqa: E731
    if sum(len(c.text) for c in qa.chunks) <= FULL_MATERIAL:
        return "\n\n".join(fmt(c) for c in qa.chunks)
    picked = [c for c in qa.chunks if expected(c, case)] + [c for c in refs if not expected(c, case)]
    return "\n\n".join(fmt(c) for c in picked)


async def run_case(qa: CaseQA, judge_gw: Gateway, case: dict) -> dict:
    result, refs, cited = await qa.ask(case["question"])
    retrieved = [c.case_id if not case.get("sections") else c.section for c in refs]
    hits = [expected(c, case) for c in refs]
    first = next((i + 1 for i, h in enumerate(hits) if h), None)
    cited_hits = [hits[n - 1] for n in cited]
    verdict, reason, judge_cost = await judge(judge_gw, case["question"], result.text, material_for(qa, case, refs), case.get("keywords"))
    return {
        "id": case["id"],
        "question": case["question"],
        "answer": result.text,
        "unanswerable": bool(case.get("unanswerable")),
        "retrieved": retrieved,
        "first_rank": first,
        "keywords_ok": keywords_ok(result.text, case.get("keywords") or []),
        "refused": refused(result.text),
        "cited_ok": any(cited_hits) if cited_hits else None,
        "verdict": verdict,
        "reason": reason,
        "model": result.model,  # 主模型失败时网关会换备用模型，记下实际用的
        "cost": round(result.cost + judge_cost, 5),
    }


def summarize(rows: list[dict], seconds: float, model: str, judge_model: str) -> dict:
    ans = [r for r in rows if not r["unanswerable"]]
    neg = [r for r in rows if r["unanswerable"]]
    pct = lambda n, d: round(100 * n / d, 1) if d else None  # noqa: E731
    cited = [r for r in ans if r["cited_ok"] is not None]
    return {
        "cases": len(rows),
        "answerable": len(ans),
        "unanswerable": len(neg),
        "hit_at_1": pct(sum(r["first_rank"] == 1 for r in ans), len(ans)),
        "hit_at_3": pct(sum(bool(r["first_rank"]) and r["first_rank"] <= 3 for r in ans), len(ans)),
        "mrr": round(sum(1 / r["first_rank"] for r in ans if r["first_rank"]) / len(ans), 3) if ans else None,
        "keyword_acc": pct(sum(r["keywords_ok"] for r in ans), len(ans)),
        "judge_correct": pct(sum(r["verdict"] == "correct" for r in ans), len(ans)),
        "judge_partial": sum(r["verdict"] == "partial" for r in ans),
        "judge_wrong": sum(r["verdict"] in ("wrong", "error") for r in ans),
        "citation_acc": pct(sum(bool(r["cited_ok"]) for r in cited), len(cited)),
        "refusal_acc": pct(sum(r["refused"] and r["verdict"] == "correct" for r in neg), len(neg)),
        "hallucinated": [r["id"] for r in neg if not r["refused"] or r["verdict"] == "wrong"],
        "cost_yuan": round(sum(r["cost"] for r in rows), 4),
        "seconds": round(seconds, 1),
        "model": "、".join(sorted({r["model"] for r in rows if r.get("model")})) or model,
        "judge_model": judge_model,
    }


async def run(qa: CaseQA, gw: Gateway, cases: list[dict] | None = None, concurrency: int = 4, title: str = "作品集问答") -> dict:
    """跑完整个评测集，返回 {ts, summary, rows}。裁判用 EVAL_JUDGE_MODELS（没配就和回答用同一组模型）"""
    if not qa.ready:
        raise AIUnavailable("知识库索引还没就绪，过几分钟再试")
    cases = cases or load_cases()
    judge_models = gw.s.eval_judge_models or gw.s.chat_models
    judge_gw = Gateway(replace(gw.s, chat_models=judge_models), gw.ledger) if judge_models != gw.s.chat_models else gw
    # 裁判看的不只是这次检索到的几段，还有这道题应该用到的资料：检索漏了也能判断回答对不对（见 material_for）
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> dict:
        async with sem:
            return await run_case(qa, judge_gw, case)

    start = time.monotonic()
    try:
        rows = await asyncio.gather(*(one(c) for c in cases))
    finally:
        if judge_gw is not gw:
            await judge_gw.close()
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": title,
        "summary": summarize(list(rows), time.monotonic() - start, gw.s.chat_models[0], judge_models[0]),
        "rows": list(rows),
    }


def summary_text(run_: dict) -> str:
    """Telegram 消息（HTML）"""
    s = run_["summary"]
    lines = [
        f"<b>📏 {escape(run_.get('title', 'RAG 评测'))}</b>（{s['cases']} 道题，{s['seconds']} 秒，约 ¥{s['cost_yuan']}）",
        "",
        f"检索：命中@1 {s['hit_at_1']}% · 命中@3 {s['hit_at_3']}% · MRR {s['mrr']}",
        f"要点命中：{s['keyword_acc']}%",
        f"裁判判对：{s['judge_correct']}%（部分对 {s['judge_partial']} 道，错 {s['judge_wrong']} 道）",
        f"引用准确：{s['citation_acc']}%",
        f"资料里没有的题，老实说没有：{s['refusal_acc']}%" + (f"（编了：{', '.join(s['hallucinated'])}）" if s["hallucinated"] else ""),
    ]
    bad = [r for r in run_["rows"] if r["verdict"] != "correct" or (not r["unanswerable"] and not r["keywords_ok"])]
    if bad:
        lines += ["", "<b>需要看一下的题</b>"]
        for r in bad[:8]:
            lines.append(f"• {escape(r['id'])} {escape(r['question'])}：{escape(r['verdict'])}，{escape(r['reason'][:60])}")
    lines += ["", f"回答模型 {escape(s['model'])}，裁判 {escape(s['judge_model'])}。明细：https://ops.liuxd.dev/api/ai/eval/latest?kind={escape(run_.get('kind', 'rag'))}"]
    return "\n".join(lines)
