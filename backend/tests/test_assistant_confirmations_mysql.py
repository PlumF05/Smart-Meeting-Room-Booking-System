"""8.4确认凭证边界、真实MySQL并发与事务；只操作fixture隔离实例。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import secrets
from threading import Barrier, Lock

from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from conftest import migration_config
from test_reservation_submission_mysql import world, encoded_password, body, NOW
from app.core.errors import DatabaseUnavailableError
from app.models import AssistantConfirmation, LoginSession, MeetingRoom, User
from app.services import assistant_confirmations as service
from app.services.booking_time import to_utc_naive
from app.services.login import InvalidCsrfError, InvalidSessionError, token_digest
from app.services.reservations import ReservationPermissionError

pytestmark = pytest.mark.mysql


@pytest.fixture(autouse=True)
def clock_and_cleanup(engine, monkeypatch):
    with Session(engine) as db, db.begin():
        db.execute(delete(AssistantConfirmation))
    clock = [NOW]
    monkeypatch.setattr(service, "get_beijing_now", lambda: clock[0])
    return clock


def credentials(world, label="student", **changes):
    user = world["users"][label]
    result = dict(user_id=user["id"], raw_token=user["token"], csrf_token=user["csrf"],
                  conversation_id="isolation-conversation")
    result.update(changes)
    return result


def prepare(engine, world, *, revision=1, payload=None, **changes):
    with Session(engine) as db:
        return service.prepare_confirmation(db, revision=revision,
            payload=body(world) if payload is None else payload, **credentials(world, **changes))


def inspect(engine, world, record, **changes):
    with Session(engine) as db:
        return service.inspect_confirmation(db, confirmation_id=record["confirmation_id"],
                                             **credentials(world, **changes))


def consume(engine, world, record, action, **changes):
    with Session(engine) as db:
        return service.consume_confirmation(db, confirmation_id=record["confirmation_id"], action=action,
                                            **credentials(world, **changes))


def table_count(engine, model):
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(model))


def test_0005_round_trip_preserves_old_tables_and_matches_metadata(engine, world):
    before = {model.__tablename__:table_count(engine,model) for model in (User,MeetingRoom,LoginSession)}
    with engine.connect() as connection:
        config = migration_config(connection)
        assert MigrationContext.configure(connection).get_current_revision() == ScriptDirectory.from_config(config).get_current_head()
        connection.rollback()
        command.downgrade(migration_config(connection), "0004")
        command.upgrade(migration_config(connection), "head")
        command.upgrade(migration_config(connection), "head")
        connection.rollback()
        command.check(migration_config(connection))
    assert before == {model.__tablename__:table_count(engine,model) for model in (User,MeetingRoom,LoginSession)}


def test_prepare_retry_preserves_original_window_and_hides_credentials(engine,world,clock_and_cleanup):
    first = prepare(engine,world)
    clock_and_cleanup[0] += timedelta(seconds=120)
    repeated = prepare(engine,world)
    assert first == repeated and first["state"] == "pending"
    from datetime import datetime
    assert (datetime.fromisoformat(first["expires_at"])-datetime.fromisoformat(first["created_at"])).total_seconds()==300
    assert first["summary"]["start_time"] == "10:00" and first["summary"]["title"] == "课程小组讨论"
    public=json.dumps(first,ensure_ascii=False)
    for secret in (world["users"]["student"]["token"],world["users"]["student"]["csrf"]):
        assert secret not in public
    assert "session_token_hash" not in public and "payload_hash" not in public
    assert table_count(engine,AssistantConfirmation)==1
    with Session(engine) as db:
        stored=db.scalar(select(AssistantConfirmation))
        assert stored.session_token_hash==token_digest(world["users"]["student"]["token"])


@pytest.mark.parametrize("delta,allowed",[(299.999999,True),(300,False),(301,False)])
def test_exact_five_minute_boundary(engine,world,clock_and_cleanup,delta,allowed):
    record=prepare(engine,world)
    clock_and_cleanup[0]+=timedelta(seconds=delta)
    callbacks=[]
    def action(db,payload):callbacks.append(True);return {"reservation_id":123}
    if allowed:
        assert consume(engine,world,record,action)["state"]=="consumed"
    else:
        assert inspect(engine,world,record)["state"]=="expired"
        assert prepare(engine,world)==inspect(engine,world,record)
        with pytest.raises(service.ConfirmationError,match="五分钟"):
            consume(engine,world,record,action)
        assert not callbacks


@pytest.mark.parametrize("change",["revision","payload"])
def test_changed_plan_supersedes_old_and_replay_cannot_revive(engine,world,change):
    old=prepare(engine,world)
    updated=prepare(engine,world,revision=2 if change=="revision" else 1,
                    payload=body(world,title="新摘要") if change=="payload" else None)
    assert updated["confirmation_id"]!=old["confirmation_id"]
    assert inspect(engine,world,old)["state"]=="superseded"
    assert prepare(engine,world)["state"]=="superseded"
    with pytest.raises(service.ConfirmationError,match="方案已变化"):
        consume(engine,world,old,lambda *a:{})


def test_invalidation_happens_before_new_model_result(engine,world):
    old=prepare(engine,world)
    with Session(engine) as db:
        service.invalidate_conversation(db,**credentials(world))
    assert inspect(engine,world,old)["state"]=="superseded"
    with pytest.raises(service.ConfirmationError):
        consume(engine,world,old,lambda *a:{})


def test_owner_conversation_session_and_revision_binding(engine,world):
    record=prepare(engine,world)
    with pytest.raises(service.ConfirmationError):
        inspect(engine,world,record,**credentials(world,"teacher"))
    with pytest.raises(service.ConfirmationError):
        inspect(engine,world,record,conversation_id="another-conversation")
    token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    with Session(engine) as db,db.begin():
        db.add(LoginSession(token_hash=token_digest(token),csrf_token=csrf,user_id=world["users"]["student"]["id"],
            session_version=1,created_at=to_utc_naive(NOW),expires_at=to_utc_naive(NOW)+timedelta(days=1)))
    with pytest.raises(service.ConfirmationError):
        inspect(engine,world,record,raw_token=token,csrf_token=csrf)
    with Session(engine) as db:
        with pytest.raises(service.ConfirmationError,match="方案已变化"):
            service.inspect_confirmation(db,confirmation_id=record["confirmation_id"],revision=2,**credentials(world))


def test_permission_csrf_disabled_and_revoked_session(engine,world):
    with pytest.raises(ReservationPermissionError):prepare(engine,world,**credentials(world,"admin"))
    with pytest.raises(InvalidCsrfError):prepare(engine,world,csrf_token=secrets.token_urlsafe(32))
    record=prepare(engine,world)
    with Session(engine) as db,db.begin():
        login=db.scalar(select(LoginSession).where(LoginSession.token_hash==token_digest(world["users"]["student"]["token"])))
        login.revoked_at=to_utc_naive(NOW)
    with pytest.raises(InvalidSessionError):consume(engine,world,record,lambda *a:{})


def test_payload_corruption_is_rejected(engine,world):
    record=prepare(engine,world)
    with Session(engine) as db,db.begin():
        stored=db.scalar(select(AssistantConfirmation))
        stored.payload={**stored.payload,"attendees":1}
    with pytest.raises(service.ConfirmationError,match="校验失败"):
        inspect(engine,world,record)
    with pytest.raises(service.ConfirmationError):
        consume(engine,world,record,lambda *a:{})


@pytest.mark.parametrize("extra",[{"user_id":999},{"source":"page"},{"status":"approved"}])
def test_payload_cannot_choose_user_source_or_status(engine,world,extra):
    with pytest.raises(service.ConfirmationError,match="格式无效"):
        prepare(engine,world,payload={**body(world),**extra})
    assert table_count(engine,AssistantConfirmation)==0


def test_ten_connections_prepare_and_consume_once(engine,world):
    barrier=Barrier(10)
    def make():
        with engine.connect() as connection:
            identifier=connection.scalar(text("SELECT CONNECTION_ID()"));connection.commit()
            barrier.wait(timeout=10)
            with Session(bind=connection) as db:
                record=service.prepare_confirmation(db,revision=1,payload=body(world),**credentials(world))
            return identifier,record
    with ThreadPoolExecutor(max_workers=10) as pool:
        results=list(pool.map(lambda _:make(),range(10)))
    assert len({identifier for identifier,_ in results})==10
    prepared=[record for _,record in results]
    assert len({p["confirmation_id"] for p in prepared})==1
    assert table_count(engine,AssistantConfirmation)==1
    callbacks=[]; mutex=Lock(); barrier=Barrier(10)
    def action(db,payload):
        with mutex: callbacks.append(True)
        room=MeetingRoom(name="确认原子写入",location="隔离测试楼",capacity=12,equipment=[])
        db.add(room);db.flush()
        return {"room_id":room.id}
    def execute():
        with engine.connect() as connection:
            identifier=connection.scalar(text("SELECT CONNECTION_ID()"));connection.commit()
            barrier.wait(timeout=10)
            with Session(bind=connection) as db:
                record=service.consume_confirmation(db,confirmation_id=prepared[0]["confirmation_id"],action=action,
                                                    **credentials(world))
            return identifier,record
    with ThreadPoolExecutor(max_workers=10) as pool:
        results=list(pool.map(lambda _:execute(),range(10)))
    assert len({identifier for identifier,_ in results})==10
    consumed=[record for _,record in results]
    assert len(callbacks)==1
    assert all(c==consumed[0] for c in consumed) and consumed[0]["state"]=="consumed"
    assert table_count(engine,MeetingRoom)==11


def test_callback_failure_rolls_back_business_and_receipt(engine,world):
    record=prepare(engine,world)
    def failure(db,payload):
        db.add(MeetingRoom(name="不能留下",location="隔离",capacity=8,equipment=[]));db.flush()
        raise SQLAlchemyError("synthetic transaction failure")
    with pytest.raises(DatabaseUnavailableError):consume(engine,world,record,failure)
    assert inspect(engine,world,record)["state"]=="pending" and table_count(engine,MeetingRoom)==10


def test_receipt_failure_rolls_back_already_flushed_business(engine,world):
    record=prepare(engine,world)
    def action(db,payload):
        db.add(MeetingRoom(name="收据失败不能留下",location="隔离",capacity=8,equipment=[]));db.flush()
        return {"room_id":123}
    def fail_receipt(db,context,instances):
        if any(isinstance(item,AssistantConfirmation) and item.state=="consumed" for item in db.dirty):
            raise SQLAlchemyError("synthetic receipt persistence failure")
    event.listen(Session,"before_flush",fail_receipt)
    try:
        with pytest.raises(DatabaseUnavailableError):consume(engine,world,record,action)
    finally:
        event.remove(Session,"before_flush",fail_receipt)
    assert table_count(engine,MeetingRoom)==10 and inspect(engine,world,record)["state"]=="pending"


def test_revocation_during_callback_rolls_back_business(engine,world):
    record=prepare(engine,world)
    def action(db,payload):
        db.add(MeetingRoom(name="会话失效不能留下",location="隔离",capacity=8,equipment=[]));db.flush()
        with Session(engine) as other,other.begin():
            login=other.scalar(select(LoginSession).where(LoginSession.token_hash==token_digest(world["users"]["student"]["token"])))
            login.revoked_at=to_utc_naive(NOW)
        return {"room_id":123}
    with pytest.raises(InvalidSessionError):consume(engine,world,record,action)
    assert table_count(engine,MeetingRoom)==10
    with Session(engine) as db:
        stored=db.scalar(select(AssistantConfirmation))
        assert stored.state=="pending" and stored.result is None


def test_invalid_result_and_crossed_deadline_roll_back_callback(engine,world,clock_and_cleanup):
    record=prepare(engine,world)
    def no_json(db,payload):
        db.add(MeetingRoom(name="非JSON不能留下",location="隔离",capacity=8,equipment=[]));db.flush()
        return {"bad":object()}
    with pytest.raises(service.ConfirmationError,match="结果无法保存"):
        consume(engine,world,record,no_json)
    assert table_count(engine,MeetingRoom)==10 and inspect(engine,world,record)["state"]=="pending"
    def cross(db,payload):
        db.add(MeetingRoom(name="过期不能留下",location="隔离",capacity=8,equipment=[]));db.flush()
        clock_and_cleanup[0]+=timedelta(seconds=300)
        return {"room_id":999}
    with pytest.raises(service.ConfirmationError,match="五分钟"):
        consume(engine,world,record,cross)
    assert table_count(engine,MeetingRoom)==10


def test_consumed_receipt_survives_expiry_and_new_reader(engine,world,clock_and_cleanup):
    record=prepare(engine,world)
    first=consume(engine,world,record,lambda *a:{"reservation_id":123,"status":"pending"})
    clock_and_cleanup[0]+=timedelta(minutes=10)
    def forbidden(*a):pytest.fail("已执行凭证不可重复执行业务")
    assert consume(engine,world,record,forbidden)==first
    assert inspect(engine,world,record)==first
