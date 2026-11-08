"""消息和管理收尾输入；不接受客户端指定身份、消息收件人或角色。"""
from datetime import date, timedelta
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from app.schemas.booking_time import RoomWindowQuery


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserStateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    is_active: bool


class StatisticsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    start_date: date
    end_date: date
    room_id: int | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def date_format(cls, value):
        return RoomWindowQuery.calendar_date(value)

    @model_validator(mode="after")
    def supported_range(self):
        if self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        if self.start_date.year < 1000 or self.end_date >= date.max - timedelta(days=1):
            raise ValueError("日期超出支持范围")
        if self.room_id is not None and not 1 <= self.room_id <= 9223372036854775807:
            raise ValueError("会议室编号不符合要求")
        return self
