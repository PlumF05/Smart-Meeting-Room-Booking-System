"""账号资料；启停状态与会话版本参与每次登录身份检查。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="role"),
        CheckConstraint(
            "(identity_type IS NULL OR identity_type IN ('teacher', 'student')) "
            "AND (role <> 'user' OR identity_type IS NOT NULL)",
            name="identity_type",
        ),
        CheckConstraint("session_version > 0", name="session_version"),
        CheckConstraint("is_active IN (0, 1)", name="is_active"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_0900_as_cs"), server_default=text("'user'")
    )
    identity_type: Mapped[str | None] = mapped_column(
        String(16, collation="utf8mb4_0900_as_cs")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    # 停用服务须在同一事务递增版本并撤销会话，重新启用不能回退版本。
    session_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    # DATETIME不保存时区：统一存UTC，之后业务时间按北京时间转换和校验。
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))")
    )
