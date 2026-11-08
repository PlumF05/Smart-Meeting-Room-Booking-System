"""阶段五审批、维护、时间补齐及并发；仅使用独立MySQL测试库。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from test_reservation_submission_mysql import world, encoded_password, client, submit, bind_user, counts, NOW, NOW_UTC
from app.core.errors import DatabaseUnavailableError
from app.models import LoginSession, MeetingRoom, Notification, OperationLog, Reservation
from app.schemas.management import DecisionInput, RoomUpdate
from app.services import management, my_reservations, reservation_lifecycle


pytestmark = pytest.mark.mysql


@pytest.fixture(autouse=True)
def clocks(monkeypatch):
    for module in (management, my_reservations, reservation_lifecycle):
        monkeypatch.setattr(module, "get_beijing_now", lambda: NOW)


def decide(engine, world, identifier, action="approve", reason=None):
    admin = world["users"]["admin"]
    with Session(engine) as db:
        return management.decide(db, identifier, DecisionInput(action=action, reason=reason),
                                 actor_id=admin["id"], raw_token=admin["token"], csrf_token=admin["csrf"])


def update(engine, world, **changes):
    admin = world["users"]["admin"]
    with Session(engine) as db:
        room = db.get(MeetingRoom, world["rooms"][0]["id"])
        values = dict(name=room.name, location=room.location, capacity=room.capacity,
                      equipment=room.equipment, is_active=room.is_active)
    values.update(changes)
    with Session(engine) as db:
        return management.update_room(db, world["rooms"][0]["id"], RoomUpdate(**values),
                                      actor_id=admin["id"], raw_token=admin["token"], csrf_token=admin["csrf"])


@pytest.mark.parametrize("action,status,reason", [("approve", "approved", None), ("reject", "rejected", "用途不符")])
def test_admin_decisions_messages_and_retries(engine, world, client, action, status, reason):
    record = submit(engine, world)
    headers = bind_user(client, world, "admin")
    response = client.post(f"/api/admin/reservations/{record.id}/decision",
                           json=dict(action=action, reason=reason), headers=headers)
    assert response.status_code == 200 and response.json()["status"] == status
    assert response.json()["action_applied"] is True
    assert response.headers["cache-control"] == "no-store"
    again = decide(engine, world, record.id, action, reason)
    assert again["action_applied"] is False and again["status"] == status
    assert counts(engine) == (1, 2, 3)
    assert "password" not in response.text and "csrf_token" not in response.text


def test_revoke_requires_approved_and_preserves_reason(engine, world):
    record = submit(engine, world)
    assert not decide(engine, world, record.id, "revoke", "调整安排")["action_applied"]
    decide(engine, world, record.id)
    result = decide(engine, world, record.id, "revoke", "调整安排")
    assert result["status"] == "revoked" and result["status_reason"] == "调整安排"
    assert counts(engine) == (1, 3, 4)


@pytest.mark.parametrize("action", ["reject", "revoke"])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reason_required(engine, world, client, action, reason):
    record = submit(engine, world)
    result = client.post(f"/api/admin/reservations/{record.id}/decision",
                         json=dict(action=action, reason=reason), headers=bind_user(client, world, "admin"))
    assert result.status_code == 422 and counts(engine) == (1, 1, 2)


def test_reason_max_length_can_be_saved_without_notification_overflow(engine, world):
    record = submit(engine, world)
    result = decide(engine, world, record.id, "reject", "因" * 1000)
    assert len(result["status_reason"]) == 1000


def test_roles_csrf_and_origin(engine, world, client):
    record = submit(engine, world)
    assert client.get("/api/admin/reservations").status_code == 401
    headers = bind_user(client, world)
    assert client.get("/api/admin/rooms").status_code == 403
    assert client.post(f"/api/admin/reservations/{record.id}/decision", json={"action": "approve"}, headers=headers).status_code == 403
    bind_user(client, world, "admin")
    assert client.post(f"/api/admin/reservations/{record.id}/decision", json={"action": "approve"}).status_code == 403
    headers = {**bind_user(client, world, "admin"), "Origin": "https://attacker.example"}
    assert client.post(f"/api/admin/reservations/{record.id}/decision", json={"action": "approve"}, headers=headers).status_code == 403
    assert counts(engine) == (1, 1, 2)


def test_default_pending_filter_room_sort_and_immediate_effective_status(engine, world, client):
    first = submit(engine, world)
    second = submit(engine, world, room_index=1, start_time="11:00", end_time="12:00")
    bind_user(client, world, "admin")
    assert [row["id"] for row in client.get("/api/admin/reservations").json()] == [first.id, second.id]
    filtered = client.get("/api/admin/reservations", params={"room_id": second.room_id}).json()
    assert [row["id"] for row in filtered] == [second.id]
    assert client.get("/api/admin/reservations", params={"direction": "desc"}).json()[0]["id"] == second.id
    with Session(engine) as db, db.begin():
        db.get(Reservation, first.id).starts_at = NOW_UTC
    assert [row["id"] for row in client.get("/api/admin/reservations").json()] == [second.id]
    assert client.get("/api/admin/reservations", params={"status": "expired"}).json()[0]["id"] == first.id


def test_submission_time_sort_is_independent_of_meeting_start_sort(engine, world, client):
    first = submit(engine, world)
    second = submit(engine, world, room_index=1, start_time="11:00", end_time="12:00")
    # 让提交顺序与会议开始顺序相反，避免两种排序误用同一字段仍然通过。
    with Session(engine) as db, db.begin():
        db.get(Reservation, first.id).submitted_at = NOW_UTC
        db.get(Reservation, second.id).submitted_at = NOW_UTC - timedelta(minutes=1)
    bind_user(client, world, "admin")
    for sort, direction, expected in (
        ("starts_at", "asc", [first.id, second.id]),
        ("submitted_at", "asc", [second.id, first.id]),
        ("submitted_at", "desc", [first.id, second.id]),
    ):
        result = client.get("/api/admin/reservations", params={"sort": sort, "direction": direction})
        assert result.status_code == 200
        assert [row["id"] for row in result.json()] == expected


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_started_pending_expires_without_late_approval_or_rejection(engine, world, action):
    record = submit(engine, world)
    with Session(engine) as db, db.begin():
        db.get(Reservation, record.id).starts_at = NOW_UTC
    result = decide(engine, world, record.id, action, "原因" if action == "reject" else None)
    assert result["status"] == "expired" and result["action_applied"] is False
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == "expired"
        log = db.scalar(select(OperationLog).where(OperationLog.action == "expire"))
        assert log.actor_kind == "system" and log.actor_user_id is None
    assert counts(engine) == (1, 2, 3)


def test_in_progress_cannot_be_revoked_or_room_disabled(engine, world):
    record = submit(engine, world)
    decide(engine, world, record.id)
    with Session(engine) as db, db.begin():
        saved = db.get(Reservation, record.id)
        saved.starts_at = NOW_UTC - timedelta(minutes=30)
        saved.ends_at = NOW_UTC + timedelta(minutes=30)
    with pytest.raises(management.ManagementError):
        decide(engine, world, record.id, "revoke", "停用")
    with pytest.raises(management.ManagementError):
        update(engine, world, is_active=False)


@pytest.mark.parametrize("change", [{"location": "新地点"}, {"capacity": 20}, {"is_active": False}])
def test_active_booking_protects_location_capacity_and_disable(engine, world, change):
    record = submit(engine, world)
    with pytest.raises(management.ManagementError) as failure:
        update(engine, world, **change)
    assert str(record.id) in failure.value.detail
    assert counts(engine) == (1, 1, 2)


def test_name_equipment_edit_preserves_snapshot_and_does_not_notify_or_reapprove(engine, world):
    record = submit(engine, world)
    updated = update(engine, world, name="改名", equipment=[])
    assert updated.name == "改名" and updated.equipment == []
    with Session(engine) as db:
        saved = db.get(Reservation, record.id)
        assert saved.status == "pending" and saved.room_name_snapshot != "改名"
        assert saved.room_equipment_snapshot == ["白板", "投影仪"]
    assert counts(engine) == (1, 2, 2)
    with pytest.raises(management.ManagementError):
        decide(engine, world, record.id)


def test_approved_equipment_edit_keeps_status_and_exposes_current_and_snapshot_details(engine, world, client):
    record = submit(engine, world)
    assert decide(engine, world, record.id)["status"] == "approved"
    before = counts(engine)
    update(engine, world, equipment=["视频会议终端"])
    bind_user(client, world)
    response = client.get(f"/api/reservations/{record.id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == detail["stored_status"] == "approved"
    assert detail["room_snapshot"]["equipment"] == ["白板", "投影仪"]
    assert detail["current_room"]["equipment"] == ["视频会议终端"]
    assert detail["required_equipment"] == ["白板"]
    # 仅新增维护操作记录；既有申请不被自动重审、取消或发送设备变更消息。
    assert counts(engine) == (before[0], before[1] + 1, before[2])


def test_restore_does_not_revive_terminal_and_duplicate_name_rejected(engine, world):
    record = submit(engine, world)
    decide(engine, world, record.id, "reject", "停用维护")
    update(engine, world, is_active=False)
    update(engine, world, is_active=True, location="新地点", capacity=20)
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == "rejected"
    with pytest.raises(management.ManagementError):
        update(engine, world, name=world["rooms"][1]["name"])


@pytest.mark.parametrize("source", ["pending", "approved"])
def test_worker_transition_idempotency_and_notification_policy(engine, world, source):
    record = submit(engine, world)
    with Session(engine) as db, db.begin():
        saved = db.get(Reservation, record.id)
        saved.status = source
        saved.starts_at = NOW_UTC - timedelta(hours=1)
        saved.ends_at = NOW_UTC
    assert reservation_lifecycle.process_due(engine) == 1
    assert reservation_lifecycle.process_due(engine) == 0
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == ("expired" if source == "pending" else "finished")
    assert counts(engine) == (1, 2, 3 if source == "pending" else 2)


def test_multiple_workers_only_one_expiry_event(engine, world):
    record = submit(engine, world)
    with Session(engine) as db, db.begin():
        db.get(Reservation, record.id).starts_at = NOW_UTC
    barrier = Barrier(2)
    def work():
        barrier.wait(timeout=5)
        return reservation_lifecycle.process_due(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work), pool.submit(work)]
        assert sum(future.result(timeout=10) for future in futures) == 1
    assert counts(engine) == (1, 2, 3)


@pytest.mark.parametrize("failure_stage", ["decision", "maintenance", "worker"])
def test_log_and_notification_failure_roll_back_business(engine, world, monkeypatch, failure_stage):
    record = submit(engine, world)
    module = {"decision": management, "maintenance": management, "worker": reservation_lifecycle}[failure_stage]
    original = module.append_operation
    def fail(db, entry, messages=()):
        original(db, entry, messages)
        raise SQLAlchemyError("synthetic failure")
    monkeypatch.setattr(module, "append_operation", fail)
    if failure_stage == "worker":
        with Session(engine) as db, db.begin():
            db.get(Reservation, record.id).starts_at = NOW_UTC
    with pytest.raises(DatabaseUnavailableError):
        if failure_stage == "decision":
            decide(engine, world, record.id)
        elif failure_stage == "maintenance":
            update(engine, world, name="不可保存")
        else:
            reservation_lifecycle.process_due(engine)
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == "pending"
        assert db.get(MeetingRoom, record.room_id).name != "不可保存"
    assert counts(engine) == (1, 1, 2)


def test_concurrent_approve_cancel_always_ends_cancelled(engine, world):
    record = submit(engine, world)
    barrier = Barrier(2)
    def approve():
        barrier.wait(timeout=5)
        return decide(engine, world, record.id)
    def cancel():
        barrier.wait(timeout=5)
        user = world["users"]["student"]
        with Session(engine) as db:
            return my_reservations.cancel_mine(db, user["id"], record.id, user["token"], user["csrf"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(approve), pool.submit(cancel)
        a.result(timeout=10)
        assert b.result(timeout=10)["status"] == "cancelled"
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == "cancelled"


def test_competing_approve_reject_preserves_first_terminal_result(engine, world):
    record = submit(engine, world)
    barrier = Barrier(2)
    def work(action):
        barrier.wait(timeout=5)
        return decide(engine, world, record.id, action, "审核意见")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(work, "approve"), pool.submit(work, "reject")
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert sum(result["action_applied"] for result in results) == 1
    assert results[0]["status"] == results[1]["status"]


def test_lock_wait_crossing_start_time_expires(engine, world, monkeypatch):
    record = submit(engine, world)
    waiting = Event()
    current = [NOW]
    monkeypatch.setattr(management, "get_beijing_now", lambda: current[0])
    def before_sql(conn, cursor, sql, params, context, many):
        if "FROM meeting_rooms" in sql and "FOR UPDATE" in sql:
            waiting.set()
    with Session(engine) as blocker:
        blocker.begin()
        blocker.scalar(select(MeetingRoom).where(MeetingRoom.id == record.room_id).with_for_update())
        event.listen(engine, "before_cursor_execute", before_sql)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(decide, engine, world, record.id)
                try:
                    assert waiting.wait(5)
                    current[0] = NOW + timedelta(days=1, hours=1)
                    blocker.commit()
                finally:
                    blocker.rollback()
                assert future.result(timeout=10)["status"] == "expired"
        finally:
            event.remove(engine, "before_cursor_execute", before_sql)
