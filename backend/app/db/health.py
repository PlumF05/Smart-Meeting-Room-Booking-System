"""通过SQLAlchemy和PyMySQL检查指定MySQL数据库是否可访问。"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import DatabaseSettings, load_database_settings
from app.db.session import build_database_url


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
    url = build_database_url(settings)
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
        # 健康探测仍使用独立短连接，不占用业务连接池或创建ORM Session。
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
