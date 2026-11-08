"""阶段七发现的注册审计遗漏及部分交叉时段的回归。"""
import secrets
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_reservation_submission_mysql import world, encoded_password, client, submit
from app.models import OperationLog, User
from app.services import auth as auth_service
from app.services.reservations import PersonalConflictError, RoomConflictError

pytestmark = pytest.mark.mysql


def payload():
    return dict(username='stage7_registration_' + secrets.token_hex(6), full_name='隔离审计测试',
                identity_type='student', password='Stage7-audit-only-2026', confirm_password='Stage7-audit-only-2026')


def test_registration_records_safe_event_in_same_transaction(engine, world, client):
    data = payload()
    response = client.post('/api/auth/register', json=data)
    assert response.status_code == 201
    identifier = response.json()['id']
    with Session(engine) as db:
        log = db.scalar(select(OperationLog).where(OperationLog.object_type == 'user',
                                                  OperationLog.object_id == identifier, OperationLog.action == 'register'))
        assert log is not None
        assert log.actor_user_id == identifier and log.actor_kind == 'user'
        assert log.source == 'page' and log.after_status == 'active'
        assert data['password'] not in str(log.__dict__)
        assert db.get(User, identifier).password_hash not in str(log.__dict__)


def test_failed_registration_event_rolls_back_account_and_does_not_record_password(engine, world, client, monkeypatch):
    data = payload()
    def fail(*args, **kwargs):
        raise SQLAlchemyError('synthetic audit failure')
    recorded = []
    monkeypatch.setattr(auth_service, 'append_operation', fail, raising=False)
    monkeypatch.setattr(auth_service, 'record_test_account', lambda **kwargs: recorded.append(True))
    response = client.post('/api/auth/register', json=data)
    assert response.status_code == 503
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.username == data['username'])) == 0
    assert recorded == []


@pytest.mark.parametrize('start,end', [('10:30', '11:30'), ('09:30', '10:30'), ('09:30', '11:30')])
def test_partial_crossing_conflicts_for_room_and_person(engine, world, start, end):
    submit(engine, world)
    with pytest.raises(RoomConflictError):
        submit(engine, world, label='teacher', start_time=start, end_time=end)
    with pytest.raises(PersonalConflictError):
        submit(engine, world, room_index=1, start_time=start, end_time=end)
