"""登录会话的建立、检查和撤销；不依赖浏览器或HTTP对象。"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.auth_config import SESSION_LIFETIME
from app.core.errors import DatabaseUnavailableError
from app.core.local_accounts import record_test_account
from app.core.security import hash_password, verify_password
from app.models import LoginSession, User
from app.schemas.auth import CurrentUser, LoginRequest, SessionInfo


class InvalidCredentialsError(Exception):
    pass


class InvalidSessionError(Exception):
    pass


class InvalidCsrfError(Exception):
    pass


@dataclass(frozen=True)
class LoginResult:
    info: SessionInfo
    token: str = field(repr=False)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def valid_token_format(token: str | None) -> bool:
    return token is not None and re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is not None


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    # 未找到账号也执行密码验证，避免直接跳过Argon2产生明显的时序差异。
    return hash_password(secrets.token_urlsafe(32))


def session_info(record: LoginSession, user: User) -> SessionInfo:
    return SessionInfo(
        user=CurrentUser(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            identity_type=user.identity_type,
            role=user.role,
            is_active=user.is_active,
        ),
        csrf_token=record.csrf_token,
        expires_at=record.expires_at.replace(tzinfo=UTC),
    )


def assert_session_valid(record: LoginSession | None, user: User | None) -> None:
    if (
        record is None
        or user is None
        or record.revoked_at is not None
        or record.expires_at <= utc_now()
        or not user.is_active
        or record.session_version != user.session_version
    ):
        raise InvalidSessionError()


def login_user(
    session: Session, data: LoginRequest, previous_token: str | None = None
) -> LoginResult:
    try:
        # 只取标量快照，验证密码后再次加锁读取，避免使用ORM缓存的旧启停状态。
        with session.begin():
            snapshot = session.execute(
                select(User.id, User.password_hash, User.session_version).where(
                    User.username == data.username
                )
            ).one_or_none()
        encoded = snapshot.password_hash if snapshot else dummy_password_hash()
        try:
            verified = verify_password(data.password.get_secret_value(), encoded)
        except UnknownHashError:
            verified = False
        if snapshot is None or not verified:
            raise InvalidCredentialsError()

        with session.begin():
            user = session.scalar(
                select(User)
                .where(User.id == snapshot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                user is None
                or not user.is_active
                or user.session_version != snapshot.session_version
                or user.password_hash != snapshot.password_hash
            ):
                raise InvalidCredentialsError()
            now = utc_now()
            if valid_token_format(previous_token):
                # 当前浏览器换号/重复登录时撤销它的旧凭证，其他浏览器会话保留。
                session.execute(
                    update(LoginSession)
                    .where(
                        LoginSession.token_hash == token_digest(previous_token),
                        LoginSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
            raw_token = secrets.token_urlsafe(32)
            record = LoginSession(
                token_hash=token_digest(raw_token),
                user_id=user.id,
                csrf_token=secrets.token_urlsafe(32),
                session_version=user.session_version,
                created_at=now,
                expires_at=now + SESSION_LIFETIME,
            )
            session.add(record)
            session.flush()
            result = LoginResult(info=session_info(record, user), token=raw_token)
        record_test_account(
            user_id=result.info.user.id,
            username=result.info.user.username,
            password=data.password.get_secret_value(),
            source="login",
        )
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def get_session_info(session: Session, raw_token: str | None) -> SessionInfo:
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            pair = session.execute(
                select(LoginSession, User)
                .join(User, LoginSession.user_id == User.id)
                .where(LoginSession.token_hash == token_digest(raw_token))
                .execution_options(populate_existing=True)
            ).one_or_none()
            if pair is None:
                raise InvalidSessionError()
            record, user = pair
            assert_session_valid(record, user)
            result = session_info(record, user)
        return result
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def logout_user(
    session: Session, raw_token: str | None, csrf_token: str | None
) -> None:
    if not valid_token_format(raw_token):
        raise InvalidSessionError()
    try:
        with session.begin():
            digest = token_digest(raw_token)
            user_id = session.scalar(
                select(LoginSession.user_id).where(LoginSession.token_hash == digest)
            )
            if user_id is None:
                raise InvalidSessionError()
            # 与之后的账户停用服务保持一致：先锁用户，再锁会话。
            user = session.scalar(
                select(User)
                .where(User.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            record = session.scalar(
                select(LoginSession)
                .where(LoginSession.token_hash == digest)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            assert_session_valid(record, user)
            if not valid_token_format(csrf_token) or not hmac.compare_digest(
                record.csrf_token, csrf_token
            ):
                raise InvalidCsrfError()
            record.revoked_at = utc_now()
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None


def revoke_all_user_sessions(session: Session, user_id: int) -> None:
    """在调用方事务中撤销全部会话；未来停用服务需在同一事务修改启用状态。"""
    if not session.in_transaction():
        raise RuntimeError("撤销全部会话必须由调用方明确建立事务")
    # 用标量锁查询读取最新版本，不能依赖同一Session里可能缓存的旧User版本。
    # 保留调用方尚未flush的is_active修改，避免完整refresh将它覆盖。
    current_version = session.scalar(
        select(User.session_version).where(User.id == user_id).with_for_update()
    )
    if current_version is None:
        raise ValueError("账号不存在")
    user = session.get(User, user_id)
    user.session_version = current_version + 1
    session.flush()
    session.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
