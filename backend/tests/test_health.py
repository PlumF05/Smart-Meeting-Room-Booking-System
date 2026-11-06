"""健康检查测试。默认不读取开发凭据，不使用SQLite，也不写入任何数据库。"""

import os
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import DatabaseSettings, load_database_settings
from app.db import health as database_health
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def configured_settings():
    # 仅作为模拟连接参数，不能用于连接开发数据库。
    return DatabaseSettings(
        host="127.0.0.1",
        port=3306,
        name="health_test_database",
        user="health_test_user",
        password="fake@password:/?#${LITERAL}",
    )


def fake_engine(monkeypatch, *, result=1, failure=None, failure_stage="execute"):
    """模拟SQLAlchemy连接，便于断言实际执行的SQL与资源释放。"""
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = result
    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False
    engine = MagicMock()
    engine.connect.return_value = context
    if failure is not None:
        if failure_stage == "connect":
            engine.connect.side_effect = failure
        else:
            connection.execute.side_effect = failure
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(database_health, "create_engine", factory)
    return factory, engine, context, connection


def test_application_and_docs_do_not_depend_on_database(monkeypatch, client):
    def forbidden_load():
        raise AssertionError("应用存活和文档不应读取数据库配置")

    monkeypatch.setattr(database_health, "load_database_settings", forbidden_load)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["component"] == "application"
    assert response.json()["code"] == "running"
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/health" in schema.json()["paths"]
    assert "503" in schema.json()["paths"]["/api/health/db"]["get"]["responses"]


def test_missing_configuration_returns_503_without_connecting(
    tmp_path, monkeypatch, client
):
    # 显式传environ={}，不读取机器上的真实SMRBS_DB_*变量。
    settings = load_database_settings(tmp_path / "does-not-exist.env", environ={})
    monkeypatch.setattr(database_health, "load_database_settings", lambda: settings)
    factory = MagicMock(side_effect=AssertionError("未配置时不得尝试连接"))
    monkeypatch.setattr(database_health, "create_engine", factory)
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json()["code"] == "not_configured"
    assert response.json()["component"] == "database"
    assert response.json()["status"] == "error"
    factory.assert_not_called()


def test_environment_values_override_file_and_keep_unspecified_file_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SMRBS_DB_HOST=127.0.0.1\n"
        "SMRBS_DB_PORT=3307\n"
        "SMRBS_DB_NAME=from_file\n"
        "SMRBS_DB_USER=file_user\n"
        "SMRBS_DB_PASSWORD='file_password'\n",
        encoding="utf-8-sig",
    )
    settings = load_database_settings(
        env_file,
        environ={"SMRBS_DB_PORT": "3308", "SMRBS_DB_USER": "environment_user"},
    )
    assert settings.host == "127.0.0.1"
    assert settings.port == 3308
    assert settings.name == "from_file"
    assert settings.user == "environment_user"
    assert settings.password.get_secret_value() == "file_password"
    assert settings.is_configured


def test_dotenv_keeps_password_special_characters_without_interpolation(tmp_path):
    password = "fake@password:/?#${SMRBS_TEST_LITERAL}"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SMRBS_DB_NAME=test_database\nSMRBS_DB_USER=test_user\n"
        f"SMRBS_DB_PASSWORD='{password}'\n",
        encoding="utf-8",
    )
    settings = load_database_settings(
        env_file, environ={"SMRBS_TEST_LITERAL": "must_not_be_substituted"}
    )
    assert settings.password.get_secret_value() == password
    assert password not in repr(settings)


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_invalid_port_is_rejected_without_showing_values(tmp_path, port):
    with pytest.raises(ValidationError) as error:
        load_database_settings(
            tmp_path / "absent.env", environ={"SMRBS_DB_PORT": port}
        )
    assert "input_value=" not in str(error.value)


@pytest.mark.parametrize("missing", ["name", "user", "password"])
def test_all_credential_fields_are_required(missing):
    fields = {"name": "test_database", "user": "test_user", "password": "fake"}
    fields[missing] = ""
    assert not DatabaseSettings(**fields).is_configured


