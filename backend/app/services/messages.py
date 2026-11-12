"""仅查询和标记本人消息，读状态可安全重试。"""
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from app.core.errors import DatabaseUnavailableError
from app.models import LoginSession, Notification, User
from app.services.booking_time import get_beijing_now, to_utc_naive, utc_naive_to_beijing
from app.services.login import InvalidSessionError, token_digest, valid_token_format
from app.services.management import ManagementError
from app.services.reservations import _assert_current_session


def list_messages(session, user_id, page=1, page_size=20, unread_only=False):
    try:
        with session.begin():
            owner = Notification.recipient_user_id == user_id
            unread_criteria = (owner, Notification.read_at.is_(None))
            unread = session.scalar(select(func.count()).select_from(Notification).where(*unread_criteria))
            criteria = unread_criteria if unread_only else (owner,)
            total = unread if unread_only else session.scalar(select(func.count()).select_from(Notification).where(owner))
            rows = session.scalars(select(Notification).where(*criteria).order_by(
                Notification.created_at.desc(), Notification.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            return dict(items=[dict(id=row.id, event_id=row.event_id, title=row.title, content=row.content,
                                    created_at=utc_naive_to_beijing(row.created_at),
                                    read_at=utc_naive_to_beijing(row.read_at) if row.read_at else None) for row in rows],
                        total=total, unread_count=unread, page=page, page_size=page_size)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def mark_read(session, user_id, identifier, raw_token, csrf_token):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            user = session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
            if user is None or not user.is_active:
                raise InvalidSessionError()
            login = session.scalar(select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token))
                                   .with_for_update().execution_options(populate_existing=True))
            item = session.scalar(select(Notification).where(Notification.id == identifier, Notification.recipient_user_id == user_id)
                                  .with_for_update().execution_options(populate_existing=True))
            now = to_utc_naive(get_beijing_now())
            _assert_current_session(login, user, csrf_token, now)
            if item is None:
                raise ManagementError("消息不存在。", 404)
            if item.read_at is None:
                item.read_at = max(now, item.created_at)
            result = dict(id=item.id, read_at=utc_naive_to_beijing(item.read_at))
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
