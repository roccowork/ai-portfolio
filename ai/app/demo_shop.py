"""客服 Agent 演示用的「清源净水」后台（公司和订单都是虚构的）。
Dify 里的客服 Agent 把这两个接口当工具调用：查订单、预约上门。预约会真的推送到我的 Telegram，演示时能当场看到效果。
只允许带 X-Demo-Token 的请求（Dify 工具里配置），不对访客直接开放。"""

ORDERS: dict[str, dict] = {
    "QY1001": {"product": "Q5 厨下净水器", "status": "已发货", "logistics": "顺丰 SF1234567890，昨天 18:20 已到达本市转运中心，预计明天送达", "install": "送达后 48 小时内安排上门安装"},
    "QY1002": {"product": "M1 台式即热净饮机", "status": "已签收", "logistics": "3 天前已签收", "install": "免安装，加水即用"},
    "QY1003": {"product": "Q5 厨下净水器", "status": "待发货", "logistics": "仓库正在备货，预计 24 小时内发出", "install": "送达后 48 小时内安排上门安装"},
    "QY1004": {"product": "RO 膜滤芯", "status": "已签收", "logistics": "上周已签收", "install": "可以预约师傅上门免费更换"},
    "QY1005": {"product": "Q5 厨下净水器", "status": "已安装", "logistics": "已签收", "install": "上周五已安装完成，整机质保 3 年"},
}


def find_order(order_id: str) -> dict:
    oid = order_id.strip().upper().replace(" ", "")
    if oid in ORDERS:
        return {"found": True, "order_id": oid, **ORDERS[oid]}
    return {"found": False, "order_id": oid, "message": "没有找到这个订单号。演示订单号是 QY1001 到 QY1005"}
