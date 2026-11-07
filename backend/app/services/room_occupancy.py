"""只读时段占位，不泄露申请人、主题或预约编号，也不预留资源。"""

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import MeetingRoom, Reservation
from app.schemas.booking_time import RoomWindowQuery
from app.services.booking_time import get_beijing_now, to_utc_naive, utc_naive_to_beijing, validate_booking_window
from app.services.rooms import RoomNotFoundError


def read_occupancy(session: Session, room_id: int, criteria: RoomWindowQuery) -> dict:
    current = get_beijing_now()
    window = validate_booking_window(criteria.date, criteria.start_time, criteria.end_time, now=current)
    now = to_utc_naive(current)
    try:
        with session.begin():
            room = session.get(MeetingRoom, room_id)
            if room is None:
                raise RoomNotFoundError()
            rows = session.execute(select(Reservation.status, Reservation.starts_at, Reservation.ends_at).where(
                Reservation.room_id == room_id,
                Reservation.starts_at < to_utc_naive(window.ends_at),
                Reservation.ends_at > to_utc_naive(window.starts_at),
                or_(and_(Reservation.status == 'pending', Reservation.starts_at > now),
                    and_(Reservation.status == 'approved', Reservation.ends_at > now)),
            ).order_by(Reservation.starts_at, Reservation.ends_at)).all()
            return {
                'room_id': room_id, 'server_now': current, 'is_active': room.is_active,
                'available': room.is_active and not rows,
                'occupancy': [dict(status=row.status, starts_at=utc_naive_to_beijing(row.starts_at),
                                   ends_at=utc_naive_to_beijing(row.ends_at)) for row in rows],
                'reserves_room': False,
            }
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
