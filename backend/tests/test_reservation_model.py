"""预约固定词汇、独立快照字段和UTC转换；不访问任何数据库或本机配置。"""

from datetime import UTC, datetime
import json

import pytest
from sqlalchemy import JSON
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import ColumnProperty

from app.core.reservation_enums import (
    RESERVATION_STATUS_LABELS, TERMINAL_RESERVATION_STATUSES,
    ReservationSource, ReservationStatus,
)
from app.models import Reservation
from app.services.booking_time import BEIJING, to_utc_naive, utc_naive_to_beijing


def test_statuses_have_only_the_seven_confirmed_values_and_public_labels():
    assert {status.value for status in ReservationStatus} == {
        "pending", "approved", "rejected", "cancelled", "expired", "revoked", "finished",
    }
    assert RESERVATION_STATUS_LABELS == {
        ReservationStatus.PENDING: "未审批",
        ReservationStatus.APPROVED: "已批准",
        ReservationStatus.REJECTED: "已驳回",
        ReservationStatus.CANCELLED: "已取消",
        ReservationStatus.EXPIRED: "已过期",
        ReservationStatus.REVOKED: "已撤销",
        ReservationStatus.FINISHED: "已结束",
    }
    assert "in_progress" not in {status.value for status in ReservationStatus}


def test_only_five_confirmed_terminal_statuses_are_marked_terminal():
    assert TERMINAL_RESERVATION_STATUSES == frozenset({
        ReservationStatus.REJECTED, ReservationStatus.CANCELLED, ReservationStatus.EXPIRED,
        ReservationStatus.REVOKED, ReservationStatus.FINISHED,
    })
    assert ReservationStatus.PENDING not in TERMINAL_RESERVATION_STATUSES
    assert ReservationStatus.APPROVED not in TERMINAL_RESERVATION_STATUSES


def test_sources_are_page_or_assistant_and_enums_serialize_as_business_strings():
    assert {source.value for source in ReservationSource} == {"page", "assistant"}
    result = json.loads(json.dumps({
        "status": ReservationStatus.PENDING, "source": ReservationSource.PAGE,
    }))
    assert result == {"status": "pending", "source": "page"}


@pytest.mark.parametrize("aware, stored", [
    (datetime(2026, 11, 8, 8, tzinfo=BEIJING), datetime(2026, 11, 8, 0)),
    (datetime(2026, 11, 8, 0, 30, tzinfo=BEIJING), datetime(2026, 11, 7, 16, 30)),
    (
        datetime(2026, 11, 7, 23, 59, 59, 123456, tzinfo=UTC),
        datetime(2026, 11, 7, 23, 59, 59, 123456),
    ),
])
def test_utc_storage_round_trip_preserves_instant_date_boundary_and_microseconds(aware, stored):
    converted = to_utc_naive(aware)
    assert converted == stored and converted.tzinfo is None
    restored = utc_naive_to_beijing(converted)
    assert restored.tzinfo == BEIJING
    assert restored == aware
    assert restored.microsecond == aware.microsecond
    assert restored.replace(tzinfo=None) == aware.astimezone(BEIJING).replace(tzinfo=None)


def test_utc_writer_rejects_a_naive_datetime_instead_of_guessing_its_timezone():
    with pytest.raises(ValueError):
        to_utc_naive(datetime(2026, 11, 8, 8))


@pytest.mark.parametrize("timezone", [UTC, BEIJING])
def test_utc_reader_rejects_an_aware_value_instead_of_reinterpreting_it(timezone):
    with pytest.raises(ValueError):
        utc_naive_to_beijing(datetime(2026, 11, 8, 8, tzinfo=timezone))


def test_room_snapshots_are_required_independent_columns_not_current_room_relations():
    mapper = Reservation.__mapper__
    for name in (
        "room_name_snapshot", "room_location_snapshot",
        "room_capacity_snapshot", "room_equipment_snapshot",
    ):
        attribute = mapper.attrs[name]
        assert isinstance(attribute, ColumnProperty)
        column = attribute.columns[0]
        assert column.nullable is False
        assert not column.foreign_keys
    assert isinstance(Reservation.__table__.c.room_equipment_snapshot.type, JSON)


def test_identity_links_restrict_delete_and_time_columns_preserve_microseconds():
    table = Reservation.__table__
    for field, target in (("user_id", "users.id"), ("room_id", "meeting_rooms.id")):
        foreign_key, = table.c[field].foreign_keys
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == "RESTRICT"
    for field in ("starts_at", "ends_at", "submitted_at", "updated_at"):
        column_type = table.c[field].type
        assert isinstance(column_type, DATETIME)
        assert column_type.fsp == 6
        assert column_type.timezone is False
    for field in ("status", "source"):
        assert table.c[field].type.collation == "utf8mb4_0900_as_cs"
