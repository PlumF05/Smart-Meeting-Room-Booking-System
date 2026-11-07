"""预约时间格式与校验结果；时间规则通过不等于会议室空闲。"""

from datetime import date as Date, datetime, time as Time
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RoomWindowQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    date: Date | None = Field(default=None, description="北京时间的使用日期，格式YYYY-MM-DD；填写时须同时提供起止时间。")
    start_time: Time | None = Field(default=None, description="北京时间的开始时刻，严格HH:mm格式，例如08:00。")
    end_time: Time | None = Field(default=None, description="同一天的结束时刻，严格HH:mm格式，例如08:30。")

    @field_validator("date", mode="before")
    @classmethod
    def calendar_date(cls, value: object) -> Date | None:
        if value is None or type(value) is Date:
            return value
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError("日期须为YYYY-MM-DD格式")
        try:
            return Date.fromisoformat(value)
        except ValueError:
            raise ValueError("请提供有效的日历日期") from None

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def clock_time(cls, value: object) -> Time | None:
        if value is None:
            return None
        if type(value) is Time:
            if value.tzinfo is None and value.second == 0 and value.microsecond == 0:
                return value
        elif isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            return Time.fromisoformat(value)
        raise ValueError("时间须为HH:mm格式，不包含秒或时区偏移")

    @model_validator(mode="after")
    def all_or_none(self) -> Self:
        fields = (self.date, self.start_time, self.end_time)
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError("日期、开始时间和结束时间需要一起填写")
        return self


class RoomWindowInfo(BaseModel):
    date: Date
    start_time: str
    end_time: str
    starts_at: datetime
    ends_at: datetime


class RoomWindowCheck(BaseModel):
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    server_now: datetime
    min_date: Date
    max_date: Date
    opens_at: str
    closes_at: str
    slot_minutes: int
    min_duration_minutes: int
    max_duration_minutes: int
    time_rules_passed: bool
    availability_checked: Literal[False] = False
    window: RoomWindowInfo | None = None
