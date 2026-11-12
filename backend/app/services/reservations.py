"""提交预约"""

import hmac
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.core.operation_enums import (
    OperationAction,
    OperationActor,
    OperationObject,
    OperationSource,
)
from app.core.reservation_enums import ReservationSource, ReservationStatus
from app.models import LoginSession, MeetingRoom, Reservation, User
from app.schemas.operations import MessageDraft, OperationEntry
from app.schemas.reservations import (
    ReservationCreate,
    ReservationCreated,
    ReservationRoomSnapshot,
)
from app.services.booking_time import (
    as_beijing_now,
    get_beijing_now,
    to_utc_naive,
    validate_booking_window,
)
from app.services.login import (
    InvalidCsrfError,
    InvalidSessionError,
    token_digest,
    valid_token_format,
)
from app.services.operations import append_operation
from app.services.rooms import RoomNotFoundError


class ReservationPermissionError(Exception):
    pass


class ReservationRuleError(Exception):
    """仅携带预先定义的、可直接展示的业务提示。"""


class RoomInactiveError(Exception):
    pass


class RoomConflictError(Exception):
    pass


class PersonalConflictError(Exception):
    pass


def _lock_administrator_recipients(session: Session) -> list[int]:
    """通知外键也会锁用户"""
    candidates = session.scalars(
        select(User.id).where(User.role == "admin").order_by(User.id)
    ).all()
    recipients = []
    for user_id in candidates:
        current_id = session.scalar(
            select(User.id)
            .where(User.id == user_id, User.role == "admin")
            .with_for_update(read=True)
        )
        if current_id is not None:
            recipients.append(current_id)
    return recipients


def _assert_current_session(
    record: LoginSession | None,
    user: User,
    csrf_token: str | None,
    now_utc: datetime,
) -> None:
    if (
        record is None
        or record.user_id != user.id
        or record.revoked_at is not None
        or record.expires_at <= now_utc
        or record.session_version != user.session_version
    ):
        raise InvalidSessionError()
    if not valid_token_format(csrf_token) or not hmac.compare_digest(
        record.csrf_token, csrf_token
    ):
        raise InvalidCsrfError()


def _has_overlap(
    session: Session,
    *,
    starts_at: datetime,
    ends_at: datetime,
    now_utc: datetime,
    user_id: int | None = None,
    room_id: int | None = None,
    exclude_id: int | None = None,
) -> bool:
    statement = select(Reservation.id).where(
        Reservation.starts_at < ends_at,
        Reservation.ends_at > starts_at,
        or_(
            and_(
                Reservation.status == ReservationStatus.PENDING,
                Reservation.starts_at > now_utc,
            ),
            and_(
                Reservation.status == ReservationStatus.APPROVED,
                Reservation.ends_at > now_utc,
            ),
        ),
    )
    if user_id is not None:
        statement = statement.where(Reservation.user_id == user_id)
    if room_id is not None:
        statement = statement.where(Reservation.room_id == room_id)
    if exclude_id is not None:
        statement = statement.where(Reservation.id != exclude_id)
    # 在申请人/房间父行锁内查询：即使原来没有预约行，也能串行检查并插入。
    return session.scalar(statement.limit(1)) is not None


