"""阿里云账户余额（值班台「💰 今日用量」用）：BSS OpenAPI QueryAccountBalance，RPC 风格签名（HMAC-SHA1）。

百炼的 API Key 查不了余额，要另外建一个 RAM 用户的 AccessKey，只给 AliyunBSSReadOnlyAccess（只读费用）权限，
放进 Secret ai-api 的 ALIYUN_BSS_AK / ALIYUN_BSS_SK。账户在国内站（aliyun.com），费用接口默认用国内站 business.aliyuncs.com；国际站改 ALIYUN_BSS_ENDPOINT。
只读，不改任何东西。
"""

import base64
import re
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx


def _enc(s: str) -> str:
    return quote(str(s), safe="~")


def sign(params: dict, secret: str) -> str:
    query = "&".join(f"{_enc(k)}={_enc(params[k])}" for k in sorted(params))
    to_sign = f"GET&{_enc('/')}&{_enc(query)}"
    return base64.b64encode(hmac.new(f"{secret}&".encode(), to_sign.encode(), hashlib.sha1).digest()).decode()


def _num(v) -> float:
    return float(str(v or "0").replace(",", ""))


async def rpc(c: httpx.AsyncClient, ak: str, sk: str, endpoint: str, action: str, extra: dict | None = None, now: datetime | None = None) -> dict:
    params = {
        "Action": action, "Version": "2017-12-14", "Format": "JSON", "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1", "SignatureVersion": "1.0", "SignatureNonce": secrets.token_hex(16),
        "Timestamp": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), **(extra or {}),
    }
    params["Signature"] = sign(params, sk)
    r = await c.get(f"https://{endpoint}/", params=params)
    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError(f"HTTP {r.status_code}") from e
    if r.status_code != 200 or not data.get("Success"):
        raise RuntimeError(str(data.get("Code") or data.get("Message") or f"HTTP {r.status_code}")[:80])
    return data.get("Data") or {}


async def last_recharge(c: httpx.AsyncClient, ak: str, sk: str, endpoint: str, now: datetime, days: int = 365) -> dict | None:
    """账户流水（QueryAccountTransactions）里最近一次进账（充值）：{time, amount, balance（充值后的余额）}；一年内没有就 None"""
    start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    best = None
    for page in range(1, 6):  # 最多翻 5 页（每页 100 条）
        d = await rpc(c, ak, sk, endpoint, "QueryAccountTransactions",
                      {"CreateTimeStart": start, "CreateTimeEnd": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "PageNum": page, "PageSize": 100}, now)
        rows = ((d.get("AccountTransactionsList") or {}).get("AccountTransactionsList")) or []
        for t in rows:
            if str(t.get("TransactionFlow")).lower() == "income" and (best is None or str(t.get("TransactionTime")) > best["time"]):
                best = {"time": str(t.get("TransactionTime") or ""), "amount": _num(t.get("Amount")), "balance": _num(t.get("Balance"))}
        if len(rows) < 100 or page * 100 >= int(d.get("TotalCount") or 0):
            break
    return best


def _cycle(now: datetime, months_back: int = 0) -> str:
    """账期 YYYY-MM（阿里云国内站按北京时间出账）"""
    t = now.astimezone(timezone(timedelta(hours=8)))
    y, m = t.year, t.month - months_back
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


async def bill(c: httpx.AsyncClient, ak: str, sk: str, endpoint: str, cycle: str, now: datetime) -> dict:
    """某个月的账单概览（QueryBillOverview）：{cycle, total, products: [(产品名, 应付金额)] 从多到少}"""
    d = await rpc(c, ak, sk, endpoint, "QueryBillOverview", {"BillingCycle": cycle}, now)
    by: dict[str, float] = {}
    for it in ((d.get("Items") or {}).get("Item")) or []:
        name = str(it.get("ProductName") or it.get("ProductCode") or "其他")
        by[name] = by.get(name, 0.0) + _num(it.get("PretaxAmount"))
    products = sorted(((k, v) for k, v in by.items() if v > 0), key=lambda x: -x[1])
    return {"cycle": cycle, "total": sum(v for _, v in products), "products": products}


