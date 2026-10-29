"""办公 Agent：用一句话操作 Odoo。模型通过函数调用使用工具：查询类工具立即执行，写入类工具只生成「待确认操作」，
人点确认后才真正写进 Odoo。所有参数由程序再校验一遍（客户、产品必须是本次查询里出现过的，日期、数量要合理）。"""

import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .gateway import ChatResult, Gateway
from .ledger import TZ
from .odoo import Odoo, OdooError

MAX_ROUNDS = 6
PLAN_TTL = 1800

ACTIVITY_ICONS = {"call": "fa-phone", "meeting": "fa-users", "email": "fa-envelope", "todo": "fa-check"}
ACTIVITY_NAMES = {"call": "电话", "meeting": "会议", "email": "邮件", "todo": "待办"}


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}}}


FOLLOW_UP = {
    "type": "object",
    "description": "跟进活动，可选",
    "properties": {
        "type": {"type": "string", "enum": list(ACTIVITY_ICONS)},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "summary": {"type": "string"},
    },
    "required": ["type", "date"],
}

TOOLS = [
    _fn("find_customers", "按名称关键字查客户（公司或联系人），返回 id、名称、城市、邮箱", {"query": {"type": "string"}}, ["query"]),
    _fn("find_products", "按名称或编码关键字查可销售的产品，返回 id、名称、编码、标价", {"query": {"type": "string"}}, ["query"]),
    _fn("customer_overview", "查看一个客户的概况：最近订单、累计成交、进行中的商机", {"customer_id": {"type": "integer"}}, ["customer_id"]),
    _fn(
        "list_opportunities",
        "列出进行中的商机，按预计收入从高到低；可按客户筛选",
        {"customer_id": {"type": "integer", "description": "可选"}, "limit": {"type": "integer", "description": "默认 10"}},
        [],
    ),
    _fn(
        "create_opportunity",
        "【需要人确认】为客户新建商机，可同时安排一个跟进活动",
        {
            "customer_id": {"type": "integer"},
            "title": {"type": "string", "description": "商机标题，简短说明需求"},
            "expected_revenue": {"type": "number", "description": "预计收入（元），不知道就不填"},
            "description": {"type": "string"},
            "follow_up": FOLLOW_UP,
        },
        ["customer_id", "title"],
    ),
    _fn(
        "schedule_activity",
        "【需要人确认】给已有的商机安排跟进活动（电话、会议、邮件、待办）",
        {"opportunity_id": {"type": "integer"}, **FOLLOW_UP["properties"]},
        ["opportunity_id", "type", "date"],
    ),
    _fn(
        "create_quotation",
        "【需要人确认】为客户生成报价单草稿，价格按产品标价，由人审核后再发给客户",
        {
            "customer_id": {"type": "integer"},
            "lines": {
                "type": "array",
                "items": {"type": "object", "properties": {"product_id": {"type": "integer"}, "quantity": {"type": "number"}}, "required": ["product_id", "quantity"]},
            },
            "note": {"type": "string", "description": "给客户的备注，可选"},
        },
        ["customer_id", "lines"],
    ),
]
WRITE_TOOLS = {"create_opportunity", "schedule_activity", "create_quotation"}

SYSTEM = """你是公司的办公助理，可以查询和操作公司的 Odoo 业务系统（CRM、销售）。今天是 {today}（{weekday}）。
规则：
1. 先用查询工具找到客户、产品、商机的 id，只能使用查询结果里出现过的 id，不要编造
2. 新建商机、安排跟进、生成报价单这三类写操作不会立即执行，会列给用户确认；调用后告诉用户「请确认」即可
3. 系统里的客户名和产品名都是英文（如 Office Chair、Large Desk）。查不到时先换更短的关键词或英文再查一次；还是找不到、或者有多个可能时，说明情况并让用户说得更具体，不要猜
4. 「下周二」这类相对日期按今天换算成 YYYY-MM-DD
5. 用中文回答，简短，列出关键数字"""


class PlanError(ValueError):
    pass


@dataclass
class Session:
    """一次对话里查到过的对象，写操作的参数必须在这里面（防止模型编造 id）。"""

    customers: dict[int, str] = field(default_factory=dict)
    products: dict[int, dict] = field(default_factory=dict)
    opportunities: dict[int, str] = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)


