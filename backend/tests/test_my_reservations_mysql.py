"""阶段四本人查询、取消、重提、事务和竞争，均在独立MySQL中验证。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select, func, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from test_reservation_submission_mysql import world, encoded_password, client, submit, body, bind_user, counts, NOW, NOW_UTC
from app.models import Reservation, OperationLog, Notification
from app.services import my_reservations as service
from app.services.login import InvalidSessionError, InvalidCsrfError
from app.core.errors import DatabaseUnavailableError


pytestmark = pytest.mark.mysql


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(service, 'get_beijing_now', lambda: NOW)


def cancel(engine, world, identifier, **changes):
    user = world['users']['student']
    values = dict(user_id=user['id'], reservation_id=identifier, raw_token=user['token'], csrf_token=user['csrf'])
    values.update(changes)
    with Session(engine) as db:
        return service.cancel_mine(db, **values)


def test_api_mine_detail_cancel_repeat_release_and_resubmit(engine, world, client):
    record = submit(engine, world)
    headers = bind_user(client, world)
    assert client.get('/api/reservations').json()[0]['id'] == record.id
    detail = client.get(f'/api/reservations/{record.id}').json()
    assert detail['room_snapshot']['capacity'] == 12 and detail['can_cancel']
    for _ in range(2):
        result = client.post(f'/api/reservations/{record.id}/cancel', json={'reason': '测试改约'}, headers=headers)
        assert result.status_code == 200 and result.json()['status'] == 'cancelled'
    assert counts(engine) == (1, 2, 4)
    assert submit(engine, world).id != record.id
    assert counts(engine) == (2, 3, 6)


def test_other_user_cannot_read_or_cancel_and_admin_cannot_access(engine, world, client):
    record = submit(engine, world)
    headers = bind_user(client, world, 'teacher')
    assert client.get('/api/reservations').json() == []
    assert client.get(f'/api/reservations/{record.id}').status_code == 404
    assert client.post(f'/api/reservations/{record.id}/cancel', json={}, headers=headers).status_code == 404
    bind_user(client, world, 'admin')
    assert client.get('/api/reservations').status_code == 403
    assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize('status', ['pending', 'approved'])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_cancel_start_boundary(engine, world, status, offset):
    record = submit(engine, world)
    with Session(engine) as db, db.begin():
        saved = db.get(Reservation, record.id)
        saved.status = status
        saved.starts_at = NOW_UTC + timedelta(seconds=offset)
    if offset > 0:
        assert cancel(engine, world, record.id)['status'] == 'cancelled'
    else:
        with pytest.raises(service.CancellationConflictError):
            cancel(engine, world, record.id)
        assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize('status', ['rejected', 'expired', 'revoked', 'finished'])
def test_terminal_cannot_cancel(engine, world, status):
    record = submit(engine, world)
    with Session(engine) as db, db.begin():
        db.get(Reservation, record.id).status = status
    with pytest.raises(service.CancellationConflictError):
        cancel(engine, world, record.id)


def test_csrf_and_source_fail_without_change(engine, world, client):
    record = submit(engine, world)
    bind_user(client, world)
    assert client.post(f'/api/reservations/{record.id}/cancel', json={}).status_code == 403
    headers = {**bind_user(client, world), 'Origin': 'https://attacker.example'}
    assert client.post(f'/api/reservations/{record.id}/cancel', json={}, headers=headers).status_code == 403
    assert counts(engine) == (1, 1, 2)


def test_cancel_notification_failure_rolls_back_status_log_and_message(engine, world, monkeypatch):
    record = submit(engine, world)
    original = service.append_operation
    def fail(db, entry, messages):
        original(db, entry, messages)
        raise SQLAlchemyError('synthetic failure')
    monkeypatch.setattr(service, 'append_operation', fail)
    with pytest.raises(DatabaseUnavailableError):
        cancel(engine, world, record.id)
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == 'pending'
    assert counts(engine) == (1, 1, 2)


def test_ten_concurrent_cancellations_have_one_event(engine, world):
    record = submit(engine, world)
    barrier = Barrier(10)
    def worker(_):
        barrier.wait(timeout=10)
        return cancel(engine, world, record.id)['status']
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert list(pool.map(worker, range(10))) == ['cancelled'] * 10
    assert counts(engine) == (1, 2, 4)


def test_failed_resubmission_does_not_restore_cancelled_record(engine, world):
    record = submit(engine, world)
    cancel(engine, world, record.id)
    submit(engine, world, label='teacher')
    from app.services.reservations import RoomConflictError
    with pytest.raises(RoomConflictError):
        submit(engine, world)
    with Session(engine) as db:
        assert db.get(Reservation, record.id).status == 'cancelled'


def test_records_survive_reader_process_restart(engine, world):
    import os
    import subprocess
    import sys

    record = submit(engine, world)
    cancel(engine, world, record.id)
    environment = os.environ.copy()
    # 仅传递fixture随机端口和随机凭据；不读取开发配置、不输出连接URL。
    environment['STAGE4_ISOLATED_URL'] = engine.url.render_as_string(hide_password=False)
    code = '''
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.services.my_reservations import read_mine
engine = create_engine(os.environ['STAGE4_ISOLATED_URL'], hide_parameters=True)
try:
    with Session(engine) as db:
        record = read_mine(db, USER_ID, RESERVATION_ID)
        assert record['status'] == 'cancelled'
        assert record['room_snapshot']['capacity'] == 12
        print('persisted')
finally:
    engine.dispose()
'''.replace('USER_ID', str(world['users']['student']['id'])).replace('RESERVATION_ID', str(record.id))
    for _ in range(2):
        result = subprocess.run([sys.executable, '-c', code], env=environment,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, '隔离读取进程失败；未输出含连接信息的子进程日志'
        assert result.stdout.strip() == 'persisted'
