"""会议室查询输入和故障安全；仅用虚构数据，不读取开发配置或连接数据库。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cli import seed_rooms as seed_cli
from app.core.errors import DatabaseUnavailableError
from app.schemas.rooms import RoomInfo, RoomSearch
from app.services.room_seed import RoomSeedConflictError, RoomSeedResult, seed_rooms
from app.services.rooms import RoomNotFoundError, get_room, list_rooms


def test_search_accepts_query_strings_and_normalizes_equipment_names():
    search = RoomSearch(attendees="12", equipment=["  投影仪  ", "白板"], is_active="false")
    assert search.attendees == 12
    assert search.equipment == ["投影仪", "白板"]
    assert search.is_active is False
    assert RoomSearch().attendees is None and RoomSearch().is_active is None


@pytest.mark.parametrize("fields", [
    {"attendees": 0}, {"attendees": -1}, {"attendees": "1.5"},
    {"attendees": "not-a-number"}, {"attendees": 2147483648},
    {"equipment": ["  "]}, {"equipment": ["设" * 51]},
    {"equipment": ["白板"] * 11}, {"is_active": "not-a-boolean"},
    {"date": "2026-11-08"}, {"start_time": "10:00"},
])
def test_search_rejects_invalid_or_unimplemented_conditions(fields):
    with pytest.raises(ValidationError):
        RoomSearch(**fields)


def test_room_response_whitelist_does_not_claim_availability_or_expose_extra_attributes():
    marker = "synthetic-private-room-marker"
    room = SimpleNamespace(
        id=1, name="测试会议室", location="教学楼101", capacity=12,
        equipment=["白板"], is_active=False,
        created_at="not-a-response-field", password_hash=marker, availability="available",
    )
    result = RoomInfo.model_validate(room).model_dump()
    assert set(result) == {"id", "name", "location", "capacity", "equipment", "is_active"}
    assert result["is_active"] is False
    assert marker not in repr(result)


@pytest.mark.parametrize("operation", ["list", "detail", "seed"])
def test_database_exceptions_do_not_escape_with_original_details(operation, caplog):
    database = MagicMock(spec=Session)
    marker = "synthetic-private-database-connection-marker"
    failure = SQLAlchemyError(marker)
    if operation == "list":
        database.scalars.side_effect = failure
        call = lambda: list_rooms(database, RoomSearch())
    elif operation == "detail":
        database.get.side_effect = failure
        call = lambda: get_room(database, 1)
    else:
        database.scalars.side_effect = failure
        call = lambda: seed_rooms(database)
    with pytest.raises(DatabaseUnavailableError) as error:
        call()
    assert marker not in str(error.value)
    assert marker not in caplog.text


def test_missing_room_uses_business_not_found_error():
    database = MagicMock(spec=Session)
    database.get.return_value = None
    with pytest.raises(RoomNotFoundError):
        get_room(database, 123)


def test_seed_cli_rejects_arguments_before_loading_database(monkeypatch, capsys):
    factory = MagicMock(side_effect=AssertionError("不能读取开发数据库配置"))
    monkeypatch.setattr(seed_cli, "create_database_engine", factory)
    marker = "synthetic-command-line-secret"
    assert seed_cli.main(["--password", marker]) == 2
    factory.assert_not_called()
    output = capsys.readouterr()
    assert marker not in output.out + output.err


@pytest.mark.parametrize("error_type", [SQLAlchemyError, ValueError, OSError])
def test_seed_cli_factory_failure_does_not_print_exception_details(
    monkeypatch, capsys, error_type,
):
    marker = "synthetic-private-config-marker"
    monkeypatch.setattr(seed_cli, "create_database_engine", MagicMock(side_effect=error_type(marker)))
    assert seed_cli.main([]) == 1
    output = capsys.readouterr()
    assert output.err
    assert marker not in output.out + output.err


@pytest.mark.parametrize("error_type", [DatabaseUnavailableError, RoomSeedConflictError])
def test_seed_cli_service_failure_disposes_engine_and_keeps_error_private(
    monkeypatch, capsys, error_type,
):
    engine = MagicMock()
    marker = "synthetic-private-service-marker"
    monkeypatch.setattr(seed_cli, "create_database_engine", lambda: engine)
    monkeypatch.setattr(seed_cli, "Session", MagicMock())
    monkeypatch.setattr(seed_cli, "seed_rooms", MagicMock(side_effect=error_type(marker)))
    assert seed_cli.main([]) == 1
    engine.dispose.assert_called_once_with()
    output = capsys.readouterr()
    assert marker not in output.out + output.err


def test_seed_cli_success_reports_counts_and_releases_engine(monkeypatch, capsys):
    engine = MagicMock()
    monkeypatch.setattr(seed_cli, "create_database_engine", lambda: engine)
    monkeypatch.setattr(seed_cli, "Session", MagicMock())
    monkeypatch.setattr(seed_cli, "seed_rooms", MagicMock(return_value=RoomSeedResult(6, 0)))
    assert seed_cli.main([]) == 0
    engine.dispose.assert_called_once_with()
    output = capsys.readouterr()
    assert "6" in output.out and "0" in output.out
