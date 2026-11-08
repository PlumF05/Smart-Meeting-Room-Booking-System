"""注册业务：固定普通权限，以数据库唯一约束协调并发重名。"""

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.core.local_accounts import record_test_account
from app.core.security import hash_password
from app.models.user import User
from app.schemas.auth import RegisteredUser, RegistrationRequest
from app.schemas.operations import OperationEntry
from app.services.operations import append_operation


class UsernameAlreadyExistsError(Exception):
    """登录名与已有注册或预置账号重复。"""


def register_user(session: Session, data: RegistrationRequest) -> RegisteredUser:
    # Argon2计算放在事务外，避免哈希期间持有数据库连接或锁。
    password_hash = hash_password(data.password.get_secret_value())
    user_flushed = False
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
            user_flushed = True
            session.refresh(user)
            # 注册和审计共同提交；不把密码、确认密码或哈希放入操作记录。
            append_operation(session, OperationEntry(
                event_key=f"user:{user.id}:register", actor_kind="user", actor_user_id=user.id,
                object_type="user", object_id=user.id, action="register", source="page", after_status="active",
            ))
            result = RegisteredUser(
                id=user.id,
                username=user.username,
                full_name=user.full_name,
                identity_type=user.identity_type,
                role=user.role,
                is_active=user.is_active,
            )
        # 仅在数据库提交成功后记录本机测试密码；辅助文件失败不影响注册结果。
        record_test_account(
            user_id=result.id, username=result.username,
            password=data.password.get_secret_value(), source="register",
        )
        return result
    except IntegrityError as error:
        # users的唯一业务键目前只有username，1062表示与已有登录名冲突。
        if not user_flushed and getattr(error.orig, "args", ())[:1] == (1062,):
            raise UsernameAlreadyExistsError() from None
        raise DatabaseUnavailableError() from None
    except SQLAlchemyError:
        raise DatabaseUnavailableError() from None
