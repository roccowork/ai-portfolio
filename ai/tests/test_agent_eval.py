"""运维 Agent 故障演练评测：注入、提议只记录不执行、程序拦截、打分、评测集格式。假的模型服务，没有集群，不花钱。"""

import asyncio
import json

import httpx
import pytest

from app import autoheal
from app import opsagent as ops
from app.config import Settings
from app.evals import agent as ev
from app.gateway import Gateway
from app.ledger import Ledger

TOOL_NAMES = {t["function"]["name"] for t in ops.TOOLS} | {"search_runbook", "propose_action"}


def call(name: str, args: dict, i: int = 0) -> list[dict]:
    return [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]


def fake_llm(plan, judged: list | None = None) -> httpx.MockTransport:
    """plan(已经返回过几次工具结果) -> 下一步：工具调用列表或最终回答。裁判一律判 correct"""

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        msgs = body["messages"]
        if msgs[0]["content"].startswith("你是评测员"):
            if judged is not None:
                judged.append(msgs[1]["content"])
            msg = {"content": json.dumps({"verdict": "correct", "reason": "和真实情况一致"})}
        else:
            step = plan(sum(m["role"] == "tool" for m in msgs))
            msg = {"content": step} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 800, "completion_tokens": 60}})

    return httpx.MockTransport(handler)


def setup(tmp_path, plan, judged=None):
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=tmp_path, samples_dir=tmp_path, daily_budget=5.0)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=fake_llm(plan, judged))
    c = ops.Cluster("http://prom", sa_dir=tmp_path / "none")  # 没有 token：离线模式，不会访问任何集群
    return gw, c


def case(cid: str) -> dict:
    return next(c for c in ev.load_cases() if c["id"] == cid)


def test_faults_inject_on_top_of_base(tmp_path):
    c = ops.Cluster("http://prom", sa_dir=tmp_path / "none")
    inj = asyncio.run(ev.resolve(c, case("a3")))["inject"]
    pod = inj["pods"][0]["pod"]
    assert pod == ev.fake_pod("ops-desk") and pod.startswith("ops-desk-")
    f = ev.Faults(c, inj)
    pods = asyncio.run(f.run("list_pods", {"namespace": "ops-desk"}))
    assert pods[0]["pod"] == pod and pods[0]["last_exit"].startswith("OOMKilled") and pods[0]["mem_limit_mib"] == 64
    logs = asyncio.run(f.run("pod_logs", {"namespace": "ops-desk", "pod": pod, "previous": True}))
    assert "GET /chatbi/" in logs and "10.42.0.1" not in logs  # 注入的日志也照样脱敏
    assert "previous=true" in asyncio.run(f.run("pod_logs", {"namespace": "ops-desk", "pod": pod}))  # 只注入了崩溃前的日志，提示它去看 previous
    assert asyncio.run(f.run("promql", {"query": 'container_memory_working_set_bytes{namespace="ops-desk"}'}))[0]["value"] == "65850982"
    assert asyncio.run(f.run("cluster_overview", {}))["mem_used_pct"] == 52.0
    with pytest.raises(PermissionError):
        asyncio.run(f.run("list_pods", {"namespace": "default"}))  # 白名单在注入层也生效
    assert [s["tool"] for s in f.seen] == ["list_pods", "pod_logs", "pod_logs", "promql", "cluster_overview"]


def test_heal_case_records_proposal_without_executing(tmp_path):
    c0 = case("h2")
    job = c0["inject"]["pods"][0]["pod"]
    steps = [
        call("list_pods", {"namespace": "ops-desk"}),
        call("pod_logs", {"namespace": "ops-desk", "pod": job}),
        call("propose_action", {"action": "rerun_cronjob", "namespace": "ops-desk", "name": "backup-ai-api", "reason": "网络超时，补跑"}),
    ]
    judged: list = []
    gw, c = setup(tmp_path, lambda n: steps[n] if n < len(steps) else "连 R2 网络超时，已提议补跑 backup-ai-api。", judged)
    row = asyncio.run(ev.run_case(gw, gw, c, c0, {}))
    assert row["tools"] == ["list_pods", "pod_logs", "propose_action"]
    assert row["proposal"]["name"] == "backup-ai-api" and row["action_ok"] and row["tools_ok"] and row["keywords_ok"]
    assert row["verdict"] == "correct" and ev.passed(row) and row["blocked"] == []
    # 裁判拿到了真实情况、Agent 看到的日志和提议
    assert "i/o timeout" in judged[0] and "补跑一次 CronJob backup-ai-api" in judged[0] and "rerun_cronjob" in judged[0]


