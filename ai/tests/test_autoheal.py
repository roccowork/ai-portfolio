"""自愈：假的 K8s API + 模型。重点测：低风险自动执行、其他等人确认、操作清单和命名空间的边界、去重。"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import autoheal
from app import opsagent as ops
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app

from .test_opsagent import CASES, tc


def fake_k8s(writes: list) -> httpx.MockTransport:
    jobs: dict[str, dict] = {
        "backup-ai-api-29000000": {
            "metadata": {"name": "backup-ai-api-29000000", "ownerReferences": [{"kind": "CronJob", "name": "backup-ai-api"}]},
            "status": {"conditions": [{"type": "Failed", "status": "True"}]},
        }
    }

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if req.method != "GET":
            writes.append((req.method, p))
            if req.method == "POST" and p.endswith("/jobs"):
                body = json.loads(req.content)
                jobs[body["metadata"]["name"]] = {**body, "status": {"succeeded": 1}}
                return httpx.Response(201, json=body)
            return httpx.Response(200, json={})
        if p == "/api/v1/alerts":
            return httpx.Response(200, json={"data": {"alerts": []}})
        if p.endswith("/cronjobs/backup-ai-api"):
            return httpx.Response(200, json={"metadata": {"name": "backup-ai-api", "uid": "u1"}, "spec": {"jobTemplate": {"metadata": {"labels": {"a": "b"}}, "spec": {"template": {}}}}})
        if p.endswith("/jobs"):
            return httpx.Response(200, json={"items": list(jobs.values())})
        if "/jobs/" in p:
            return httpx.Response(200, json=jobs[p.rsplit("/", 1)[1]])
        if p.endswith("/deployments/uptime-kuma"):
            return httpx.Response(200, json={"metadata": {"generation": 2}, "spec": {"replicas": 1}, "status": {"observedGeneration": 2, "updatedReplicas": 1, "readyReplicas": 1}})
        if p.endswith("/pods/lonely"):
            return httpx.Response(200, json={"metadata": {"name": "lonely"}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def llm(script: list) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(500)
        step = script.pop(0)
        msg = {"content": step} if isinstance(step, str) else {"content": "", "tool_calls": step}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    return httpx.MockTransport(handler)


def alert(name: str, fp: str, **labels) -> dict:
    return {"status": "firing", "fingerprint": fp, "startsAt": "2026-10-28T01:00:00Z", "labels": {"alertname": name, "severity": "warning", **labels}, "annotations": {"summary": "x"}}


@pytest.fixture
def env(tmp_path):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("t")
    writes: list = []
    c = ops.Cluster("http://prom", "http://kube", sa_dir=sa, transport=fake_k8s(writes))
    script: list = []
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, daily_budget=1.0)
    ledger = Ledger(tmp_path / "ai.db")
    gw = Gateway(s, ledger, transport=llm(script))
    h = autoheal.Healer(gw, c, autoheal.IncidentStore(ledger._db, ledger._lock), daily=3, poll=0, timeout=5)
    sent: list = []

    async def notify(text, buttons=None):
        sent.append((text, buttons))

    h.notify = notify
    return h, script, writes, sent, (s, gw, c)


async def drain(h: autoheal.Healer) -> None:
    while h.tasks:
        await asyncio.gather(*list(h.tasks))


def test_backup_failure_is_rerun_automatically(env):
    h, script, writes, sent, _ = env
    script += [
        [tc("list_alerts", {}, 1)],
        [tc("propose_action", {"action": "rerun_cronjob", "namespace": "ops-desk", "name": "backup-ai-api", "reason": "R2 偶发超时，补跑一次"}, 2)],
        "原因：上传 R2 时网络超时。处理：补跑备份。",
    ]

    async def run():
        assert h.on_webhook({"alerts": [alert("BackupJobFailed", "f1", namespace="ops-desk", job_name="backup-ai-api-29000000")]}) == 1
        await drain(h)

    asyncio.run(run())
    inc = h.store.recent()[0]
    assert inc["status"] == "fixed" and inc["risk"] == "auto"
    assert "已删除 1 个失败的旧 Job" in inc["result"]
    assert ("POST", "/apis/batch/v1/namespaces/ops-desk/jobs") in writes
    assert ("DELETE", "/apis/batch/v1/namespaces/ops-desk/jobs/backup-ai-api-29000000") in writes
    # 先发「执行中」，再发结果；自动执行的不带按钮
    assert len(sent) == 2 and sent[0][1] is None and "✅ 已恢复" in sent[1][0]
    # 同一条告警 4 小时内重发不再处理
    assert h.on_webhook({"alerts": [alert("BackupJobFailed", "f1")]}) == 0


def test_restart_waits_for_confirmation(env):
    h, script, writes, sent, _ = env
    script += [
        [tc("propose_action", {"action": "restart_deployment", "namespace": "monitoring", "name": "uptime-kuma", "reason": "进程卡死"}, 1)],
        "原因：uptime-kuma 不响应。处理：滚动重启。",
    ]

    async def run():
        h.on_webhook({"alerts": [alert("PodRestartingOften", "f2", namespace="monitoring")]})
        await drain(h)
        assert h.store.recent()[0]["status"] == "waiting" and not writes
        assert sent[-1][1] == [[("✅ 执行", "heal:ok:1"), ("🙅 忽略", "heal:no:1")]]
        text = await h.on_button("heal:ok:1")
        assert "执行中" in text
        await drain(h)
        assert "已经处理过了" in await h.on_button("heal:ok:1")

    asyncio.run(run())
    assert writes == [("PATCH", "/apis/apps/v1/namespaces/monitoring/deployments/uptime-kuma")]
    assert h.store.get(1)["status"] == "fixed"


def test_boundaries(env):
    h, script, writes, _sent, _ = env

    async def check(args: dict) -> str:
        try:
            await h.validate(args)
        except (PermissionError, ValueError) as e:
            return str(e)
        return "ok"

    async def run():
        assert "不允许" in await check({"action": "restart_deployment", "namespace": "kube-system", "name": "coredns"})
        assert "自己" in await check({"action": "restart_deployment", "namespace": "ops-desk", "name": "ai-api"})
        assert "自己" in await check({"action": "delete_pod", "namespace": "ops-desk", "name": "ai-api-5d9c-abcde"})
        assert "清单" in await check({"action": "exec_shell", "namespace": "ops-desk", "name": "x"})
        assert "不合法" in await check({"action": "delete_pod", "namespace": "ops-desk", "name": "../secrets"})
        assert "没有控制器" in await check({"action": "delete_pod", "namespace": "ops-desk", "name": "lonely"})
        # 不处理的告警、提示级别
        payload = {"alerts": [alert("Watchdog", "w"), {**alert("CPUThrottlingHigh", "i"), "labels": {"alertname": "CPUThrottlingHigh", "severity": "info"}}]}
        assert h.on_webhook(payload) == 0

    asyncio.run(run())
    assert not writes and not script


def test_no_action_and_endpoints(env, tmp_path):
    _h, script, writes, _sent, (s, gw, c) = env
    script += ["原因：磁盘使用 86%，主要是镜像缓存。重启解决不了，建议人工清理旧镜像。"]
    with TestClient(create_app(s, gw, cluster=c)) as client:
        r = client.post("/internal/alertmanager", json={"alerts": [alert("NodeDiskHigh", "f3")]})
        assert r.json() == {"started": 1}
        items = []
        for _ in range(50):
            items = client.get("/api/ai/heal/recent").json()["items"]
            if items:
                break
        assert items[0]["alert"] == "NodeDiskHigh" and items[0]["action"] == "" and "人处理" in items[0]["status"]
        client.post("/internal/alertmanager", json={"alerts": [{**alert("NodeDiskHigh", "f3"), "status": "resolved", "endsAt": "2026-10-28T02:00:00Z"}]})
    assert not writes


def test_cap_and_errors_still_notify(env):
    h, script, _writes, sent, _ = env
    h.daily = 0  # 今天的分析次数已经用完

    async def go():
        h.on_webhook({"alerts": [alert("NodeDiskHigh", "cap1", namespace="monitoring")]})
        await drain(h)

    asyncio.run(go())
    assert len(sent) == 1 and "NodeDiskHigh" in sent[0][0] and "没有诊断" in sent[0][0]


def test_analysis_error_is_sent(env):
    h, script, _writes, sent, (s, gw, c) = env
    s.api_key = ""  # 模型不可用：分析出错

    async def go():
        h.on_webhook({"alerts": [alert("NodeDiskHigh", "err1")]})
        await drain(h)

    asyncio.run(go())
    assert len(sent) == 1 and "分析出错" in sent[0][0]


def test_card_explains_the_alert_first(env):
    h, script, _writes, sent, _ = env
    script += ["原因：磁盘使用 86%。建议人工清理。"]
    a = alert("NodeDiskHigh", "card1", namespace="monitoring")
    a["annotations"] = {"summary": "根分区使用超过 85%"}

    async def go():
        h.on_webhook({"alerts": [a]})
        await drain(h)

    asyncio.run(go())
    text = sent[0][0]
    lines = text.split("\n")
    assert lines[0].startswith("🛠 <b>自愈 #") and "NodeDiskHigh" in lines[0]
    assert lines[1] == "告警：🟡 警告 · 根分区使用超过 85% · 10-28 09:00 开始"  # 新加坡时间
    assert lines[2] == "对象：monitoring" and lines[3].startswith("分析：")
