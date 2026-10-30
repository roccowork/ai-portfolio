"""轻量 AI 网关：所有模型调用都经过这里。

负责：按用途选模型、失败重试并降级到下一个模型、按 token 估算费用并记账、
每日费用上限、Prometheus 指标。业务代码只关心「问什么」，不关心「问谁、花多少」。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
import numpy as np
from prometheus_client import Counter, Histogram

from .config import Settings
from .ledger import Ledger

# 网站访客能触发的功能，合计受 public_budget 限制。主人自己用的（值班台 dutybot、自愈 autoheal、评测 eval、
# 建索引 index、运维手册检索 opsdocs、私人助理 assistant）只受全站上限限制
PUBLIC_FEATURES = frozenset({"ask", "extract", "triage", "minutes", "chatbi", "agent", "content", "opsagent", "digest"})

log = logging.getLogger("ai")

CALLS = Counter("ai_calls_total", "模型调用次数", ["feature", "model", "outcome"])
TOKENS = Counter("ai_tokens_total", "消耗的 token", ["feature", "model", "direction"])
COST = Counter("ai_cost_yuan_total", "估算费用（元）", ["feature", "model"])
LATENCY = Histogram("ai_call_seconds", "单次模型调用耗时", ["feature", "model"], buckets=(0.5, 1, 2, 4, 8, 15, 30, 60))
BLOCKED = Counter("ai_blocked_total", "被拦下的请求", ["feature", "reason"])


def describe(e: Exception) -> str:
    """把模型服务的报错压成一行，方便 kubectl logs 直接看出原因（欠费、无额度、模型名错等）。"""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} {e.response.text[:300]}"
    return f"{type(e).__name__}: {e}"


class AIUnavailable(Exception):
    """没有配置密钥、超出预算或所有模型都失败。message 直接展示给用户。"""


@dataclass
class ChatResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost: float
    seconds: float
    # 模型要求调用的工具（Agent 用），原样保留，格式同 OpenAI 的 tool_calls
    tool_calls: list = field(default_factory=list)


class Gateway:
    def __init__(self, settings: Settings, ledger: Ledger, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.ledger = ledger
        self.client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=settings.timeout,
            transport=transport,
        )

    def price(self, model: str, tokens_in: int, tokens_out: int) -> float:
        pin, pout = self.s.prices.get(model, (0.002, 0.006))  # 没登记的模型按偏高的价格估算
        return tokens_in / 1000 * pin + tokens_out / 1000 * pout

    def check_budget(self, feature: str) -> None:
        if not self.s.enabled:
            BLOCKED.labels(feature, "disabled").inc()
            raise AIUnavailable("AI 功能还没有启用（未配置模型密钥）")
        if self.ledger.spent_today() >= self.s.daily_budget:
            BLOCKED.labels(feature, "budget").inc()
            raise AIUnavailable("今天的 AI 费用额度已用完，明天会自动恢复")
        if feature in PUBLIC_FEATURES and self.ledger.spent_today(PUBLIC_FEATURES) >= self.s.public_budget:
            BLOCKED.labels(feature, "public_budget").inc()
            raise AIUnavailable("今天给访客体验的 AI 额度已用完，明天会自动恢复")

    async def _post(self, path: str, payload: dict) -> dict:
        # 网络抖动、限流（429）和服务端错误重试一次；其他 4xx（参数错、密钥错）直接失败
        for attempt in range(2):
            try:
                r = await self.client.post(path, json=payload)
            except httpx.TransportError:
                if attempt:
                    raise
                await asyncio.sleep(1)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt:
                    r.raise_for_status()
                await asyncio.sleep(1.5)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def chat(
        self, feature: str, messages: list[dict], kind: str = "chat", json_mode: bool = False, max_tokens: int = 800, tools: list[dict] | None = None
    ) -> ChatResult:
        """按 kind（chat / vision）依次尝试模型，第一个成功的返回。"""
        self.check_budget(feature)
        models = self.s.vision_models if kind == "vision" else self.s.chat_models
        last: Exception | None = None
        for model in models:
            payload: dict = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2, **self.s.payload_extra}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            if tools:
                payload["tools"] = tools
            t0 = time.perf_counter()
            try:
                data = await self._post("chat/completions", payload)
            except (httpx.HTTPError, ValueError) as e:
                CALLS.labels(feature, model, "error").inc()
                log.warning("%s 调用 %s 失败：%s", feature, model, describe(e))
                last = e
                continue
            seconds = time.perf_counter() - t0
            usage = data.get("usage") or {}
            tin, tout = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            cost = self.price(model, tin, tout)
            CALLS.labels(feature, model, "ok").inc()
            TOKENS.labels(feature, model, "in").inc(tin)
            TOKENS.labels(feature, model, "out").inc(tout)
            COST.labels(feature, model).inc(cost)
            LATENCY.labels(feature, model).observe(seconds)
            self.ledger.record(feature, model, tin, tout, cost)
            msg = data["choices"][0]["message"]
            return ChatResult(
                text=msg.get("content") or "", model=model, tokens_in=tin, tokens_out=tout, cost=cost, seconds=seconds, tool_calls=msg.get("tool_calls") or []
            )
        raise AIUnavailable("模型服务暂时不可用，请稍后再试") from last

    async def transcribe(self, feature: str, audio_url: str) -> ChatResult:
        """语音转文字。百炼的 qwen3-asr 走同一个 OpenAI 兼容接口，按音频秒数计费（usage.seconds）。"""
        self.check_budget(feature)
        model = self.s.asr_model
        payload = {"model": model, "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio_url}}]}]}
        t0 = time.perf_counter()
        try:
            data = await self._post("chat/completions", payload)
        except (httpx.HTTPError, ValueError) as e:
            CALLS.labels(feature, model, "error").inc()
            log.warning("%s 调用 %s 失败：%s", feature, model, describe(e))
            raise AIUnavailable("语音识别服务暂时不可用，可以先用示例文字体验") from e
        seconds = time.perf_counter() - t0
        usage = data.get("usage") or {}
        cost = float(usage.get("seconds", 0)) * self.s.asr_price_per_sec
        tin, tout = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        CALLS.labels(feature, model, "ok").inc()
        COST.labels(feature, model).inc(cost)
        LATENCY.labels(feature, model).observe(seconds)
        self.ledger.record(feature, model, tin, tout, cost)
        text = data["choices"][0]["message"].get("content") or ""
        return ChatResult(text=text, model=model, tokens_in=tin, tokens_out=tout, cost=cost, seconds=seconds)

    async def embed(self, feature: str, texts: list[str]) -> np.ndarray:
        """文本向量。百炼每次最多 10 条，这里自动分批。返回已归一化的矩阵，余弦相似度直接用点积。"""
        self.check_budget(feature)
        model = self.s.embed_model
        vecs: list[list[float]] = []
        for i in range(0, len(texts), 10):
            batch = texts[i : i + 10]
            t0 = time.perf_counter()
            try:
                data = await self._post("embeddings", {"model": model, "input": batch, "encoding_format": "float"})
            except (httpx.HTTPError, ValueError) as e:
                CALLS.labels(feature, model, "error").inc()
                log.warning("%s 调用 %s 失败：%s", feature, model, describe(e))
                raise AIUnavailable("向量服务暂时不可用，请稍后再试") from e
            LATENCY.labels(feature, model).observe(time.perf_counter() - t0)
            tin = int((data.get("usage") or {}).get("total_tokens", 0))
            cost = self.price(model, tin, 0)
            CALLS.labels(feature, model, "ok").inc()
            TOKENS.labels(feature, model, "in").inc(tin)
            COST.labels(feature, model).inc(cost)
            self.ledger.record(feature, model, tin, 0, cost)
            vecs.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
        m = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.where(norms == 0, 1, norms)

    async def close(self) -> None:
        await self.client.aclose()
