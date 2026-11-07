"""固定时钟验证北京时间规则；不读取本机配置或连接数据库。"""

from datetime import UTC, date, datetime, time, timedelta

from pydantic import ValidationError
import pytest

from app.schemas.booking_time import RoomWindowQuery
from app.services import booking_time
from app.services.booking_time import BEIJING, BookingTimeError, validate_booking_window


TODAY = date(2026, 11, 7)
BEFORE_OPEN = datetime(2026, 11, 7, 7, 59, tzinfo=BEIJING)


@pytest.mark.parametrize("day, start, end", [
    (TODAY, time(8), time(8, 30)),
    (TODAY, time(8), time(12)),
    (TODAY, time(18), time(22)),
    (TODAY + timedelta(days=6), time(21, 30), time(22)),
])
def test_legal_boundaries_return_a_same_day_beijing_window(day, start, end):
    result = validate_booking_window(day, start, end, now=BEFORE_OPEN)
    assert result.date == day
    assert result.start_time == start and result.end_time == end
    assert result.starts_at == datetime.combine(day, start, tzinfo=BEIJING)
    assert result.ends_at == datetime.combine(day, end, tzinfo=BEIJING)
    assert result.starts_at.utcoffset() == timedelta(hours=8)
    assert result.starts_at.date() == result.ends_at.date()


@pytest.mark.parametrize("offset", [-1, 7])
def test_only_today_through_day_six_is_inside_the_booking_window(offset):
    with pytest.raises(BookingTimeError):
        validate_booking_window(TODAY + timedelta(days=offset), time(10), time(11), now=BEFORE_OPEN)


@pytest.mark.parametrize("start, end", [
    (time(7, 30), time(8)), (time(21, 30), time(22, 30)),
    (time(8, 15), time(9)), (time(8), time(9, 15)),
    (time(8), time(8)), (time(10), time(9)),
    (time(21), time(8)), (time(8), time(12, 30)),
    (time(8, 0, 1), time(9)), (time(8), time(9, 0, 0, 1)),
    (time(8, tzinfo=UTC), time(9)), (time(8), time(9, tzinfo=BEIJING)),
])
def test_outside_hours_alignment_duration_and_cross_day_inputs_are_rejected(start, end):
    with pytest.raises(BookingTimeError) as error:
        validate_booking_window(TODAY, start, end, now=BEFORE_OPEN)
    assert str(error.value)


def test_start_must_be_strictly_later_than_current_time_to_microsecond():
    just_before = datetime(2026, 11, 7, 9, 59, 59, 999999, tzinfo=BEIJING)
    assert validate_booking_window(TODAY, time(10), time(10, 30), now=just_before)
    for now in (
        datetime(2026, 11, 7, 10, tzinfo=BEIJING),
        datetime(2026, 11, 7, 10, 0, 0, 1, tzinfo=BEIJING),
    ):
        with pytest.raises(BookingTimeError):
            validate_booking_window(TODAY, time(10), time(10, 30), now=now)


def test_utc_clock_is_converted_before_deciding_today_and_the_seventh_day():
    # UTC的7日17点已是北京8日1点；窗口为8日至14日。
    now = datetime(2026, 11, 7, 17, tzinfo=UTC)
    for day in (date(2026, 11, 8), date(2026, 11, 14)):
        result = validate_booking_window(day, time(8), time(9), now=now)
        assert result.starts_at.date() == day
    for day in (date(2026, 11, 7), date(2026, 11, 15)):
        with pytest.raises(BookingTimeError):
            validate_booking_window(day, time(8), time(9), now=now)


def test_naive_clock_is_a_programming_error_rather_than_a_guessed_timezone():
    with pytest.raises(ValueError):
        validate_booking_window(TODAY, time(10), time(11), now=datetime(2026, 11, 7, 9))


def test_default_clock_is_read_once_and_can_be_controlled_without_os_clock_changes(monkeypatch):
    calls = []

    def now():
        calls.append(True)
        return BEFORE_OPEN

    monkeypatch.setattr(booking_time, "get_beijing_now", now)
    assert validate_booking_window(TODAY, time(8), time(8, 30)).date == TODAY
    assert calls == [True]


@pytest.mark.parametrize("day, now", [
    (date(2026, 10, 1), datetime(2026, 9, 30, 12, tzinfo=BEIJING)),
    (date(2026, 11, 8), BEFORE_OPEN),
])
def test_holidays_and_weekends_follow_the_same_confirmed_rules(day, now):
    assert validate_booking_window(day, time(8), time(12), now=now).date == day


def test_query_accepts_no_window_or_a_complete_calendar_and_minute_pair():
    empty = RoomWindowQuery()
    assert empty.date is None and empty.start_time is None and empty.end_time is None
    complete = RoomWindowQuery(date="2026-11-08", start_time="08:00", end_time="09:30")
    assert complete.date == date(2026, 11, 8)
    assert complete.start_time == time(8) and complete.end_time == time(9, 30)


@pytest.mark.parametrize("fields", [
    {"date": "2026-11-08"}, {"start_time": "08:00"}, {"end_time": "09:00"},
    {"date": "2026-11-08", "start_time": "08:00"},
    {"date": "2026-11-08", "end_time": "09:00"},
    {"start_time": "08:00", "end_time": "09:00"},
])
def test_partial_query_is_rejected_instead_of_silently_ignoring_conditions(fields):
    with pytest.raises(ValidationError):
        RoomWindowQuery(**fields)


@pytest.mark.parametrize("field, invalid", [
    ("date", "2026-02-30"), ("date", "2026-2-01"),
    ("date", "2026-11-08T00:00:00"), ("date", "20261108"),
    ("date", 1794096000), ("date", datetime(2026, 11, 8)),
    ("start_time", "8:00"), ("start_time", "08:00:00"),
    ("start_time", "08:00+08:00"), ("end_time", "24:00"),
    ("end_time", "09:60"), ("end_time", 36000),
    ("end_time", time(9, tzinfo=UTC)), ("end_time", time(9, 0, 1)),
])
def test_query_requires_unambiguous_calendar_dates_and_wall_clock_minutes(field, invalid):
    fields = {"date": "2026-11-08", "start_time": "08:00", "end_time": "09:00"}
    fields[field] = invalid
    with pytest.raises(ValidationError):
        RoomWindowQuery(**fields)


def test_query_cannot_supply_a_fake_server_clock_or_other_unknown_parameters():
    marker = "synthetic-not-a-real-secret"
    with pytest.raises(ValidationError) as error:
        RoomWindowQuery(now=marker)
    assert marker not in str(error.value)