def create_reservation_in_transaction(
    session: Session,
    data: ReservationCreate,
    *,
    user_id: int,
    raw_token: str | None,
    csrf_token: str | None,
    source: ReservationSource = ReservationSource.PAGE,
) -> ReservationCreated:
    """共享提交核心；调用方持有事务，同时提交预约、消息及自己的确认收据。

    按user→room→administrator→login顺序加锁。此函数只flush，不拥有commit或rollback。
    """
    if not session.in_transaction():
        raise RuntimeError("预约核心须在调用方事务内执行")
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    source = ReservationSource(source)
    # 同一申请人的不同房间预约、同一房间的不同申请人预约都必须排队校验。
    user = session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise InvalidSessionError()
    if user.role != "user":
        raise ReservationPermissionError()
    room = session.scalar(
        select(MeetingRoom)
        .where(MeetingRoom.id == data.room_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    recipients = _lock_administrator_recipients(session)
    record = session.scalar(
        select(LoginSession)
        .where(LoginSession.token_hash == token_digest(raw_token))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # 不能在请求刚到达时取时间：等待上述锁后，开始时间/会话可能已经失效。
    current = as_beijing_now(get_beijing_now())
    now_utc = to_utc_naive(current)
    _assert_current_session(record, user, csrf_token, now_utc)
    window = validate_booking_window(
        data.date, data.start_time, data.end_time, now=current
    )
    if room is None:
        raise RoomNotFoundError()
    if not room.is_active:
        raise RoomInactiveError()
    if data.attendees > room.capacity:
        raise ReservationRuleError("参会人数超过会议室当前容量。")
    required_equipment = list(dict.fromkeys(data.required_equipment))
    if not set(required_equipment).issubset(room.equipment):
        raise ReservationRuleError("会议室当前设备不能满足全部需求。")
    starts_at, ends_at = to_utc_naive(window.starts_at), to_utc_naive(window.ends_at)
    if _has_overlap(
        session,
        user_id=user.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now_utc=now_utc,
    ):
        raise PersonalConflictError()
    if _has_overlap(
        session,
        room_id=room.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now_utc=now_utc,
    ):
        raise RoomConflictError()

    reservation = Reservation(
        user_id=user.id,
        room_id=room.id,
        title=data.title,
        purpose=data.purpose,
        attendees=data.attendees,
        starts_at=starts_at,
        ends_at=ends_at,
        status=ReservationStatus.PENDING,
        source=source,
        required_equipment=required_equipment,
        room_name_snapshot=room.name,
        room_location_snapshot=room.location,
        room_capacity_snapshot=room.capacity,
        room_equipment_snapshot=list(room.equipment),
        submitted_at=now_utc,
        updated_at=now_utc,
    )
    session.add(reservation)
    session.flush()
    append_operation(
        session,
        OperationEntry(
            event_key=f"reservation:{reservation.id}:submit",
            actor_kind=OperationActor.USER,
            actor_user_id=user.id,
            object_type=OperationObject.RESERVATION,
            object_id=reservation.id,
            action=OperationAction.SUBMIT,
            source=OperationSource(source.value),
            after_status=ReservationStatus.PENDING,
        ),
        [
            MessageDraft(
                recipient_user_id=recipient_id,
                title="新的会议室预约申请",
                content=(
                    f"预约编号{reservation.id}：{room.name}，"
                    f"{window.date.isoformat()} {window.start_time:%H:%M}"
                    f"至{window.end_time:%H:%M}（北京时间），请审核。"
                ),
            )
            for recipient_id in recipients
        ],
    )
    result = ReservationCreated(
        id=reservation.id,
        user_id=user.id,
        room_id=room.id,
        title=reservation.title,
        purpose=reservation.purpose,
        attendees=reservation.attendees,
        date=window.date,
        start_time=window.start_time.strftime("%H:%M"),
        end_time=window.end_time.strftime("%H:%M"),
        starts_at=window.starts_at,
        ends_at=window.ends_at,
        status=ReservationStatus.PENDING,
        source=source,
        required_equipment=required_equipment,
        room_snapshot=ReservationRoomSnapshot(
            name=reservation.room_name_snapshot,
            location=reservation.room_location_snapshot,
            capacity=reservation.room_capacity_snapshot,
            equipment=reservation.room_equipment_snapshot,
        ),
        submitted_at=current,
    )
    return result


# 前端页面
def create_reservation(
    session: Session,
    data: ReservationCreate,
    *,
    user_id: int,
    raw_token: str | None,
    csrf_token: str | None,
    source: ReservationSource = ReservationSource.PAGE,
) -> ReservationCreated:
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    source = ReservationSource(source)
    try:
        with session.begin():
            result = create_reservation_in_transaction(
                session,
                data,
                user_id=user_id,
                raw_token=raw_token,
                csrf_token=csrf_token,
                source=source,
            )
        return result
    except SQLAlchemyError:
        # 包括锁超时、死锁和提交失败；不向HTTP响应传播SQL、参数或底层连接信息。
        raise DatabaseUnavailableError() from None
