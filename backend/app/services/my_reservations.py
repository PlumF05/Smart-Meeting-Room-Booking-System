"""本人记录和取消；历史不删除，取消与提交遵循相同锁顺序。"""
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import User, MeetingRoom, Reservation, LoginSession, OperationLog
from app.schemas.operations import OperationEntry, MessageDraft
from app.services.operations import append_operation
from app.services.booking_time import get_beijing_now, to_utc_naive, utc_naive_to_beijing
from app.services.login import InvalidSessionError, token_digest, valid_token_format
from app.services.reservations import _lock_administrator_recipients, _assert_current_session, ReservationPermissionError


class ReservationNotFoundError(Exception):
    pass


class CancellationConflictError(Exception):
    pass


def present(record, room, now, reminded=None):
    effective = record.status
    if effective == 'pending' and record.starts_at <= now:
        effective = 'expired'
    elif effective == 'approved' and record.ends_at <= now:
        effective = 'finished'
    result = dict(
        id=record.id, room_id=record.room_id, title=record.title, purpose=record.purpose,
        attendees=record.attendees, starts_at=utc_naive_to_beijing(record.starts_at),
        ends_at=utc_naive_to_beijing(record.ends_at), submitted_at=utc_naive_to_beijing(record.submitted_at),
        status=effective, stored_status=record.status, status_reason=record.status_reason,
        source=record.source, required_equipment=record.required_equipment,
        can_cancel=record.status in ('pending', 'approved') and record.starts_at > now,
        room_snapshot=dict(name=record.room_name_snapshot, location=record.room_location_snapshot,
                           capacity=record.room_capacity_snapshot, equipment=record.room_equipment_snapshot),
        current_room=dict(name=room.name, location=room.location, capacity=room.capacity,
                          equipment=room.equipment, is_active=room.is_active),
    )
    if reminded is not None:
        result.update(has_reminded=reminded, can_remind=not reminded and record.status == 'pending' and record.starts_at > now)
    return result


def read_mine(session: Session, user_id: int, reservation_id: int | None = None):
    try:
        with session.begin():
            statement = select(Reservation, MeetingRoom).join(MeetingRoom, Reservation.room_id == MeetingRoom.id).where(Reservation.user_id == user_id)
            if reservation_id is not None:
                statement = statement.where(Reservation.id == reservation_id)
            rows = session.execute(statement.order_by(Reservation.submitted_at.desc(), Reservation.id.desc())).all()
            if reservation_id is not None and not rows:
                raise ReservationNotFoundError()
            now = to_utc_naive(get_beijing_now())
            reminded = set(session.scalars(select(OperationLog.object_id).where(
                OperationLog.object_type == 'reservation', OperationLog.action == 'remind',
                OperationLog.object_id.in_([record.id for record, room in rows]))))
            result = [present(record, room, now, record.id in reminded) for record, room in rows]
            return result[0] if reservation_id is not None else result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def cancel_mine(session: Session, user_id: int, reservation_id: int, raw_token: str | None,
                csrf_token: str | None, reason: str | None = None):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            user = session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
            if user is None or not user.is_active:
                raise InvalidSessionError()
            if user.role != 'user':
                raise ReservationPermissionError()
            room_id = session.scalar(select(Reservation.room_id).where(Reservation.id == reservation_id, Reservation.user_id == user_id))
            if room_id is None:
                raise ReservationNotFoundError()
            room = session.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id).with_for_update().execution_options(populate_existing=True))
            recipients = _lock_administrator_recipients(session)
            login = session.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token)).with_for_update().execution_options(populate_existing=True))
            record = session.scalar(select(Reservation).where(Reservation.id == reservation_id, Reservation.user_id == user_id).with_for_update().execution_options(populate_existing=True))
            now = to_utc_naive(get_beijing_now())
            _assert_current_session(login, user, csrf_token, now)
            if record.status == 'cancelled':
                return present(record, room, now)
            if record.status not in ('pending', 'approved') or record.starts_at <= now:
                raise CancellationConflictError()
            before = record.status
            record.status, record.status_reason, record.updated_at = 'cancelled', reason, now
            append_operation(session, OperationEntry(
                event_key=f'reservation:{record.id}:cancel', actor_kind='user', actor_user_id=user_id,
                object_type='reservation', object_id=record.id, action='cancel', source='page',
                before_status=before, after_status='cancelled', reason=reason,
            ), [MessageDraft(recipient_user_id=recipient, title='预约已取消',
                             content=f'预约编号{record.id}已由申请人取消。') for recipient in recipients])
            result = present(record, room, now)
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
