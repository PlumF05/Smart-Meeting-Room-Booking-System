"""按时间终止预约；调用方持有用户、房间、预约锁并管理事务。"""
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import MeetingRoom, Reservation, User
from app.schemas.operations import MessageDraft, OperationEntry
from app.services.booking_time import get_beijing_now, to_utc_naive
from app.services.operations import append_operation


def due_condition(now):
    return or_(and_(Reservation.status == "pending", Reservation.starts_at <= now),
               and_(Reservation.status == "approved", Reservation.ends_at <= now))


def apply_time_transition(session: Session, record: Reservation, now) -> bool:
    if record.status == "pending" and record.starts_at <= now:
        status, action, reason = "expired", "expire", "到开始时间仍未审批，申请已过期。"
    elif record.status == "approved" and record.ends_at <= now:
        status, action, reason = "finished", "finish", "预约计划时段已结束。"
    else:
        return False
    before = record.status
    record.status, record.status_reason, record.updated_at = status, reason, now
    append_operation(session, OperationEntry(
        event_key=f"reservation:{record.id}:{action}", actor_kind="system",
        object_type="reservation", object_id=record.id, action=action, source="system",
        before_status=before, after_status=status, reason=reason,
    ), [MessageDraft(recipient_user_id=record.user_id, title="预约申请已过期", content=reason)]
       if status == "expired" else [])
    return True


def process_due(engine, batch_size: int = 100) -> int:
    """每条记录独立事务；多worker竞争时锁后复验，唯一事件只生成一次。"""
    try:
        with Session(engine) as session, session.begin():
            ids = list(session.scalars(select(Reservation.id).where(
                due_condition(to_utc_naive(get_beijing_now()))
            ).order_by(Reservation.id).limit(batch_size)))
        changed = 0
        for identifier in ids:
            with Session(engine, autoflush=False) as session, session.begin():
                refs = session.execute(select(Reservation.user_id, Reservation.room_id).where(
                    Reservation.id == identifier)).one_or_none()
                if refs is None:
                    continue
                session.scalar(select(User.id).where(User.id == refs.user_id).with_for_update())
                session.scalar(select(MeetingRoom.id).where(MeetingRoom.id == refs.room_id).with_for_update())
                record = session.scalar(select(Reservation).where(Reservation.id == identifier)
                                        .with_for_update().execution_options(populate_existing=True))
                if record is not None:
                    changed += int(apply_time_transition(session, record, to_utc_naive(get_beijing_now())))
        return changed
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
