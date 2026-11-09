"""助手提交与页面共享规则、事务和真实MySQL并发；不接触开发库。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import AssistantConfirmation, LoginSession, MeetingRoom, OperationLog, Reservation
from app.schemas.reservations import ReservationCreate
from app.services import assistant_confirmations as confirmations
from app.services import assistant_submission as service
from app.services import my_reservations, reservations
from app.services.login import InvalidCsrfError, InvalidSessionError, token_digest
from test_reservation_submission_mysql import body, counts, encoded_password, NOW, submit, world

pytestmark = pytest.mark.mysql


@pytest.fixture(autouse=True)
def clock(engine, monkeypatch):
    current = [NOW]
    monkeypatch.setattr(confirmations, "get_beijing_now", lambda: current[0])
    monkeypatch.setattr(my_reservations, "get_beijing_now", lambda: current[0])
    return current


def actor(world, label="student", **changes):
    user = world["users"][label]
    values = dict(user_id=user["id"], raw_token=user["token"], csrf_token=user["csrf"],
                  conversation_id="assistant-submit-isolated", revision=1)
    values.update(changes)
    return values


def prepare(engine, world, *, label="student", **changes):
    with Session(engine) as db:
        return confirmations.prepare_confirmation(db, payload=body(world, **changes), **actor(world, label))


def call(engine, world, confirmation, *, inspect=False, label="student", **changes):
    function = service.inspect_submission if inspect else service.submit_confirmed
    with Session(engine) as db:
        return function(db, confirmation_id=confirmation["confirmation_id"], **actor(world, label, **changes))


def test_preview_is_read_only_and_confirm_submits_pending_once(engine, world):
    confirmation = prepare(engine, world)
    preview = call(engine, world, confirmation, inspect=True)
    assert preview["confirmation"]["state"] == "pending" and preview["reservation"] is None
    assert counts(engine) == (0, 0, 0)
    result = call(engine, world, confirmation)
    reservation = result["reservation"]
    assert (reservation["status"], reservation["source"]) == ("pending", "assistant")
    assert reservation["title"] == "课程小组讨论"
    assert result["confirmation"]["result"] == {"reservation_id": reservation["id"]}
    assert counts(engine) == (1, 1, 2)
    repeated = call(engine, world, confirmation)
    assert repeated == result and call(engine, world, confirmation, inspect=True) == result
    with Session(engine) as db:
        log = db.scalar(select(OperationLog))
        assert log.action == "submit" and log.source == "assistant"
        assert log.after_status == "pending"


def test_ten_real_connections_same_confirmation_create_exactly_one_reservation(engine, world):
    confirmation = prepare(engine, world)
    barrier = Barrier(10)

    def worker():
        with engine.connect() as connection:
            connection_id = connection.scalar(text("SELECT CONNECTION_ID()"))
            connection.commit()
            barrier.wait(timeout=10)
            with Session(bind=connection) as db:
                return connection_id, service.submit_confirmed(db,
                    confirmation_id=confirmation["confirmation_id"], **actor(world))

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(worker) for _ in range(10)]
        results = [future.result(timeout=30) for future in futures]
    assert len({connection_id for connection_id, _ in results}) == 10
    assert len({result["reservation"]["id"] for _, result in results}) == 1
    assert all(result["confirmation"]["state"] == "consumed" for _, result in results)
    assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize("competition", ["room", "person"])
def test_page_and_assistant_compete_under_same_parent_locks(engine, world, competition):
    confirmation = prepare(engine, world)
    barrier = Barrier(2)

    def worker(assistant):
        with engine.connect() as connection, Session(bind=connection) as db:
            identifier = connection.scalar(text("SELECT CONNECTION_ID()"))
            connection.commit()
            barrier.wait(timeout=10)
            try:
                if assistant:
                    result = service.submit_confirmed(db,
                        confirmation_id=confirmation["confirmation_id"], **actor(world))
                    return identifier, result["reservation"]["id"]
                person = world["users"]["teacher" if competition == "room" else "student"]
                result = reservations.create_reservation(db,
                    ReservationCreate(**body(world, 0 if competition == "room" else 1)),
                    user_id=person["id"], raw_token=person["token"], csrf_token=person["csrf"])
                return identifier, result.id
            except (reservations.RoomConflictError, reservations.PersonalConflictError):
                return identifier, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, assistant) for assistant in (True, False)]
        results = [future.result(timeout=30) for future in futures]
    assert len({identifier for identifier, _ in results}) == 2
    assert sum(result is not None for _, result in results) == 1
    assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize("change,error", [
    ("disabled", reservations.RoomInactiveError),
    ("capacity", reservations.ReservationRuleError),
    ("equipment", reservations.ReservationRuleError),
    ("room_conflict", reservations.RoomConflictError),
    ("personal_conflict", reservations.PersonalConflictError),
])
def test_confirm_rechecks_current_business_rules(engine, world, change, error):
    confirmation = prepare(engine, world)
    if change == "room_conflict":
        submit(engine, world, label="teacher")
    elif change == "personal_conflict":
        submit(engine, world, room_index=1)
    else:
        with Session(engine) as db, db.begin():
            room = db.get(MeetingRoom, world["rooms"][0]["id"])
            if change == "disabled": room.is_active = False
            elif change == "capacity": room.capacity = 1
            else: room.equipment = []
    before = counts(engine)
    with pytest.raises(error):
        call(engine, world, confirmation)
    assert counts(engine) == before
    assert call(engine, world, confirmation, inspect=True)["confirmation"]["state"] == "pending"


@pytest.mark.parametrize("failure", ["operation", "receipt", "commit"])
def test_reservation_operation_messages_and_receipt_roll_back_together(engine, world, monkeypatch, failure):
    confirmation = prepare(engine, world)
    original = reservations.append_operation

    def fail_operation(*args, **kwargs):
        original(*args, **kwargs)
        raise SQLAlchemyError("synthetic isolated operation failure")

    def fail_receipt(db, context, instances):
        if any(isinstance(value, AssistantConfirmation) and value.state == "consumed" for value in db.dirty):
            raise SQLAlchemyError("synthetic isolated receipt failure")

    def fail_commit(db):
        if db.info.get("fail_assistant_commit"):
            raise SQLAlchemyError("synthetic isolated commit failure")

    if failure == "operation": monkeypatch.setattr(reservations, "append_operation", fail_operation)
    target, name, listener = (Session, "before_flush", fail_receipt) if failure == "receipt" else (Session, "before_commit", fail_commit)
    if failure != "operation": event.listen(target, name, listener)
    try:
        with Session(engine) as db:
            db.info["fail_assistant_commit"] = failure == "commit"
            with pytest.raises(DatabaseUnavailableError):
                service.submit_confirmed(db, confirmation_id=confirmation["confirmation_id"], **actor(world))
    finally:
        if failure != "operation": event.remove(target, name, listener)
    assert counts(engine) == (0, 0, 0)
    state = call(engine, world, confirmation, inspect=True)["confirmation"]
    assert state["state"] == "pending" and state["result"] is None


def test_expiry_changes_session_and_wrong_owner_cannot_submit(engine, world, clock):
    confirmation = prepare(engine, world)
    with pytest.raises(confirmations.ConfirmationError):
        call(engine, world, confirmation, label="teacher")
    with pytest.raises(confirmations.ConfirmationError):
        call(engine, world, confirmation, revision=2)
    with pytest.raises(InvalidCsrfError):
        call(engine, world, confirmation, csrf_token="X" * 43)
    clock[0] += timedelta(seconds=300)
    with pytest.raises(confirmations.ConfirmationError, match="五分钟"):
        call(engine, world, confirmation)
    assert call(engine, world, confirmation, inspect=True)["confirmation"]["state"] == "expired"
    assert counts(engine) == (0, 0, 0)


def test_revoked_login_cannot_submit_or_inspect_receipt(engine, world):
    confirmation = prepare(engine, world)
    with Session(engine) as db, db.begin():
        login = db.scalar(select(LoginSession).where(
            LoginSession.token_hash == token_digest(world["users"]["student"]["token"])))
        login.revoked_at = NOW.replace(tzinfo=None)
    with pytest.raises(InvalidSessionError): call(engine, world, confirmation)
    with pytest.raises(InvalidSessionError): call(engine, world, confirmation, inspect=True)
    assert counts(engine) == (0, 0, 0)


def test_consumed_confirmation_reads_current_status_even_after_expiry(engine, world, clock):
    confirmation = prepare(engine, world)
    result = call(engine, world, confirmation)
    identifier = result["reservation"]["id"]
    with Session(engine) as db, db.begin():
        reservation = db.get(Reservation, identifier)
        reservation.status = "rejected"
        reservation.status_reason = "隔离测试已处理"
    clock[0] += timedelta(minutes=10)
    repeated = call(engine, world, confirmation)
    assert repeated["reservation"]["id"] == identifier
    assert repeated["reservation"]["status"] == "rejected"
    assert repeated["reservation"]["status_reason"] == "隔离测试已处理"
    assert call(engine, world, confirmation, inspect=True) == repeated
    assert counts(engine) == (1, 1, 2)


def test_result_read_failure_does_not_resubmit_committed_operation(engine, world, monkeypatch):
    confirmation = prepare(engine, world)
    original = service.read_mine

    def unavailable(*args): raise DatabaseUnavailableError()
    monkeypatch.setattr(service, "read_mine", unavailable)
    with pytest.raises(DatabaseUnavailableError): call(engine, world, confirmation)
    assert counts(engine) == (1, 1, 2)
    monkeypatch.setattr(service, "read_mine", original)
    inspected = call(engine, world, confirmation, inspect=True)
    assert inspected["confirmation"]["state"] == "consumed"
    assert inspected["reservation"]["status"] == "pending"
    assert call(engine, world, confirmation) == inspected
    assert counts(engine) == (1, 1, 2)


def test_transaction_kernel_never_commits_its_callers_transaction(engine, world):
    user = world["users"]["student"]
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="事务"):
            reservations.create_reservation_in_transaction(db, ReservationCreate(**body(world)),
                user_id=user["id"], raw_token=user["token"], csrf_token=user["csrf"])
        with db.begin():
            reservations.create_reservation_in_transaction(db, ReservationCreate(**body(world)),
                user_id=user["id"], raw_token=user["token"], csrf_token=user["csrf"])
            db.rollback()
    assert counts(engine) == (0, 0, 0)
