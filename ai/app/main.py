"""AI 服务入口。对外只开放 /api/ai/*（由 Ingress 转发），/metrics 只给集群内的 Prometheus 采集。"""

import asyncio
import contextlib
import html
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Literal

import httpx

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel, Field

from . import agent as ag
from . import autoheal
from . import chatbi
from . import content as ct
from . import demo_shop
from . import dutybot
from . import opsdocs as od
from .evals import agent as agent_eval
from .evals import rag as rag_eval
from .evals.queue import EvalQueue
from . import digest as dg
from . import extract as ex
from . import minutes as mn
from .telegram import OpsBot, Telegram
from . import opsagent as ops
from . import opswall
from . import triage as tr
from .config import Settings
from .gateway import PUBLIC_FEATURES, AIUnavailable, ChatResult, Gateway
from .ledger import Ledger
from .odoo import Odoo, OdooError
from .rag import CaseQA, load_chunks

log = logging.getLogger("ai")

# 对话里的联系方式：手机号、邮箱、带「微信 / wx / vx」前缀的微信号
CONTACT_RE = re.compile(
    r"(?<!\d)(1[3-9]\d{9})(?!\d)|([\w.+-]+@[\w-]+\.[\w.-]+)|(?:微信号?|wechat|wx|vx|v信)\s*[:：是为]?\s*([A-Za-z][-_A-Za-z0-9]{5,19})",
    re.I,
)


def find_contact(text: str) -> str | None:
    m = CONTACT_RE.search(text)
    return next((g for g in m.groups() if g), None) if m else None
# uvicorn 只配置了自己的日志；不加这行，索引就绪、模型调用失败这些记录在 kubectl logs 里看不到
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx 的 INFO 日志会打出完整 URL，Telegram 的 URL 里带机器人 token
logging.getLogger("httpx").setLevel(logging.WARNING)

# 每完成一次，估算替人省下的分钟数（查资料、手工录入的大致耗时，页面上会注明是估算）
SAVED_MIN = {"ask": 5, "extract": 3, "triage": 3, "minutes": 20, "chatbi": 15, "agent": 10, "content": 30, "opsagent": 20, "digest": 15}
MAX_UPLOAD = 2 * 1024 * 1024
MAX_AUDIO = int(2.5 * 1024 * 1024)  # Ingress 限制请求体 3MB
SAMPLE_RE = re.compile(r"^[a-z0-9-]{1,40}$")

SPENT = Gauge("ai_spent_today_yuan", "今天已用的估算费用（元）")
BUDGET = Gauge("ai_daily_budget_yuan", "每日费用上限（元）")
INDEX_READY = Gauge("ai_index_ready", "案例库向量索引是否就绪")