MODEL_PRODUCT = re.compile(r"百炼|灵积|大模型|dashscope|bailian|sfm", re.I)
MODEL_NAME = re.compile(r"(qwen|deepseek|kimi|moonshot|glm|baichuan|llama|text-embedding|gte|wanx|paraformer|cosyvoice|sambert)[\w.\-]*", re.I)


def model_label(it: dict) -> str:
    """账单明细里认出模型名：先在实例 ID、计费项、配置里找常见模型名，找不到就用实例 ID（截短）"""
    blob = " ".join(str(it.get(k) or "") for k in ("InstanceID", "InstanceConfig", "BillingItem", "InstanceSpec", "Item", "NickName"))
    if m := MODEL_NAME.search(blob):
        return m.group(0).lower()
    return (str(it.get("InstanceID") or it.get("BillingItem") or "其他"))[:30]


async def model_bill(c: httpx.AsyncClient, ak: str, sk: str, endpoint: str, cycle: str, now: datetime) -> list[tuple[str, float]]:
    """本月大模型（百炼 / 灵积）的账单明细（QueryInstanceBill）按模型汇总：[(模型, 应付金额)]，从多到少，0 元的不要"""
    by: dict[str, float] = {}
    for page in range(1, 6):
        d = await rpc(c, ak, sk, endpoint, "QueryInstanceBill", {"BillingCycle": cycle, "PageNum": page, "PageSize": 300}, now)
        rows = ((d.get("Items") or {}).get("Item")) or []
        for it in rows:
            if MODEL_PRODUCT.search(f"{it.get('ProductName') or ''} {it.get('ProductCode') or ''}"):
                k = model_label(it)
                by[k] = by.get(k, 0.0) + _num(it.get("PretaxAmount"))
        if len(rows) < 300 or page * 300 >= int(d.get("TotalCount") or 0):
            break
    return sorted(((k, v) for k, v in by.items() if v > 0), key=lambda x: -x[1])


async def balance(ak: str, sk: str, endpoint: str, transport: httpx.AsyncBaseTransport | None = None, now: datetime | None = None) -> dict:
    """返回 {amount（现在可用余额）, currency, recharge: {time, amount, balance} | None, spent（最近一次充值后花了多少） | None}；
    余额查不到抛 RuntimeError，流水查不到只是没有 spent"""
    now = now or datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=15, transport=transport) as c:
        d = await rpc(c, ak, sk, endpoint, "QueryAccountBalance", now=now)
        out = {"amount": _num(d.get("AvailableAmount")), "currency": d.get("Currency") or "", "recharge": None, "spent": None}
        try:
            if rec := await last_recharge(c, ak, sk, endpoint, now):
                out["recharge"], out["spent"] = rec, max(0.0, rec["balance"] - out["amount"])
        except (RuntimeError, httpx.HTTPError) as e:
            out["error"] = str(e)[:60]
        # 本月和上月的账单，按产品分（2026-11-14 用户要求「再丰富一下」）
        try:
            out["month"] = await bill(c, ak, sk, endpoint, _cycle(now), now)
            out["last_month"] = await bill(c, ak, sk, endpoint, _cycle(now, 1), now)
        except (RuntimeError, httpx.HTTPError) as e:
            out["bill_error"] = str(e)[:60]
        try:
            out["models"] = await model_bill(c, ak, sk, endpoint, _cycle(now), now)
        except (RuntimeError, httpx.HTTPError) as e:
            out["models_error"] = str(e)[:60]
    if out["recharge"] and out["spent"] is not None:
        try:
            since = datetime.fromisoformat(out["recharge"]["time"].replace("Z", "+00:00"))
            days = max(1.0, (now - since).total_seconds() / 86400)
            out["per_day"] = out["spent"] / days
            out["days_left"] = int(out["amount"] / out["per_day"]) if out["per_day"] > 0 else None
        except ValueError:
            pass
    return out
