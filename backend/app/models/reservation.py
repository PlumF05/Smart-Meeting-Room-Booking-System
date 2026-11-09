"""预约事实与提交时房间快照；并发占位和状态流转由后续业务服务控制。"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("attendees > 0", name="attendees"),
        CheckConstraint("room_capacity_snapshot > 0", name="snapshot_capacity"),
        CheckConstraint("attendees <= room_capacity_snapshot", name="attendees_capacity"),
        CheckConstraint("ends_at > starts_at", name="time_order"),
        CheckConstraint("JSON_TYPE(required_equipment) = 'ARRAY'", name="required_equipment_array"),
        CheckConstraint("JSON_TYPE(room_equipment_snapshot) = 'ARRAY'", name="room_equipment_array"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired', 'revoked', 'finished')",
            name="status",
        ),
        CheckConstraint("source IN ('page', 'assistant')", name="source"),
        # 两个索引辅助之后的房间/个人冲突查询，不是防止时段重叠的唯一约束。
        Index("ix_reservations_room_occupancy", "room_id", "status", "starts_at", "ends_at"),
        Index("ix_reservations_user_occupancy", "user_id", "status", "starts_at", "ends_at"),
        Index("ix_reservations_submitted_at", "submitted_at"),
        {
            "mysql_engine": "InnoDB", "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    room_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("meeting_rooms.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(1000))
    attendees: Mapped[int] = mapped_column(Integer)
    # DATETIME不保留时区：提交时须先转换UTC，再去掉tzinfo，不能直接去掉北京时区。
    starts_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    ends_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    status: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_0900_as_cs"), server_default=text("'pending'"),
    )
    source: Mapped[str] = mapped_column(
        String(16, collation="utf8mb4_0900_as_cs"), server_default=text("'page'"),
    )
    required_equipment: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=text("(JSON_ARRAY())"),
    )
    # 独立保存当时的资料；改会议室当前信息时不能回写这些快照字段。
    room_name_snapshot: Mapped[str] = mapped_column(String(100))
    room_location_snapshot: Mapped[str] = mapped_column(String(255))
    room_capacity_snapshot: Mapped[int] = mapped_column(Integer)
    room_equipment_snapshot: Mapped[list[str]] = mapped_column(JSON)
    # 当前状态的说明，完整的逐次处理记录在后续操作记录表保存。
    status_reason: Mapped[str | None] = mapped_column(String(1000))
    submitted_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))"),
        # 这是SQLAlchemy更新时生成的表达式，不是MySQL自动更新时间触发器。
        onupdate=text("UTC_TIMESTAMP(6)"),
    )
