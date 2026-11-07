"""按需创建连接池；每次请求独立使用Session，由业务服务控制提交。"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DatabaseSettings, load_database_settings
from app.core.errors import DatabaseUnavailableError


def build_database_url(settings: DatabaseSettings) -> URL:
    if not settings.is_configured:
        raise ValueError("数据库尚未配置，请在本地填写数据库名、账号和密码。")
    return URL.create(
        "mysql+pymysql",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
        query={"charset": "utf8mb4"},
    )


def create_database_engine(settings: DatabaseSettings | None = None) -> Engine:
    settings = settings if settings is not None else load_database_settings()
    return create_engine(
        build_database_url(settings),
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=5,
        isolation_level="READ COMMITTED",
        connect_args={"connect_timeout": 3, "read_timeout": 3, "write_timeout": 3},
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # 导入模块时不读取凭据、不连接数据库，保证应用存活检查可以独立运行。
    return create_database_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    # 这里只交付和关闭Session，不自动提交；未提交的事务在关闭时回滚。
    # 之后写业务由服务使用 with session.begin() 决定原子操作的边界。
    try:
        session_factory = get_session_factory()
    except (OSError, ValueError, SQLAlchemyError):
        raise DatabaseUnavailableError() from None
    with session_factory() as session:
        yield session