def test_invalid_config_returns_503_without_leaking_input(monkeypatch, client, tmp_path):
    marker = "fake-sensitive-port-marker"

    def bad_settings():
        return load_database_settings(
            tmp_path / "absent.env", environ={"SMRBS_DB_PORT": marker}
        )

    monkeypatch.setattr(database_health, "load_database_settings", bad_settings)
    factory = MagicMock()
    monkeypatch.setattr(database_health, "create_engine", factory)
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json()["code"] == "invalid_configuration"
    assert marker not in response.text
    factory.assert_not_called()


def test_success_uses_mysql_read_only_query_and_releases_resources(
    monkeypatch, client, configured_settings
):
    monkeypatch.setattr(
        database_health, "load_database_settings", lambda: configured_settings
    )
    factory, engine, context, connection = fake_engine(monkeypatch)
    response = client.get("/api/health/db")
    assert response.status_code == 200
    assert response.json()["code"] == "connected"
    url = factory.call_args.args[0]
    assert url.drivername == "mysql+pymysql"
    assert url.username == configured_settings.user
    assert url.password == configured_settings.password.get_secret_value()
    assert url.database == configured_settings.name
    assert url.query["charset"] == "utf8mb4"
    options = factory.call_args.kwargs
    assert options["poolclass"] is NullPool
    assert options["echo"] is False
    assert options["hide_parameters"] is True
    assert options["connect_args"] == {
        "connect_timeout": 3,
        "read_timeout": 3,
        "write_timeout": 3,
    }
    engine.connect.assert_called_once_with()
    connection.execute.assert_called_once()
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    connection.execute.return_value.scalar_one.assert_called_once_with()
    connection.commit.assert_not_called()
    context.__exit__.assert_called_once()
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("failure_stage", ["connect", "execute"])
def test_database_failure_is_sanitized_and_disposes_engine(
    monkeypatch, client, configured_settings, caplog, failure_stage
):
    marker = "fake-sensitive-database-exception-marker"
    monkeypatch.setattr(
        database_health, "load_database_settings", lambda: configured_settings
    )
    _, engine, context, _ = fake_engine(
        monkeypatch,
        failure=SQLAlchemyError(marker),
        failure_stage=failure_stage,
    )
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json()["code"] == "unavailable"
    assert marker not in response.text
    assert marker not in caplog.text
    assert configured_settings.password.get_secret_value() not in response.text
    assert configured_settings.password.get_secret_value() not in caplog.text
    engine.dispose.assert_called_once_with()
    if failure_stage == "execute":
        context.__exit__.assert_called_once()


def test_unexpected_query_value_is_not_reported_as_connected(
    monkeypatch, client, configured_settings
):
    monkeypatch.setattr(
        database_health, "load_database_settings", lambda: configured_settings
    )
    _, engine, context, _ = fake_engine(monkeypatch, result=2)
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json()["code"] == "unavailable"
    context.__exit__.assert_called_once()
    engine.dispose.assert_called_once_with()


def test_real_pymysql_connection_to_non_listening_local_port_is_controlled(
    monkeypatch, client
):
    # 绑定但不监听的临时本机端口，不接触开发MySQL，也不模拟驱动。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        settings = DatabaseSettings(
            host="127.0.0.1",
            port=reserved_port.getsockname()[1],
            name="unused_probe_database",
            user="unused_probe_user",
            password="not-a-real-password",
        )
        monkeypatch.setattr(database_health, "load_database_settings", lambda: settings)
        started = time.monotonic()
        response = client.get("/api/health/db")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["code"] == "unavailable"
    assert elapsed < 8, "本地拒绝连接不应无限等待"
    assert settings.password.get_secret_value() not in response.text


@pytest.mark.mysql
@pytest.mark.skipif(
    os.environ.get("SMRBS_RUN_MYSQL_TEST") != "1",
    reason="需显式启用，并在本地提供真实MySQL配置；默认不连接开发数据库",
)
def test_explicit_real_mysql_read_only_probe():
    result = database_health.check_database()
    assert result.ready, f"只读MySQL探测尚未通过，状态码：{result.code}"
