"""短期操作授权与结果收据；不保存登录原文凭据或聊天历史。"""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssistantConfirmation(Base):
    __tablename__ = "assistant_confirmations"
    __table_args__ = (
        UniqueConstraint("user_id", "session_token_hash", "conversation_id", "revision", "payload_hash",
                         name="uq_assistant_confirmations_plan"),
        Index("ix_assistant_confirmations_conversation", "user_id", "session_token_hash", "conversation_id"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("session_version > 0", name="session_version"),
        CheckConstraint("action = 'submit'", name="action"),
        CheckConstraint("state IN ('pending','consumed','superseded','expired')", name="state"),
        CheckConstraint("expires_at > created_at", name="expires_at"),
        CheckConstraint("JSON_TYPE(payload) = 'OBJECT'", name="payload_object"),
        CheckConstraint("(state = 'consumed' AND result IS NOT NULL) OR (state <> 'consumed' AND result IS NULL)",
                        name="result_state"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(VARCHAR(43, charset="ascii", collation="ascii_bin"), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    session_token_hash: Mapped[str] = mapped_column(VARCHAR(64, charset="ascii", collation="ascii_bin"))
    session_version: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[str] = mapped_column(VARCHAR(128, charset="ascii", collation="ascii_bin"))
    revision: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(16, collation="utf8mb4_0900_as_cs"))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(VARCHAR(64, charset="ascii", collation="ascii_bin"))
    state: Mapped[str] = mapped_column(String(16, collation="utf8mb4_0900_as_cs"))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    consumed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
