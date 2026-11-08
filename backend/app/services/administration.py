"""账号启停、提交日期统计和只读操作记录。"""
from datetime import datetime, time, timedelta
import secrets
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from app.core.errors import DatabaseUnavailableError
from app.core.reservation_enums import ReservationStatus
from app.models import OperationLog, Reservation, User
from app.schemas.operations import OperationEntry
from app.services.booking_time import BEIJING, get_beijing_now, to_utc_naive, utc_naive_to_beijing
from app.services.login import InvalidSessionError, revoke_all_user_sessions, valid_token_format
from app.services.management import ManagementError, _authenticate_admin, list_applications
from app.services.operations import append_operation


def active_condition(now):
    return or_(and_(Reservation.status == "pending", Reservation.starts_at > now),
               and_(Reservation.status == "approved", Reservation.ends_at > now))


def user_info(user, count):
    return dict(id=user.id, username=user.username, full_name=user.full_name, identity_type=user.identity_type,
                role=user.role, is_active=user.is_active, created_at=utc_naive_to_beijing(user.created_at),
                active_reservation_count=count)


def list_users(session, q="", page=1, page_size=20):
    try:
        with session.begin():
            criteria = []
            if q:
                criteria.append(or_(User.username.contains(q, autoescape=True), User.full_name.contains(q, autoescape=True)))
            total = session.scalar(select(func.count()).select_from(User).where(*criteria))
            users = list(session.scalars(select(User).where(*criteria).order_by(User.id).offset((page-1)*page_size).limit(page_size)))
            active = dict(session.execute(select(Reservation.user_id, func.count()).where(
                Reservation.user_id.in_([user.id for user in users]), active_condition(to_utc_naive(get_beijing_now())))
                .group_by(Reservation.user_id)).all())
            return dict(items=[user_info(user, active.get(user.id, 0)) for user in users], total=total, page=page, page_size=page_size)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def related_reservations(session, user_id):
    try:
        with session.begin():
            if session.get(User, user_id) is None:
                raise ManagementError("账号不存在。", 404)
        return list_applications(session, status="all", user_id=user_id)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def set_user_state(session, target_id, is_active, *, actor_id, raw_token, csrf_token):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            target = session.scalar(select(User).where(User.id == target_id).with_for_update().execution_options(populate_existing=True))
            if target is None:
                raise ManagementError("账号不存在。", 404)
            # 不锁住一个管理员再等待其他管理员，且该能力始终不能启停管理员。
            if target.role != "user":
                raise ManagementError("只能启停普通账号。", 403)
            now = _authenticate_admin(session, actor_id, raw_token, csrf_token)
            if target.is_active != is_active:
                before = "active" if target.is_active else "inactive"
                target.is_active = is_active
                if not is_active:
                    revoke_all_user_sessions(session, target.id)
                append_operation(session, OperationEntry(
                    event_key=f"user:{target.id}:{secrets.token_hex(12)}", actor_kind="user", actor_user_id=actor_id,
                    object_type="user", object_id=target.id, action="enable" if is_active else "disable", source="page",
                    before_status=before, after_status="active" if is_active else "inactive",
                ))
            count = session.scalar(select(func.count()).select_from(Reservation).where(
                Reservation.user_id == target.id, active_condition(now)))
            result = user_info(target, count)
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def statistics(session, criteria):
    current = get_beijing_now()
    start = to_utc_naive(datetime.combine(criteria.start_date, time.min, BEIJING))
    end = to_utc_naive(datetime.combine(criteria.end_date + timedelta(days=1), time.min, BEIJING))
    now = to_utc_naive(current)
    effective = case(
        (and_(Reservation.status == "pending", Reservation.starts_at <= now), "expired"),
        (and_(Reservation.status == "approved", Reservation.ends_at <= now), "finished"),
        else_=Reservation.status,
    )
    try:
        with session.begin():
            query = select(effective, func.count()).where(Reservation.submitted_at >= start, Reservation.submitted_at < end)
            if criteria.room_id is not None:
                query = query.where(Reservation.room_id == criteria.room_id)
            counts = {status.value: 0 for status in ReservationStatus}
            counts.update(dict(session.execute(query.group_by(effective)).all()))
            return dict(total=sum(counts.values()), counts=counts, query_time=current,
                        definition="按提交日期筛选，按当前状态统计", start_date=criteria.start_date, end_date=criteria.end_date)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def list_operations(session, page=1, page_size=20, object_type=None, object_id=None):
    try:
        with session.begin():
            conditions = []
            if object_type is not None:
                conditions.append(OperationLog.object_type == object_type)
            if object_id is not None:
                conditions.append(OperationLog.object_id == object_id)
            total = session.scalar(select(func.count()).select_from(OperationLog).where(*conditions))
            rows = session.scalars(select(OperationLog).where(*conditions).order_by(OperationLog.id.desc())
                                   .offset((page-1)*page_size).limit(page_size)).all()
            fields = ("id", "actor_kind", "actor_user_id", "object_type", "object_id", "action", "source",
                      "before_status", "after_status", "result", "reason")
            return dict(items=[{**{field: getattr(row, field) for field in fields}, "created_at": utc_naive_to_beijing(row.created_at)}
                               for row in rows], total=total, page=page, page_size=page_size)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
