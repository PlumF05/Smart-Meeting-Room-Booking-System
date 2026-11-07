"""不依赖数据库的输入安全与故障响应检查。"""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import auth as auth_api
from app.db import session as database_session
from app.db.session import get_db
from app.main import app


PASSWORD = "private-test-only-password"
CONFIRMATION = "different-private-test-confirmation"


@pytest.fixture
def registration_client():
    session = MagicMock(spec=Session)

    def fake_db():
        yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = fake_db
    try:
        with TestClient(app) as client:
            yield client, session
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def payload(**changes):
    data = {
        "username": "non_database_registration_test",
        "full_name": "输入验证用户",
        "identity_type": "student",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    data.update(changes)
    return data


def test_password_mismatch_response_does_not_echo_input(
    registration_client, monkeypatch, caplog
):
    client, session = registration_client
    service = MagicMock(side_effect=AssertionError("输入失败不应调用注册服务"))
    monkeypatch.setattr(auth_api, "register_user", service)
    response = client.post("/api/auth/register", json=payload(confirm_password=CONFIRMATION))
    assert response.status_code == 422
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert CONFIRMATION not in response.text and CONFIRMATION not in caplog.text
    service.assert_not_called()
    session.begin.assert_not_called()


def test_malformed_json_does_not_echo_passwords(registration_client, caplog):
    client, session = registration_client
    response = client.post(
        "/api/auth/register",
        content='{"password":"' + PASSWORD + '","confirm_password":"' + CONFIRMATION + '",',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert CONFIRMATION not in response.text and CONFIRMATION not in caplog.text
    session.begin.assert_not_called()


def test_database_failure_is_503_without_exception_or_credentials(
    registration_client, caplog
):
    client, session = registration_client
    marker = "private-test-database-connection-marker"
    session.begin.side_effect = SQLAlchemyError(marker)
    response = client.post("/api/auth/register", json=payload())
    assert response.status_code == 503
    for secret in (PASSWORD, marker):
        assert secret not in response.text and secret not in caplog.text
    assert "set-cookie" not in response.headers


def test_database_factory_failure_is_safe_503(
    registration_client, monkeypatch, caplog
):
    client, _ = registration_client
    marker = "private-test-invalid-database-configuration-marker"

    def fail_factory():
        raise ValueError(marker)

    monkeypatch.setattr(database_session, "get_session_factory", fail_factory)
    # 暂时使用真实get_db生命周期，但在读取配置或连接开发库之前截断工厂。
    override = app.dependency_overrides.pop(get_db)
    try:
        response = client.post("/api/auth/register", json=payload())
    finally:
        app.dependency_overrides[get_db] = override
    assert response.status_code == 503
    assert marker not in response.text and marker not in caplog.text
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
