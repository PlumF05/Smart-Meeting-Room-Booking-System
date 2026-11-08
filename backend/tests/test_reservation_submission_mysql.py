"""提交预约的真实事务与竞争测试；仅使用fixture新建的独立MySQL实例。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
import secrets
from threading import Barrier, Event

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api import reservations as reservation_api
from app.core.security import hash_password
from app.db.session import get_db
from app.main import app
from app.models import AssistantConfirmation, LoginSession, MeetingRoom, Notification, OperationLog, Reservation, User
from app.schemas.reservations import ReservationCreate
from app.services import login as login_service, reservations as service
from app.services.booking_time import BEIJING, BookingTimeError, to_utc_naive


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="必须显式启用fixture独立MySQL实例，不得使用开发库",
    ),
]
NOW = datetime(2026, 11, 7, 9, tzinfo=BEIJING)
NOW_UTC = to_utc_naive(NOW)
PASSWORD = "submission-isolated-only-Password-2026"
COOKIE = "smrbs_session"


@pytest.fixture(scope="module")
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def world(engine, encoded_password, monkeypatch):
    # engine由conftest创建且在本模块独占；删除仅针对这一个随机端口上的临时库。
    with Session(engine) as database, database.begin():
        for model in (AssistantConfirmation, Notification, OperationLog, Reservation, LoginSession, MeetingRoom, User):
            database.execute(delete(model))
    monkeypatch.setattr(service, "get_beijing_now", lambda: NOW)
    monkeypatch.setattr(login_service, "utc_now", lambda: NOW_UTC)
    result = {"users": {}, "rooms": []}
    with Session(engine) as database, database.begin():
        for label, role, identity, active in (
            ("student", "user", "student", True), ("teacher", "user", "teacher", True),
            ("admin", "admin", None, True), ("inactive_admin", "admin", None, False),
        ):
            user = User(
                username="submit_" + label + "_" + secrets.token_hex(4), full_name="隔离提交账号",
                password_hash=encoded_password, role=role, identity_type=identity,
                is_active=active, session_version=1,
            )
            database.add(user)
            database.flush()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            database.add(LoginSession(
                token_hash=login_service.token_digest(token), csrf_token=csrf, user_id=user.id,
                session_version=1, created_at=NOW_UTC - timedelta(hours=1),
                expires_at=NOW_UTC + timedelta(days=10),
            ))
            result["users"][label] = {"id": user.id, "username": user.username, "token": token, "csrf": csrf}
        for index in range(10):
            room = MeetingRoom(
                name=f"隔离房间{index}", location=f"实验楼{index + 101}", capacity=12,
                equipment=["白板", "投影仪"], is_active=True,
            )
            database.add(room)
            database.flush()
            result["rooms"].append({"id": room.id, "name": room.name, "location": room.location})
    return result


@pytest.fixture
def client(engine, world):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

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


def body(world, room_index=0, **changes):
    values = dict(
        room_id=world["rooms"][room_index]["id"], title="  课程小组讨论  ",
        purpose="  讨论课程设计任务  ", attendees=12, date="2026-11-08",
        start_time="10:00", end_time="11:00", required_equipment=["白板"],
    )
    values.update(changes)
    return values


def bind_user(client, world, label="student"):
    user = world["users"][label]
    client.cookies.clear()
    client.cookies.set(COOKIE, user["token"], domain="testserver.local", path="/")
    return {"X-CSRF-Token": user["csrf"]}


def submit(engine, world, *, label="student", room_index=0, **changes):
    user = world["users"][label]
    with Session(engine, autoflush=False, expire_on_commit=False) as database:
        return service.create_reservation(
            database, ReservationCreate(**body(world, room_index, **changes)), user_id=user["id"],
            raw_token=user["token"], csrf_token=user["csrf"],
        )


def counts(engine):
    with Session(engine) as database:
        return tuple(database.scalar(select(func.count()).select_from(model))
                     for model in (Reservation, OperationLog, Notification))


@pytest.mark.parametrize("label", ["student", "teacher"])
def test_submit_saves_pending_snapshot_event_and_all_admin_messages_atomically(
    engine, world, client, label, encoded_password, caplog,
):
    headers = bind_user(client, world, label)
    response = client.post("/api/reservations", json=body(world), headers=headers)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["user_id"] == world["users"][label]["id"]
    assert (data["status"], data["source"]) == ("pending", "page")
    assert data["title"] == "课程小组讨论" and data["purpose"] == "讨论课程设计任务"
    assert data["starts_at"] == "2026-11-08T10:00:00+08:00"
    assert data["ends_at"] == "2026-11-08T11:00:00+08:00"
    assert data["submitted_at"] == NOW.isoformat()
    assert data["room_snapshot"] == {
        "name": world["rooms"][0]["name"], "location": world["rooms"][0]["location"],
        "capacity": 12, "equipment": ["白板", "投影仪"],
    }
    assert counts(engine) == (1, 1, 2)
    with Session(engine) as database:
        record = database.get(Reservation, data["id"])
        assert record.starts_at == datetime(2026, 11, 8, 2)
        assert record.ends_at == datetime(2026, 11, 8, 3)
        assert record.submitted_at == NOW_UTC and record.updated_at == NOW_UTC
        assert record.required_equipment == ["白板"] and record.status_reason is None
        operation = database.scalar(select(OperationLog))
        assert (operation.event_key, operation.actor_kind, operation.actor_user_id) == (
            f"reservation:{record.id}:submit", "user", record.user_id,
        )
        assert (operation.object_type, operation.object_id, operation.action) == ("reservation", record.id, "submit")
        assert (operation.source, operation.before_status, operation.after_status, operation.result) == (
            "page", None, "pending", "success",
        )
        messages = database.scalars(select(Notification)).all()
        assert {notice.recipient_user_id for notice in messages} == {
            world["users"]["admin"]["id"], world["users"]["inactive_admin"]["id"],
        }
        assert all(notice.event_id == operation.id and notice.read_at is None for notice in messages)
        room = database.get(MeetingRoom, record.room_id)
        room.name, room.capacity, room.equipment = "后续改名", 20, []
        database.commit()
    with Session(engine) as database:
        saved = database.get(Reservation, data["id"])
        assert saved.room_name_snapshot == data["room_snapshot"]["name"]
        assert saved.room_capacity_snapshot == 12 and saved.room_equipment_snapshot == ["白板", "投影仪"]
    for value in (PASSWORD, encoded_password, world["users"][label]["token"], world["users"][label]["csrf"]):
        assert value not in response.text and value not in caplog.text
    assert not {"password", "password_hash", "token", "csrf_token", "recipients"} & data.keys()


def test_anonymous_and_administrator_cannot_submit(engine, world, client):
    assert client.post("/api/reservations", json=body(world)).status_code == 401
    headers = bind_user(client, world, "admin")
    response = client.post("/api/reservations", json=body(world), headers=headers)
    assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize("csrf", [None, "A" * 43, "invalid", "非ASCII"])
def test_csrf_failure_creates_nothing(engine, world, client, csrf):
    bind_user(client, world)
    headers = {} if csrf is None else {"X-CSRF-Token": csrf}
    if csrf == "非ASCII":
        # HTTP头必须ASCII；通过业务层验证格式，不要求HTTP客户端发送非法头。
        user = world["users"]["student"]
        with Session(engine) as database, pytest.raises(login_service.InvalidCsrfError):
            service.create_reservation(database, ReservationCreate(**body(world)), user_id=user["id"],
                                       raw_token=user["token"], csrf_token=csrf)
    else:
        assert client.post("/api/reservations", json=body(world), headers=headers).status_code == 403
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_origin_rejected_even_with_valid_csrf(engine, world, client, headers):
    headers = {**bind_user(client, world), **headers}
    assert client.post("/api/reservations", json=body(world), headers=headers).status_code == 403
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize("change,code", [
    ({"room_id": 9223372036854775807}, 404), ({"attendees": 13}, 422),
    ({"required_equipment": ["白板", "视频会议终端"]}, 422),
    ({"date": "2026-11-14"}, 422), ({"start_time": "10:15"}, 422),
    ({"date": "2026-11-07", "start_time": "09:00", "end_time": "10:00"}, 422),
    ({"user_id": 1}, 422), ({"status": "approved"}, 422), ({"source": "assistant"}, 422),
])
def test_invalid_resource_rule_and_server_controlled_fields_create_nothing(engine, world, client, change, code):
    response = client.post("/api/reservations", json=body(world, **change), headers=bind_user(client, world))
    assert response.status_code == code
    assert response.headers["cache-control"] == "no-store"
    if isinstance(response.json()["detail"], list):
        assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
    assert counts(engine) == (0, 0, 0)


def test_current_disabled_room_rejected(engine, world, client):
    with Session(engine) as database, database.begin():
        database.get(MeetingRoom, world["rooms"][0]["id"]).is_active = False
    response = client.post("/api/reservations", json=body(world), headers=bind_user(client, world))
    assert response.status_code == 409 and "停用" in response.json()["detail"]
    assert counts(engine) == (0, 0, 0)


def test_no_administrator_does_not_block_valid_pending_booking(engine, world):
    with Session(engine) as database, database.begin():
        ids = [world["users"][name]["id"] for name in ("admin", "inactive_admin")]
        database.execute(delete(LoginSession).where(LoginSession.user_id.in_(ids)))
        database.execute(delete(User).where(User.id.in_(ids)))
    assert submit(engine, world).status == "pending"
    assert counts(engine) == (1, 1, 0)


def test_room_and_cross_room_personal_conflicts_are_safe_and_adjacent_is_allowed(engine, world, client):
    submit(engine, world)
    response = client.post("/api/reservations", json=body(world), headers=bind_user(client, world, "teacher"))
    assert response.status_code == 409 and "当前会议室" in response.json()["detail"]
    assert "课程小组讨论" not in response.text and "隔离提交账号" not in response.text
    response = client.post("/api/reservations", json=body(world, 1), headers=bind_user(client, world))
    assert response.status_code == 409 and "您在" in response.json()["detail"]
    # 两种相邻边界都不重叠，同一人也可紧接着使用。
    submit(engine, world, start_time="09:00", end_time="10:00")
    submit(engine, world, start_time="11:00", end_time="12:00")
    assert counts(engine) == (3, 3, 6)


def test_repeated_post_is_conflict_without_extra_event_or_message(engine, world, client):
    headers = bind_user(client, world)
    first = client.post("/api/reservations", json=body(world), headers=headers)
    second = client.post("/api/reservations", json=body(world), headers=headers)
    assert first.status_code == 201 and second.status_code == 409
    assert counts(engine) == (1, 1, 2)


def add_existing(engine, world, *, status, starts_at, ends_at, label="teacher", room_index=0):
    with Session(engine) as database, database.begin():
        record = Reservation(
            user_id=world["users"][label]["id"], room_id=world["rooms"][room_index]["id"],
            title="原有记录", purpose="占位边界测试", attendees=1, status=status, source="page",
            starts_at=to_utc_naive(starts_at), ends_at=to_utc_naive(ends_at), required_equipment=[],
            room_name_snapshot="旧名称", room_location_snapshot="旧位置", room_capacity_snapshot=12,
            room_equipment_snapshot=[],
        )
        database.add(record)
        database.flush()
        return record.id


@pytest.mark.parametrize("status", ["pending", "approved"])
def test_future_effective_holds_block_room(engine, world, status):
    add_existing(engine, world, status=status, starts_at=NOW + timedelta(days=1, hours=1),
                 ends_at=NOW + timedelta(days=1, hours=2))
    with pytest.raises(service.RoomConflictError):
        submit(engine, world)
    assert counts(engine) == (1, 0, 0)


@pytest.mark.parametrize("status", ["rejected", "cancelled", "expired", "revoked", "finished"])
def test_terminal_records_do_not_hold_room(engine, world, status):
    old = add_existing(engine, world, status=status, starts_at=NOW + timedelta(days=1, hours=1),
                       ends_at=NOW + timedelta(days=1, hours=2))
    assert submit(engine, world).status == "pending"
    with Session(engine) as database:
        assert database.get(Reservation, old).status == status


@pytest.mark.parametrize("status,blocks", [("pending", False), ("approved", True)])
def test_started_pending_is_ignored_but_in_progress_approved_still_holds(engine, world, status, blocks):
    old = add_existing(engine, world, status=status, starts_at=NOW,
                       ends_at=NOW + timedelta(hours=3))
    if blocks:
        with pytest.raises(service.RoomConflictError):
            submit(engine, world, date="2026-11-07", start_time="10:00", end_time="11:00")
    else:
        submit(engine, world, date="2026-11-07", start_time="10:00", end_time="11:00")
    with Session(engine) as database:
        assert database.get(Reservation, old).status == status  # 本轮不执行时间状态worker。


def test_clock_sampled_once_after_locks(engine, world, monkeypatch):
    calls = []

    def clock():
        calls.append(True)
        return NOW

    monkeypatch.setattr(service, "get_beijing_now", clock)
    submit(engine, world)
    assert calls == [True]


@pytest.mark.parametrize("stage", ["after_event", "notification_insert", "commit"])
def test_event_message_and_commit_failure_roll_back_whole_submission(engine, world, client, monkeypatch, stage, caplog):
    original = service.append_operation
    listener = None
    target = None
    name = None
    if stage == "after_event":
        def fail_after_event(database, entry, messages):
            original(database, entry, [])
            raise SQLAlchemyError("synthetic private SQL and parameter failure")

        monkeypatch.setattr(service, "append_operation", fail_after_event)
    elif stage == "notification_insert":
        def fail_notification(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().startswith("INSERT INTO notifications"):
                raise SQLAlchemyError("synthetic private notification failure")

        target, name, listener = engine, "before_cursor_execute", fail_notification
    else:
        def fail_commit(database):
            # 只影响提交预约事务；认证查询的短事务不能被此故障替换。
            records = database.scalars(select(Reservation.id)).all()
            if records:
                raise SQLAlchemyError("synthetic private commit failure")

        target, name, listener = Session, "before_commit", fail_commit
    if listener:
        event.listen(target, name, listener)
    try:
        response = client.post("/api/reservations", json=body(world), headers=bind_user(client, world))
    finally:
        if listener:
            event.remove(target, name, listener)
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "数据库暂不可用，请稍后重试。"}
    assert "synthetic private" not in response.text and "synthetic private" not in caplog.text
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize("change,code", [
    ("disabled", 401), ("role", 403), ("revoked", 401), ("version", 401), ("expired", 401),
])
def test_authentication_rechecked_after_dependency_read(engine, world, client, monkeypatch, change, code):
    original = reservation_api.create_reservation
    user_id = world["users"]["student"]["id"]

    def change_after_auth(*args, **kwargs):
        with Session(engine) as database, database.begin():
            user = database.get(User, user_id)
            record = database.scalar(select(LoginSession).where(LoginSession.user_id == user_id))
            if change == "disabled":
                user.is_active = False
            elif change == "role":
                user.role = "admin"
            elif change == "revoked":
                record.revoked_at = NOW_UTC
            elif change == "version":
                user.session_version += 1
            else:
                record.expires_at = NOW_UTC
        return original(*args, **kwargs)

    monkeypatch.setattr(reservation_api, "create_reservation", change_after_auth)
    response = client.post("/api/reservations", json=body(world), headers=bind_user(client, world))
    assert response.status_code == code
    assert counts(engine) == (0, 0, 0)


def test_token_of_another_user_cannot_authorize_claimed_applicant(engine, world):
    student, teacher = world["users"]["student"], world["users"]["teacher"]
    with Session(engine) as database, pytest.raises(login_service.InvalidSessionError):
        service.create_reservation(
            database, ReservationCreate(**body(world)), user_id=student["id"],
            raw_token=teacher["token"], csrf_token=teacher["csrf"],
        )
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize("change", ["room", "user", "session"])
def test_preloaded_orm_cache_does_not_hide_new_resource_or_authentication_state(engine, world, change):
    student = world["users"]["student"]
    with Session(engine, autoflush=False, expire_on_commit=False) as cached:
        with cached.begin():
            old_user = cached.get(User, student["id"])
            old_room = cached.get(MeetingRoom, world["rooms"][0]["id"])
            old_session = cached.scalar(select(LoginSession).where(LoginSession.user_id == student["id"]))
        with Session(engine) as other, other.begin():
            if change == "room":
                other.get(MeetingRoom, old_room.id).capacity = 1
            elif change == "user":
                other.get(User, student["id"]).is_active = False
            else:
                other.get(LoginSession, old_session.id).revoked_at = NOW_UTC
        expected = service.ReservationRuleError if change == "room" else login_service.InvalidSessionError
        with pytest.raises(expected):
            service.create_reservation(
                cached, ReservationCreate(**body(world)), user_id=student["id"],
                raw_token=student["token"], csrf_token=student["csrf"],
            )
        # 强引用保证测试确实覆盖同一ORM identity map中的旧对象。
        assert old_user.id == student["id"] and old_room.id == world["rooms"][0]["id"]
        assert old_session.user_id == student["id"]
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize("change", ["start_passed", "capacity", "equipment", "disabled"])
def test_waiting_for_room_lock_rechecks_time_and_latest_room_values(engine, world, monkeypatch, change):
    waiting = Event()
    acquired = Event()
    clock_called = Event()
    current = [NOW]

    def clock():
        clock_called.set()
        return current[0]

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if "FROM meeting_rooms" in statement and "FOR UPDATE" in statement:
            waiting.set()

    def after_execute(connection, cursor, statement, parameters, context, executemany):
        if "FROM meeting_rooms" in statement and "FOR UPDATE" in statement:
            acquired.set()

    monkeypatch.setattr(service, "get_beijing_now", clock)
    with Session(engine) as blocker:
        blocker.begin()
        room = blocker.scalar(select(MeetingRoom).where(MeetingRoom.id == world["rooms"][0]["id"]).with_for_update())
        event.listen(engine, "before_cursor_execute", before_execute)
        event.listen(engine, "after_cursor_execute", after_execute)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    submit, engine, world, date="2026-11-07", start_time="10:00", end_time="11:00",
                )
                try:
                    assert waiting.wait(5), "提交没有到达会议室行锁"
                    assert not acquired.is_set() and not clock_called.is_set()
                    if change == "start_passed":
                        current[0] = NOW + timedelta(hours=1)
                    elif change == "capacity":
                        room.capacity = 1
                    elif change == "equipment":
                        room.equipment = []
                    else:
                        room.is_active = False
                    blocker.commit()
                finally:
                    # 断言失败也须先释放锁，避免executor等待挂起线程。
                    blocker.rollback()
                expected = {
                    "start_passed": BookingTimeError, "capacity": service.ReservationRuleError,
                    "equipment": service.ReservationRuleError, "disabled": service.RoomInactiveError,
                }[change]
                with pytest.raises(expected):
                    future.result(timeout=10)
        finally:
            event.remove(engine, "before_cursor_execute", before_execute)
            event.remove(engine, "after_cursor_execute", after_execute)
    assert clock_called.is_set() and counts(engine) == (0, 0, 0)


def add_applicants(engine, world, count):
    with Session(engine) as database, database.begin():
        for index in range(count):
            user = User(
                username="race_" + secrets.token_hex(6), password_hash="synthetic-unused-login-hash",
                full_name="竞争测试申请人", role="user", identity_type="student", is_active=True, session_version=1,
            )
            database.add(user)
            database.flush()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            database.add(LoginSession(
                token_hash=login_service.token_digest(token), csrf_token=csrf, user_id=user.id,
                session_version=1, created_at=NOW_UTC - timedelta(hours=1),
                expires_at=NOW_UTC + timedelta(days=1),
            ))
            world["users"][f"race{index}"] = {"id": user.id, "token": token, "csrf": csrf}


@pytest.mark.parametrize("competition", ["same_room", "same_user"])
def test_ten_independent_connections_create_exactly_one_hold(engine, world, competition):
    if competition == "same_room":
        add_applicants(engine, world, 8)
        labels = ["student", "teacher", *[f"race{index}" for index in range(8)]]
        expected_conflict = service.RoomConflictError
    else:
        labels = ["student"] * 10
        expected_conflict = service.PersonalConflictError
    barrier = Barrier(10)
    connections = set()

    def worker(index):
        user = world["users"][labels[index]]
        with engine.connect() as connection, Session(
            bind=connection, autoflush=False, expire_on_commit=False,
        ) as database:
            # 每个线程实际持有不同MySQL连接，随后结束探测事务再进入业务事务。
            with database.begin():
                connection_id = database.scalar(select(func.connection_id()))
                barrier.wait(timeout=10)
            try:
                result = service.create_reservation(
                    database, ReservationCreate(**body(world, index if competition == "same_user" else 0)),
                    user_id=user["id"], raw_token=user["token"], csrf_token=user["csrf"],
                )
                return connection_id, result.id
            except expected_conflict:
                return connection_id, None

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, index) for index in range(10)]
        results = [future.result(timeout=30) for future in futures]
    connections.update(connection_id for connection_id, _ in results)
    assert len(connections) == 10
    assert sum(identifier is not None for _, identifier in results) == 1
    assert counts(engine) == (1, 1, 2)


def test_different_users_different_rooms_can_both_submit(engine, world):
    barrier = Barrier(2)

    def worker(label, room_index):
        barrier.wait(timeout=5)
        return submit(engine, world, label=label, room_index=room_index)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, "student", 0), executor.submit(worker, "teacher", 1)]
        assert all(future.result(timeout=15).status == "pending" for future in futures)
    assert counts(engine) == (2, 2, 4)

