"""后台定时任务一览（值班台的「🗓 定时任务」）：从 K8s 读出所有 CronJob，算出下次运行时间（换成 UTC+8，见 localtime.py），配上中文说明。
只读，不能改、不能触发；改时间还是改仓库里的 YAML。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# 哪个机器人发：daily-english 用私人助理的机器人，其余用值班台
BOT = {"daily-english": "私人助理"}
# CronJob 名字 → (分组, 中文说明)。分组 push：会发消息（在总控台「推送」页看和开关，脚本发之前查 pushEnabled）；其他是后台维护。新增 CronJob 时顺手补一行
KNOWN = {
    "daily-english": ("push", "📘 每日运维英语"),
    "weekly-report": ("push", "📈 网站周报"),
    "r2-usage-monthly": ("push", "☁️ R2 用量月报"),
    "r2-usage-check": ("push", "☁️ R2 用量检查（超 80% 才提醒）"),
    "ops-desk-status": ("ops", "🩺 网站状态采集"),
    "backup-ai-api": ("ops", "💾 备份网站 AI 服务数据（账本、自愈记录）"),
    "backup-assistant": ("ops", "💾 备份私人助理数据"),
    "backup-dify": ("ops", "💾 备份 Dify"),
}
WEEKDAYS = "日一二三四五六"
from .localtime import LABEL, TZ as SG


def tz_name(tz: str) -> str:
    """CronJob 的时区怎么称呼：和显示时区同为 UTC+8 的统一叫 LABEL（值班台里显示，见 localtime.py）；
    其他时区不写城市名（2026-11-14 用户要求值班台不出现洛杉矶时间），写成 UTC±N"""
    if tz == "UTC":
        return "UTC 时间"
    try:
        off = datetime.now(ZoneInfo(tz)).utcoffset() or timedelta(0)
    except (KeyError, ValueError):
        return tz
    if off == datetime.now(SG).utcoffset():
        return LABEL
    h = off.total_seconds() / 3600
    return f"UTC{h:+g}"


def _field(expr: str, lo: int, hi: int) -> set[int]:
    """解析 cron 的一个字段：* */n a a-b a-b/n a,b"""
    out: set[int] = set()
    for part in expr.split(","):
        rng, _, step = part.partition("/")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = map(int, rng.split("-"))
        else:
            a = b = int(rng)
            if step:
                b = hi
        out.update(range(a, b + 1, int(step) if step else 1))
    return out


def next_run(schedule: str, tz: str, after: datetime) -> datetime | None:
    """按 CronJob 自己的时区算下一次运行时间（返回 UTC）。逐分钟往后找，最多找 40 天。"""
    minute, hour, dom, month, dow = schedule.split()
    mins, hours, doms, months = _field(minute, 0, 59), _field(hour, 0, 23), _field(dom, 1, 31), _field(month, 1, 12)
    dows = {d % 7 for d in _field(dow, 0, 7)}
    t = after.astimezone(ZoneInfo(tz)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(40 * 24 * 60):
        if t.month in months and t.hour in hours and t.minute in mins:
            in_dom, in_dow = t.day in doms, (t.weekday() + 1) % 7 in dows
            # 标准 cron：日期和星期都限定时，满足其一就运行
            if (in_dom or in_dow) if dom != "*" and dow != "*" else (in_dom and in_dow):
                return t.astimezone(timezone.utc)
        t += timedelta(minutes=1)
    return None


def describe(schedule: str) -> str:
    """把常见写法翻译成中文；看不懂的原样返回。"""
    minute, hour, dom, month, dow = schedule.split()
    if minute.startswith("*/") and hour == dom == month == dow == "*":
        return f"每 {minute[2:]} 分钟"
    if not (minute.isdigit() and hour.isdigit() and month == "*"):
        return schedule
    at = f"{int(hour):02d}:{int(minute):02d}"
    if dom == "*" and dow == "*":
        return f"每天 {at}"
    if dom == "*" and dow.isdigit():
        return f"每周{WEEKDAYS[int(dow) % 7]} {at}"
    if dow == "*" and dom.isdigit():
        return f"每月 {dom} 号 {at}"
    return schedule


def local_when(schedule: str, nxt: datetime) -> str:
    """按下一次运行的时刻，把频率写成 UTC+8，如 每天 09:00、每周一 09:20。"""
    minute, hour, dom, month, dow = schedule.split()
    if not (minute.isdigit() and hour.isdigit() and month == "*"):
        return describe(schedule)
    sg = nxt.astimezone(SG)
    if dom == "*" and dow == "*":
        return f"每天 {sg:%H:%M}"
    if dom == "*" and dow.isdigit():
        return f"每周{WEEKDAYS[(sg.weekday() + 1) % 7]} {sg:%H:%M}"
    if dow == "*" and dom.isdigit():
        return f"每月 {sg.day} 号 {sg:%H:%M}"
    return describe(schedule)


def summarize(items: list[dict], my_tz: str, now: datetime | None = None) -> list[dict]:
    """items 是 K8s 的 CronJob 对象。返回排好序的 [{group, title, when, tz, next, last_ok, suspended}]，时间都换成 my_tz。"""
    now = now or datetime.now(timezone.utc)
    mine = ZoneInfo(my_tz)
    out = []
    for cj in items:
        name = cj["metadata"]["name"]
        spec, status = cj.get("spec") or {}, cj.get("status") or {}
        tz = spec.get("timeZone") or "UTC"
        group, title = KNOWN.get(name, ("ops", name))
        nxt = None if spec.get("suspend") else next_run(spec["schedule"], tz, now)
        if nxt:
            when = local_when(spec["schedule"], nxt)
        else:
            when = describe(spec["schedule"])
            if tz != my_tz and not when.startswith("每 "):
                when += f"（{tz_name(tz)}）"
        last, ok = status.get("lastScheduleTime"), status.get("lastSuccessfulTime")
        out.append(
            {
                "key": name,
                "group": group,
                "title": title,
                "when": when,
                "next": nxt.astimezone(mine) if nxt else None,
                # 最近一次触发后有成功记录才算成功；还没跑过就是 None
                # 正在跑的那次还没结束时，lastSuccessfulTime 还是上一次的，不能算失败
                "last_ok": "running" if status.get("active") else None if not last else bool(ok and ok >= last),
                "suspended": bool(spec.get("suspend")),
            }
        )
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(out, key=lambda x: (x["group"] != "push", x["next"] or far))
