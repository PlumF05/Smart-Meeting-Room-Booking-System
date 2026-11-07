"""仅使用独立MySQL检查公开占位与隐私字段。"""
from datetime import datetime
import secrets

import pytest
from sqlalchemy.orm import Session

from app.models import MeetingRoom, Reservation, User
from app.schemas.booking_time import RoomWindowQuery
from app.services import room_occupancy as service
from app.services.booking_time import BEIJING


@pytest.mark.mysql
@pytest.mark.parametrize('status,blocked', [('pending', True), ('approved', True), ('cancelled', False)])
def test_public_occupancy_is_read_only_and_hides_private_fields(engine, monkeypatch, status, blocked):
    monkeypatch.setattr(service, 'get_beijing_now', lambda: datetime(2026, 11, 7, 9, tzinfo=BEIJING))
    suffix = secrets.token_hex(8)
    with Session(engine) as db, db.begin():
        user = User(username=suffix, password_hash='synthetic', full_name='private-name', identity_type='student')
        room = MeetingRoom(name=suffix, location='test', capacity=10, equipment=[], is_active=True)
        db.add_all([user, room])
        db.flush()
        room_id = room.id
        db.add(Reservation(user_id=user.id, room_id=room.id, title='private-title', purpose='private-purpose',
                           attendees=1, starts_at=datetime(2026, 11, 8, 2), ends_at=datetime(2026, 11, 8, 3),
                           status=status, room_name_snapshot=suffix, room_location_snapshot='test',
                           room_capacity_snapshot=10, room_equipment_snapshot=[]))
    with Session(engine) as db:
        result = service.read_occupancy(db, room_id, RoomWindowQuery(
            date='2026-11-08', start_time='10:00', end_time='11:00'))
    assert result['available'] is (not blocked)
    assert len(result['occupancy']) == int(blocked)
    assert result['reserves_room'] is False
    assert 'private-' not in str(result)
    assert all(set(row) == {'status', 'starts_at', 'ends_at'} for row in result['occupancy'])
