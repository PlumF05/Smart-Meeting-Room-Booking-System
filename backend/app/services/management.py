"""人工审批与既有会议室维护；所有写操作锁后复验身份、时间和当前资料。"""
import secrets

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.errors import DatabaseUnavailableError
from app.core.reservation_enums import RESERVATION_STATUS_LABELS, ReservationStatus
from app.models import LoginSession, MeetingRoom, Reservation, User
from app.schemas.management import DecisionInput, RoomUpdate
from app.schemas.operations import MessageDraft, OperationEntry
from app.schemas.rooms import RoomInfo
from app.services.booking_time import (
    BookingTimeError, get_beijing_now, to_utc_naive, utc_naive_to_beijing, validate_booking_window,
)
from app.services.login import InvalidSessionError, token_digest, valid_token_format
from app.services.my_reservations import present
from app.services.operations import append_operation
from app.services.reservation_lifecycle import apply_time_transition
from app.services.reservations import _assert_current_session, _has_overlap, _lock_administrator_recipients


class ManagementError(Exception):
    def __init__(self, detail: str, status: int = 409):
        self.detail, self.status = detail, status


def _authenticate_admin(session, actor_id, raw_token, csrf_token):
    # 先取得通知收件人共享锁，再锁会话，与普通提交和换号登录使用兼容顺序。
    _lock_administrator_recipients(session)
    actor = session.scalar(select(User).where(User.id == actor_id).with_for_update(read=True)
                           .execution_options(populate_existing=True))
    if actor is None or not actor.is_active:
        raise InvalidSessionError()
    if actor.role != "admin":
        raise ManagementError("当前账号无管理权限。", 403)
    login = session.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token))
                           .with_for_update().execution_options(populate_existing=True))
    now = to_utc_naive(get_beijing_now())
    _assert_current_session(login, actor, csrf_token, now)
    return now


def _record_info(record, room, applicant, now):
    return {**present(record, room, now),
            "can_approve": record.status == "pending" and record.starts_at > now,
            "can_reject": record.status == "pending" and record.starts_at > now,
            "can_revoke": record.status == "approved" and record.starts_at > now,
            "applicant": dict(
        id=applicant.id, username=applicant.username, full_name=applicant.full_name,
        is_active=applicant.is_active,
    )}


