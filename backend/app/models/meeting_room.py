"""会议室的固定资料；某时段空闲与否由未来的预约查询计算。"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Integer, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MeetingRoom(Base):
    __tablename__ = "meeting_rooms"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="capacity"),
        CheckConstraint("JSON_TYPE(equipment) = 'ARRAY'", name="equipment_array"),
        CheckConstraint("is_active IN (0, 1)", name="is_active"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    location: Mapped[str] = mapped_column(String(255))
    capacity: Mapped[int] = mapped_column(Integer)
    # 修改设备时整体赋值新列表；列表元素及筛选规则由后续接口/业务服务校验。
    equipment: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=text("(JSON_ARRAY())")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("(UTC_TIMESTAMP(6))")
    )
