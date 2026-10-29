"""Odoo 的 JSON-RPC 客户端：登录一次拿到 uid，之后用 execute_kw 调模型方法。权限由 Odoo 里 ai-agent 账号的角色决定。"""

import itertools

import httpx


class OdooError(Exception):
    pass


class Odoo:
    def __init__(self, url: str, db: str, user: str, password: str, transport: httpx.AsyncBaseTransport | None = None):
        self.db, self.user, self.password = db, user, password
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=30, transport=transport)
        self.uid: int | None = None
        self._ids = itertools.count(1)

    async def _call(self, service: str, method: str, *args):
        r = await self.client.post(
            "/jsonrpc", json={"jsonrpc": "2.0", "method": "call", "id": next(self._ids), "params": {"service": service, "method": method, "args": list(args)}}
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            err = body["error"]
            raise OdooError((err.get("data") or {}).get("message") or err.get("message") or "Odoo 返回错误")
        return body["result"]

    async def execute(self, model: str, method: str, args: list, kwargs: dict | None = None):
        if self.uid is None:
            self.uid = await self._call("common", "login", self.db, self.user, self.password)
            if not self.uid:
                raise OdooError("Odoo 登录失败，检查 ai-agent 账号密码")
        return await self._call("object", "execute_kw", self.db, self.uid, self.password, model, method, args, kwargs or {})

    async def search_read(self, model: str, domain: list, fields: list[str], limit: int = 10, order: str | None = None) -> list[dict]:
        kw: dict = {"fields": fields, "limit": limit}
        if order:
            kw["order"] = order
        return await self.execute(model, "search_read", [domain], kw)

    async def close(self) -> None:
        await self.client.aclose()
