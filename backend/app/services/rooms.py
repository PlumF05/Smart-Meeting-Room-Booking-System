"""按真实会议室属性查询，不计算尚未接入的预约占位。"""

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import MeetingRoom
from app.schemas.rooms import RoomInfo, RoomSearch


class RoomNotFoundError(Exception):
    pass


def list_rooms(session: Session, criteria: RoomSearch) -> list[RoomInfo]:
    statement = select(MeetingRoom).order_by(MeetingRoom.id)
    if criteria.attendees is not None:
        statement = statement.where(MeetingRoom.capacity >= criteria.attendees)
    if criteria.is_active is not None:
        statement = statement.where(MeetingRoom.is_active == criteria.is_active)
    for equipment in dict.fromkeys(criteria.equipment):
        # 每个所需设备都必须精确存在；参数绑定和JSON_QUOTE保留引号等字面字符。
        statement = statement.where(
            func.json_contains(MeetingRoom.equipment, func.json_quote(equipment)) == 1
        )
    try:
        with session.begin():
            rooms = session.scalars(statement).all()
            return [RoomInfo.model_validate(room) for room in rooms]
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def get_room(session: Session, room_id: int) -> RoomInfo:
    try:
        with session.begin():
            room = session.get(MeetingRoom, room_id)
            if room is None:
                raise RoomNotFoundError()
            return RoomInfo.model_validate(room)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
