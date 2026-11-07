"""事件对应的站内消息；已读时间为空表示未读，不重复保存is_read字段。"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("CHAR_LENGTH(TRIM(title)) > 0", name="title"),
        CheckConstraint("CHAR_LENGTH(TRIM(content)) > 0", name="content"),
        CheckConstraint("read_at IS NULL OR read_at >= created_at", name="read_at"),
        UniqueConstraint("event_id", "recipient_user_id", name="uq_notifications_event_id_recipient_user_id"),
        Index("ix_notifications_recipient_read_time", "recipient_user_id", "read_at", "created_at"),
        {
            "mysql_engine": "InnoDB", "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("operation_logs.id", ondelete="RESTRICT"))
    recipient_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))"),
    )
    read_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
