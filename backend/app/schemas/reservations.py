"""提交预约的输入和结果；申请人、状态、入口及会议室快照由服务确定。"""

from datetime import date as Date, time as Time
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.reservation_enums import ReservationSource
from app.schemas.booking_time import RoomWindowQuery
from app.schemas.rooms import EquipmentName


class ReservationCreate(RoomWindowQuery):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    room_id: int = Field(ge=1, le=9223372036854775807)
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    attendees: int = Field(ge=1, le=2147483647)
    date: Date = Field(description="北京时间的预约日期，严格YYYY-MM-DD格式。")
    start_time: Time = Field(description="北京时间的开始时刻，严格HH:mm格式。")
    end_time: Time = Field(description="同一天的结束时刻，严格HH:mm格式。")
    required_equipment: list[EquipmentName] = Field(
        default_factory=list, max_length=10, description="所需设备，会议室须全部具备。",
    )

    @field_validator("title", "purpose", mode="before")
    @classmethod
    def optional_text(cls, value):
        return "" if value is None else value

    @field_validator("required_equipment")
    @classmethod
    def distinct_equipment(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class ReservationRoomSnapshot(BaseModel):
    name: str
    location: str
    capacity: int
    equipment: list[str]


class ReservationCreated(BaseModel):
    id: int
    user_id: int
    room_id: int
    title: str
    purpose: str
    attendees: int
    date: Date
    start_time: str
    end_time: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal["pending"]
    source: ReservationSource
    required_equipment: list[str]
    room_snapshot: ReservationRoomSnapshot
    submitted_at: AwareDatetime