def test_guard_blocks_self_heal_and_counts_it(tmp_path):
    c0 = asyncio.run(ev.resolve(ops.Cluster("http://prom", sa_dir=tmp_path / "none"), case("h7")))
    pod = c0["inject"]["pods"][0]["pod"]
    steps = [call("list_pods", {"namespace": "ops-desk"}), call("propose_action", {"action": "delete_pod", "namespace": "ops-desk", "name": pod, "reason": "重建"})]
    gw, c = setup(tmp_path, lambda n: steps[n] if n < len(steps) else "ai-api 内存超限 OOMKilled，不能处理自己，建议调大 limit。")
    row = asyncio.run(ev.run_case(gw, gw, c, c0, {}))
    assert row["proposal"] is None and row["action_ok"]  # 程序拦下了，最后没有提议
    assert row["blocked"] == ["ai-api 不能处理自己"]


def test_wrong_action_and_missing_tools_fail(tmp_path):
    steps = [call("propose_action", {"action": "restart_deployment", "namespace": "ops-desk", "name": "ops-desk", "reason": "重启试试"})]
    gw, c = setup(tmp_path, lambda n: steps[n] if n < len(steps) else "已重启。")
    row = asyncio.run(ev.run_case(gw, gw, c, case("h1"), {}))
    assert row["action_ok"] is False and row["tools_ok"] is False and not ev.passed(row)  # 内存不够不该重启，也没查 Pod 就动手


def test_requires_healthy_skips_when_cluster_has_alerts(tmp_path, monkeypatch):
    gw, c = setup(tmp_path, lambda n: "正常")
    monkeypatch.setitem(ev.OFFLINE, "list_alerts", [{"name": "NodeDiskHigh"}])
    row = asyncio.run(ev.run_case(gw, gw, c, case("a1"), {}))
    assert "NodeDiskHigh" in row["skipped"]
    monkeypatch.setitem(ev.OFFLINE, "list_alerts", [])
    steps = [call("cluster_overview", {})]
    gw, c = setup(tmp_path, lambda n: steps[n] if n < len(steps) else "整体正常：CPU 9.5%，内存 52%，磁盘 46%。")
    row = asyncio.run(ev.run_case(gw, gw, c, case("a1"), {}))
    assert not row.get("skipped") and ev.passed(row)


def test_run_summary_and_message(tmp_path):
    steps = [call("list_pods", {"namespace": "ops-desk"})]
    gw, c = setup(tmp_path, lambda n: steps[n] if n < len(steps) else "网站 Pod 内存超过上限被 OOMKilled，建议调大 limit。")
    out = asyncio.run(ev.run(gw, c, [case("a3"), case("s1")]))
    s = out["summary"]
    assert s["cases"] == 2 and s["judge_correct"] == 100.0 and s["safety_cases"] == 1 and s["avg_tool_calls"] == 1.0
    assert s["pass_rate"] == 100.0 and s["keyword_acc"] == 50.0  # 两题用的同一个假回答，s1 没说「不能」，要点没中（通过只看裁判、工具和处置）
    text = ev.summary_text(out)
    assert text.startswith("<b>🧪 运维 Agent 评测</b>") and "kind=agent" in text


def test_real_cases_are_well_formed(tmp_path):
    cases = ev.load_cases()
    assert len(cases) >= 20 and len({c["id"] for c in cases}) == len(cases)
    c = ops.Cluster("http://prom", sa_dir=tmp_path / "none")
    for raw in cases:
        x = asyncio.run(ev.resolve(c, raw))
        assert "{pod:" not in json.dumps(x, ensure_ascii=False), x["id"]
        assert x["mode"] in ("heal", "ask"), x["id"]
        assert x.get("truth") or x.get("requires_healthy"), x["id"]
        for group in x.get("expect", {}).get("tools", []):
            assert set(group.split("|")) <= TOOL_NAMES, (x["id"], group)
        if x["mode"] == "heal":
            assert "action" in x["expect"] and x["alert"]["labels"]["alertname"], x["id"]
            exp = x["expect"]["action"]
            for o in exp if isinstance(exp, list) else [exp] if exp else []:
                autoheal.check_action(o)  # 标准答案本身要能通过程序的校验
        else:
            assert x["question"], x["id"]
    assert sum(c.get("safety", False) for c in cases) >= 5 and sum(c["mode"] == "heal" for c in cases) >= 8


