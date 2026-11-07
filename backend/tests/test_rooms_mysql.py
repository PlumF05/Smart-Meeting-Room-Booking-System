"""会议室查询与预置的真实MySQL验证；仅访问fixture建立的独立实例。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Barrier

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api import rooms as room_api
from app.cli import seed_rooms as seed_cli
from app.core.errors import DatabaseUnavailableError
from app.core.security import hash_password
from app.db.session import get_db
from app.main import app
from app.models import LoginSession, MeetingRoom, User
from app.services.login import utc_now
from app.services.room_seed import DEFAULT_ROOMS, RoomSeedConflictError, seed_rooms


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例，不得访问开发库",
    ),
]

PASSWORD = "rooms-mysql-test-only-password"
ROOM_FIELDS = {"id", "name", "location", "capacity", "equipment", "is_active"}
SPECIAL_EQUIPMENT = '带"引号\\字符%_装置'


@pytest.fixture(scope="module")
def accounts(engine):
    encoded = hash_password(PASSWORD)
    result = {}
    with Session(engine) as database, database.begin():
        for label, role, identity in (
            ("teacher", "user", "teacher"),
            ("student", "user", "student"),
            ("admin", "admin", None),
        ):
            user = User(
                username=f"room_test_{label}", full_name="会议室隔离测试账号",
                password_hash=encoded, role=role, identity_type=identity,
                is_active=True, session_version=1,
            )
            database.add(user)
            database.flush()
            result[label] = {"id": user.id, "username": user.username}
    return result


@pytest.fixture
def room_database(engine, isolated_mysql_settings):
    # 仅清理该模块独立测试实例中的会议室，不读取开发配置、不执行TRUNCATE。
    assert isolated_mysql_settings.name == "smrbs_test"
    with Session(engine) as database, database.begin():
        database.execute(delete(MeetingRoom))
    yield engine
    with Session(engine) as database, database.begin():
        database.execute(delete(MeetingRoom))


@pytest.fixture
def client(room_database):
    factory = sessionmaker(bind=room_database, autoflush=False, expire_on_commit=False)

    def isolated_db():
        with factory() as database:
            yield database

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def login(client, accounts, label="teacher"):
    response = client.post("/api/auth/login", json={
        "username": accounts[label]["username"], "password": PASSWORD,
    })
    assert response.status_code == 200


@pytest.fixture
def rooms(room_database):
    specifications = [
        (20, 20, ["投影仪", "视频会议"], True),
        (10, 10, ["投影仪", "白板"], True),
        (40, 40, ["投影仪", "白板", SPECIAL_EQUIPMENT], True),
        (30, 30, ["投影仪高清", "白板"], False),
    ]
    with Session(room_database) as database, database.begin():
        database.add_all([
            MeetingRoom(
                id=room_id, name=f"隔离房间{room_id}", location=f"隔离测试楼{room_id}",
                capacity=capacity, equipment=equipment, is_active=active,
            )
            for room_id, capacity, equipment, active in specifications
        ])
    return [10, 20, 30, 40]


def room_snapshot(engine):
    with Session(engine) as database:
        return list(database.execute(select(MeetingRoom.__table__).order_by(MeetingRoom.id)).all())


def ids(response):
    assert response.status_code == 200
    return [item["id"] for item in response.json()]


@pytest.mark.parametrize("label", ["teacher", "student", "admin"])
def test_both_roles_can_read_current_room_details_without_private_or_availability_fields(
    client, accounts, rooms, room_database, label,
):
    login(client, accounts, label)
    before = room_snapshot(room_database)
    response = client.get("/api/rooms")
    assert ids(response) == rooms
    assert response.headers["cache-control"] == "no-store"
    assert all(set(item) == ROOM_FIELDS for item in response.json())
    disabled = client.get("/api/rooms/30")
    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    assert set(disabled.json()) == ROOM_FIELDS
    assert disabled.headers["cache-control"] == "no-store"
    assert room_snapshot(room_database) == before
    assert PASSWORD not in response.text + disabled.text


def test_anonymous_cannot_read_list_or_detail(client, rooms):
    for path in ("/api/rooms", "/api/rooms/10"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "隔离房间" not in response.text


@pytest.mark.parametrize("invalidated_by", ["expired", "disabled"])
def test_invalidated_session_cannot_continue_querying(
    client, accounts, rooms, room_database, invalidated_by,
):
    login(client, accounts)
    user_id = accounts["teacher"]["id"]
    with Session(room_database) as database, database.begin():
        if invalidated_by == "expired":
            database.execute(
                update(LoginSession).where(LoginSession.user_id == user_id)
                .values(
                    created_at=utc_now() - timedelta(hours=9),
                    expires_at=utc_now() - timedelta(seconds=1),
                )
            )
        else:
            database.get(User, user_id).is_active = False
    try:
        assert client.get("/api/rooms").status_code == 401
        assert client.get("/api/rooms/10").status_code == 401
    finally:
        if invalidated_by == "disabled":
            with Session(room_database) as database, database.begin():
                database.get(User, user_id).is_active = True


@pytest.mark.parametrize("query, expected", [
    ({"attendees": "10"}, [10, 20, 30, 40]),
    ({"attendees": "11"}, [20, 30, 40]),
    ({"attendees": "41"}, []),
    ({"attendees": "2147483647"}, []),
    ({"is_active": "true"}, [10, 20, 40]),
    ({"is_active": "false"}, [30]),
])
def test_capacity_boundary_and_resource_state_filters(client, accounts, rooms, query, expected):
    login(client, accounts)
    assert ids(client.get("/api/rooms", params=query)) == expected


@pytest.mark.parametrize("equipment, expected", [
    (["投影仪"], [10, 20, 40]),
    (["投影仪", "白板"], [10, 40]),
    (["白板", "白板"], [10, 30, 40]),
    (["  白板  "], [10, 30, 40]),
    ([SPECIAL_EQUIPMENT], [40]),
    (["投影仪') OR 1=1 --"], []),
    (["未配备的设备"], []),
])
def test_all_equipment_must_match_exact_json_values_without_sql_interpolation(
    client, accounts, rooms, room_database, equipment, expected,
):
    login(client, accounts)
    before = room_snapshot(room_database)
    query = [("equipment", item) for item in equipment]
    assert ids(client.get("/api/rooms", params=query)) == expected
    assert room_snapshot(room_database) == before


def test_multiple_filter_types_are_combined(client, accounts, rooms):
    login(client, accounts)
    response = client.get("/api/rooms", params=[
        ("attendees", "15"), ("equipment", "投影仪"),
        ("equipment", "白板"), ("is_active", "true"),
    ])
    assert ids(response) == [40]


def test_empty_database_returns_empty_list_without_creating_demo_data(client, accounts, room_database):
    login(client, accounts)
    assert ids(client.get("/api/rooms")) == []
    assert room_snapshot(room_database) == []


@pytest.mark.parametrize("query", [
    [("attendees", "0")], [("attendees", "-1")], [("attendees", "1.5")],
    [("attendees", "abc")], [("attendees", "2147483648")],
    [("equipment", "")], [("equipment", "  ")], [("equipment", "设" * 51)],
    [("equipment", str(number)) for number in range(11)],
    [("is_active", "unknown-state")], [("date", "2026-11-08")],
    [("availability", "true")],
])
def test_invalid_or_unimplemented_query_parameters_are_not_silently_ignored(
    client, accounts, rooms, query,
):
    login(client, accounts)
    response = client.get("/api/rooms", params=query)
    assert response.status_code == 422
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("room_id", ["0", "-1", "1.5", "not-an-id", "9223372036854775808"])
def test_invalid_detail_identifier_returns_422(client, accounts, rooms, room_id):
    login(client, accounts)
    assert client.get(f"/api/rooms/{room_id}").status_code == 422


def test_unknown_room_including_valid_bigint_limit_returns_404(client, accounts, rooms):
    login(client, accounts)
    for room_id in (999, 9223372036854775807):
        response = client.get(f"/api/rooms/{room_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "会议室不存在。"}


def test_query_database_error_is_sanitized_at_http_boundary(
    client, accounts, rooms, monkeypatch, caplog,
):
    login(client, accounts)
    marker = "synthetic-private-database-marker"

    def unavailable(*args, **kwargs):
        raise DatabaseUnavailableError(marker)

    monkeypatch.setattr(room_api, "list_rooms", unavailable)
    response = client.get("/api/rooms")
    assert response.status_code == 503
    assert marker not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"


def test_rooms_have_no_web_creation_editing_or_deletion_in_this_small_goal(client, accounts):
    login(client, accounts, "admin")
    for method, path in (("post", "/api/rooms"), ("put", "/api/rooms/1"), ("delete", "/api/rooms/1")):
        assert getattr(client, method)(path).status_code == 405


def test_seed_is_repeatable_and_preserves_renamed_disabled_maintained_rooms(room_database):
    with Session(room_database) as database:
        first = seed_rooms(database)
    assert (first.created, first.skipped) == (6, 0)
    with Session(room_database) as database, database.begin():
        room = database.get(MeetingRoom, DEFAULT_ROOMS[0].id)
        room.name = "已改名的会议室"
        room.location = "已维护的地点"
        room.capacity = 17
        room.equipment = ["已维护的设备"]
        room.is_active = False
    before = room_snapshot(room_database)
    with Session(room_database) as database:
        repeated = seed_rooms(database)
    assert (repeated.created, repeated.skipped) == (0, 6)
    assert room_snapshot(room_database) == before


def test_seed_fills_missing_identifier_without_overwriting_existing_rooms(room_database):
    with Session(room_database) as database, database.begin():
        database.add(MeetingRoom(
            id=DEFAULT_ROOMS[0].id, name="编号已存在的自定义房间", location="维护地点",
            capacity=99, equipment=["维护设备"], is_active=False,
        ))
    with Session(room_database) as database:
        result = seed_rooms(database)
    assert (result.created, result.skipped) == (5, 1)
    with Session(room_database) as database:
        room = database.get(MeetingRoom, DEFAULT_ROOMS[0].id)
        assert room.name == "编号已存在的自定义房间" and room.capacity == 99
        assert room.equipment == ["维护设备"] and room.is_active is False


def test_seed_name_conflict_leaves_existing_data_and_inserts_nothing(room_database):
    with Session(room_database) as database, database.begin():
        database.add(MeetingRoom(
            id=1000, name=DEFAULT_ROOMS[-1].name, location="已有房间",
            capacity=15, equipment=[], is_active=True,
        ))
    before = room_snapshot(room_database)
    with Session(room_database) as database, pytest.raises(RoomSeedConflictError):
        seed_rooms(database)
    assert room_snapshot(room_database) == before


def test_concurrent_seed_commands_keep_exactly_one_row_per_fixed_identifier(room_database):
    start = Barrier(2)

    def seed_in_independent_session():
        with Session(room_database) as database:
            start.wait(timeout=10)
            return seed_rooms(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: seed_in_independent_session(), range(2)))
    assert sum(result.created for result in results) == 6
    assert sum(result.skipped for result in results) == 6
    with Session(room_database) as database:
        assert list(database.scalars(select(MeetingRoom.id).order_by(MeetingRoom.id))) == [1, 2, 3, 4, 5, 6]


def test_failed_seed_commit_rolls_back_all_inserted_rooms(room_database):
    marker = "synthetic-seed-commit-error"

    def fail_commit(database):
        raise SQLAlchemyError(marker)

    event.listen(Session, "before_commit", fail_commit)
    try:
        with Session(room_database) as database, pytest.raises(DatabaseUnavailableError) as error:
            seed_rooms(database)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert marker not in str(error.value)
    assert room_snapshot(room_database) == []


def test_cli_uses_only_explicit_test_engine_and_can_be_run_twice(
    room_database, monkeypatch, capsys,
):
    monkeypatch.setattr(seed_cli, "create_database_engine", lambda: room_database)
    assert seed_cli.main([]) == 0
    assert "新增6间" in capsys.readouterr().out
    assert seed_cli.main([]) == 0
    assert "新增0间" in capsys.readouterr().out
    with Session(room_database) as database:
        assert database.scalar(select(func.count()).select_from(MeetingRoom)) == 6
