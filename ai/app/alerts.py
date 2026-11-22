"""告警只有一个入口（2026-11-22 起）：Alertmanager 把所有告警发到 ai-api 的 /internal/alertmanager。

- 运维 Agent 诊断得了的（warning / critical，不在 autoheal.SKIP_ALERTS 里，也不是 ai-api 自己的）交给 autoheal，推诊断
- 诊断不了的（ai-api 自己出问题、模型费用 / 报错、VPS 掉线……）用这里的 text() 拼成消息，直接推到值班台

原来后者由单独的转发器 wecom-relay（Node，还能发企业微信）负责，和 ai-api 是两套；现在合成一套，少一个 Pod。
代价：ai-api 自己整个挂掉时发不出告警，由 UptimeRobot 从外面拨测 /api/ai/health 发邮件兜底（见 docs/runbook.md）。
"""

from datetime import datetime
from html import escape

from .localtime import TZ

SEVERITY = {"critical": ("🔴", "严重"), "warning": ("🟠", "警告"), "info": ("🔵", "提示")}
MAX = 3900  # Telegram 一条最多 4096 字，留点余量


def about_ai_api(labels: dict) -> bool:
    """告警说的是 ai-api 自己：它这时可能诊断不了自己，原始告警照常推"""
    return labels.get("namespace") == "ops-desk" and any(
        v == "ai-api" or str(v or "").startswith("ai-api-") for v in (labels.get(k) for k in ("deployment", "pod", "job_name", "container"))
    )


def diagnosed_by_agent(alert: dict, skip: set[str]) -> bool:
    labels = alert.get("labels") or {}
    if labels.get("severity") not in ("warning", "critical") or labels.get("alertname") in skip:
        return False
    return not about_ai_api(labels)


def _time(iso: str | None) -> str:
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return "-"
    return "-" if t.year < 2000 else t.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def text(alerts: list[dict]) -> str | None:
    """一组告警拼成一条 Telegram 消息（HTML）：告警中的在前，已恢复的在后"""
    if not alerts:
        return None
    firing = [a for a in alerts if a.get("status") == "firing"]
    resolved = [a for a in alerts if a.get("status") != "firing"]
    name = escape(str((alerts[0].get("labels") or {}).get("alertname") or "告警"))
    if firing:
        icon = SEVERITY.get((firing[0].get("labels") or {}).get("severity") or "", ("🔴", ""))[0]
        parts = [f"<b>{icon} {name}：{len(firing)} 条告警中</b>"]
    else:
        parts = [f"<b>✅ {name}：已恢复</b>"]
    for a in firing + resolved:
        labels, ann = a.get("labels") or {}, a.get("annotations") or {}
        label = SEVERITY.get(labels.get("severity") or "", SEVERITY["warning"])[1]
        on = a.get("status") == "firing"
        lines = [f"<b>{escape(str(ann.get('summary') or labels.get('alertname') or '未命名告警'))}</b>", f"┃ 状态：{label + ' · 告警中' if on else '已恢复'}"]
        if ann.get("description"):
            lines.append(f"┃ 详情：{escape(str(ann['description']))}")
        # kube-state-metrics 的告警（Job、Deployment 等）里 pod 是它自己，先取真正出问题的资源名
        obj = labels.get("job_name") or labels.get("cronjob") or labels.get("deployment") or labels.get("statefulset") or labels.get("daemonset") or labels.get("pod")
        target = "/".join(x for x in (labels.get("namespace"), obj) if x) if labels.get("namespace") else labels.get("name") or labels.get("instance")
        if target:
            lines.append(f"┃ 对象：{escape(str(target))}")
        lines.append(f"┃ 开始：{_time(a.get('startsAt'))}")
        if not on:
            lines.append(f"┃ 恢复：{_time(a.get('endsAt'))}")
        parts.append("\n".join(lines))
    out = "\n\n".join(parts)
    return out if len(out) <= MAX else out[:MAX] + "\n\n…（内容过长已截断，请到总控台告警中心查看）"