def test_eval_queue_runs_later_jobs_instead_of_dropping():
    from app.evals.queue import EvalQueue

    async def main():
        q, order, gate = EvalQueue(), [], asyncio.Event()

        async def slow():
            await gate.wait()
            order.append("rag")

        async def fast():
            order.append("agent")

        assert q.submit("RAG 评测", slow) == "started"
        assert q.submit("Agent 评测", fast) == "queued"  # 以前这里直接放弃了
        assert q.submit("Agent 评测", fast) == "dup"
        gate.set()
        await asyncio.gather(*q.tasks)
        assert order == ["rag", "agent"] and not q.names
        assert q.submit("Agent 评测", fast) == "started"
        await asyncio.gather(*q.tasks)

    asyncio.run(main())


def test_grounding_check_rejects_unused_tool_claims(tmp_path):
    """回答里写「list_pods 显示……」却没调用过：程序让它重来一次，这次真的去查"""
    seen: list = []
    steps = {0: "list_pods 显示 uptime-kuma 重启 127 次。", 1: call("list_pods", {"namespace": "monitoring"})}
    answers = iter([steps[0], steps[1], "Uptime Kuma 没有就绪，建议删除 Pod 重建。"])

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        step = next(answers)
        msg = {"content": step} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})

    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=tmp_path, samples_dir=tmp_path, daily_budget=5.0)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=httpx.MockTransport(handler))
    c = ops.Cluster("http://prom", sa_dir=tmp_path / "none")
    f = ev.Faults(c, {})
    answer, steps_, _ = asyncio.run(ops.investigate(gw, c, "Uptime Kuma 打不开了", runner=f.run))
    assert answer.startswith("Uptime Kuma 没有就绪") and [x["tool"] for x in steps_] == ["list_pods"]
    assert "程序核对：你的回答提到了 list_pods，但这次并没有调用" in seen[1]["messages"][-1]["content"]
    assert "wecom-relay" in seen[0]["messages"][0]["content"]  # 系统提示里有组件和命名空间的对照


def test_numbers_must_come_from_tool_results():
    """2026-10-30 值班台真实回答：Prometheus 3.1G、ai-api 480MB，可它们的上限只有 1280Mi、384Mi"""
    evidence = json.dumps([{"pod": "prometheus-0", "mem_mib": 1120.5, "mem_limit_mib": 1280}, {"pod": "ai-api-x", "mem_mib": 210.3, "mem_limit_mib": 384}]) + '{"mem_used_pct": 52.4}'
    fake = "节点内存使用率 82%，Prometheus 容器占用 3145728000 字节（3.1 GB），ai-api 占用 480MB。"
    assert ops.unsupported_numbers(fake, evidence) == ["82", "3145728000", "3.1", "480"]
    good = "内存使用率 52%，正常。Prometheus 占 1.1G（上限 1280Mi），ai-api 210MB，重启 0 次。"
    assert ops.unsupported_numbers(good, evidence) == []


def test_agent_retries_when_numbers_are_made_up(tmp_path):
    seen: list = []
    answers = iter([call("cluster_overview", {}), "内存使用率 82%，可用 2.1G，偏高。", "内存使用率 52%，正常。"])

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        step = next(answers)
        msg = {"content": step} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})

    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=tmp_path, samples_dir=tmp_path, daily_budget=5.0)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=httpx.MockTransport(handler))
    c = ops.Cluster("http://prom", sa_dir=tmp_path / "none")
    answer, _, _ = asyncio.run(ops.investigate(gw, c, "服务器内存高吗", runner=ev.Faults(c, {}).run))
    assert answer == "内存使用率 52%，正常。"
    assert "回答里这些数字在工具结果里找不到：82、2.1" in seen[-1]["messages"][-1]["content"]
