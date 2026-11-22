"""总控台：节点状态和连线「流动中」都从真实数据算出来；某一块查不到不影响其他；没有密钥看不到。"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import console, tgauth
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
            res = [{"metric": {"group": "own", "name": n}, "value": [0, "1"]} for n in ["ops.liuxd.dev", "liuxd.dev", "cv.liuxd.dev", "stats.liuxd.dev"]]
            res += [{"metric": {"group": "own", "name": "it.liuxd.dev"}, "value": [0, "0"]}]
            res += [{"metric": {"group": "vps", "name": "示例VPS"}, "value": [0, "0"]}]  # VPS 由 VPSDown 和私人助理报，不重复
            res += [{"metric": {"group": "dependency", "name": n}, "value": [0, "1"]} for n in ["阿里云百炼", "GitHub", "Cloudflare", "Telegram"]]
            return httpx.Response(200, json={"status": "success", "data": {"result": res}})
        if "traefik" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "value": [0, "3.5"]}]}})
        if "kube_pod_status_phase" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "value": [0, "42"]}]}})
        if "ai_calls_total" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "value": [0, "12"]}]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})
    if p == "/api/v1/query_range":  # 已恢复告警：VPSDown 在 2 小时前响了 5 分钟，TargetDown 一直在响
        end = NOW.timestamp()
        ago = lambda m: [end - m * 60, "1"]  # noqa: E731
        res = [{"metric": {"alertname": "VPSDown", "alertstate": "firing", "namespace": "ops-desk", "name": "示例VPS", "severity": "warning"}, "values": [ago(m) for m in range(124, 119, -1)]},
               {"metric": {"alertname": "VPSDown", "alertstate": "pending", "namespace": "ops-desk", "name": "示例VPS", "severity": "warning"}, "values": [ago(m) for m in range(127, 124, -1)]},
               # 发布时 ai-api 重启了 2 分钟：只到了「等待中」，没推送
               {"metric": {"alertname": "DeploymentReplicasLow", "alertstate": "pending", "namespace": "ops-desk", "deployment": "ai-api", "severity": "warning"}, "values": [ago(m) for m in (40, 39)]},
               {"metric": {"alertname": "TargetDown", "alertstate": "firing", "severity": "warning"}, "values": [ago(m) for m in range(30, -1, -1)]}]
        return httpx.Response(200, json={"status": "success", "data": {"result": res}})
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
                "heal_fixed": 3, "enabled": True, "db": {"mb": 2.4, "incidents": 7, "evals": 5}, "kb": {"ready": True, "chunks": 120, "hit": 92.5}}

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
    assert "edges" not in d  # 「数据流」页 2026-11-22 删了
    assert n["sites"]["status"] == "bad" and "it.liuxd.dev" in "".join(n["sites"]["lines"])
    assert n["analyst"]["status"] == "bad"  # umami 应用异常
    assert n["editor"]["status"] == "ok" and "3 条选题" in n["editor"]["lines"][0]
    assert n["archivist"]["status"] == "warn" and "backup-github" in "".join(n["archivist"]["lines"])
    assert "今天回答 5 次" in n["xiaoxiao"]["lines"] and n["kb"]["status"] == "ok"
    assert "本周分析 2 个岗位，共 4 个" in n["coach"]["lines"]
    assert d["summary"]["staff"] == 9 and "统计员" in d["summary"]["problems"]
    # 每个数字只在负责它的卡片上：拨测 → 监控员，备份 → 档案员；Telegram 只剩「我」一张卡片
    assert n["sites"]["lines"] == ["打不开：it.liuxd.dev"] and n["visitors"]["lines"] == ["最近 15 分钟 3.5 次请求 / 分钟"]
    assert "7 天可用率 99.9%" in n["watcher"]["lines"] and "最近一次 5 小时前" in n["archivist"]["lines"]
    assert n["dbs"]["lines"] == ["AI 账本 2.4 MB", "自愈记录 7 条，评测 5 次"] and n["repos"]["lines"][0].startswith("GitHub 正常")
    assert n["k3s"]["lines"][0] == "42 个 Pod 运行中" and "telegram" not in n and n["me"]["lines"][0].startswith("Telegram 正常")


def test_paused_project(tmp_path):
    # 私人助理报「栏目暂停」：编辑标成暂停，不算问题，也不算在岗人数
    fr = {"ts": iso(3), "status": "ok", "candidates": 30, "picks": 0, "sources": 7, "sources_ok": 7, "paused": True}
    d = asyncio.run(make(tmp_path, {"bot": True, "frontier": fr}).build())
    n = {x["id"]: x for x in d["nodes"]}
    assert n["editor"]["status"] == "paused" and "暂停" in n["editor"]["lines"][0]
    assert "编辑" not in d["summary"]["problems"] and d["summary"]["staff"] == 8


def test_assistant_down_does_not_break(tmp_path):
    d = asyncio.run(make(tmp_path, None).build())
    n = {x["id"]: x for x in d["nodes"]}
    assert n["butler"]["status"] == "bad" and n["visitors"]["status"] == "ok"


def test_private_staff_from_assistant(tmp_path):
    # 私人数字员工由私人助理报上来（这里是虚构的示例）；非法的节点、状态都丢掉或纠正，连线（edges）不再读
    staff = [
        {"id": "demo", "name": "示例员工", "role": "示例岗位", "desc": "说明", "link": "https://ops.liuxd.dev/me/x/#k={k}", "status": "ok", "detail": "demo",
         "lines": ["今天干了 3 件事"], "edges": [{"from": "me", "to": "p-demo", "label": "交给它", "flow": True},
                                           {"from": "p-demo", "to": "butler", "label": "汇报"},
                                           {"from": "p-demo", "to": "nowhere", "label": "无效"},
                                           {"from": "me", "to": "butler", "label": "不许改普通节点之间的线"}]},
        {"id": "demo", "name": "重复的 id"},
        {"id": "", "name": "没有 id"},
        {"id": "odd", "name": "怪状态", "status": "hacked", "link": "javascript:alert(1)"},
        "不是对象",
    ]
    d = asyncio.run(make(tmp_path, {"bot": True, "staff": staff}).build())
    n = {x["id"]: x for x in d["nodes"]}
    assert n["p-demo"]["col"] == "staff" and n["p-demo"]["status"] == "ok" and n["p-demo"]["lines"] == ["今天干了 3 件事"]
    assert n["p-demo"]["name"] == "示例员工" and n["p-odd"]["status"] == "unknown" and "link" not in n["p-odd"]
    assert n["p-demo"]["detail"] == "p:demo" and "detail" not in n["p-odd"]
    assert d["summary"]["staff"] == 11


def test_alert_center_and_resources(tmp_path):
    # 告警中心：拨测打不开、Argo CD 不健康、备份过期都排成一张表，异常在前；资源中心按分组列出，带详情入口
    asst = {"bot": True, "alerts": [{"level": "warn", "title": "示例 VPS 掉线", "detail": "演示数据"}, {"level": "oops", "title": "非法级别"}],
            "resources": [{"id": "vps1", "group": "server", "name": "示例 VPS", "status": "ok", "lines": ["在线"], "detail": "vps"},
                          {"id": "x", "group": "nowhere", "name": "非法分组"}, {"id": "y", "group": "server", "name": "非法详情", "detail": "../x"}]}
    d = asyncio.run(make(tmp_path, asst).build())
    titles = [a["title"] for a in d["alerts"]]
    assert "it.liuxd.dev 打不开" in titles and "umami 没同步或不健康" in titles and "backup-github 超过 26 小时没成功" in titles
    assert "示例 VPS 掉线" in titles and "非法级别" not in titles
    assert not any("示例VPS" in t for t in titles)
    rec = d["recovered"]
    assert [r["title"] for r in rec] == ["DeploymentReplicasLow", "VPSDown"]
    assert rec[0]["level"] == "flash" and not rec[0]["pushed"] and rec[0]["detail"] == "ops-desk · ai-api" and rec[0]["minutes"] == 2
    assert rec[1]["pushed"] and rec[1]["minutes"] == 8 and rec[1]["detail"] == "ops-desk · 示例VPS"  # 等待中 3 分钟 + 告警 5 分钟
    assert [a["level"] for a in d["alerts"]] == sorted((a["level"] for a in d["alerts"]), key=lambda x: console.LEVELS[x])
    r = {x["id"]: x for x in d["resources"]}
    assert r["probe-it.liuxd.dev"]["status"] == "bad" and r["probe-it.liuxd.dev"]["group"] == "site" and r["probe-it.liuxd.dev"]["detail"] == "s:it.liuxd.dev"
    assert r["probe-ops.liuxd.dev"]["lines"][0].startswith("作品集网站")  # 每个网站一句话简介
    assert r["probe-阿里云百炼"]["group"] == "dependency" and r["k3s"]["detail"] == "status" and r["argo"]["status"] == "warn"
    assert r["p-vps1"]["detail"] == "p:vps" and "p-x" not in r and "probe-示例VPS" not in r and "detail" not in r["p-y"]
    assert d["summary"]["alerts"] == len(d["alerts"]) and d["summary"]["cost_today"] == 0.12
    assert {n["id"]: n.get("detail") for n in d["nodes"]}["xiaoxiao"] == "leads"


def test_private_pages(tmp_path):
    # 私人网页做成标签页：只收 /me/ 下面的路径（虚构的示例）
    pages = [{"key": "demo", "name": "示例页", "path": "/me/demo/"}, {"key": "evil", "name": "外站", "path": "https://evil.example/"},
             {"key": "Bad Key", "name": "x", "path": "/me/x/"}, {"key": "up", "name": "上级", "path": "/me/../api/"}]
    d = asyncio.run(make(tmp_path, {"bot": True, "pages": pages}).build())
    assert d["pages"] == [{"key": "p-demo", "name": "示例页", "path": "/me/demo/"}]


def test_telegram_auth():
    # Telegram 小程序的 initData：签名对、没过期、是主人本人才放行
    now = 1_800_000_000
    good = tgauth.sign({"auth_date": str(now - 60), "user": '{"id":42,"first_name":"x"}', "query_id": "q"}, "bot-token")
    assert tgauth.verify(good, "bot-token", "42", now=now)
    assert not tgauth.verify(good, "other-token", "42", now=now)  # 别的机器人签的
    assert not tgauth.verify(good, "bot-token", "43", now=now)  # 不是主人
    assert not tgauth.verify(good, "bot-token", "42", now=now + 3 * 86400)  # 过期
    assert not tgauth.verify(good.replace("id%22%3A42", "id%22%3A43"), "bot-token", "43", now=now)  # 改了内容签名就不对
    assert not tgauth.verify("", "bot-token", "42") and not tgauth.verify("a=%%%", "bot-token", "42")


def test_endpoint_needs_telegram(tmp_path):
    import time

    calls: list = []

    class FakeTg:
        chat_id = "42"

        async def call(self, method, **params):
            calls.append((method, params))
            if method == "getUpdates":
                await asyncio.sleep(3600)
            return True

        async def send(self, text, *a, **k):
            calls.append(("send", {"text": text, **k}))
            return {}

        async def close(self):
            pass

    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path, tg_token="bot-token", tg_chat_id="42", me_token="k" * 32)
    good = tgauth.sign({"auth_date": str(int(time.time())), "user": '{"id":42}'}, "bot-token")
    with TestClient(create_app(s, Gateway(s, Ledger(tmp_path / "ai.db")), telegram=FakeTg())) as c:  # type: ignore[arg-type]
        assert c.post("/api/ai/console", json={"init": "auth_date=1&hash=x"}).status_code == 404
        # 不在集群里（没有 ServiceAccount）时身份对了也查不到
        assert c.post("/api/ai/console", json={"init": good}).status_code == 503
        # 浏览器入口：私人密钥
        assert c.post("/api/ai/console", json={"init": "key:" + "k" * 32}).status_code == 503
        assert c.post("/api/ai/console", json={"init": "key:wrong"}).status_code == 404
        assert c.post("/api/ai/console/detail", json={"init": "x=1", "section": "pods"}).status_code == 404
        assert c.post("/api/ai/console/detail", json={"init": good, "section": "nope"}).status_code == 404
        assert c.post("/api/ai/console/detail", json={"init": good, "section": 's:x"} or up{'}).status_code == 404  # 不能拼进 PromQL
        assert c.post("/api/ai/console/detail", json={"init": good, "section": "s:阿里云百炼"}).status_code == 503
    # 值班台机器人输入框左边的菜单按钮设成总控台小程序
    # 菜单改版后发一次说明并收起旧键盘；再启动不重复发
    notes = [p for m, p in calls if m == "send"]
    assert len(notes) == 1 and notes[0]["keyboard"]["keyboard"] == [[{"text": "📖 使用方法"}]] and "总控台" in notes[0]["text"]
    calls.clear()
    with TestClient(create_app(s, Gateway(s, Ledger(tmp_path / "ai.db")), telegram=FakeTg())):  # type: ignore[arg-type]
        pass
    assert not [p for m, p in calls if m == "send"]
    menu = next(p for m, p in calls if m == "setChatMenuButton")
    assert menu["menu_button"] == {"type": "web_app", "text": "总控台", "web_app": {"url": "https://ops.liuxd.dev/console/"}}


def test_health_accepts_head(tmp_path):
    # UptimeRobot 默认用 HEAD 拨测 /api/ai/health（ai-api 挂了只能靠它从外面报）：HEAD 和 GET 都是 200
    s = Settings(api_key="test", data_dir=tmp_path, cases_dir=CASES, samples_dir=tmp_path)
    with TestClient(create_app(s, Gateway(s, Ledger(tmp_path / "ai.db")))) as c:
        assert c.head("/api/ai/health").status_code == 200 and c.get("/api/ai/health").json()["status"] == "ok"


def test_site_detail(tmp_path):
    # 资源中心点开单个网站：状态、托管在哪、证书谁续期、延迟从哪测的
    from app import probes

    b = make(tmp_path, {"bot": True})
    t = asyncio.run(probes.site_text(b.c, "ops.liuxd.dev"))
    assert "🟢 正常" in t and "cert-manager" in t and "香港服务器" in t
