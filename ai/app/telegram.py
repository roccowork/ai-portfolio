"""Telegram 机器人的通用部分：发消息、长轮询收消息、把模型输出转成 Telegram 支持的 HTML。

网站和私人助理各用一个机器人：一个机器人同一时间只能有一个程序收消息（getUpdates），分开以后互不影响。
这里的 OpsBot 是网站这边的机器人（值班台）：自愈消息上的「执行 / 忽略」按钮、只读的菜单、直接提问交给运维 Agent；
私人助理在私有仓库，复用这个文件里的 Telegram 客户端。
"""

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger("ai.telegram")


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Telegram 一条消息最多 4096 字：按行拼成不超过 limit 的几段（<b> 不会跨行，按行切不会切坏标签）。"""
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:  # 单行超长（很少见）只能硬切
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def to_html(text: str) -> str:
    """模型的回答偶尔带 Markdown：转义后把 **加粗** 换成 <b>，去掉 `代码` 的反引号。"""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return re.sub(r"`([^`]+)`", r"\1", out)


class Telegram:
    def __init__(self, token: str, chat_id: str, transport: httpx.AsyncBaseTransport | None = None):
        self.chat_id = str(chat_id)
        # URL 里带 token：httpx 的请求日志会把它打出来，所以 main.py 把 httpx 日志调到 WARNING
        self.client = httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{token}/", timeout=70, transport=transport)
        self.file_base = f"https://api.telegram.org/file/bot{token}/"

    async def call(self, method: str, **params) -> dict | list | bool:
        r = await self.client.post(method, json=params)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} 失败：{body.get('description')}")
        return body["result"]

    async def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None, keyboard: dict | None = None) -> dict:
        """buttons：消息下面的按钮；keyboard：输入框下面常驻的菜单键盘。"""
        params: dict = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        elif keyboard:
            params["reply_markup"] = keyboard
        return await self.call("sendMessage", **params)  # type: ignore[return-value]

    async def download(self, file_id: str) -> bytes:
        """下载用户发来的文件（图片），Telegram 机器人最多能下 20MB。"""
        info = await self.call("getFile", file_id=file_id)
        r = await self.client.get(self.file_base + info["file_path"])  # type: ignore[index]
        r.raise_for_status()
        return r.content

    async def close(self) -> None:
        await self.client.aclose()


MenuItem = tuple[str, str, str, Callable[[], Awaitable[str]]]  # (按钮文字, 命令, 命令说明, 生成回复的函数)


class OpsBot:
    """网站这边的机器人（值班台）：只理主人。
    按钮点击（自愈的「执行 / 忽略」）交给 on_button；输入框下面的菜单和 / 命令走 menu；其他文字交给 ask（运维 Agent）。"""

    def __init__(
        self,
        tg: Telegram,
        on_button: Callable[[str], Awaitable[str]],
        menu: list[MenuItem] | None = None,
        ask: Callable[[str], Awaitable[str]] | None = None,
        help_text: str = "",
    ):
        self.tg, self.on_button, self.offset = tg, on_button, 0
        self.menu, self.ask, self.help_text = menu or [], ask, help_text
        self.actions: dict[str, Callable[[], Awaitable[str]]] = {}
        for label, cmd, _, fn in self.menu:
            self.actions[label] = self.actions[f"/{cmd}"] = fn
        rows = [label for label, *_ in self.menu] + ["❓ 帮助"]
        self.keyboard = {
            "keyboard": [[{"text": t} for t in rows[i : i + 2]] for i in range(0, len(rows), 2)],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    async def reply(self, text: str) -> None:
        for part in split_message(text):
            await self.tg.send(part)

    async def handle(self, upd: dict) -> None:
        if cb := upd.get("callback_query"):
            if str(cb.get("message", {}).get("chat", {}).get("id")) != self.tg.chat_id:
                return
            reply = await self.on_button(cb.get("data", ""))
            await self.tg.call("answerCallbackQuery", callback_query_id=cb["id"])
            msg = cb["message"]
            await self.tg.call("editMessageText", chat_id=self.tg.chat_id, message_id=msg["message_id"], text=reply, parse_mode="HTML")
            return
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != self.tg.chat_id:
            return  # 只理主人
        text = (msg.get("text") or "").strip()
        key = text.split("@")[0]  # 群里命令会带 @机器人名
        if key in ("/start", "/help", "❓ 帮助") or not text:
            await self.tg.send(self.help_text or "我是网站的值班台机器人", keyboard=self.keyboard)
            return
        if fn := self.actions.get(key):
            try:
                await self.reply(await fn())
            except Exception as e:  # noqa: BLE001
                await self.tg.send(f"查询失败：{html.escape(str(e)[:150])}")
            return
        if not self.ask:
            await self.tg.send(self.help_text or "我只认菜单里的按钮", keyboard=self.keyboard)
            return
        await self.tg.send("🔍 运维 Agent 正在查，一般 10～30 秒……")
        try:
            await self.reply(await self.ask(text))
        except Exception as e:  # noqa: BLE001
            await self.tg.send(f"运维 Agent 出错了：{html.escape(str(e)[:150])}")

    async def run(self) -> None:
        """长轮询；出错只记日志、5 秒后重试，不影响 AI 服务。"""
        if self.menu:
            try:  # 命令菜单设置失败不影响其他功能
                cmds = [{"command": cmd, "description": desc} for _, cmd, desc, _ in self.menu] + [{"command": "help", "description": "功能说明和菜单"}]
                await self.tg.call("setMyCommands", commands=cmds)
            except Exception as e:  # noqa: BLE001
                log.warning("设置命令菜单失败：%s", e)
        while True:
            try:
                updates = await self.tg.call("getUpdates", offset=self.offset, timeout=50, allowed_updates=["message", "callback_query"])
                for upd in updates:  # type: ignore[union-attr]
                    self.offset = upd["update_id"] + 1
                    try:
                        await self.handle(upd)
                    except Exception as e:  # noqa: BLE001
                        log.warning("处理消息出错：%s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 网络、Telegram 报错都只记日志，机器人不能因此停掉
                log.warning("拉取 Telegram 消息失败，5 秒后重试：%s", e)
                await asyncio.sleep(5)
