"""五分钟凭证及内部原子执行基础；本模块不注册HTTP执行入口。"""
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, timedelta
import hashlib
import hmac
import json
import re
import secrets

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import AssistantConfirmation, LoginSession, User
from app.schemas.reservations import ReservationCreate
from app.services.booking_time import get_beijing_now, to_utc_naive, validate_booking_window
from app.services.login import InvalidSessionError, token_digest, valid_token_format
from app.services.reservations import ReservationPermissionError, _assert_current_session

CONFIRMATION_LIFETIME = timedelta(minutes=5)


class ConfirmationError(Exception):
    MESSAGES = {
        "invalid_plan": "确认方案格式无效，请重新生成摘要。",
        "not_found": "确认凭证不存在或不属于当前对话。",
        "superseded": "方案已变化，请使用最新摘要重新确认。",
        "expired": "确认已超过五分钟，请重新生成摘要。",
        "tampered": "确认方案校验失败，请重新生成摘要。",
        "invalid_result": "操作结果无法保存，操作未提交。",
    }

    def __init__(self, code: str):
        self.code = code
        self.message = self.MESSAGES[code]
        super().__init__(self.message)


def _context(conversation_id: str, revision: int | None = None) -> None:
    if (not isinstance(conversation_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", conversation_id) is None
        or (revision is not None and (type(revision) is not int or not 1 <= revision <= 9223372036854775807))):
        raise ConfirmationError("invalid_plan")


def _payload(payload: dict) -> tuple[dict, str]:
    try:
        data = ReservationCreate.model_validate(payload)
        result = data.model_dump(mode="json")
        result["start_time"] = data.start_time.strftime("%H:%M")
        result["end_time"] = data.end_time.strftime("%H:%M")
        canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, AttributeError):
        raise ConfirmationError("invalid_plan") from None
    return result, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _authorize(db: Session, user_id: int, raw_token: str | None, csrf_token: str | None,
               *, lock_login=False):
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    user = db.scalar(select(User).where(User.id == user_id).with_for_update()
                     .execution_options(populate_existing=True))
    if user is None or not user.is_active:
        raise InvalidSessionError()
    if user.role != "user":
        raise ReservationPermissionError()
    query = select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token))
    if lock_login:
        query = query.with_for_update()
    login = db.scalar(query.execution_options(populate_existing=True))
    _assert_current_session(login, user, csrf_token, to_utc_naive(get_beijing_now()))
    return user, login


def _matching(db: Session, user_id: int, digest: str, conversation_id: str):
    return select(AssistantConfirmation).where(
        AssistantConfirmation.user_id == user_id,
        AssistantConfirmation.session_token_hash == digest,
        AssistantConfirmation.conversation_id == conversation_id,
    ).order_by(AssistantConfirmation.id).with_for_update().execution_options(populate_existing=True)


def _verify(record: AssistantConfirmation, login: LoginSession) -> None:
    if record.session_version != login.session_version:
        raise InvalidSessionError()
    try:
        _, actual = _payload(record.payload)
    except ConfirmationError:
        raise ConfirmationError("tampered") from None
    if not hmac.compare_digest(actual, record.payload_hash) or record.action != "submit":
        raise ConfirmationError("tampered")


def _present(record: AssistantConfirmation, now) -> dict:
    state = "expired" if record.state == "pending" and record.expires_at <= now else record.state
    return dict(confirmation_id=record.token, conversation_id=record.conversation_id,
                revision=record.revision, action=record.action, summary=deepcopy(record.payload),
                created_at=record.created_at.replace(tzinfo=UTC).isoformat(),
                expires_at=record.expires_at.replace(tzinfo=UTC).isoformat(), state=state,
                result=deepcopy(record.result))


