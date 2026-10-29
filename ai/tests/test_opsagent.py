"""运维 Agent：假的 K8s API + Prometheus + 模型。重点测安全边界：命名空间白名单、日志脱敏、只读。"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import opsagent as ops
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app

CASES = Path(__file__).resolve().parents[2] / "src" / "content" / "cases"
LOG = "GET /api 200 from 203.0.113.7 user=a@b.com token=abcdef123\nOOM: killed ai worker\n"


def fake_cluster(seen: list) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        assert req.method == "GET"  # 只读
        if req.url.path == "/api/v1/alerts":
            return httpx.Response(200, json={"data": {"alerts": [{"labels": {"alertname": "KubePodCrashLooping", "namespace": "ops-desk", "pod": "ai-api-1"}, "state": "firing", "annotations": {"summary": "crash"}}]}})
        if req.url.path == "/api/v1/query":
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {"pod": "ai-api-1", "container": "api", "instance": "10.0.0.5:9100"}, "value": [0, str(380 * 2**20)]}]}})
        if req.url.path.endswith("/log"):
            return httpx.Response(200, text=LOG)
        if req.url.path.endswith("/pods"):
            return httpx.Response(200, json={"items": [{
                "metadata": {"name": "ai-api-1", "creationTimestamp": "2026-10-20T00:00:00Z"},
                "spec": {"containers": [{"name": "api", "resources": {"limits": {"memory": "384Mi"}}}]},
                "status": {"phase": "Running", "containerStatuses": [{"name": "api", "ready": True, "restartCount": 3, "state": {"running": {}}, "lastState": {"terminated": {"reason": "OOMKilled", "finishedAt": "2026-10-25T01:00:00Z"}}}]},
            }]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def tc(name: str, args: dict, i: int) -> dict:
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def cluster(tmp_path):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("t")
    seen: list = []
    return ops.Cluster("http://prom", "http://kube", sa_dir=sa, transport=fake_cluster(seen)), seen


def test_redact():
    out = ops.redact(LOG)
    assert "203.0.113.7" not in out and "a@b.com" not in out and "abcdef123" not in out
    assert "OOM: killed" in out


def test_tools_respect_allowlist(cluster):
    c, seen = cluster
    with pytest.raises(PermissionError):
        asyncio.run(ops.run_tool(c, "list_pods", {"namespace": "kube-public"}))
    with pytest.raises(PermissionError):
        asyncio.run(ops.run_tool(c, "pod_logs", {"namespace": "ops-desk", "pod": "../secrets"}))
    pods = asyncio.run(ops.run_tool(c, "list_pods", {"namespace": "ops-desk"}))
    assert pods[0]["last_exit"].startswith("OOMKilled") and pods[0]["mem_limit_mib"] == 384 and pods[0]["mem_mib"] == 380
    assert seen and all(m == "GET" for m, _ in seen)


def test_investigate_endpoint(tmp_path, cluster):
    c, _ = cluster
    script = [
        [tc("list_alerts", {}, 1)],
        [tc("list_pods", {"namespace": "ops-desk"}, 2), tc("pod_logs", {"namespace": "ops-desk", "pod": "ai-api-1"}, 3)],
        [tc("list_pods", {"namespace": "secret-stuff"}, 4)],
        "结论：ai-api 内存接近上限被 OOMKilled，建议调高 limits（需要人执行）。",
    ]
    sent: list = []

    def llm(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(500)
        sent.append(json.loads(req.content)["messages"])
        step = script.pop(0)
        msg = {"content": step} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 900, "completion_tokens": 80}})

    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, daily_budget=1.0)
    gw = Gateway(s, Ledger(tmp_path / "ai.db"), transport=httpx.MockTransport(llm))
    with TestClient(create_app(s, gw, cluster=c)) as client:
        r = client.post("/api/ai/opsagent", json={"question": "ai-api 为什么老重启？"})
        assert r.status_code == 200
        body = r.json()
        assert "OOMKilled" in body["answer"]
        assert [st["tool"] for st in body["steps"]] == ["list_alerts", "list_pods", "pod_logs", "list_pods"]
        assert body["steps"][-1]["summary"].startswith("被程序拦下")
        # 官网公开的接口不能查日志，模型硬要调用也会被拦下；工具列表里也没有它
        assert body["steps"][2]["summary"].startswith("被程序拦下")
        assert "pod_logs" not in json.dumps(ops.PUBLIC_TOOLS)
        # 交给模型的日志已脱敏
        tool_msgs = [m["content"] for m in sent[-1] if m["role"] == "tool"]
        assert not any("203.0.113.7" in t or "10.0.0.5" in t for t in tool_msgs)
