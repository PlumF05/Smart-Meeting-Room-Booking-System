"""本机HTTP演示的认证参数；不包含密码或签名密钥。"""

from datetime import timedelta


SESSION_COOKIE_NAME = "smrbs_session"
SESSION_LIFETIME = timedelta(hours=8)
# 本期仅运行本机HTTP；部署HTTPS时必须改为True。
COOKIE_SECURE = False
TRUSTED_ORIGINS = frozenset({
    "http://127.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:5173", "http://localhost:5173",
})
