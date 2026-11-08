"""业务记录与站内消息的独立MySQL验证，不读取开发配置或修改开发数据库。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import secrets
from threading import Barrier

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import LoginSession, MeetingRoom, Notification, OperationLog, Reservation, User
from app.schemas.operations import MessageDraft, OperationEntry
from app.services.operations import append_operation


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="必须使用fixture建立的独立MySQL实例",
    ),
]
BACKEND = Path(__file__).resolve().parents[1]
START = datetime(2026, 11, 8, 2)


def unique(prefix="event"):
    return prefix + "_" + secrets.token_hex(6)


def migration_config(connection):
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def old_data(engine):
    with engine.connect() as connection:
        return {
            model.__tablename__: list(connection.execute(select(model.__table__).order_by(model.id)))
            for model in (User, MeetingRoom, LoginSession, Reservation)
        }


def reservation_values(refs):
    return dict(
        user_id=refs["user"], room_id=refs["room"], title="事务隔离预约",
        purpose="验证业务和消息共同提交", attendees=6,
        starts_at=START, ends_at=START + timedelta(hours=1), required_equipment=["白板"],
        room_name_snapshot="隔离会议室", room_location_snapshot="测试楼101",
        room_capacity_snapshot=12, room_equipment_snapshot=["白板"],
    )


@pytest.fixture
def refs(engine):
    with Session(engine) as database, database.begin():
        applicant = User(username=unique("applicant"), password_hash="synthetic-not-a-login-hash",
                         full_name="隔离申请人", identity_type="student")
        recipient = User(username=unique("admin"), password_hash="synthetic-not-a-login-hash",
                         full_name="隔离管理员", role="admin", identity_type=None)
        room = MeetingRoom(name=unique("隔离房间"), location="测试楼101", capacity=12, equipment=["白板"])
        database.add_all([applicant, recipient, room])
        database.flush()
        result = {"user": applicant.id, "recipient": recipient.id, "room": room.id}
        reservation = Reservation(**reservation_values(result))
        database.add(reservation)
        database.flush()
        result["reservation"] = reservation.id
    return result


def event_values(refs, **changes):
    values = dict(
        event_key=unique(), actor_kind="user", actor_user_id=refs["user"],
        object_type="reservation", object_id=refs["reservation"], action="submit", source="page",
        before_status=None, after_status="pending", result="success", reason=None,
    )
    values.update(changes)
    return values


def message(refs, **changes):
    values = dict(recipient_user_id=refs["recipient"], title="有新的预约申请", content="请查看并处理申请。")
    values.update(changes)
    return MessageDraft(**values)


def counts(engine):
    with Session(engine) as database:
        return tuple(database.scalar(select(func.count()).select_from(model))
                     for model in (Reservation, OperationLog, Notification))


def test_0004_and_0005_round_trip_preserves_existing_users_rooms_sessions_and_reservations(engine):
    # 本模块的临时实例独占，回退不会触及开发数据库。
    with engine.connect() as connection:
        command.downgrade(migration_config(connection), "0003")
    with Session(engine) as database, database.begin():
        user = User(username=unique("preserved"), password_hash="synthetic-not-a-login-hash",
                    full_name="迁移保留账号", identity_type="teacher")
        room = MeetingRoom(name=unique("保留房间"), location="测试楼101", capacity=12, equipment=["白板"])
        database.add_all([user, room])
        database.flush()
        database.add_all([
            Reservation(**reservation_values({"user": user.id, "room": room.id})),
            LoginSession(token_hash=secrets.token_hex(32), csrf_token="x" * 43, user_id=user.id,
                         session_version=1, expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)),
        ])
    before = old_data(engine)
    with engine.connect() as connection:
        config = migration_config(connection)
        command.upgrade(config, "0004")
        assert MigrationContext.configure(connection).get_current_revision() == "0004"
        assert {"operation_logs", "notifications"} <= set(inspect(connection).get_table_names())
        connection.rollback()
        command.upgrade(config, "head")
        assert MigrationContext.configure(connection).get_current_revision() == "0005"
        assert "assistant_confirmations" in inspect(connection).get_table_names()
        connection.rollback()
        command.check(config)
    assert old_data(engine) == before
    with engine.connect() as connection:
        config = migration_config(connection)
        command.downgrade(config, "0003")
        assert not {"operation_logs", "notifications", "assistant_confirmations"} & set(inspect(connection).get_table_names())
        connection.rollback()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        connection.rollback()
        command.check(config)
    assert old_data(engine) == before


def test_event_and_notifications_are_invisible_to_other_sessions_until_caller_commits(engine, refs):
    values = event_values(refs)
    with Session(engine) as database, database.begin():
        event = append_operation(database, OperationEntry(**values), [message(refs)])
        event_id = event.id
        assert database.in_transaction() and event_id > 0
        with Session(engine) as observer:
            assert observer.scalar(select(OperationLog.id).where(OperationLog.event_key == values["event_key"])) is None
            assert observer.scalar(select(Notification.id).where(Notification.event_id == event_id)) is None
    with Session(engine) as observer:
        stored = observer.get(OperationLog, event_id)
        notice = observer.scalar(select(Notification).where(Notification.event_id == event_id))
        assert stored.event_key == values["event_key"] and stored.result == "success"
        assert notice.recipient_user_id == refs["recipient"] and notice.read_at is None
        assert notice.title == "有新的预约申请" and notice.content == "请查看并处理申请。"
        assert stored.created_at.tzinfo is None and notice.created_at.tzinfo is None


@pytest.mark.parametrize("failure", ["business_exception", "invalid_message_recipient"])
def test_failed_outer_transaction_rolls_back_reservation_event_and_all_messages(engine, refs, failure):
    before = counts(engine)
    values = event_values(refs)
    error_type = RuntimeError if failure == "business_exception" else DBAPIError
    with pytest.raises(error_type):
        with Session(engine) as database, database.begin():
            reservation = Reservation(**reservation_values(refs))
            database.add(reservation)
            database.flush()
            values["object_id"] = reservation.id
            messages = [message(refs)]
            if failure == "invalid_message_recipient":
                messages.append(message(refs, recipient_user_id=9223372036854775807))
            append_operation(database, OperationEntry(**values), messages)
            if failure == "business_exception":
                raise RuntimeError("synthetic business rollback")
    assert counts(engine) == before
    with Session(engine) as observer:
        assert observer.scalar(select(OperationLog.id).where(OperationLog.event_key == values["event_key"])) is None


def test_closing_a_session_without_commit_discards_flushed_event_and_messages(engine, refs):
    values = event_values(refs)
    before = counts(engine)
    with Session(engine) as database:
        database.begin()
        append_operation(database, OperationEntry(**values), [message(refs)])
    assert counts(engine) == before


def test_reusing_event_key_is_a_database_error_not_a_second_event_or_false_success(engine, refs):
    values = event_values(refs)
    with Session(engine) as database, database.begin():
        event_id = append_operation(database, OperationEntry(**values), [message(refs)]).id
    before = counts(engine)
    with Session(engine) as database, database.begin():
        with pytest.raises(DBAPIError) as error:
            append_operation(database, OperationEntry(**values), [message(refs)])
        assert error.value.orig.args[0] == 1062
        database.rollback()
    assert counts(engine) == before
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(Notification).where(Notification.event_id == event_id)) == 1


def test_concurrent_same_event_key_commits_one_event_and_one_message(engine, refs):
    values = event_values(refs)
    start = Barrier(2)

    def write_in_independent_transaction():
        try:
            with Session(engine) as database, database.begin():
                start.wait(timeout=10)
                event_id = append_operation(database, OperationEntry(**values), [message(refs)]).id
            return "committed", event_id
        except DBAPIError as error:
            # 同一事件的竞争失败方交由外层事务回滚，不能伪装成第二次提交成功。
            assert error.orig.args[0] == 1062
            return "duplicate", None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_in_independent_transaction) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(status for status, _ in results) == ["committed", "duplicate"]
    with Session(engine) as database:
        events = list(database.scalars(select(OperationLog).where(OperationLog.event_key == values["event_key"])))
        assert len(events) == 1
        notices = list(database.scalars(select(Notification).where(Notification.event_id == events[0].id)))
        assert len(notices) == 1 and notices[0].recipient_user_id == refs["recipient"]


def test_one_event_can_notify_multiple_users_but_not_the_same_user_twice(engine, refs):
    with Session(engine) as database, database.begin():
        event_id = append_operation(database, OperationEntry(**event_values(refs)), [
            message(refs), message(refs, recipient_user_id=refs["user"]),
        ]).id
    with Session(engine) as database, database.begin():
        database.add(Notification(event_id=event_id, **message(refs).model_dump()))
        with pytest.raises(DBAPIError) as error:
            database.flush()
        assert error.value.orig.args[0] == 1062
        database.rollback()
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(Notification).where(Notification.event_id == event_id)) == 2


def test_duplicate_recipient_and_failure_notification_guards_write_nothing(engine, refs):
    before = counts(engine)
    with Session(engine) as database, database.begin():
        with pytest.raises(ValueError):
            append_operation(database, OperationEntry(**event_values(refs)), [message(refs), message(refs)])
        with pytest.raises(ValueError):
            append_operation(database, OperationEntry(**event_values(refs, result="failure")), [message(refs)])
    assert counts(engine) == before


def test_system_event_has_no_human_actor_and_successful_rejection_can_notify(engine, refs):
    with Session(engine) as database, database.begin():
        system_id = append_operation(database, OperationEntry(**event_values(
            refs, actor_kind="system", actor_user_id=None, source="system", action="expire",
            before_status="pending", after_status="expired",
        )), [message(refs, recipient_user_id=refs["user"], title="申请已过期")]).id
        rejection_id = append_operation(database, OperationEntry(**event_values(
            refs, actor_user_id=refs["recipient"], action="reject", before_status="pending",
            after_status="rejected", reason="时间安排不合适",
        )), [message(refs, recipient_user_id=refs["user"], title="申请已驳回")]).id
    with Session(engine) as database:
        assert database.get(OperationLog, system_id).actor_user_id is None
        assert database.get(OperationLog, rejection_id).result == "success"
        assert database.scalar(select(func.count()).select_from(Notification).where(Notification.event_id.in_([system_id, rejection_id]))) == 2


@pytest.mark.parametrize("changes", [
    {"event_key": " "}, {"object_id": 0}, {"actor_kind": "USER"},
    {"object_type": "Reservation"}, {"action": "SUBMIT"}, {"source": "PAGE"},
    {"result": "succéss"}, {"actor_user_id": None},
    {"actor_kind": "system", "source": "system"},
    {"actor_kind": "system", "actor_user_id": None, "source": "page"},
    {"source": "system"}, {"before_status": " "}, {"after_status": ""}, {"reason": " "},
])
def test_database_rejects_invalid_fixed_vocabulary_actor_links_and_empty_summaries(engine, refs, changes):
    with Session(engine) as database, database.begin():
        database.add(OperationLog(**event_values(refs, **changes)))
        with pytest.raises(DBAPIError) as error:
            database.flush()
        assert error.value.orig.args[0] == 3819
        database.rollback()


@pytest.mark.parametrize("missing", ["actor", "event", "recipient"])
def test_database_rejects_nonexistent_actor_event_or_message_recipient(engine, refs, missing):
    with Session(engine) as database, database.begin():
        if missing == "actor":
            database.add(OperationLog(**event_values(refs, actor_user_id=9223372036854775807)))
        elif missing == "event":
            database.add(Notification(event_id=9223372036854775807, **message(refs).model_dump()))
        else:
            event = OperationLog(**event_values(refs))
            database.add(event)
            database.flush()
            database.add(Notification(event_id=event.id, **message(refs, recipient_user_id=9223372036854775807).model_dump()))
        with pytest.raises(DBAPIError) as error:
            database.flush()
        assert error.value.orig.args[0] == 1452
        database.rollback()


@pytest.mark.parametrize("target", ["actor", "recipient", "event"])
def test_message_and_audit_history_prevent_parent_deletion_instead_of_cascading(engine, refs, target):
    with Session(engine) as database, database.begin():
        extra_actor = User(username=unique("actor"), password_hash="synthetic-not-a-login-hash", full_name="仅日志引用", identity_type="teacher")
        extra_recipient = User(username=unique("reader"), password_hash="synthetic-not-a-login-hash", full_name="仅消息引用", identity_type="student")
        database.add_all([extra_actor, extra_recipient])
        database.flush()
        actor_id, recipient_id = extra_actor.id, extra_recipient.id
        event_id = append_operation(database, OperationEntry(**event_values(refs, actor_user_id=actor_id)), [message(refs, recipient_user_id=recipient_id)]).id
    with Session(engine) as database, database.begin():
        with pytest.raises(DBAPIError) as error:
            if target == "event":
                database.execute(delete(OperationLog).where(OperationLog.id == event_id))
            else:
                database.execute(delete(User).where(User.id == (actor_id if target == "actor" else recipient_id)))
        assert error.value.orig.args[0] == 1451
        database.rollback()
    with Session(engine) as database:
        assert database.get(OperationLog, event_id) is not None
        assert database.scalar(select(Notification.id).where(Notification.event_id == event_id)) is not None


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_read_at_cannot_precede_message_creation_and_null_means_unread(engine, refs, offset):
    created = datetime(2026, 11, 7, 10)
    with Session(engine) as database, database.begin():
        event = OperationLog(**event_values(refs))
        database.add(event)
        database.flush()
        database.add(Notification(event_id=event.id, **message(refs).model_dump(), created_at=created,
                                  read_at=created + timedelta(microseconds=offset)))
        if offset < 0:
            with pytest.raises(DBAPIError) as error:
                database.flush()
            assert error.value.orig.args[0] == 3819
            database.rollback()
        else:
            database.flush()


@pytest.mark.parametrize("field", ["title", "content"])
def test_database_rejects_blank_message_text(engine, refs, field):
    with Session(engine) as database, database.begin():
        event = OperationLog(**event_values(refs))
        database.add(event)
        database.flush()
        values = message(refs).model_dump()
        values[field] = " "
        database.add(Notification(event_id=event.id, **values))
        with pytest.raises(DBAPIError) as error:
            database.flush()
        assert error.value.orig.args[0] == 3819
        database.rollback()


def test_server_defaults_are_utc_even_when_connection_timezone_is_beijing(engine, refs):
    with engine.connect() as connection:
        original = connection.scalar(text("SELECT @@session.time_zone"))
        try:
            connection.execute(text("SET SESSION time_zone='+08:00'"))
            event_id = connection.execute(OperationLog.__table__.insert().values(**event_values(refs))).inserted_primary_key[0]
            message_id = connection.execute(Notification.__table__.insert().values(event_id=event_id, **message(refs).model_dump())).inserted_primary_key[0]
            now = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
            event_created = connection.scalar(select(OperationLog.created_at).where(OperationLog.id == event_id))
            message_created = connection.scalar(select(Notification.created_at).where(Notification.id == message_id))
            assert 0 <= (now - event_created).total_seconds() < 5
            assert 0 <= (now - message_created).total_seconds() < 5
        finally:
            connection.rollback()
            connection.execute(text("SET SESSION time_zone=:zone"), {"zone": original})
            connection.rollback()


def test_actual_indexes_and_column_collations_match_event_and_inbox_queries(engine):
    with engine.connect() as connection:
        schema = inspect(connection)
        event_indexes = schema.get_indexes("operation_logs")
        message_indexes = schema.get_indexes("notifications")
        assert any(row["unique"] and row["column_names"] == ["event_key"] for row in event_indexes)
        assert any(row["column_names"] == ["object_type", "object_id", "created_at"] for row in event_indexes)
        assert any(row["column_names"] == ["actor_user_id", "created_at"] for row in event_indexes)
        assert any(row["unique"] and row["column_names"] == ["event_id", "recipient_user_id"] for row in message_indexes)
        assert any(row["column_names"] == ["recipient_user_id", "read_at", "created_at"] for row in message_indexes)
        columns = {row.COLUMN_NAME: (row.CHARACTER_SET_NAME, row.COLLATION_NAME)
                   for row in connection.execute(text(
                       "SELECT COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='operation_logs'"
                   ))}
        assert columns["event_key"] == ("ascii", "ascii_bin")
        for name in ("actor_kind", "object_type", "action", "source", "result"):
            assert columns[name][1] == "utf8mb4_0900_as_cs"