def _date(v: str | None, today: date) -> str:
    try:
        d = datetime.strptime(str(v), "%Y-%m-%d").date()
    except ValueError as e:
        raise PlanError(f"日期格式不对：{v}") from e
    if not today <= d <= today + timedelta(days=365):
        raise PlanError(f"日期要在今天到一年内：{v}")
    return d.isoformat()


def _follow_up(f: dict | None, today: date) -> dict | None:
    if not f:
        return None
    if f.get("type") not in ACTIVITY_ICONS:
        raise PlanError("跟进类型只能是电话、会议、邮件、待办")
    return {"type": f["type"], "date": _date(f.get("date"), today), "summary": str(f.get("summary") or "")[:100]}


def propose(name: str, args: dict, sess: Session, today: date) -> dict:
    """把模型要求的写操作校验后变成一条待确认操作（含给人看的说明）。校验失败抛 PlanError，错误会反馈给模型。"""
    if name == "create_opportunity":
        cid = int(args.get("customer_id") or 0)
        if cid not in sess.customers:
            raise PlanError("customer_id 不是查询结果里的客户，请先用 find_customers 查")
        revenue = args.get("expected_revenue")
        if revenue is not None and not 0 <= float(revenue) <= 10_000_000:
            raise PlanError("预计收入不合理")
        fu = _follow_up(args.get("follow_up"), today)
        details = [f"客户：{sess.customers[cid]}", f"商机：{args.get('title', '')[:80]}"]
        if revenue:
            details.append(f"预计收入：{float(revenue):,.0f} 元")
        if fu:
            details.append(f"跟进：{fu['date']} {ACTIVITY_NAMES[fu['type']]} {fu['summary']}".rstrip())
        return {
            "kind": name,
            "title": "新建商机",
            "details": details,
            "args": {"customer_id": cid, "title": str(args.get("title", ""))[:80], "expected_revenue": revenue, "description": str(args.get("description") or "")[:500], "follow_up": fu},
        }
    if name == "schedule_activity":
        oid = int(args.get("opportunity_id") or 0)
        if oid not in sess.opportunities:
            raise PlanError("opportunity_id 不是查询结果里的商机，请先用 list_opportunities 查")
        fu = _follow_up(args, today)
        return {
            "kind": name,
            "title": "安排跟进",
            "details": [f"商机：{sess.opportunities[oid]}", f"{fu['date']} {ACTIVITY_NAMES[fu['type']]} {fu['summary']}".rstrip()],
            "args": {"opportunity_id": oid, "follow_up": fu},
        }
    if name == "create_quotation":
        cid = int(args.get("customer_id") or 0)
        if cid not in sess.customers:
            raise PlanError("customer_id 不是查询结果里的客户，请先用 find_customers 查")
        lines = []
        for ln in args.get("lines") or []:
            pid, qty = int(ln.get("product_id") or 0), float(ln.get("quantity") or 0)
            if pid not in sess.products:
                raise PlanError(f"product_id {pid} 不是查询结果里的产品，请先用 find_products 查")
            if not 0 < qty <= 10000:
                raise PlanError("数量要在 1～10000 之间")
            lines.append({"product_id": pid, "quantity": qty})
        if not lines:
            raise PlanError("报价单至少要有一行产品")
        details = [f"客户：{sess.customers[cid]}"]
        total = 0.0
        for ln in lines:
            p = sess.products[ln["product_id"]]
            total += p["list_price"] * ln["quantity"]
            details.append(f"{p['name']} × {ln['quantity']:g}，标价 {p['list_price']:,.2f}")
        details.append(f"按标价估算合计 {total:,.2f}（以 Odoo 计算为准）")
        return {"kind": name, "title": "生成报价单草稿", "details": details, "args": {"customer_id": cid, "lines": lines, "note": str(args.get("note") or "")[:500]}}
    raise PlanError(f"未知操作 {name}")


