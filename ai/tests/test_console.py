"""总控台：节点状态和连线「流动中」都从真实数据算出来；某一块查不到不影响其他；没有密钥看不到。"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import console
from app import opsagent as ops
from app.config import Settings
from app.gateway import Gateway
from app.ledger import Ledger
from app.main import create_app

NOW = datetime.now(timezone.utc)
CASES = Path(__file__).resolve().parents[2] / "src" / "content" / "cases"


def iso(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def fake(req: httpx.Request) -> httpx.Response:
    p, q = req.url.path, req.url.params.get("query", "")
    if p == "/api/v1/query":
        if q.startswith("probe_success"):
            res = [{"metric": {"group": "own", "name": n}, "value": [0, "1"]} for n in ["ops.liuxd.dev", "liuxd.dev", "cv.liuxd.dev", "stats.liuxd.dev", "dify.liuxd.dev"]]
            res += [{"metric": {"group": "own", "name": "it.liuxd.dev"}, "value": [0, "0"]}]
            res += [{"metric": {"group": "dependency", "name": n}, "value": [0, "1"]} for n in ["阿里云百炼", "GitHub", "Cloudflare", "Telegram"]]
            return httpx.Response(200, json={"status": "success", "data": {"result": res}})
        if "traefik" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "value": [0, "3.5"]}]}})
        if "ai_calls_total" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "value": [0, "12"]}]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})
    if p == "/api/v1/alerts":
        return httpx.Response(200, json={"data": {"alerts": []}})
    if "applications" in p:
        return httpx.Response(200, json={"items": [
            {"metadata": {"name": "ops-desk"}, "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}},
            {"metadata": {"name": "personal"}, "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}},
            {"metadata": {"name": "umami"}, "status": {"sync": {"status": "OutOfSync"}, "health": {"status": "Degraded"}}},
        ]})
    return httpx.Response(404)


def make(tmp_path, assistant: dict | None):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("t")
    c = ops.Cluster("http://prom", "http://kube", sa_dir=sa, transport=httpx.MockTransport(fake))

    async def status():
        return {"deploys": [{"sha": "abc", "time": iso(2), "state": "current"}], "availability7d": 99.9,
                "backups": [{"name": "backup-ai-api", "lastSuccess": iso(5)}, {"name": "backup-github", "lastSuccess": iso(30)}]}

    def local():
        return {"usage_today": {"calls": 8, "cost": 0.12, "saved_min": 0}, "by_feature_today": {"ask": 5}, "heal_24h": [{"status": "fixed"}],
                "heal_fixed": 3, "enabled": True, "kb": {"ready": True, "chunks": 120, "hit": 92.5}}

    b = console.Console(c, status, local, "http://assistant/internal/summary")

    async def asst():
        if assistant is None:
            raise httpx.ConnectError("down")
        return assistant

    b.assistant = asst  # type: ignore[method-assign]
    return b


def test_build_from_real_signals(tmp_path):
    fr = {"ts": iso(3), "status": "ok", "candidates": 30, "picks": 3, "sources": 7, "sources_ok": 6}
    d = asyncio.run(make(tmp_path, {"bot": True, "frontier": fr, "career": {"total": 4, "week": 2, "applied": 1, "interview": 1, "offer": 0}}).build())
    n = {x["id"]: x for x in d["nodes"]}
    e = {(x["from"], x["to"]): x["state"] for x in d["edges"]}
    assert n["sites"]["status"] == "bad" and "it.liuxd.dev" in "".join(n["sites"]["lines"])
    assert n["analyst"]["status"] == "bad"  # umami 应用异常
    assert n["editor"]["status"] == "ok" and "3 条选题" in n["editor"]["lines"][0]
    assert n["archivist"]["status"] == "warn" and "backup-github" in "".join(n["archivist"]["lines"])
    assert "今天回答 5 次" in n["xiaoxiao"]["lines"] and n["kb"]["status"] == "ok"
    # 流动：有提问、有模型调用、刚发布过、AI 前沿刚跑过；GitHub 镜像备份过期了不算流动；连到异常节点的线标红
    assert e[("visitors", "xiaoxiao")] == "flow" and e[("gateway", "models")] == "flow" and e[("deployer", "k3s")] == "flow"
    assert e[("news", "editor")] == "flow" and e[("archivist", "repos")] == "idle" and e[("visitors", "analyst")] == "bad"
    assert "本周分析 2 个岗位，共 4 个" in n["coach"]["lines"] and e[("me", "coach")] == "flow"
    assert d["summary"]["staff"] == 9 and "统计员" in d["summary"]["problems"]


def test_assistant_down_does_not_break(tmp_path):
    d = asyncio.run(make(tmp_path, None).build())
    n = {x["id"]: x for x in d["nodes"]}
    assert n["butler"]["status"] == "bad" and n["visitors"]["status"] == "ok"


def test_endpoint_needs_key(tmp_path):
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, me_token="secret-k")
    with TestClient(create_app(s, Gateway(s, Ledger(tmp_path / "ai.db")))) as c:
        assert c.post("/api/ai/console", json={"k": "wrong"}).status_code == 404
        # 不在集群里（没有 ServiceAccount）时密钥对了也查不到
        assert c.post("/api/ai/console", json={"k": "secret-k"}).status_code == 503
