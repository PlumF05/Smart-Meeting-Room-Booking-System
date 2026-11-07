"""时间查询接口与真实登录的集成；只使用fixture的独立MySQL实例。"""

from datetime import UTC, datetime, timedelta
import os

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api import rooms as room_api
from app.core.security import hash_password
from app.db.session import get_db
from app.main import app
from app.models import LoginSession, MeetingRoom, User
from app.services.booking_time import BEIJING
from app.services.login import utc_now
from app.services.room_seed import seed_rooms


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例，不得访问开发库",
    ),
]

PASSWORD = "room-window-mysql-only-Password-2026"
NOW = datetime(2026, 11, 7, 9, tzinfo=BEIJING)
EXPECTED_FIELDS = {
    "timezone", "server_now", "min_date", "max_date", "opens_at", "closes_at",
    "slot_minutes", "min_duration_minutes", "max_duration_minutes",
    "time_rules_passed", "availability_checked", "window",
}


@pytest.fixture(scope="module")
def accounts(engine):
    encoded = hash_password(PASSWORD)
    result = {}
    with Session(engine) as database, database.begin():
        for label, role, identity in (("student", "user", "student"), ("admin", "admin", None)):
            user = User(
                username=f"window_test_{label}", full_name="时间窗口隔离测试账号",
                password_hash=encoded, role=role, identity_type=identity,
                is_active=True, session_version=1,
            )
            database.add(user)
            database.flush()
            result[label] = {"id": user.id, "username": user.username}
    with Session(engine) as database:
        seed_rooms(database)
    return result


@pytest.fixture
def client(engine, accounts):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def isolated_db():
        with factory() as database:
            yield database

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[room_api.get_beijing_now] = lambda: NOW
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def login(client, accounts, role="student"):
    response = client.post("/api/auth/login", json={
        "username": accounts[role]["username"], "password": PASSWORD,
    })
    assert response.status_code == 200


def valid_query(**changes):
    result = {"date": "2026-11-08", "start_time": "10:00", "end_time": "11:00"}
    result.update(changes)
    return result


def database_snapshot(engine):
    with Session(engine) as database:
        return {
            "rooms": list(database.execute(select(MeetingRoom.__table__).order_by(MeetingRoom.id)).all()),
            "users": database.scalar(select(func.count()).select_from(User)),
            "sessions": list(database.execute(select(LoginSession.__table__).order_by(LoginSession.id)).all()),
        }


@pytest.mark.parametrize("role", ["student", "admin"])
def test_both_roles_read_server_rules_without_claiming_availability_or_writing(engine, client, accounts, role):
    login(client, accounts, role)
    before = database_snapshot(engine)
    response = client.get("/api/rooms/query-window")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == EXPECTED_FIELDS
    assert data["timezone"] == "Asia/Shanghai"
    assert datetime.fromisoformat(data["server_now"]) == NOW
    assert data["min_date"] == "2026-11-07" and data["max_date"] == "2026-11-13"
    assert (data["opens_at"], data["closes_at"]) == ("08:00", "22:00")
    assert (data["slot_minutes"], data["min_duration_minutes"], data["max_duration_minutes"]) == (30, 30, 240)
    assert data["time_rules_passed"] is False and data["window"] is None
    assert data["availability_checked"] is False
    assert PASSWORD not in response.text
    assert database_snapshot(engine) == before


@pytest.mark.parametrize("role", ["student", "admin"])
def test_valid_window_is_aware_beijing_but_does_not_reserve_a_room(engine, client, accounts, role):
    login(client, accounts, role)
    before = database_snapshot(engine)
    response = client.get("/api/rooms/query-window", params=valid_query())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == EXPECTED_FIELDS
    assert data["time_rules_passed"] is True and data["availability_checked"] is False
    assert data["window"] == {
        "date": "2026-11-08", "start_time": "10:00", "end_time": "11:00",
        "starts_at": "2026-11-08T10:00:00+08:00", "ends_at": "2026-11-08T11:00:00+08:00",
    }
    assert database_snapshot(engine) == before


def test_anonymous_cannot_read_rules_or_validate_a_window(client):
    for params in ({}, valid_query()):
        response = client.get("/api/rooms/query-window", params=params)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "server_now" not in response.json()