def prepare_confirmation(db: Session, *, user_id: int, raw_token: str | None, csrf_token: str | None,
                         conversation_id: str, revision: int, payload: dict) -> dict:
    _context(conversation_id, revision)
    normalized, digest = _payload(payload)
    try:
        with db.begin():
            user, login = _authorize(db, user_id, raw_token, csrf_token)
            records = db.scalars(_matching(db, user_id, token_digest(raw_token), conversation_id)).all()
            same = next((r for r in records if r.revision == revision and r.payload_hash == digest), None)
            if same is not None:
                _verify(same, login)
                # HTTP重试不能延长已展示摘要的授权窗口，也不复活失效旧方案。
                _authorize(db, user_id, raw_token, csrf_token, lock_login=True)
                result = _present(same, to_utc_naive(get_beijing_now()))
            else:
                if records and max(r.revision for r in records) > revision:
                    raise ConfirmationError("superseded")
                request = ReservationCreate.model_validate(normalized)
                for record in records:
                    if record.state == "pending":
                        record.state = "superseded"
                _authorize(db, user_id, raw_token, csrf_token, lock_login=True)
                now = get_beijing_now()
                validate_booking_window(request.date, request.start_time, request.end_time, now=now)
                created = to_utc_naive(now)
                record = AssistantConfirmation(token=secrets.token_urlsafe(32), user_id=user.id,
                    session_token_hash=token_digest(raw_token), session_version=user.session_version,
                    conversation_id=conversation_id, revision=revision, action="submit", payload=normalized,
                    payload_hash=digest, state="pending", created_at=created,
                    expires_at=created + CONFIRMATION_LIFETIME, result=None)
                db.add(record)
                db.flush()
                result = _present(record, created)
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def _find(db: Session, user_id: int, raw_token: str, conversation_id: str, confirmation_id: str):
    if not valid_token_format(confirmation_id):
        raise ConfirmationError("not_found")
    return db.scalar(_matching(db, user_id, token_digest(raw_token), conversation_id)
                     .where(AssistantConfirmation.token == confirmation_id))


def inspect_confirmation(db: Session, *, user_id: int, raw_token: str | None, csrf_token: str | None,
                         conversation_id: str, confirmation_id: str, revision: int | None = None) -> dict:
    _context(conversation_id, revision)
    try:
        with db.begin():
            _, login = _authorize(db, user_id, raw_token, csrf_token)
            record = _find(db, user_id, raw_token, conversation_id, confirmation_id)
            if record is None:
                raise ConfirmationError("not_found")
            _verify(record, login)
            if revision is not None and record.revision != revision:
                raise ConfirmationError("superseded")
            _authorize(db, user_id, raw_token, csrf_token, lock_login=True)
            result = _present(record, to_utc_naive(get_beijing_now()))
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def invalidate_conversation(db: Session, *, user_id: int, raw_token: str | None, csrf_token: str | None,
                            conversation_id: str, before_revision: int | None = None) -> None:
    _context(conversation_id)
    try:
        with db.begin():
            _authorize(db, user_id, raw_token, csrf_token)
            for record in db.scalars(_matching(db, user_id, token_digest(raw_token), conversation_id)):
                if record.state == "pending" and (before_revision is None or record.revision < before_revision):
                    record.state = "superseded"
            _authorize(db, user_id, raw_token, csrf_token, lock_login=True)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def consume_confirmation(db: Session, *, user_id: int, raw_token: str | None, csrf_token: str | None,
                         conversation_id: str, confirmation_id: str,
                         action: Callable[[Session, dict], dict], revision: int | None = None) -> dict:
    """内部事务接口：回调不可自行begin/commit，须复用实际业务规则及既有锁顺序。

    先锁申请人和凭证；不提前锁登录会话，避免后续room→admin→login锁顺序倒置。
    回调结束再锁定会话复核，已失效则整个事务回滚。8.4无HTTP执行入口。
    """
    _context(conversation_id, revision)
    try:
        with db.begin():
            _, login = _authorize(db, user_id, raw_token, csrf_token)
            record = _find(db, user_id, raw_token, conversation_id, confirmation_id)
            if record is None:
                raise ConfirmationError("not_found")
            _verify(record, login)
            if revision is not None and record.revision != revision:
                raise ConfirmationError("superseded")
            if record.state == "consumed":
                _authorize(db, user_id, raw_token, csrf_token, lock_login=True)
                return _present(record, to_utc_naive(get_beijing_now()))
            if record.state != "pending":
                raise ConfirmationError("superseded" if record.state == "superseded" else "expired")
            if record.expires_at <= to_utc_naive(get_beijing_now()):
                raise ConfirmationError("expired")
            normalized, _ = _payload(record.payload)
            result = action(db, deepcopy(normalized))
            try:
                if not isinstance(result, dict):
                    raise ValueError()
                saved = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
            except (ValueError, TypeError):
                raise ConfirmationError("invalid_result") from None
            _authorize(db, user_id, raw_token, csrf_token, lock_login=True)
            # 时间在锁等待或回调后跨越边界，尚未提交的业务也必须一同撤回。
            now = to_utc_naive(get_beijing_now())
            if record.expires_at <= now:
                raise ConfirmationError("expired")
            record.state, record.result, record.consumed_at = "consumed", saved, now
            db.flush()
            result = _present(record, now)
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
