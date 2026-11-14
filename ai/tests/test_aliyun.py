"""阿里云账户余额：签名和官方文档示例一致；余额 + 最近一次充值后花了多少。"""

import asyncio
from datetime import datetime, timezone

import httpx

from app import aliyun, dutybot


def test_signature_matches_official_example():
    # 阿里云 RPC 签名文档里的示例
    p = {"AccessKeyId": "testid", "Action": "DescribeRegions", "Format": "XML", "SignatureMethod": "HMAC-SHA1",
         "SignatureNonce": "3ee8c1b8-83d3-44af-a94f-4e0ad82fd6cf", "SignatureVersion": "1.0", "Timestamp": "2016-02-23T12:46:24Z", "Version": "2014-05-26"}
    assert aliyun.sign(p, "testsecret") == "OLeaidS1JvxuMvnyHOwuJ+uX5qY="


def fake(tx_ok=True):
    def handler(req: httpx.Request) -> httpx.Response:
        action = req.url.params["Action"]
        assert req.url.params["Signature"] and req.url.params["AccessKeyId"] == "ak"
        if action == "QueryAccountBalance":
            return httpx.Response(200, json={"Success": True, "Data": {"AvailableAmount": "16.88", "Currency": "USD"}})
        if not tx_ok:
            return httpx.Response(403, json={"Success": False, "Code": "NotAuthorized"})
        rows = [{"TransactionFlow": "Expense", "TransactionTime": "2026-11-10T01:00:00Z", "Amount": "0.50", "Balance": "19.50"},
                {"TransactionFlow": "Income", "TransactionTime": "2026-11-02T03:00:00Z", "Amount": "20.00", "Balance": "20.00"},
                {"TransactionFlow": "Income", "TransactionTime": "2026-10-01T03:00:00Z", "Amount": "5.00", "Balance": "5.00"}]
        return httpx.Response(200, json={"Success": True, "Data": {"TotalCount": 3, "AccountTransactionsList": {"AccountTransactionsList": rows}}})

    return httpx.MockTransport(handler)


def test_balance_and_spent_since_last_recharge():
    b = asyncio.run(aliyun.balance("ak", "sk", "x.example", fake(), datetime(2026, 11, 14, tzinfo=timezone.utc)))
    assert b["amount"] == 16.88 and b["recharge"]["time"].startswith("2026-11-02") and round(b["spent"], 2) == 3.12
    line = dutybot.balance_line(b)
    assert "还剩 $16.88" in line and "2026-11-02 充值 $20.00" in line and "一共花了 $3.12" in line


def test_transactions_denied_still_shows_balance():
    b = asyncio.run(aliyun.balance("ak", "sk", "x.example", fake(tx_ok=False)))
    line = dutybot.balance_line(b)
    assert "还剩 $16.88" in line and "查不到（NotAuthorized）" in line
    assert "查不到" in dutybot.balance_line(None, "InvalidAccessKeyId")