def list_applications(session, status="pending", room_id=None, sort="starts_at", direction="asc", user_id=None):
    try:
        with session.begin():
            query = select(Reservation, MeetingRoom, User).join(
                MeetingRoom, Reservation.room_id == MeetingRoom.id).join(User, Reservation.user_id == User.id)
            if room_id is not None:
                query = query.where(Reservation.room_id == room_id)
            if user_id is not None:
                query = query.where(Reservation.user_id == user_id)
            order = Reservation.starts_at if sort == "starts_at" else Reservation.submitted_at
            rows = session.execute(query.order_by(order.desc() if direction == "desc" else order.asc(), Reservation.id)).all()
            now = to_utc_naive(get_beijing_now())
            records = [_record_info(record, room, user, now) for record, room, user in rows]
            # 按即时有效状态筛选，避免worker尚未运行时将过期记录显示为待审批。
            return [record for record in records if status == "all" or record["status"] == status]
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def decide(session, reservation_id, data: DecisionInput, *, actor_id, raw_token, csrf_token):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            refs = session.execute(select(Reservation.user_id, Reservation.room_id).where(
                Reservation.id == reservation_id)).one_or_none()
            if refs is None:
                raise ManagementError("预约不存在。", 404)
            applicant = session.scalar(select(User).where(User.id == refs.user_id).with_for_update()
                                       .execution_options(populate_existing=True))
            room = session.scalar(select(MeetingRoom).where(MeetingRoom.id == refs.room_id).with_for_update()
                                  .execution_options(populate_existing=True))
            _authenticate_admin(session, actor_id, raw_token, csrf_token)
            record = session.scalar(select(Reservation).where(Reservation.id == reservation_id).with_for_update()
                                    .execution_options(populate_existing=True))
            # 等预约行锁也可能跨过开始时刻，所以在所有锁完成后再次取时间并验证会话。
            now = to_utc_naive(get_beijing_now())
            actor = session.get(User, actor_id)
            login = session.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token)))
            _assert_current_session(login, actor, csrf_token, now)
            timed = apply_time_transition(session, record, now)
            expected = "approved" if data.action == "revoke" else "pending"
            if timed or record.status != expected:
                label = RESERVATION_STATUS_LABELS[ReservationStatus(record.status)]
                return {**_record_info(record, room, applicant, now), "action_applied": False,
                        "message": f"记录当前为{label}，未重复或覆盖处理。"}
            if record.starts_at <= now:
                raise ManagementError("预约已经开始，不能撤销或补批。")
            target = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[data.action]
            if data.action == "approve":
                starts, ends = utc_naive_to_beijing(record.starts_at), utc_naive_to_beijing(record.ends_at)
                if starts.date() != ends.date():
                    raise ManagementError("申请时段不能跨日。")
                try:
                    validate_booking_window(starts.date(), starts.time(), ends.time(), now=utc_naive_to_beijing(now))
                except BookingTimeError as error:
                    raise ManagementError(str(error)) from None
                if not room.is_active:
                    raise ManagementError("会议室已停用，不能批准。")
                if record.attendees > room.capacity or not set(record.required_equipment).issubset(room.equipment):
                    raise ManagementError("当前会议室容量或设备不能满足申请，请核对后驳回或维护资源。")
                if _has_overlap(session, user_id=record.user_id, starts_at=record.starts_at,
                                ends_at=record.ends_at, now_utc=now, exclude_id=record.id):
                    raise ManagementError("申请人存在其他重叠有效预约，不能批准。")
                if _has_overlap(session, room_id=room.id, starts_at=record.starts_at,
                                ends_at=record.ends_at, now_utc=now, exclude_id=record.id):
                    raise ManagementError("会议室存在其他重叠有效预约，不能批准。")
            before = record.status
            record.status, record.status_reason, record.updated_at = target, data.reason, now
            append_operation(session, OperationEntry(
                event_key=f"reservation:{record.id}:{data.action}", actor_kind="user", actor_user_id=actor_id,
                object_type="reservation", object_id=record.id, action=data.action, source="page",
                before_status=before, after_status=target, reason=data.reason,
            ), [MessageDraft(recipient_user_id=record.user_id, title="预约审批结果",
                             content=(f"预约编号{record.id}：{RESERVATION_STATUS_LABELS[ReservationStatus(target)]}。"
                                      + (f"原因：{data.reason}" if data.reason else ""))[:1000])])
            result = {**_record_info(record, room, applicant, now), "action_applied": True,
                      "message": f"已处理：{RESERVATION_STATUS_LABELS[ReservationStatus(target)]}。"}
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def update_room(session, room_id, data: RoomUpdate, *, actor_id, raw_token, csrf_token):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            room = session.scalar(select(MeetingRoom).where(MeetingRoom.id == room_id).with_for_update()
                                  .execution_options(populate_existing=True))
            now = _authenticate_admin(session, actor_id, raw_token, csrf_token)
            if room is None:
                raise ManagementError("会议室不存在。", 404)
            # 采用有效占位条件，已到开始时间的未审批申请不继续阻挡维护。
            protected = (data.location != room.location or data.capacity != room.capacity
                         or (room.is_active and not data.is_active))
            if protected:
                ids = list(session.scalars(select(Reservation.id).where(
                    Reservation.room_id == room_id,
                    or_(and_(Reservation.status == "pending", Reservation.starts_at > now),
                        and_(Reservation.status == "approved", Reservation.ends_at > now)),
                ).order_by(Reservation.id).limit(20)))
                if ids:
                    raise ManagementError("存在未结束的有效预约，请先处理记录编号：" + "、".join(map(str, ids)))
            duplicate = session.scalar(select(MeetingRoom.id).where(MeetingRoom.name == data.name, MeetingRoom.id != room_id))
            if duplicate is not None:
                raise ManagementError("会议室名称已存在。")
            changed = any(getattr(room, key) != getattr(data, key)
                          for key in ("name", "location", "capacity", "equipment", "is_active"))
            if changed:
                before = "active" if room.is_active else "inactive"
                action = "edit" if room.is_active == data.is_active else "enable" if data.is_active else "disable"
                for key in ("name", "location", "capacity", "equipment", "is_active"):
                    setattr(room, key, getattr(data, key))
                append_operation(session, OperationEntry(
                    event_key=f"room:{room.id}:{secrets.token_hex(12)}", actor_kind="user", actor_user_id=actor_id,
                    object_type="meeting_room", object_id=room.id, action=action, source="page",
                    before_status=before, after_status="active" if room.is_active else "inactive",
                    reason=data.reason or None,
                ))
            result = RoomInfo.model_validate(room)
        return result
    except IntegrityError as error:
        if getattr(error.orig, "args", ())[:1] == (1062,):
            raise ManagementError("会议室名称已存在。") from None
        raise DatabaseUnavailableError() from None
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
