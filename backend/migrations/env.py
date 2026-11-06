"""Alembic迁移入口；升级不在FastAPI启动时自动执行。"""

from logging.config import fileConfig

from alembic import context
from alembic.util import CommandError
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.db.base import Base
from app.db.session import create_database_engine
from app import models  # noqa: F401：导入模型后才会登记到Base.metadata。


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # 离线生成DDL只需要MySQL方言，不读取任何本机凭据。
    context.configure(
        dialect_name="mysql",
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 集成测试传入隔离实例的连接，不能退回读取开发库配置。
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        migrate_with_connection(supplied_connection)
        return

    engine = None
    try:
        engine = create_database_engine()
        with engine.connect() as connection:
            migrate_with_connection(connection)
    except (SQLAlchemyError, OSError, ValueError):
        raise CommandError(
            "数据库迁移失败，请在本地检查连接配置、账号权限及数据库迁移状态。"
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