async def run_read(odoo: Odoo, name: str, args: dict, sess: Session) -> object:
    if name == "find_customers":
        q = str(args.get("query", ""))[:50]
        rows = await odoo.search_read("res.partner", [("name", "ilike", q)], ["name", "city", "email", "is_company"], limit=6)
        for r in rows:
            sess.customers[r["id"]] = r["name"]
        return rows
    if name == "find_products":
        q = str(args.get("query", ""))[:50]
        rows = await odoo.search_read(
            "product.product", [("sale_ok", "=", True), "|", ("name", "ilike", q), ("default_code", "ilike", q)], ["name", "default_code", "list_price"], limit=6
        )
        for r in rows:
            sess.products[r["id"]] = {"name": r["name"], "list_price": float(r["list_price"] or 0)}
        return rows
    if name == "customer_overview":
        cid = int(args.get("customer_id") or 0)
        partner = await odoo.search_read("res.partner", [("id", "=", cid)], ["name", "city", "email", "phone"], limit=1)
        if not partner:
            return {"error": "客户不存在"}
        sess.customers[cid] = partner[0]["name"]
        orders = await odoo.search_read(
            "sale.order", [("partner_id", "child_of", cid)], ["name", "date_order", "amount_total", "state"], limit=8, order="date_order desc"
        )
        leads = await odoo.search_read(
            "crm.lead", [("partner_id", "child_of", cid), ("type", "=", "opportunity")], ["name", "expected_revenue", "probability", "stage_id", "date_deadline"], limit=8
        )
        for ld in leads:
            sess.opportunities[ld["id"]] = ld["name"]
        confirmed = [o for o in orders if o["state"] in ("sale", "done")]
        return {
            "customer": partner[0],
            "recent_orders": orders,
            "confirmed_total_recent": round(sum(o["amount_total"] for o in confirmed), 2),
            "open_opportunities": leads,
        }
    if name == "list_opportunities":
        domain: list = [("type", "=", "opportunity")]
        if args.get("customer_id"):
            domain.append(("partner_id", "child_of", int(args["customer_id"])))
        rows = await odoo.search_read(
            "crm.lead",
            domain,
            ["name", "partner_id", "expected_revenue", "probability", "stage_id", "date_deadline"],
            limit=min(int(args.get("limit") or 10), 20),
            order="expected_revenue desc",
        )
        for r in rows:
            sess.opportunities[r["id"]] = r["name"]
        return rows
    return {"error": f"未知工具 {name}"}


async def ask(gw: Gateway, odoo: Odoo, instruction: str) -> tuple[str, Session, list[ChatResult]]:
    now = datetime.now(TZ)
    today = now.date()
    weekday = "一二三四五六日"[today.weekday()]
    msgs: list[dict] = [{"role": "system", "content": SYSTEM.format(today=today.isoformat(), weekday=f"星期{weekday}")}, {"role": "user", "content": instruction}]
    sess = Session()
    calls: list[ChatResult] = []
    for _ in range(MAX_ROUNDS):
        r = await gw.chat("agent", msgs, tools=TOOLS, max_tokens=600)
        calls.append(r)
        if not r.tool_calls:
            return r.text.strip(), sess, calls
        msgs.append({"role": "assistant", "content": r.text or "", "tool_calls": r.tool_calls})
        for tc in r.tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                if name in WRITE_TOOLS:
                    action = propose(name, args, sess, today)
                    sess.actions.append(action)
                    result: object = {"status": "已加入待确认清单，用户确认后才会执行"}
                    summary = f"待确认：{action['title']}"
                else:
                    result = await run_read(odoo, name, args, sess)
                    summary = f"返回 {len(result)} 条" if isinstance(result, list) else "已查询"
            except PlanError as e:
                result, summary = {"error": str(e)}, f"被程序拦下：{e}"
            except OdooError as e:
                result, summary = {"error": str(e)}, f"Odoo 报错：{e}"
            sess.steps.append({"tool": name, "args": args, "summary": summary, "write": name in WRITE_TOOLS})
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(result, ensure_ascii=False, default=str)[:4000]})
    return "步骤太多，先停在这里。请把要求拆小一点再试。", sess, calls


async def _activity_type(odoo: Odoo, kind: str) -> int | None:
    rows = await odoo.search_read("mail.activity.type", [("icon", "=", ACTIVITY_ICONS[kind])], ["id"], limit=1)
    return rows[0]["id"] if rows else None


async def _lead_model_id(odoo: Odoo) -> int:
    """活动要指明挂在哪个模型上（ir.model 的 id），但销售账号无权读 ir.model。
    从已有的商机活动上取这个 id（演示数据里有）；实在没有再查 ir.model。"""
    rows = await odoo.search_read("mail.activity", [("res_model", "=", "crm.lead")], ["res_model_id"], limit=1)
    if rows and rows[0].get("res_model_id"):
        return rows[0]["res_model_id"][0]
    model = await odoo.search_read("ir.model", [("model", "=", "crm.lead")], ["id"], limit=1)
    return model[0]["id"]


