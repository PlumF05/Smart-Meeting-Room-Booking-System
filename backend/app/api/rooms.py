"""登录后可查询会议室资料；当前接口不作时段可预约承诺。"""

from typing import Annotated
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_current_user
from app.db.session import get_db
from app.schemas.rooms import RoomInfo, RoomSearch
from app.schemas.booking_time import RoomWindowCheck, RoomWindowInfo, RoomWindowQuery
from app.services.booking_time import (
    BOOKING_DAYS, CLOSES_AT, MAX_DURATION_MINUTES, MIN_DURATION_MINUTES, OPENS_AT,
    SLOT_MINUTES, BookingTimeError, as_beijing_now, get_beijing_now, validate_booking_window,
)
from app.services.rooms import RoomNotFoundError, get_room, list_rooms


router = APIRouter(
    prefix="/api/rooms", tags=["会议室"], dependencies=[Depends(get_current_user)],
    responses={401: {"description": "未登录或登录已失效"}, 503: {"description": "数据库暂不可用"}},
)


@router.get(
    "", response_model=list[RoomInfo], summary="查询会议室基础资料",
    description="按人数、所需设备（全部满足）及启停状态筛选，按编号排序。未提供时段占位查询。",
)
def rooms(
    criteria: Annotated[RoomSearch, Query()],
    session: Annotated[Session, Depends(get_db)],
) -> list[RoomInfo]:
    return list_rooms(session, criteria)


@router.get(
    "/query-window", response_model=RoomWindowCheck, summary="读取北京时间规则或校验查询时段",
    description="不传参数时读取当前规则；传入日期、起止时间时三项须完整。只检查时间规则，不检查或产生预约占位。",
    responses={422: {"description": "时间格式、完整性或业务规则不符合要求"}},
)
def query_window(
    criteria: Annotated[RoomWindowQuery, Query()],
    now: Annotated[datetime, Depends(get_beijing_now)],
) -> RoomWindowCheck:
    # 同一响应只读一次时钟，避免恰好跨越午夜时规则日期与校验基准不一致。
    current = as_beijing_now(now)
    window = None
    if criteria.date is not None:
        try:
            checked = validate_booking_window(criteria.date, criteria.start_time, criteria.end_time, now=current)
        except BookingTimeError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        window = RoomWindowInfo(
            date=checked.date, start_time=checked.start_time.strftime("%H:%M"),
            end_time=checked.end_time.strftime("%H:%M"),
            starts_at=checked.starts_at, ends_at=checked.ends_at,
        )
    return RoomWindowCheck(
        server_now=current, min_date=current.date(), max_date=current.date() + timedelta(days=BOOKING_DAYS - 1),
        opens_at=OPENS_AT.strftime("%H:%M"), closes_at=CLOSES_AT.strftime("%H:%M"),
        slot_minutes=SLOT_MINUTES, min_duration_minutes=MIN_DURATION_MINUTES,
        max_duration_minutes=MAX_DURATION_MINUTES, time_rules_passed=window is not None, window=window,
    )


@router.get("/{room_id}/occupancy", summary="查询会议室时段占位")
def room_occupancy(
    room_id: Annotated[int, Path(ge=1, le=9223372036854775807)],
    criteria: Annotated[RoomWindowQuery, Query()],
    session: Annotated[Session, Depends(get_db)],
):
    from app.services.room_occupancy import read_occupancy
    if criteria.date is None:
        raise HTTPException(status_code=422, detail="请同时填写日期、开始时间和结束时间。")
    try:
        return read_occupancy(session, room_id, criteria)
    except RoomNotFoundError:
        raise HTTPException(status_code=404, detail="会议室不存在。") from None
    except BookingTimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.get("/occupancy", summary="一次查询全部会议室的公开时段占位")
def all_occupancy(criteria: Annotated[RoomWindowQuery, Query()], session: Annotated[Session, Depends(get_db)]):
    from app.services.room_occupancy import read_all_occupancy
    try:
        return read_all_occupancy(session, criteria)
    except BookingTimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.get(
    "/{room_id}", response_model=RoomInfo, summary="查看指定会议室基础资料",
    responses={404: {"description": "会议室不存在"}},
)
def room_detail(
    room_id: Annotated[int, Path(ge=1, le=9223372036854775807)],
    session: Annotated[Session, Depends(get_db)],
) -> RoomInfo:
    try:
        return get_room(session, room_id)
    except RoomNotFoundError:
        raise HTTPException(status_code=404, detail="会议室不存在。") from None
