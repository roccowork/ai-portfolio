"""单据信息提取：图片交给视觉模型，按固定格式输出 JSON，再由程序核对金额。模型负责「看」，规则负责「查」。"""

import base64
import io
import json
import re

from PIL import Image

from .gateway import ChatResult, Gateway

PROMPT = """识别这张单据，只输出一个 JSON 对象，不要任何解释。格式：
{"doc_type": "发票|送货单|收据|其他",
 "number": "单号，没有就 null",
 "date": "YYYY-MM-DD，没有就 null",
 "seller": "开票方/发货方，没有就 null",
 "buyer": "购买方/收货方，没有就 null",
 "items": [{"name": "品名", "qty": 数量, "unit_price": 单价, "amount": 金额}],
 "total": 合计金额数字,
 "remarks": "手写备注或异常说明，没有就 null"}
看不清的字段填 null，不要猜。数字不要带单位和千分位。"""

MAX_SIDE = 1600


def prepare_image(data: bytes) -> str:
    """缩小到长边 1600 像素并转成 JPEG：省 token，也挡掉不是图片的文件。"""
    img = Image.open(io.BytesIO(data))
    img.load()
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型没有返回 JSON")
    return json.loads(m.group(0))


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def check(doc: dict) -> list[str]:
    """程序核对：数量 × 单价 = 金额、各行金额之和 = 合计、关键字段不缺。返回问题清单，空表示通过。"""
    issues: list[str] = []
    items = doc.get("items") or []
    total_lines = 0.0
    for i, it in enumerate(items, 1):
        qty, price, amount = _num(it.get("qty")), _num(it.get("unit_price")), _num(it.get("amount"))
        if amount is None:
            issues.append(f"第 {i} 行缺少金额")
            continue
        total_lines += amount
        if qty is not None and price is not None and abs(qty * price - amount) > 0.01 + amount * 0.001:
            issues.append(f"第 {i} 行：数量 × 单价 = {qty * price:.2f}，与金额 {amount:.2f} 不一致")
    total = _num(doc.get("total"))
    if total is None:
        issues.append("没有识别出合计金额")
    elif items and abs(total_lines - total) > 0.01 + total * 0.001:
        issues.append(f"各行金额之和 {total_lines:.2f} 与合计 {total:.2f} 不一致")
    for key, name in (("number", "单号"), ("date", "日期")):
        if not doc.get(key):
            issues.append(f"没有识别出{name}")
    return issues


async def extract(gw: Gateway, image: bytes) -> tuple[ChatResult, dict, list[str]]:
    url = prepare_image(image)
    result = await gw.chat(
        "extract",
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": PROMPT}]}],
        kind="vision",
        max_tokens=1000,
    )
    doc = parse_json(result.text)
    return result, doc, check(doc)
