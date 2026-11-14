"""阿里云账户余额（值班台「💰 今日用量」用）：BSS OpenAPI QueryAccountBalance，RPC 风格签名（HMAC-SHA1）。

百炼的 API Key 查不了余额，要另外建一个 RAM 用户的 AccessKey，只给 AliyunBSSReadOnlyAccess（只读费用）权限，
放进 Secret ai-api 的 ALIYUN_BSS_AK / ALIYUN_BSS_SK。百炼用的是国际站（dashscope-intl），费用接口默认也用国际站。
只读，不改任何东西。
"""

import base64
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
    return out
