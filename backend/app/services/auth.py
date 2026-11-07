"""注册业务：固定普通权限，以数据库唯一约束协调并发重名。"""

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.core.security import hash_password
from app.models.user import User
from app.schemas.auth import RegisteredUser, RegistrationRequest


class UsernameAlreadyExistsError(Exception):
    """登录名与已有注册或预置账号重复。"""


def register_user(session: Session, data: RegistrationRequest) -> RegisteredUser:
    # Argon2计算放在事务外，避免哈希期间持有数据库连接或锁。
    password_hash = hash_password(data.password.get_secret_value())
    try:
        with session.begin():
            user = User(
                username=data.username,
                full_name=data.full_name,
                identity_type=data.identity_type,
                password_hash=password_hash,
                role="user",
                is_active=True,
                session_version=1,
            )
            session.add(user)
            # 不先查重再依赖查询结果写入；数据库唯一约束是并发时的最终保障。
            session.flush()
            session.refresh(user)
            result = RegisteredUser(
                id=user.id,
                username=user.username,
                full_name=user.full_name,
                identity_type=user.identity_type,
                role=user.role,
                is_active=user.is_active,
            )
        # 离开begin块、成功提交后才返回；提交失败不能返回注册成功。
        return result
    except IntegrityError as error:
        # users的唯一业务键目前只有username，1062表示与已有登录名冲突。
        if getattr(error.orig, "args", ()) and error.orig.args[0] == 1062:
            raise UsernameAlreadyExistsError() from None
        raise DatabaseUnavailableError() from None
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
