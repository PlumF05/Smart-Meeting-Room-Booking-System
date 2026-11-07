"""登录会话；只存会话凭证摘要，不能用数据库中的摘要直接登录。"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, text
from sqlalchemy.dialects.mysql import DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (
        CheckConstraint("session_version > 0", name="session_version"),
        CheckConstraint("expires_at > created_at", name="expires_at"),
        {
            "mysql_engine": "InnoDB", "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(
        VARCHAR(64, charset="ascii", collation="ascii_bin"), unique=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # 同步CSRF令牌可被当前登录者读取；只有它不能获得登录权限。
    csrf_token: Mapped[str] = mapped_column(VARCHAR(43, charset="ascii", collation="ascii_bin"))
    session_version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))")
    )
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    revoked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
