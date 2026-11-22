"""总控台的身份认证：只认主人的 Telegram 账号。两种入口（2026-11-22 加了浏览器：小程序窗口没法最小化，电脑上来回切不方便）：
- 值班台机器人的 Telegram 小程序（Mini App）：initData，见下
- 浏览器：Telegram 官方登录按钮（Login Widget），前端把登录结果以 "login:" 开头的 query string 发来，见 verify_login

Telegram 打开小程序时给页面一串 initData（里面有用户 id、时间），用机器人的 token 签了名。
校验方法见 https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app ：
secret = HMAC-SHA256(key="WebAppData", msg=bot_token)；hash = HMAC-SHA256(key=secret, msg=按键名排序的 "k=v" 用换行连起来，不含 hash)。
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE = 24 * 3600  # 小程序打开超过一天就要重新打开
LOGIN_MAX_AGE = 7 * 24 * 3600  # 浏览器登录一次管 7 天


def verify_login(data: str, bot_token: str, owner_id: str, now: float | None = None, max_age: int = LOGIN_MAX_AGE) -> bool:
    """Telegram 登录按钮的结果（https://core.telegram.org/widgets/login#checking-authorization）：
    secret = SHA256(bot_token)；hash = HMAC-SHA256(key=secret, msg=按键名排序的 "k=v" 用换行连起来，不含 hash)"""
    if not data or not bot_token or not owner_id:
        return False
    try:
        fields = dict(parse_qsl(data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return False
    got = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    want = hmac.new(hashlib.sha256(bot_token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(got, want):
        return False
    try:
        age = (now or time.time()) - int(fields.get("auth_date", "0"))
    except ValueError:
        return False
    return 0 <= age <= max_age + 60 and fields.get("id") == str(owner_id)


def verify(init_data: str, bot_token: str, owner_id: str, now: float | None = None, max_age: int = MAX_AGE) -> bool:
    """签名对、没过期、是主人本人才返回 True。"login:" 开头的是浏览器登录按钮的结果"""
    if init_data.startswith("login:"):
        return verify_login(init_data[6:], bot_token, owner_id, now)
    if not init_data or not bot_token or not owner_id:
        return False
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return False
    got = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(got, want):
        return False
    try:
        age = (now or time.time()) - int(fields.get("auth_date", "0"))
        user = json.loads(fields.get("user", "{}"))
    except ValueError:
        return False
    return 0 <= age <= max_age + 60 and str(user.get("id")) == str(owner_id)


def sign(fields: dict[str, str], bot_token: str) -> str:
    """测试用：按 Telegram 的规则给一组字段签名，返回 initData 字符串"""
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def sign_login(fields: dict[str, str], bot_token: str) -> str:
    """测试用：按登录按钮的规则签名，返回 "login:" 开头的字符串"""
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    h = hmac.new(hashlib.sha256(bot_token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return "login:" + urlencode({**fields, "hash": h})
