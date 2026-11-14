"""显示用的时区（2026-11-14）。公开仓库不写所在地：
- TZ：从环境变量 LOCAL_TZ 读（真实值在服务器的 Secret ai-api 里），默认 Asia/Shanghai；都是 UTC+8，换了也不影响钟点
- 网站、AI 提示词里一律写「UTC+8」（访客也看得到）；只有 Telegram 值班台自己的文字用 LOCAL_TZ_LABEL（Secret 里配，默认也是 UTC+8）"""

import os
from zoneinfo import ZoneInfo

TZ_KEY = os.environ.get("LOCAL_TZ", "Asia/Shanghai")
TZ = ZoneInfo(TZ_KEY)
PUBLIC_LABEL = "UTC+8"
LABEL = os.environ.get("LOCAL_TZ_LABEL", PUBLIC_LABEL)