@pytest.mark.parametrize("condition", ["expired", "disabled"])
def test_invalid_session_cannot_validate_even_a_legal_time(engine, client, accounts, condition):
    login(client, accounts)
    user_id = accounts["student"]["id"]
    with Session(engine) as database, database.begin():
        if condition == "disabled":
            database.get(User, user_id).is_active = False
        else:
            current = utc_now()
            database.execute(
                update(LoginSession).where(LoginSession.user_id == user_id)
                .values(created_at=current - timedelta(hours=2), expires_at=current - timedelta(hours=1))
            )
    try:
        assert client.get("/api/rooms/query-window", params=valid_query()).status_code == 401
    finally:
        if condition == "disabled":
            with Session(engine) as database, database.begin():
                database.get(User, user_id).is_active = True


@pytest.mark.parametrize("query", [
    {"date": "2026-11-08"}, {"start_time": "10:00"}, {"end_time": "11:00"},
    {"date": "2026-11-08", "start_time": "10:00"},
    {"date": "2026-11-08", "end_time": "11:00"},
    {"start_time": "10:00", "end_time": "11:00"},
    valid_query(date="2026-02-30"), valid_query(date="2026-11-08T00:00:00"),
    valid_query(start_time="10:00:00"), valid_query(start_time="10:00+08:00"),
    valid_query(end_time="24:00"), {"now": "2000-01-01T00:00:00Z"},
    {"attendees": "10"},
])
def test_invalid_format_partial_and_unknown_parameters_are_rejected(client, accounts, query):
    login(client, accounts)
    response = client.get("/api/rooms/query-window", params=query)
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert isinstance(errors, list)
    assert all("input" not in item and "ctx" not in item for item in errors)


@pytest.mark.parametrize("query", [
    valid_query(date="2026-11-06"), valid_query(date="2026-11-14"),
    valid_query(start_time="07:30", end_time="08:00"),
    valid_query(start_time="21:30", end_time="22:30"),
    valid_query(start_time="10:15"), valid_query(start_time="12:00", end_time="11:00"),
    valid_query(start_time="10:00", end_time="14:30"),
    valid_query(date="2026-11-07", start_time="09:00", end_time="10:00"),
])
def test_current_business_rules_fail_with_safe_specific_explanation(client, accounts, query):
    login(client, accounts)
    response = client.get("/api/rooms/query-window", params=query)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert isinstance(response.json()["detail"], str) and response.json()["detail"]
    assert PASSWORD not in response.text


def test_clock_is_sampled_once_and_normalized_before_building_rule_dates(client, accounts):
    login(client, accounts)
    calls = []

    def clock():
        calls.append(True)
        # UTC日期仍为7日，但北京时间已为8日。
        return datetime(2026, 11, 7, 17, tzinfo=UTC)

    app.dependency_overrides[room_api.get_beijing_now] = clock
    response = client.get("/api/rooms/query-window", params=valid_query(start_time="08:00", end_time="12:00"))
    assert response.status_code == 200
    data = response.json()
    assert calls == [True]
    assert data["server_now"] == "2026-11-08T01:00:00+08:00"
    assert data["min_date"] == "2026-11-08" and data["max_date"] == "2026-11-14"


def test_same_input_is_rechecked_against_current_server_time(client, accounts):
    login(client, accounts)
    query = valid_query(date="2026-11-07", start_time="10:00", end_time="11:00")
    assert client.get("/api/rooms/query-window", params=query).status_code == 200
    app.dependency_overrides[room_api.get_beijing_now] = lambda: datetime(2026, 11, 7, 10, tzinfo=BEIJING)
    assert client.get("/api/rooms/query-window", params=query).status_code == 422


def test_existing_room_list_keeps_its_own_contract_and_still_returns_real_data(client, accounts):
    login(client, accounts)
    response = client.get("/api/rooms")
    assert response.status_code == 200 and len(response.json()) == 6
    assert all(set(room) == {"id", "name", "location", "capacity", "equipment", "is_active"} for room in response.json())
    assert client.get("/api/rooms", params=valid_query()).status_code == 422
    assert client.get("/api/rooms/1").status_code == 200
