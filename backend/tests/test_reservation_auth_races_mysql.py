"""提交与换号登录的锁竞争；仅使用fixture创建的独立MySQL实例。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
import secrets
from threading import Event, local

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import LoginSession, MeetingRoom, Notification, OperationLog, Reservation, User
from app.schemas.auth import LoginRequest
from app.schemas.reservations import ReservationCreate
from app.services import reservations as service
from app.services.booking_time import BEIJING
from app.services.login import InvalidSessionError, login_user, token_digest, utc_now


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="只使用独立临时MySQL实例，不使用开发库",
    ),
]
NOW = datetime(2026, 11, 7, 9, tzinfo=BEIJING)
PASSWORD = "isolated-submission-auth-race-password"


@pytest.fixture(scope="module")
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def refs(engine, encoded_password, monkeypatch):
    monkeypatch.setattr(service, "get_beijing_now", lambda: NOW)
    suffix = secrets.token_hex(8)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with Session(engine) as database, database.begin():
        user = User(
            username=f"race_user_{suffix}", full_name="隔离申请人", identity_type="student",
            password_hash=encoded_password, role="user", is_active=True, session_version=1,
        )
        admin = User(
            username=f"race_admin_{suffix}", full_name="隔离管理员", identity_type=None,
            password_hash=encoded_password, role="admin", is_active=True, session_version=1,
        )
        room = MeetingRoom(
            name=f"race_room_{suffix}", location="隔离楼101", capacity=10,
            equipment=["白板"], is_active=True,
        )
        database.add_all([user, admin, room])
        database.flush()
        real_now = utc_now()
        record = LoginSession(
            user_id=user.id, token_hash=token_digest(token), csrf_token=csrf,
            session_version=1, created_at=real_now, expires_at=real_now + timedelta(days=3650),
        )
        database.add(record)
        result = dict(user=user.id, admin=admin.id, admin_name=admin.username, room=room.id,
                      token=token, csrf=csrf)
    return result


def submit(database, refs):
    return service.create_reservation(
        database,
        ReservationCreate.model_validate(dict(
            room_id=refs["room"], title="隔离提交", purpose="检查事务和认证竞争",
            attendees=5, date="2026-11-08", start_time="10:00", end_time="11:00",
            required_equipment=["白板"],
        )),
        user_id=refs["user"], raw_token=refs["token"], csrf_token=refs["csrf"],
    )


def assert_no_business_rows(engine, refs):
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(Reservation).where(
            Reservation.user_id == refs["user"],
        )) == 0
        event_ids = select(OperationLog.id).where(OperationLog.actor_user_id == refs["user"])
        assert database.scalar(select(func.count()).select_from(Notification).where(
            Notification.event_id.in_(event_ids),
        )) == 0
        assert database.scalar(select(func.count()).select_from(OperationLog).where(
            OperationLog.actor_user_id == refs["user"],
        )) == 0


def test_admin_switch_login_can_revoke_previous_cookie_before_waiting_submit_locks_session(engine, refs):
    admin_locked = Event()
    submit_reached_admin = Event()
    allow_login_to_continue = Event()
    worker = local()

    def after_sql(connection, cursor, statement, parameters, context, executemany):
        sql = statement.lower()
        if getattr(worker, "kind", None) == "login" and "from users" in sql and "for update" in sql:
            admin_locked.set()
            assert allow_login_to_continue.wait(5), "未收到释放登录事务的测试信号"

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        sql = statement.lower()
        # SQLAlchemy在MySQL8探测后使用FOR SHARE，未探测的方言可使用旧等价语法。
        if getattr(worker, "kind", None) == "submit" and (
            "for share" in sql or "lock in share mode" in sql
        ):
            submit_reached_admin.set()

    def switch_login():
        worker.kind = "login"
        with Session(engine) as database:
            return login_user(
                database, LoginRequest(username=refs["admin_name"], password=PASSWORD),
                previous_token=refs["token"],
            )

    def waiting_submit():
        worker.kind = "submit"
        with Session(engine) as database:
            try:
                submit(database, refs)
            except InvalidSessionError:
                return "invalid_session"
        return "unexpected_success"

    event.listen(engine, "before_cursor_execute", before_sql)
    event.listen(engine, "after_cursor_execute", after_sql)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            login_future = pool.submit(switch_login)
            try:
                assert admin_locked.wait(5), "换号登录未取得管理员行锁"
                submit_future = pool.submit(waiting_submit)
                assert submit_reached_admin.wait(5), "预约未到达收件人行锁"
                allow_login_to_continue.set()
                assert login_future.result(timeout=8).info.user.id == refs["admin"]
                assert submit_future.result(timeout=8) == "invalid_session"
            finally:
                allow_login_to_continue.set()
    finally:
        event.remove(engine, "before_cursor_execute", before_sql)
        event.remove(engine, "after_cursor_execute", after_sql)
    assert_no_business_rows(engine, refs)
    with Session(engine) as database:
        previous = database.scalar(select(LoginSession).where(
            LoginSession.token_hash == token_digest(refs["token"]),
        ))
        assert previous.revoked_at is not None
