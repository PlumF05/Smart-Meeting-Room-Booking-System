"""阶段六的隔离MySQL验收：消息、催办、账号、统计和只读审计。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from test_reservation_submission_mysql import world, encoded_password, client, submit, bind_user, counts, NOW, NOW_UTC
from app.models import LoginSession, Notification, OperationLog, Reservation, User
from app.services import administration, management, messages, my_reservations, reminders, room_occupancy
from app.core.errors import DatabaseUnavailableError

pytestmark = pytest.mark.mysql


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    for module in (administration, management, messages, my_reservations, reminders, room_occupancy):
        monkeypatch.setattr(module, "get_beijing_now", lambda: NOW)


def remind(engine, world, identifier):
    user = world['users']['student']
    with Session(engine) as db:
        return reminders.remind(db, user['id'], identifier, user['token'], user['csrf'])


def test_message_owner_pagination_mark_read_and_replay(engine, world, client):
    submit(engine, world)
    submit(engine, world, start_time='11:00', end_time='12:00')
    headers = bind_user(client, world, 'admin')
    page = client.get('/api/messages', params={'page_size': 1})
    assert page.status_code == 200 and page.headers['cache-control'] == 'no-store'
    data = page.json()
    assert data['total'] == data['unread_count'] == 2 and len(data['items']) == 1
    identifier = data['items'][0]['id']
    other = client.get('/api/messages', params={'page': 2, 'page_size': 1}).json()['items'][0]['id']
    assert other != identifier
    first = client.post(f'/api/messages/{identifier}/read', json={}, headers=headers)
    second = client.post(f'/api/messages/{identifier}/read', json={}, headers=headers)
    assert first.status_code == 200 and first.json() == second.json()
    unread = client.get('/api/messages', params={'unread_only': True}).json()
    assert unread['unread_count'] == unread['total'] == 1 and unread['items'][0]['id'] == other
    assert client.get('/api/messages', params={'page': 0}).status_code == 422
    headers = bind_user(client, world, 'student')
    assert client.get('/api/messages').json()['total'] == 0
    assert client.post(f'/api/messages/{identifier}/read', json={}, headers=headers).status_code == 404


def test_single_reminder_flags_and_unchanged_reservation(engine, world, client):
    record = submit(engine, world)
    headers = bind_user(client, world)
    before = client.get('/api/reservations').json()[0]
    assert before['can_remind'] and not before['has_reminded']
    response = client.post(f'/api/reservations/{record.id}/remind', json={}, headers=headers)
    assert response.status_code == 200 and response.json()['has_reminded']
    assert counts(engine) == (1, 2, 4)
    assert '已催办' in client.post(f'/api/reservations/{record.id}/remind', json={}, headers=headers).json()['message']
    after = client.get(f'/api/reservations/{record.id}').json()
    assert after['has_reminded'] and not after['can_remind'] and after['status'] == 'pending'
    with Session(engine) as db:
        saved = db.get(Reservation, record.id)
        assert saved.submitted_at == saved.updated_at == NOW_UTC
    assert counts(engine) == (1, 2, 4)


def test_ten_real_connections_only_one_reminder_event(engine, world):
    record = submit(engine, world)
    barrier = Barrier(10)
    user = world['users']['student']
    def worker(_):
        with engine.connect() as connection, Session(connection, autoflush=False) as db:
            barrier.wait(timeout=10)
            return reminders.remind(db, user['id'], record.id, user['token'], user['csrf'])
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(10)))
    assert all(row['has_reminded'] for row in results)
    assert counts(engine) == (1, 2, 4)


@pytest.mark.parametrize('status', ['approved', 'rejected', 'cancelled', 'expired', 'revoked', 'finished'])
def test_processed_records_cannot_be_reminded(engine, world, status):
    record = submit(engine, world)
    with Session(engine) as db, db.begin():
        db.get(Reservation, record.id).status = status
    with pytest.raises(management.ManagementError):
        remind(engine, world, record.id)
    assert counts(engine) == (1, 1, 2)


def test_reminder_start_boundary_privacy_csrf_and_extra_fields(engine, world, client):
    record = submit(engine, world)
    headers = bind_user(client, world, 'teacher')
    url = f'/api/reservations/{record.id}/remind'
    assert client.post(url, json={}, headers=headers).status_code == 404
    bind_user(client, world)
    assert client.post(url, json={}).status_code == 403
    headers = bind_user(client, world)
    assert client.post(url, json={'recipient_user_id': 1}, headers=headers).status_code == 422
    with Session(engine) as db, db.begin():
        db.get(Reservation, record.id).starts_at = NOW_UTC
    assert client.post(url, json={}, headers=headers).status_code == 409
    assert counts(engine) == (1, 1, 2)


def test_reminder_event_failure_rolls_back_and_retry_succeeds(engine, world, monkeypatch):
    record = submit(engine, world)
    original = reminders.append_operation
    def fail(db, entry, drafts):
        original(db, entry, drafts)
        raise SQLAlchemyError('synthetic failure')
    monkeypatch.setattr(reminders, 'append_operation', fail)
    with pytest.raises(DatabaseUnavailableError):
        remind(engine, world, record.id)
    assert counts(engine) == (1, 1, 2)
    monkeypatch.setattr(reminders, 'append_operation', original)
    assert remind(engine, world, record.id)['has_reminded']


def test_account_search_disable_preview_session_revocation_and_preserved_booking(engine, world, client):
    record = submit(engine, world)
    target = world['users']['student']
    headers = bind_user(client, world, 'admin')
    row = client.get('/api/admin/users', params={'q': target['username']}).json()['items'][0]
    assert row['active_reservation_count'] == 1 and 'password_hash' not in row
    assert client.get('/api/admin/users', params={'q': '%'}).json()['total'] == 0
    assert client.get(f"/api/admin/users/{target['id']}/reservations").json()[0]['id'] == record.id
    url = f"/api/admin/users/{target['id']}/state"
    disabled = client.patch(url, json={'is_active': False}, headers=headers)
    assert disabled.status_code == 200 and disabled.json()['active_reservation_count'] == 1
    assert client.patch(url, json={'is_active': False}, headers=headers).status_code == 200
    assert counts(engine) == (1, 2, 2)
    assert client.patch(url, json={'is_active': True}, headers=headers).status_code == 200
    bind_user(client, world, 'student')
    assert client.get('/api/auth/me').status_code == 401
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == 'pending'
        assert db.scalar(select(LoginSession).where(LoginSession.user_id == target['id'])).revoked_at is not None


def test_account_state_failure_rolls_back_user_session_and_log(engine, world, client, monkeypatch):
    submit(engine, world)
    headers = bind_user(client, world, 'admin')
    target = world['users']['student']
    original = administration.append_operation
    def fail(db, entry):
        original(db, entry)
        raise SQLAlchemyError('synthetic failure')
    monkeypatch.setattr(administration, 'append_operation', fail)
    assert client.patch(f"/api/admin/users/{target['id']}/state", json={'is_active': False}, headers=headers).status_code == 503
    with Session(engine) as db:
        assert db.get(User, target['id']).is_active
        assert db.scalar(select(LoginSession).where(LoginSession.user_id == target['id'])).revoked_at is None
    assert counts(engine) == (1, 1, 2)


def test_admin_protection_sensitive_fields_and_audit_read_only(engine, world, client):
    submit(engine, world)
    headers = bind_user(client, world, 'admin')
    assert client.patch(f"/api/admin/users/{world['users']['admin']['id']}/state", json={'is_active': False}, headers=headers).status_code == 403
    audit = client.get('/api/admin/operations', params={'object_type': 'reservation', 'page_size': 1})
    assert audit.status_code == 200 and audit.json()['total'] == 1
    assert not any(key in audit.text for key in ('password_hash', 'csrf_token', 'token_hash'))
    identifier = audit.json()['items'][0]['id']
    assert client.delete(f'/api/admin/operations/{identifier}').status_code in (404, 405)
    bind_user(client, world)
    for url in ('/api/admin/users', '/api/admin/operations', '/api/admin/statistics?start_date=2026-11-07&end_date=2026-11-07'):
        assert client.get(url).status_code == 403


def test_statistics_beijing_submission_date_boundaries_current_status_and_room(engine, world, client):
    rows = [submit(engine, world, room_index=i, start_time=f'{10+i:02d}:00', end_time=f'{11+i:02d}:00') for i in range(4)]
    with Session(engine) as db, db.begin():
        for record, submitted in zip(rows, [datetime(2026,11,6,15,59,59,999999), datetime(2026,11,6,16),
                                             datetime(2026,11,7,15,59,59,999999), datetime(2026,11,7,16)]):
            db.get(Reservation, record.id).submitted_at = submitted
        expired = db.get(Reservation, rows[1].id)
        expired.starts_at = NOW_UTC
        finished = db.get(Reservation, rows[2].id)
        finished.status, finished.starts_at, finished.ends_at = 'approved', NOW_UTC-timedelta(hours=2), NOW_UTC-timedelta(hours=1)
    bind_user(client, world, 'admin')
    params = {'start_date': '2026-11-07', 'end_date': '2026-11-07'}
    result = client.get('/api/admin/statistics', params=params)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['total'] == 2 and data['counts']['expired'] == data['counts']['finished'] == 1
    assert sum(data['counts'].values()) == 2 and data['query_time'] == NOW.isoformat()
    assert data['definition'] == '按提交日期筛选，按当前状态统计'
    assert client.get('/api/admin/statistics', params={**params, 'room_id': rows[1].room_id}).json()['total'] == 1


@pytest.mark.parametrize('params', [
    {'start_date':'2026-11-08','end_date':'2026-11-07'},
    {'start_date':'2026-02-30','end_date':'2026-11-07'},
    {'start_date':'0001-01-01','end_date':'9999-12-31'},
])
def test_bad_statistics_dates_are_422(engine, world, client, params):
    bind_user(client, world, 'admin')
    assert client.get('/api/admin/statistics', params=params).status_code == 422


def test_bulk_occupancy_has_all_rooms_and_no_private_details(engine, world, client):
    record = submit(engine, world)
    bind_user(client, world, 'teacher')
    result = client.get('/api/rooms/occupancy', params={'date':'2026-11-08','start_time':'10:00','end_time':'11:00'})
    assert result.status_code == 200 and len(result.json()) == 10
    occupied = next(row for row in result.json() if row['room_id'] == record.room_id)
    assert not occupied['available'] and occupied['occupancy'][0]['status'] == 'pending'
    assert 'title' not in result.text and 'user_id' not in result.text and 'purpose' not in result.text
    assert client.get('/api/rooms/occupancy').status_code == 422
