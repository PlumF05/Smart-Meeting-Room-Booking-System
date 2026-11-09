"""预约提交边界：请求只含用户可编辑字段，严格检查格式及数据库字段上限。"""

from datetime import date, datetime, time, timezone

from pydantic import ValidationError
import pytest

from app.schemas.reservations import ReservationCreate, ReservationCreated


def valid_request(**changes):
    value = {
        "room_id": 1, "title": "课程讨论", "purpose": "讨论设计方案", "attendees": 8,
        "date": "2026-11-08", "start_time": "09:00", "end_time": "10:00",
        "required_equipment": ["投影仪"],
    }
    return {**value, **changes}


def test_required_request_fields_are_required():
    for field in ("room_id", "attendees", "date", "start_time", "end_time"):
        data = valid_request()
        del data[field]
        with pytest.raises(ValidationError) as error:
            ReservationCreate.model_validate(data)
        assert any(item["loc"] == (field,) and item["type"] == "missing" for item in error.value.errors())


def test_normalizes_text_and_equipment_without_changing_time_semantics():
    data = ReservationCreate.model_validate(valid_request(
        title="  课程讨论  ", purpose="  讨论设计方案\n", required_equipment=[" 投影仪 ", "白板", "投影仪"],
    ))
    assert data.title == "课程讨论"
    assert data.purpose == "讨论设计方案"
    assert data.required_equipment == ["投影仪", "白板"]
    assert data.date == date(2026, 11, 8)
    assert data.start_time == time(9)
    assert data.end_time == time(10)


def test_required_equipment_is_optional_with_independent_empty_default():
    value = valid_request()
    del value["required_equipment"]
    first = ReservationCreate.model_validate(value)
    second = ReservationCreate.model_validate(value)
    first.required_equipment.append("白板")
    assert second.required_equipment == []


@pytest.mark.parametrize("field,value", [
    ("room_id", 0), ("room_id", -1), ("room_id", 9223372036854775808),
    ("room_id", True), ("room_id", 1.0), ("room_id", "1"),
    ("attendees", 0), ("attendees", -1), ("attendees", 2147483648),
    ("attendees", True), ("attendees", 8.0), ("attendees", "8"),
    ("title", "题" * 201), ("title", 1),
    ("purpose", "目" * 1001), ("purpose", 1),
    ("date", None), ("date", "2026-2-08"), ("date", "2026-02-30"),
    ("date", "2026-11-08T00:00:00"), ("date", datetime(2026, 11, 8)),
    ("start_time", None), ("start_time", "9:00"), ("start_time", "09:00:00"),
    ("start_time", "09:00+08:00"), ("start_time", "24:00"), ("start_time", time(9, 0, 1)),
    ("end_time", None), ("end_time", "10:00:00"), ("end_time", "10:00Z"),
    ("required_equipment", None), ("required_equipment", "投影仪"),
    ("required_equipment", [""]), ("required_equipment", ["  "]),
    ("required_equipment", ["设" * 51]), ("required_equipment", [1]),
    ("required_equipment", [f"设备{i}" for i in range(11)]),
])
def test_rejects_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        ReservationCreate.model_validate(valid_request(**{field: value}))


@pytest.mark.parametrize("field,value", [
    ("user_id", 2), ("status", "approved"), ("source", "assistant"),
    ("now", "2026-11-07T00:00:00+08:00"), ("submitted_at", "2026-11-07T00:00:00Z"),
    ("room_capacity_snapshot", 100), ("room_snapshot", {"capacity": 100}),
    ("approval_required", False),
])
def test_does_not_accept_server_controlled_or_unknown_fields(field, value):
    with pytest.raises(ValidationError) as error:
        ReservationCreate.model_validate(valid_request(**{field: value}))
    assert any(item["loc"] == (field,) and item["type"] == "extra_forbidden" for item in error.value.errors())


def test_permits_exact_database_limits():
    data = ReservationCreate.model_validate(valid_request(
        room_id=9223372036854775807, attendees=2147483647,
        title="题" * 200, purpose="目" * 1000, required_equipment=["设" * 50] * 10,
    ))
    assert data.room_id == 9223372036854775807
    assert data.attendees == 2147483647
    assert len(data.title) == 200 and len(data.purpose) == 1000
    assert data.required_equipment == ["设" * 50]


def test_format_schema_does_not_claim_resource_or_time_business_validation():
    # 开始已过去、未对齐30分钟或人数超过真实容量由服务结合服务器时钟及房间重验。
    data = ReservationCreate.model_validate(valid_request(
        date="2020-01-01", start_time="09:01", end_time="09:02", attendees=100,
    ))
    assert data.start_time == time(9, 1)


def test_validation_error_text_hides_submitted_value():
    marker = "private-user-input"
    with pytest.raises(ValidationError) as error:
        ReservationCreate.model_validate(valid_request(room_id=marker))
    assert marker not in str(error.value)


def created_response(**changes):
    return {
        "id": 1, "user_id": 1, "room_id": 1, "title": "课程讨论", "purpose": "讨论设计方案",
        "attendees": 8, "date": date(2026, 11, 8), "start_time": "09:00", "end_time": "10:00",
        "starts_at": datetime(2026, 11, 8, 1, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 11, 8, 2, tzinfo=timezone.utc),
        "status": "pending", "source": "page", "required_equipment": ["投影仪"],
        "room_snapshot": {"name": "一号会议室", "location": "教学楼", "capacity": 10, "equipment": ["投影仪"]},
        "submitted_at": datetime(2026, 11, 7, tzinfo=timezone.utc), **changes,
    }


@pytest.mark.parametrize("field", ["starts_at", "ends_at", "submitted_at"])
def test_creation_response_requires_explicit_timezone(field):
    with pytest.raises(ValidationError):
        ReservationCreated.model_validate(created_response(**{field: datetime(2026, 11, 8)}))


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled", "expired", "revoked", "finished"])
def test_creation_response_cannot_imply_approval_or_terminal_state(status):
    with pytest.raises(ValidationError):
        ReservationCreated.model_validate(created_response(status=status))