async def _schedule(odoo: Odoo, lead_id: int, fu: dict) -> None:
    vals = {"res_model_id": await _lead_model_id(odoo), "res_id": lead_id, "date_deadline": fu["date"], "summary": fu["summary"] or ACTIVITY_NAMES[fu["type"]]}
    type_id = await _activity_type(odoo, fu["type"])
    if type_id:
        vals["activity_type_id"] = type_id
    await odoo.execute("mail.activity", "create", [vals])


async def execute(odoo: Odoo, actions: list[dict], public_url: str) -> list[dict]:
    """人确认后执行。每条单独执行、单独报告结果，一条失败不影响其他条。"""
    out = []
    # 打上「AI 创建」标签，方便在 Odoo 里筛出 AI 建的记录；没有建标签的权限就不打
    try:
        tag = await odoo.search_read("crm.tag", [("name", "=", "AI 创建")], ["id"], limit=1)
        tag_id = tag[0]["id"] if tag else await odoo.execute("crm.tag", "create", [{"name": "AI 创建"}])
        tags = [(4, tag_id)]
    except OdooError:
        tags = []
    for a in actions:
        args = a["args"]
        try:
            if a["kind"] == "create_opportunity":
                vals = {"name": args["title"], "partner_id": args["customer_id"], "type": "opportunity", "description": args["description"], "tag_ids": tags}
                if args.get("expected_revenue"):
                    vals["expected_revenue"] = float(args["expected_revenue"])
                lead_id = await odoo.execute("crm.lead", "create", [vals])
                text = f"已新建商机 #{lead_id}"
                if args.get("follow_up"):
                    # 商机已经建好了，回访没排上也要如实告诉人，而不是整条报失败
                    try:
                        await _schedule(odoo, lead_id, args["follow_up"])
                        text += "，已安排回访"
                    except OdooError as e:
                        text += f"，但回访没排上（{e}），请在 Odoo 里手动安排"
                out.append({"title": a["title"], "ok": True, "text": text, "url": f"{public_url}/odoo/crm/{lead_id}"})
            elif a["kind"] == "schedule_activity":
                await _schedule(odoo, args["opportunity_id"], args["follow_up"])
                out.append({"title": a["title"], "ok": True, "text": "已安排跟进", "url": f"{public_url}/odoo/crm/{args['opportunity_id']}"})
            elif a["kind"] == "create_quotation":
                vals = {
                    "partner_id": args["customer_id"],
                    "order_line": [(0, 0, {"product_id": ln["product_id"], "product_uom_qty": ln["quantity"]}) for ln in args["lines"]],
                    "tag_ids": tags,
                }
                if args.get("note"):
                    vals["note"] = args["note"]
                so_id = await odoo.execute("sale.order", "create", [vals])
                so = await odoo.search_read("sale.order", [("id", "=", so_id)], ["name", "amount_total"], limit=1)
                out.append(
                    {"title": a["title"], "ok": True, "text": f"已生成报价单 {so[0]['name']}，合计 {so[0]['amount_total']:,.2f}", "url": f"{public_url}/odoo/sales/{so_id}"}
                )
        except OdooError as e:
            out.append({"title": a["title"], "ok": False, "text": f"执行失败：{e}"})
    return out


class PlanStore:
    """待确认的操作暂存在内存里，30 分钟过期；只有发起的访客能确认。服务重启后需要重新提问。"""

    def __init__(self) -> None:
        self._plans: dict[str, tuple[float, str, list[dict]]] = {}

    def put(self, visitor: str, actions: list[dict]) -> str:
        now = time.time()
        self._plans = {k: v for k, v in self._plans.items() if v[0] > now}
        pid = secrets.token_urlsafe(12)
        self._plans[pid] = (now + PLAN_TTL, visitor, actions)
        return pid

    def take(self, pid: str, visitor: str) -> list[dict] | None:
        item = self._plans.get(pid)
        if not item or item[0] < time.time() or item[1] != visitor:
            return None
        del self._plans[pid]
        return item[2]
