"""仅供本机命令预置管理员，不提供管理员新增账户的Web接口。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.admin_config import AdminSeedSettings
from app.core.errors import DatabaseUnavailableError
from app.core.local_accounts import record_test_account
from app.core.security import hash_password
from app.models import User


class AdminSeedConflictError(Exception):
    pass


class AdminPasswordRequiredError(Exception):
    pass


@dataclass(frozen=True)
class AdminSeedResult:
    user_id: int
    username: str
    created: bool


def existing_result(user: User) -> AdminSeedResult:
    if user.role != "admin":
        raise AdminSeedConflictError()
    return AdminSeedResult(user_id=user.id, username=user.username, created=False)


def seed_admin(session: Session, settings: AdminSeedSettings) -> AdminSeedResult:
    try:
        # 已存在的管理员无需提供密码，重复运行不构成改密或重新启用。
        with session.begin():
            existing = session.scalar(select(User).where(User.username == settings.username))
            if existing is not None:
                return existing_result(existing)
        if settings.password is None:
            raise AdminPasswordRequiredError()
        password_hash = hash_password(settings.password.get_secret_value())
        try:
            with session.begin():
                user = User(
                    username=settings.username, full_name=settings.full_name,
                    password_hash=password_hash, role="admin", identity_type=None,
                    is_active=True, session_version=1,
                )
                session.add(user)
                session.flush()
                result = AdminSeedResult(user_id=user.id, username=user.username, created=True)
            record_test_account(
                user_id=result.user_id, username=result.username,
                password=settings.password.get_secret_value(), source="seed_admin",
            )
            return result
        except IntegrityError as error:
            if not (getattr(error.orig, "args", ()) and error.orig.args[0] == 1062):
                raise
            # 并发命令可能同时查到不存在，唯一约束决定最终只插入一次。
            with session.begin():
                existing = session.scalar(
                    select(User).where(User.username == settings.username)
                    .execution_options(populate_existing=True)
                )
                if existing is None:
                    raise DatabaseUnavailableError() from None
                return existing_result(existing)
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
