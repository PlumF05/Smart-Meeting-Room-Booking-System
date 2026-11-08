"""只读候选查询；复用时间、资料筛选和有效占位规则，不创建预约。"""
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.core.errors import DatabaseUnavailableError
from app.schemas.booking_time import RoomWindowQuery
from app.schemas.meeting_assistant import Demand
from app.schemas.rooms import RoomSearch
from app.services.booking_time import get_beijing_now,to_utc_naive,validate_booking_window
from app.services.reservations import _has_overlap
from app.services.rooms import list_rooms
from app.services.room_occupancy import read_all_occupancy


def find_candidates(db:Session,user_id:int,draft:dict)->dict:
    demand=Demand.model_validate(draft)
    criteria=RoomWindowQuery(date=demand.date,start_time=demand.start_time,end_time=demand.end_time)
    now=get_beijing_now()
    window=validate_booking_window(criteria.date,criteria.start_time,criteria.end_time,now=now)
    try:
        with db.begin():
            personal=_has_overlap(db,user_id=user_id,starts_at=to_utc_naive(window.starts_at),
                                  ends_at=to_utc_naive(window.ends_at),now_utc=to_utc_naive(now))
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
    if personal:
        return dict(rooms=[],checked_at=now.isoformat(),reason='personal')
    rooms=list_rooms(db,RoomSearch(attendees=demand.attendees,equipment=demand.equipment,is_active=True))
    occupancy={r['room_id']:r for r in read_all_occupancy(db,criteria)}
    rooms=[r.model_dump() for r in rooms if occupancy.get(r.id,{}).get('available') is True]
    rooms.sort(key=lambda r:(r['capacity'],r['name'],r['id']))
    return dict(rooms=rooms,checked_at=get_beijing_now().isoformat(),reason='available' if rooms else 'conditions')
