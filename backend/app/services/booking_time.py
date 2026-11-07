"""查询与之后预约提交共用的北京时间规则；不读取或创建预约占位。"""

from dataclasses import dataclass
from datetime import UTC, date as Date, datetime, time as Time, timedelta
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
BOOKING_DAYS = 7
OPENS_AT = Time(8)
CLOSES_AT = Time(22)
SLOT_MINUTES = 30
MIN_DURATION_MINUTES = 30
MAX_DURATION_MINUTES = 240


class BookingTimeError(Exception):
    """可向用户展示的固定业务提示，不携带原始请求。"""


@dataclass(frozen=True)
class BookingWindow:
    date: Date
    start_time: Time
    end_time: Time
    starts_at: datetime
    ends_at: datetime


def get_beijing_now() -> datetime:
    return datetime.now(BEIJING)


def as_beijing_now(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("业务时钟必须提供带时区的时间")
    return now.astimezone(BEIJING)


def to_utc_naive(value: datetime) -> datetime:
    """将明确带时区的时刻转换为项目DATETIME列所保存的无时区UTC值。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("写入UTC时间前必须提供明确的时区")
    return value.astimezone(UTC).replace(tzinfo=None)


def utc_naive_to_beijing(value: datetime) -> datetime:
    """读取约定为UTC的数据库值；先标明UTC，再转换北京展示时间。"""
    if value.tzinfo is not None:
        raise ValueError("数据库UTC时间应为无时区值")
    return value.replace(tzinfo=UTC).astimezone(BEIJING)


def validate_booking_window(
    date: Date, start_time: Time, end_time: Time, *, now: datetime | None = None,
) -> BookingWindow:
    current = as_beijing_now(now if now is not None else get_beijing_now())
    today = current.date()
    if not today <= date <= today + timedelta(days=BOOKING_DAYS - 1):
        raise BookingTimeError("使用日期只能是北京时间的今天及之后6天。")
    if any(value.tzinfo is not None or value.second or value.microsecond for value in (start_time, end_time)):
        raise BookingTimeError("起止时间须为北京时间，且按30分钟对齐，不包含秒或时区偏移。")
    if not (OPENS_AT <= start_time <= CLOSES_AT and OPENS_AT <= end_time <= CLOSES_AT):
        raise BookingTimeError("每日开放时间为08:00至22:00。")
    if start_time.minute % SLOT_MINUTES or end_time.minute % SLOT_MINUTES:
        raise BookingTimeError("开始和结束时间须按30分钟对齐。")
    if end_time <= start_time:
        raise BookingTimeError("结束时间须晚于开始时间，且不能跨日。")
    starts_at = datetime.combine(date, start_time, tzinfo=BEIJING)
    ends_at = datetime.combine(date, end_time, tzinfo=BEIJING)
    duration = ends_at - starts_at
    if not timedelta(minutes=MIN_DURATION_MINUTES) <= duration <= timedelta(minutes=MAX_DURATION_MINUTES):
        raise BookingTimeError("单次时长须为30分钟至4小时。")
    if starts_at <= current:
        raise BookingTimeError("开始时间必须晚于服务器当前北京时间。")
    return BookingWindow(date, start_time, end_time, starts_at, ends_at)
