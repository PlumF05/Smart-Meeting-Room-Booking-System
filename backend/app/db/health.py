"""通过SQLAlchemy和PyMySQL检查指定MySQL数据库是否可访问。"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import DatabaseSettings, load_database_settings


DatabaseCode = Literal[
    "connected", "not_configured", "invalid_configuration", "unavailable"
]


@dataclass(frozen=True)
class DatabaseCheck:
    code: DatabaseCode

    @property
    def ready(self) -> bool:
        return self.code == "connected"


def _execute_probe(settings: DatabaseSettings) -> bool:
    # 使用URL.create处理密码中的@、:、/等字符，不手工拼接连接字符串。
    url = URL.create(
        "mysql+pymysql",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
        query={"charset": "utf8mb4"},
    )
    engine = create_engine(
        url,
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "read_timeout": 3, "write_timeout": 3},
    )
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        # 健康探测使用短连接；本轮还未建立业务用的ORM Session。
        engine.dispose()


def check_database() -> DatabaseCheck:
    try:
        settings = load_database_settings()
    except (OSError, ValueError):
        return DatabaseCheck("invalid_configuration")

    if not settings.is_configured:
        return DatabaseCheck("not_configured")

    try:
        connected = _execute_probe(settings)
    except (SQLAlchemyError, OSError, ValueError):
        # 不记录或返回原始数据库异常，避免把账号、主机或凭据带到日志和接口。
        return DatabaseCheck("unavailable")
    return DatabaseCheck("connected" if connected else "unavailable")