def create_app(
    settings: Settings | None = None,
    gateway: Gateway | None = None,
    odoo: Odoo | None = None,
    cluster: ops.Cluster | None = None,
    telegram: Telegram | None = None,
) -> FastAPI:
    s = settings or Settings()
    ledger = gateway.ledger if gateway else Ledger(s.data_dir / "ai.db")
    gw = gateway or Gateway(s, ledger)
    qa = CaseQA(gw, load_chunks(s.cases_dir))
    docs = od.OpsDocs(gw, od.load_doc_chunks(s.opsdocs_dir))
    runbook = od.tool(docs)  # 运维 Agent 的「查运维手册」工具
    erp = odoo or (Odoo(s.odoo_url, s.odoo_db, s.odoo_user, s.odoo_password) if s.odoo_password else None)
    plans = ag.PlanStore()
    k8s = cluster or ops.Cluster(s.prom_url)

    async def ops_status() -> dict:
        """服务器状态：集群概况 + 正在触发的告警（只读）"""
        overview = await ops.run_tool(k8s, "cluster_overview", {})
        alerts = await ops.run_tool(k8s, "list_alerts", {})
        # 只算需要处理的：提示级（info / none）不算告警
        firing = [a for a in alerts if a.get("state") == "firing" and a.get("severity") not in ("info", "none")]  # type: ignore[union-attr]
        return {**overview, "alerts": firing}

    async def site_traffic() -> list[tuple[str, int, int]]:
        """最近 7 个完整的天，每天的请求数和 5xx（新加坡时间按天切）。Prometheus 只保留 7 天"""
        if not k8s.enabled:
            return []
        end = datetime.now(chatbi.SG).replace(hour=0, minute=0, second=0, microsecond=0)
        site = 'service="ops-desk-ops-desk-http@kubernetes"'
        out: dict[str, list[int]] = {}
        for i, expr in enumerate((f"sum(increase(traefik_service_requests_total{{{site}}}[1d]))", f'sum(increase(traefik_service_requests_total{{{site}, code=~"5.."}}[1d]))')):
            params = {"query": expr, "start": (end - timedelta(days=6)).timestamp(), "end": end.timestamp(), "step": 86400}
            r = await k8s.prom.get("/api/v1/query_range", params=params)
            r.raise_for_status()
            for series in r.json()["data"]["result"]:
                for ts, v in series["values"]:
                    day = (datetime.fromtimestamp(float(ts), chatbi.SG) - timedelta(days=1)).date().isoformat()  # 每个点是前 24 小时的量
                    out.setdefault(day, [0, 0])[i] = round(float(v))
        return [(d, req, err) for d, (req, err) in sorted(out.items())]

    async def recent_deploys() -> list[dict]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(s.status_url)
            r.raise_for_status()
            return r.json().get("deploys") or []

    bi = chatbi.OpsData(ledger._db, ledger._lock, s.data_dir / "chatbi" / "ops.db", site_traffic, recent_deploys)

    healer = autoheal.Healer(gw, k8s, autoheal.IncidentStore(ledger._db, ledger._lock), s.heal_daily)
    healer.extra_tools = runbook

    # 网站的 Telegram 机器人（Secret telegram-ops）：自愈审批按钮、访客留言、演示预约。私人助理用另一个机器人
    tg = telegram or (Telegram(s.tg_token, s.tg_chat_id) if s.tg_token and s.tg_chat_id else None)
    async def platform() -> str:
        """平台实况的数据和网页值班大屏是同一份：站点 nginx 提供的 /status.json（集群内地址）"""
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(s.status_url)
            r.raise_for_status()
            return dutybot.platform_text(r.json())

    evals = EvalQueue()
    EVAL_START = {"started": ("开始跑", ""), "queued": ("排队跑", "。前面还有评测在跑，跑完马上接着跑这个")}

    async def start_eval() -> str:
        """两套评测（作品集问答、运维知识库）要跑几分钟：后台跑，每跑完一套发一次结果，不卡住机器人"""
        sets = [("rag", "作品集问答评测", qa, rag_eval.CASES), ("ops-rag", "运维知识库评测", docs, rag_eval.OPS_CASES)]
        ready = [x for x in sets if x[2].ready]
        if not ready:
            return "知识库索引还没就绪，过几分钟再试"

        async def job() -> None:
            for kind, title, index, path in ready:
                try:
                    out = await rag_eval.run(index, gw, rag_eval.load_cases(path), title=title)
                    out["kind"] = kind
                    ledger.add_eval(kind, out)
                    text = rag_eval.summary_text(out)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s失败：%s", title, e)
                    text = f"{title}失败：{html.escape(str(e)[:150])}"
                if tg:
                    await tg.send(text)

        n = sum(len(rag_eval.load_cases(x[3])) for x in ready)
        state = evals.submit("RAG 评测", job)
        if state == "dup":
            return "RAG 评测已经在跑或在排队了，跑完会把结果发给你"
        verb, note = EVAL_START[state]
        return f"📏 {verb}评测：{'、'.join(x[1] for x in ready)}，共 {n} 道题，大约 2～3 分钟，花一两毛钱。每跑完一套发一次结果{note}"

    async def start_agent_eval() -> str:
        """故障演练评测：在真实集群数据上注入故障，Agent 只读，自愈题的提议只记录不执行"""
        cases = agent_eval.load_cases()

        async def job() -> None:
            try:
                out = await agent_eval.run(gw, k8s, cases, extra_tools=runbook if docs.ready else None)
                ledger.add_eval("agent", out)
                text = agent_eval.summary_text(out)
            except Exception as e:  # noqa: BLE001
                log.warning("运维 Agent 评测失败：%s", e)
                text = f"运维 Agent 评测失败：{html.escape(str(e)[:150])}"
            if tg:
                await tg.send(text)

        state = evals.submit("Agent 评测", job)
        if state == "dup":
            return "Agent 评测已经在跑或在排队了，跑完会把结果发给你"
        live = "真实集群数据" if k8s.enabled else "没有集群权限，只用注入的数据"
        verb, note = EVAL_START[state]
        return f"🧪 {verb}运维 Agent 评测：{len(cases)} 道故障演练题（{live}），大约 3～5 分钟，花几毛钱。只读，不会动集群{note}"

    menu = [
        ("🖥 服务器状态", "status", "CPU、内存、磁盘和不正常的 Pod", lambda: dutybot.status_text(k8s)),
        ("🚨 当前告警", "alerts", "正在触发的告警", lambda: dutybot.alerts_text(k8s)),
        ("🩹 自愈记录", "heal", "运维 Agent 最近处理的故障", lambda: asyncio.to_thread(dutybot.heal_text, healer.store)),
        ("📊 平台实况", "live", "最近发布、可用率、备份、证书", platform),
        ("🗓 定时任务", "jobs", "服务器上的备份、周报等任务", lambda: dutybot.jobs_text(k8s)),
        ("💬 最近留言", "leads", "网站访客的留言", lambda: asyncio.to_thread(dutybot.leads_text, ledger)),
        ("📏 RAG 评测", "eval", "跑一遍作品集问答和运维知识库的评测集", start_eval),
        ("🧪 Agent 评测", "agenteval", "故障演练：注入故障，看运维 Agent 的诊断和处置", start_agent_eval),
        ("💰 今日用量", "usage", "AI 演示今天的调用和费用", lambda: asyncio.to_thread(dutybot.usage_text, ledger, s.daily_budget, s.public_budget)),
    ]
    bot = OpsBot(tg, healer.on_button, menu, dutybot.make_ask(gw, k8s, runbook), dutybot.HELP) if tg else None
    if tg:
        healer.notify = tg.send  # type: ignore[assignment]

    async def build_index() -> None:
        # 启动后在后台建索引，失败（比如还没配置密钥）每 10 分钟重试一次，不影响服务启动
        while not qa.ready:
            try:
                await qa.build()
                INDEX_READY.set(1)
                log.info("知识库索引就绪：%d 段", len(qa.chunks))
            except Exception as e:  # noqa: BLE001
                log.warning("建索引失败，10 分钟后重试：%s", e)
                await asyncio.sleep(600)
        # 运维手册：建不好不影响作品集问答和其他功能，运维 Agent 查手册时会说「还没加载」
        while docs.chunks and not docs.ready:
            try:
                await docs.build()
                log.info("运维手册索引就绪：%d 段", len(docs.chunks))
            except Exception as e:  # noqa: BLE001
                log.warning("运维手册建索引失败，10 分钟后重试：%s", e)
                await asyncio.sleep(600)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(build_index())
        poll = asyncio.create_task(bot.run()) if bot else None
        yield
        task.cancel()
        if poll:
            poll.cancel()
        if tg:
            await tg.close()
        await gw.close()
        if erp:
            await erp.close()
        await k8s.close()

    app = FastAPI(title="ops-desk AI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.qa = qa

    def client_ip(req: Request) -> str:
        # Traefik 用 externalTrafficPolicy: Local，X-Real-Ip 就是访客的真实地址
        return req.headers.get("x-real-ip") or (req.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (req.client.host if req.client else "-")

    def guard(req: Request, feature: str) -> None:
        """每个访客每天：单个功能的次数，加上所有功能合计的次数（换着功能刷也有上限）"""
        ip = client_ip(req)
        if not ledger.take_quota(ip, feature, s.quota[feature]):
            raise HTTPException(429, f"今天的体验次数已用完（每天 {s.quota[feature]} 次），明天再来")
        if not ledger.take_quota(ip, "total", s.quota["total"]):
            raise HTTPException(429, f"今天的体验次数已用完（所有功能合计每天 {s.quota['total']} 次），明天再来")

    def meta(r: ChatResult) -> dict:
        return {"model": r.model, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost": round(r.cost, 5), "seconds": round(r.seconds, 2)}

    def meta_sum(rs: list[ChatResult]) -> dict:
        """一个功能调用了多次模型时（转写 + 整理、生成 SQL + 解读），合计展示。"""
        models = list(dict.fromkeys(r.model for r in rs))
        return {
            "model": " + ".join(models),
            "tokens_in": sum(r.tokens_in for r in rs),
            "tokens_out": sum(r.tokens_out for r in rs),
            "cost": round(sum(r.cost for r in rs), 5),
            "seconds": round(sum(r.seconds for r in rs), 2),
        }

    @app.exception_handler(AIUnavailable)
    async def unavailable(_: Request, e: AIUnavailable):
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.get("/api/ai/health")
    def health():
        return {"status": "ok", "enabled": s.enabled, "index_ready": qa.ready, "opsdocs_ready": docs.ready, "opsdocs_chunks": len(docs.chunks)}

    @app.get("/api/ai/stats")
    def stats():
        out = ledger.summary()
        out["budget"] = {"daily": s.daily_budget, "left": round(max(0.0, s.daily_budget - ledger.spent_today()), 3),
                         "public": s.public_budget, "public_left": round(max(0.0, s.public_budget - ledger.spent_today(PUBLIC_FEATURES)), 3)}
        out["enabled"] = s.enabled
        return out

    class HistMsg(BaseModel):
        role: Literal["user", "assistant"]
        content: str = Field(max_length=1500)

    async def save_lead(ip: str, name: str, contact: str, company: str, need: str, chat: str) -> None:
        """线索存进 ai.db，再推到我的 Telegram；推送失败不影响访客，记录已经在数据库里"""
        lid = ledger.add_lead(ip, name, contact, company, need, chat)
        if not tg:
            return
        e = html.escape
        text = (
            f"<b>💬 作品集新留言 #{lid}</b>\n"
            f"称呼：{e(name) or '-'}\n联系方式：<code>{e(contact)}</code>\n公司：{e(company) or '-'}\n留言：{e(need)}"
            + (f"\n\n<b>和 AI 助手的对话</b>\n{e(chat[-1500:])}" if chat else "")
        )
        try:
            await tg.send(text)
        except Exception as ex_:  # noqa: BLE001
            log.warning("新线索推送 Telegram 失败：%s", ex_)

    class AskIn(BaseModel):
        question: str = Field(min_length=2, max_length=300)
        history: list[HistMsg] = Field(default_factory=list, max_length=8)

    @app.post("/api/ai/ask")
    async def ask(body: AskIn, req: Request):
        if not qa.ready:
            gw.check_budget("ask")  # 没配置密钥时给出明确原因
            raise HTTPException(503, "知识库还在准备中，请过几分钟再试")
        guard(req, "ask")
        question, history = body.question.strip(), [h.model_dump() for h in body.history]
        note = ""
        # 访客常常直接在对话框里报手机号或微信，而不去点「给我留言」：这里也当作线索推送
        if contact := find_contact(question):
            chat = "\n".join(f"{'访客' if h['role'] == 'user' else 'AI'}：{h['content']}" for h in history)
            need = next((h["content"] for h in reversed(history) if h["role"] == "user"), "") or question
            await save_lead(client_ip(req), "", contact, "", need, f"{chat}\n访客：{question}".strip())
            note = f"访客刚刚在对话里留下了联系方式（{contact}），系统已经转给作者。请确认已收到，告诉访客作者本人会尽快回复，并可以继续回答问题。"
        result, refs, cited = await qa.ask(question, history, note)
        ledger.add_saved("ask", SAVED_MIN["ask"])
        sources = [
            {"n": i + 1, "id": c.case_id, "title": c.title, "section": c.section, "cited": (i + 1) in cited} for i, c in enumerate(refs)
        ]
        return {"answer": result.text, "sources": sources, "meta": meta(result)}

    class LeadIn(BaseModel):
        name: str = Field(default="", max_length=40)
        contact: str = Field(min_length=3, max_length=80)
        company: str = Field(default="", max_length=80)
        need: str = Field(min_length=2, max_length=1000)
        chat: str = Field(default="", max_length=4000)

    @app.post("/api/ai/lead")
    async def lead(body: LeadIn, req: Request):
        """作品集 AI 助手里的留言：存进 ai.db，再推到我的 Telegram"""
        guard(req, "lead")
        await save_lead(client_ip(req), body.name.strip(), body.contact.strip(), body.company.strip(), body.need.strip(), body.chat.strip())
        return {"ok": True}

    # ── 客服 Agent 演示：Dify 里的 Agent 把这两个接口当工具（清源净水，虚构公司） ──
    def demo_auth(req: Request) -> None:
        if not s.demo_tool_token:
            raise HTTPException(503, "演示工具未启用")
        if not secrets.compare_digest(req.headers.get("x-demo-token", ""), s.demo_tool_token):
            raise HTTPException(401, "令牌不对")

    @app.get("/api/ai/demo/order")
    def demo_order(order_id: str, req: Request):
        """查订单：返回商品、状态、物流和安装安排"""
        demo_auth(req)
        return demo_shop.find_order(order_id[:20])

    class BookingIn(BaseModel):
        name: str = Field(min_length=1, max_length=20)
        phone: str = Field(min_length=5, max_length=20)
        address: str = Field(min_length=2, max_length=100)
        time: str = Field(min_length=2, max_length=40)
        service: str = Field(default="上门安装", max_length=40)

    @app.post("/api/ai/demo/booking")
    async def demo_booking(body: BookingIn, req: Request):
        """预约上门：记一条并推送到我的 Telegram（演示时当场能看到）"""
        demo_auth(req)
        if not ledger.take_quota("demo-agent", "booking", s.demo_booking_daily):
            raise HTTPException(429, "今天的演示预约次数已用完，明天再试")
        bid = f"YY{secrets.randbelow(900000) + 100000}"
        if tg:
            e = html.escape
            try:
                await tg.send(
                    f"<b>📅 客服 Agent 演示：新预约 {bid}</b>（清源净水，虚构公司）\n"
                    f"{e(body.service)}｜{e(body.time)}\n{e(body.name)}，{e(body.phone)}\n{e(body.address)}"
                )
            except Exception as ex_:  # noqa: BLE001
                log.warning("演示预约推送失败：%s", ex_)
        return {"ok": True, "booking_id": bid, "message": f"已预约{body.service}，时间 {body.time}，师傅出发前会电话联系"}

    @app.post("/api/ai/extract")
    async def extract(req: Request, file: UploadFile | None = File(None), sample: str | None = Form(None)):
        if sample:
            if not SAMPLE_RE.match(sample):
                raise HTTPException(400, "示例不存在")
            path = s.samples_dir / f"{sample}.png"
            if not path.is_file():
                raise HTTPException(400, "示例不存在")
            data = path.read_bytes()
        elif file:
            if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
                raise HTTPException(400, "只支持 JPG、PNG、WebP 图片")
            data = await file.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(400, "图片不能超过 2MB")
        else:
            raise HTTPException(400, "请上传图片或选择一个示例")
        gw.check_budget("extract")
        guard(req, "extract")
        try:
            result, doc, issues = await ex.extract(gw, data)
        except (ValueError, OSError) as e:
            raise HTTPException(422, f"无法识别：{e}") from e
        ledger.add_saved("extract", SAVED_MIN["extract"])
        return {"doc": doc, "issues": issues, "meta": meta(result)}

    class TriageIn(BaseModel):
        text: str = Field(min_length=5, max_length=2000)

    @app.post("/api/ai/triage")
    async def triage(body: TriageIn, req: Request):
        gw.check_budget("triage")
        guard(req, "triage")
        try:
            result, doc, reasons = await tr.triage(gw, body.text.strip())
        except ValueError as e:
            raise HTTPException(422, f"无法分析：{e}") from e
        ledger.add_saved("triage", SAVED_MIN["triage"])
        return {"ticket": doc, "reasons": reasons, "meta": meta(result)}

    @app.post("/api/ai/minutes")
    async def minutes(
        req: Request, file: UploadFile | None = File(None), sample: str | None = Form(None), text: str | None = Form(None)
    ):
        if text is not None:
            transcript = text.strip()
            if not 10 <= len(transcript) <= 6000:
                raise HTTPException(400, "文字要在 10～6000 字之间")
            audio, ctype = None, ""
        elif sample:
            path = s.samples_dir / f"{sample}.mp3"
            if not SAMPLE_RE.match(sample) or not path.is_file():
                raise HTTPException(400, "示例不存在")
            audio, ctype = path.read_bytes(), "audio/mpeg"
        elif file:
            ctype = (file.content_type or "").split(";")[0]
            if ctype not in mn.AUDIO_TYPES:
                raise HTTPException(400, "只支持 MP3、WAV、M4A、OGG、WebM 录音")
            audio = await file.read(MAX_AUDIO + 1)
            if len(audio) > MAX_AUDIO:
                raise HTTPException(400, "录音不能超过 2.5MB（约 2 分钟）")
        else:
            raise HTTPException(400, "请上传录音、录一段或粘贴文字")
        gw.check_budget("minutes")
        guard(req, "minutes")
        calls: list[ChatResult] = []
        if audio is not None:
            transcript, asr = await mn.transcribe(gw, audio, ctype)
            if asr:
                calls.append(asr)
            if len(transcript.strip()) < 10:
                raise HTTPException(422, "没有识别出足够的语音内容")
        try:
            result, doc, issues = await mn.summarize(gw, transcript)
        except ValueError as e:
            raise HTTPException(422, f"无法整理：{e}") from e
        calls.append(result)
        ledger.add_saved("minutes", SAVED_MIN["minutes"])
        return {"transcript": transcript, "minutes": doc, "issues": issues, "cached": audio is not None and len(calls) == 1, "meta": meta_sum(calls)}

    class BiIn(BaseModel):
        question: str = Field(min_length=2, max_length=200)

    @app.post("/api/ai/chatbi")
    async def chatbi_ask(body: BiIn, req: Request):
        gw.check_budget("chatbi")
        guard(req, "chatbi")
        try:
            out = await chatbi.ask(gw, await bi.ensure(), body.question.strip())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        ledger.add_saved("chatbi", SAVED_MIN["chatbi"])
        calls = out.pop("calls")
        return {**out, "meta": meta_sum(calls)}

    class ContentIn(BaseModel):
        source: str = Field(min_length=20, max_length=3000)
        platforms: list[str] = Field(min_length=1, max_length=4)

    @app.post("/api/ai/content")
    async def content(body: ContentIn, req: Request):
        platforms = list(dict.fromkeys(p for p in body.platforms if p in ct.PLATFORMS))
        if not platforms:
            raise HTTPException(400, "请至少选一个平台")
        gw.check_budget("content")
        guard(req, "content")
        try:
            versions, issues, fixed, calls = await ct.generate(gw, body.source.strip(), platforms)
        except ValueError as e:
            raise HTTPException(422, f"生成失败：{e}") from e
        ledger.add_saved("content", SAVED_MIN["content"])
        return {
            "versions": [{"code": c, "name": ct.PLATFORMS[c]["name"], **versions.get(c, {}), "issues": issues.get(c, [])} for c in platforms],
            "fixed": fixed,
            "meta": meta_sum(calls),
        }

    class DigestIn(BaseModel):
        items: list[str] = Field(min_length=2, max_length=30)

    @app.post("/api/ai/digest")
    async def digest(body: DigestIn, req: Request):
        items = [t.strip() for t in body.items if t.strip()][:30]
        if len(items) < 2 or sum(len(t) for t in items) > 8000:
            raise HTTPException(400, "请提供 2～30 条消息，总共不超过 8000 字")
        gw.check_budget("digest")
        guard(req, "digest")
        try:
            result, doc, issues = await dg.brief(gw, items)
        except ValueError as e:
            raise HTTPException(422, f"整理失败：{e}") from e
        ledger.add_saved("digest", SAVED_MIN["digest"])
        return {"brief": doc, "issues": issues, "meta": meta(result)}

    class OpsIn(BaseModel):
        question: str = Field(min_length=2, max_length=500)

    # Alertmanager 的 webhook（deploy/platform/kube-prometheus-stack.yaml 的 autoheal 接收器）。
    # 不在 /api/ai 下面：Ingress 只把 /api/ai 转给 ai-api，这个地址只有集群内部能访问
    @app.post("/internal/alertmanager")
    async def alert_hook(req: Request):
        if not k8s.enabled:
            return {"started": 0}
        return {"started": healer.on_webhook(await req.json())}

    # 最近一次评测的完整结果（每道题的问题、回答、检索到的资料、裁判理由），公开：题目和回答都是关于作品集的
    @app.get("/api/ai/eval/latest")
    def eval_latest(kind: str = "rag"):
        out = ledger.latest_eval(kind)
        if not out:
            raise HTTPException(404, "还没有跑过评测")
        return {**out, "history": ledger.eval_history(kind)}

    @app.get("/api/ai/heal/recent")
    def heal_recent():
        return {"items": autoheal.public_records(healer.store)}

    async def status_json() -> dict:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(s.status_url)
            r.raise_for_status()
            return r.json()

    def wall_extras() -> dict:
        """大屏底部：三套评测最近一次的分数、自愈统计（都在账本 ai.db 里）"""
        evals = {}
        for kind in ("rag", "ops-rag", "agent"):
            run = ledger.latest_eval(kind)
            sm = (run or {}).get("summary") or {}
            evals[kind] = {k: sm.get(k) for k in ("ts", "cases", "hit_at_1", "judge_correct", "pass_rate", "refusal_acc")} | {"ts": (run or {}).get("ts")} if run else None
        with ledger._lock:
            total, fixed = ledger._db.execute("SELECT COUNT(*), COALESCE(SUM(status = 'fixed'), 0) FROM incidents").fetchone()
        return {"evals": evals, "heal": {"total": total, "fixed": fixed}}

    wall = opswall.OpsWall(k8s, status_json, wall_extras)

    @app.get("/api/ai/ops/overview")
    async def ops_overview():
        return await wall.overview()

    @app.get("/api/ai/ops/trend")
    async def ops_trend():
        return await wall.trend()

    @app.post("/api/ai/opsagent/stream")
    async def ops_agent_stream(body: OpsIn, req: Request):
        """和 /api/ai/opsagent 一样，但每一步实时推给网页（Server-Sent Events）：思考、调用工具、程序核对、结论"""
        if not k8s.enabled:
            raise HTTPException(503, "运维 Agent 只能在集群里运行（没有 ServiceAccount）")
        gw.check_budget("opsagent")
        guard(req, "opsagent")
        queue: asyncio.Queue = asyncio.Queue()

        async def run() -> None:
            try:
                answer, steps, calls = await ops.investigate(gw, k8s, body.question.strip(), public=True, extra=runbook, on_event=queue.put)
                ledger.add_saved("opsagent", SAVED_MIN["opsagent"])
                await queue.put({"type": "answer", "answer": answer, "steps": steps, "meta": meta_sum(calls)})
            except AIUnavailable as e:
                await queue.put({"type": "error", "detail": str(e)})
            except Exception as e:  # noqa: BLE001
                log.warning("运维 Agent 出错：%s", e)
                await queue.put({"type": "error", "detail": "排查出错了，请稍后再试"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())

        async def events():
            try:
                while (item := await queue.get()) is not None:
                    yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
            finally:
                if not task.done():
                    task.cancel()  # 访客关掉页面就不再继续花钱

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/ai/opsagent")
    async def ops_agent(body: OpsIn, req: Request):
        if not k8s.enabled:
            raise HTTPException(503, "运维 Agent 只能在集群里运行（没有 ServiceAccount）")
        gw.check_budget("opsagent")
        guard(req, "opsagent")
        answer, steps, calls = await ops.investigate(gw, k8s, body.question.strip(), public=True, extra=runbook)
        ledger.add_saved("opsagent", SAVED_MIN["opsagent"])
        return {"answer": answer, "steps": steps, "meta": meta_sum(calls)}

    class AgentIn(BaseModel):
        instruction: str = Field(min_length=2, max_length=500)

    def need_odoo() -> Odoo:
        if not erp:
            raise HTTPException(503, "办公 Agent 还没有连接 Odoo（未配置 ODOO_PASSWORD）")
        return erp

    @app.post("/api/ai/agent")
    async def agent_ask(body: AgentIn, req: Request):
        odoo_client = need_odoo()
        gw.check_budget("agent")
        guard(req, "agent")
        try:
            answer, sess, calls = await ag.ask(gw, odoo_client, body.instruction.strip())
        except OdooError as e:
            raise HTTPException(502, f"Odoo 暂时不可用：{e}") from e
        ledger.add_saved("agent", SAVED_MIN["agent"])
        plan_id = plans.put(ledger.visitor_id(client_ip(req)), sess.actions) if sess.actions else None
        actions = [{"title": a["title"], "details": a["details"]} for a in sess.actions]
        return {"answer": answer, "steps": sess.steps, "plan_id": plan_id, "actions": actions, "meta": meta_sum(calls)}

    class ConfirmIn(BaseModel):
        plan_id: str = Field(min_length=8, max_length=40)

    @app.post("/api/ai/agent/confirm")
    async def agent_confirm(body: ConfirmIn, req: Request):
        odoo_client = need_odoo()
        actions = plans.take(body.plan_id, ledger.visitor_id(client_ip(req)))
        if actions is None:
            raise HTTPException(404, "这组操作已过期或已执行，请重新提问")
        return {"results": await ag.execute(odoo_client, actions, s.odoo_public_url)}

    @app.get("/metrics")
    def metrics():
        SPENT.set(ledger.spent_today())
        BUDGET.set(s.daily_budget)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app

