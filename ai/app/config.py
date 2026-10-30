"""配置全部来自环境变量（Deployment 和 Secret），代码里不写任何密钥和地址。"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def _prices() -> dict[str, tuple[float, float]]:
    """每千 token 的估算价格（元）：输入, 输出。格式 model=in/out;model=in/out，可在 Deployment 里覆盖。"""
    raw = os.environ.get(
        "AI_PRICES",
        "qwen-plus=0.0008/0.002;qwen-plus-2025-12-01=0.0008/0.002;qwen-plus-latest=0.0008/0.002;qwen3.7-plus=0.002/0.008;qwen-flash=0.00015/0.0015;qwen3-vl-plus=0.001/0.01;text-embedding-v3=0.0005/0;"
        "mistralai/mistral-medium-3.1=0.00288/0.0144;mistralai/mistral-small-3.2-24b-instruct=0.00043/0.0013",
    )
    out: dict[str, tuple[float, float]] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        model, price = item.split("=", 1)
        pin, pout = price.split("/")
        out[model.strip()] = (float(pin), float(pout))
    return out


@dataclass
class Settings:
    # 阿里云百炼（通义千问）的 OpenAI 兼容接口。国际站和国内站地址不同，由 Secret 提供
    api_key: str = field(default_factory=lambda: os.environ.get("DASHSCOPE_API_KEY", ""))
    base_url: str = field(
        default_factory=lambda: os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    )
    # 模型按用途分组，前面的优先，失败时依次降级。百炼的免费额度按模型名分开算（别名和带日期的版本各 100 万 token），
    # 2026-10-30 别名 qwen-plus 的额度用完、开着「用完即停」，全站降级到 qwen-flash。改用固定日期的版本：
    # 价格和 qwen-plus 一样，行为不会随别名更新而变，评测结果前后可比
    chat_models: list[str] = field(default_factory=lambda: _list("AI_CHAT_MODELS", "qwen-plus-2025-12-01,qwen-plus-latest,qwen-flash"))
    vision_models: list[str] = field(default_factory=lambda: _list("AI_VISION_MODELS", "qwen3-vl-plus"))
    embed_model: str = field(default_factory=lambda: os.environ.get("AI_EMBED_MODEL", "text-embedding-v3"))
    # 语音识别按音频秒数计费，单独配置
    asr_model: str = field(default_factory=lambda: os.environ.get("AI_ASR_MODEL", "qwen3-asr-flash"))
    asr_price_per_sec: float = field(default_factory=lambda: float(os.environ.get("AI_ASR_PRICE", "0.00022")))
    prices: dict[str, tuple[float, float]] = field(default_factory=_prices)
    # 全站每天的费用上限（元）。超过后当天所有 AI 功能暂停，第二天自动恢复
    daily_budget: float = field(default_factory=lambda: float(os.environ.get("AI_DAILY_BUDGET", "1.6")))
    # 每个访客每天的次数上限（按功能）
    quota: dict[str, int] = field(
        default_factory=lambda: {
            "ask": int(os.environ.get("AI_QUOTA_ASK", "30")),
            "lead": int(os.environ.get("AI_QUOTA_LEAD", "5")),
            "extract": int(os.environ.get("AI_QUOTA_EXTRACT", "5")),
            "triage": int(os.environ.get("AI_QUOTA_TRIAGE", "20")),
            "minutes": int(os.environ.get("AI_QUOTA_MINUTES", "3")),
            "chatbi": int(os.environ.get("AI_QUOTA_CHATBI", "20")),
            "agent": int(os.environ.get("AI_QUOTA_AGENT", "10")),
            "content": int(os.environ.get("AI_QUOTA_CONTENT", "10")),
            "opsagent": int(os.environ.get("AI_QUOTA_OPSAGENT", "10")),
            "digest": int(os.environ.get("AI_QUOTA_DIGEST", "10")),
        }
    )
    # Odoo（办公 Agent 操作的业务系统），集群内部地址；密码在 Secret ai-api 的 ODOO_PASSWORD，没配就不启用
    odoo_url: str = field(default_factory=lambda: os.environ.get("ODOO_URL", "http://odoo.odoo.svc.cluster.local:8069"))
    odoo_db: str = field(default_factory=lambda: os.environ.get("ODOO_DB", "odoo"))
    odoo_user: str = field(default_factory=lambda: os.environ.get("ODOO_USER", "ai-agent"))
    odoo_public_url: str = field(default_factory=lambda: os.environ.get("ODOO_PUBLIC_URL", "https://odoo.liuxd.dev"))
    odoo_password: str = field(default_factory=lambda: os.environ.get("ODOO_PASSWORD", ""))
    # 客服 Agent 演示（Dify 工具调用 /api/ai/demo/*）的访问令牌，Dify 工具里配同一个值；没配就关闭这两个接口
    demo_tool_token: str = field(default_factory=lambda: os.environ.get("DEMO_TOOL_TOKEN", ""))
    # 运维 Agent 自愈：每天最多分析几条告警（每条都要调用模型）
    heal_daily: int = field(default_factory=lambda: int(os.environ.get("HEAL_DAILY", "10")))
    # 预约演示全站每天最多几次：请求都来自 Dify 所在的服务器，按访客 IP 限不住，只能限总数，防止 Telegram 被刷屏
    demo_booking_daily: int = field(default_factory=lambda: int(os.environ.get("DEMO_BOOKING_DAILY", "30")))
    # 运维 Agent 查询的 Prometheus（kube-prometheus-stack，集群内地址）
    prom_url: str = field(default_factory=lambda: os.environ.get("PROM_URL", "http://kps-prometheus.monitoring.svc:9090"))
    # 效果评测的裁判模型（ai/app/evals/），没配就和回答用同一组模型
    eval_judge_models: list[str] = field(default_factory=lambda: _list("EVAL_JUDGE_MODELS", ""))
    # 平台实况的数据（站点 nginx 提供的 /status.json，集群内地址），值班台的「📊 平台实况」读它
    status_url: str = field(default_factory=lambda: os.environ.get("STATUS_URL", "http://ops-desk.ops-desk.svc/status.json"))
    # 网站的 Telegram 机器人（Secret telegram-ops）：自愈审批、访客留言；没配就不启动
    tg_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    tg_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))
    # 请求参数里附加的字段（比如 OpenRouter 的路由要求），默认没有
    payload_extra: dict = field(default_factory=dict)
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("AI_DATA_DIR", "/data")))
    cases_dir: Path = field(default_factory=lambda: Path(os.environ.get("AI_CASES_DIR", "/app/knowledge")))
    # 运维知识库：镜像里的 docs/runbook.md、architecture.md、migrate-server.md（ai/app/opsdocs.py）
    opsdocs_dir: Path = field(default_factory=lambda: Path(os.environ.get("AI_OPSDOCS_DIR", "/app/opsdocs")))
    samples_dir: Path = field(default_factory=lambda: Path(os.environ.get("AI_SAMPLES_DIR", "/app/samples")))
    timeout: float = field(default_factory=lambda: float(os.environ.get("AI_TIMEOUT", "60")))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)
