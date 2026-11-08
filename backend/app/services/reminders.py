"""每条申请仅一次成功催办；事件唯一键和事务防重复。"""
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.core.errors import DatabaseUnavailableError
from app.models import LoginSession, MeetingRoom, OperationLog, Reservation, User
from app.schemas.operations import MessageDraft, OperationEntry
from app.services.booking_time import get_beijing_now, to_utc_naive
from app.services.login import InvalidSessionError, token_digest, valid_token_format
from app.services.management import ManagementError
from app.services.operations import append_operation
from app.services.reservations import _assert_current_session, _lock_administrator_recipients


def remind(session, user_id, identifier, raw_token, csrf_token):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            user = session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
            if user is None or not user.is_active:
                raise InvalidSessionError()
            if user.role != "user":
                raise ManagementError("仅普通用户可催办本人申请。", 403)
            room_id = session.scalar(select(Reservation.room_id).where(Reservation.id == identifier, Reservation.user_id == user_id))
            if room_id is None:
                raise ManagementError("预约不存在。", 404)
            session.scalar(select(MeetingRoom.id).where(MeetingRoom.id == room_id).with_for_update())
            recipients = _lock_administrator_recipients(session)
            login = session.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token))
                                   .with_for_update().execution_options(populate_existing=True))
            record = session.scalar(select(Reservation).where(Reservation.id == identifier).with_for_update()
                                    .execution_options(populate_existing=True))
            now = to_utc_naive(get_beijing_now())
            _assert_current_session(login, user, csrf_token, now)
            if record.status != "pending" or record.starts_at <= now:
                raise ManagementError("仅可催办尚未开始的本人待审批申请。")
            key = f"reservation:{identifier}:remind"
            if session.scalar(select(OperationLog.id).where(OperationLog.event_key == key)) is not None:
                return dict(id=identifier, has_reminded=True, message="已催办，请勿重复发送。")
            append_operation(session, OperationEntry(
                event_key=key, actor_kind="user", actor_user_id=user_id, object_type="reservation",
                object_id=identifier, action="remind", source="page", before_status="pending", after_status="pending",
            ), [MessageDraft(recipient_user_id=recipient, title="预约申请催办", content=f"申请人催办预约编号{identifier}，请查看申请后人工处理。")
                for recipient in recipients])
        return dict(id=identifier, has_reminded=True, message="催办已发送。")
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
