"""固定字段的业务事件；不保存请求正文、密码或完整助手对话。"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.mysql import DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (
        CheckConstraint("CHAR_LENGTH(TRIM(event_key)) > 0", name="event_key"),
        CheckConstraint("object_id > 0", name="object_id"),
        CheckConstraint("actor_kind IN ('user', 'system')", name="actor_kind"),
        CheckConstraint("object_type IN ('user', 'meeting_room', 'reservation')", name="object_type"),
        CheckConstraint(
            "action IN ('register', 'submit', 'approve', 'reject', 'cancel', 'revoke', "
            "'expire', 'finish', 'remind', 'edit', 'disable', 'enable')", name="action",
        ),
        CheckConstraint("source IN ('page', 'assistant', 'system')", name="source"),
        CheckConstraint("result IN ('success', 'failure')", name="result"),
        CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL AND source IN ('page', 'assistant')) "
            "OR (actor_kind = 'system' AND actor_user_id IS NULL AND source = 'system')",
            name="actor_source",
        ),
        CheckConstraint("before_status IS NULL OR CHAR_LENGTH(TRIM(before_status)) > 0", name="before_status"),
        CheckConstraint("after_status IS NULL OR CHAR_LENGTH(TRIM(after_status)) > 0", name="after_status"),
        CheckConstraint("reason IS NULL OR CHAR_LENGTH(TRIM(reason)) > 0", name="reason"),
        Index("ix_operation_logs_object_time", "object_type", "object_id", "created_at"),
        Index("ix_operation_logs_actor_time", "actor_user_id", "created_at"),
        Index("ix_operation_logs_created_at", "created_at"),
        {
            "mysql_engine": "InnoDB", "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 同一逻辑操作的重试须复用此键；新键代表新事件，不是业务请求自动幂等。
    event_key: Mapped[str] = mapped_column(VARCHAR(64, charset="ascii", collation="ascii_bin"), unique=True)
    actor_kind: Mapped[str] = mapped_column(String(16, collation="utf8mb4_0900_as_cs"))
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    object_type: Mapped[str] = mapped_column(String(16, collation="utf8mb4_0900_as_cs"))
    # 多类对象共用一张审计表；此列不是多态外键，业务服务负责核实目标。
    object_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32, collation="utf8mb4_0900_as_cs"))
    source: Mapped[str] = mapped_column(String(16, collation="utf8mb4_0900_as_cs"))
    before_status: Mapped[str | None] = mapped_column(String(32))
    after_status: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_0900_as_cs"), server_default=text("'success'"),
    )
    reason: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))"),
    )
