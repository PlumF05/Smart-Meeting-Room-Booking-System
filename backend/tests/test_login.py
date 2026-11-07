"""无需数据库的登录输入安全和故障响应检查。"""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app


PASSWORD = "private-test-login-password"


@pytest.fixture
def login_client():
    database = MagicMock(spec=Session)

    def fake_db():
        yield database

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = fake_db
    try:
        with TestClient(app) as client:
            yield client, database
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("payload", [
    {"username": "unit_user"},
    {"password": PASSWORD},
    {"username": "  ", "password": PASSWORD},
    {"username": "unit_user", "password": ""},
    {"username": "unit_user", "password": {"secret": PASSWORD}},
    {"username": "unit_user", "password": PASSWORD, "role": "admin"},
])
def test_login_input_rejected_without_echoing_passwords(login_client, payload, caplog):
    client, database = login_client
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 422
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    database.begin.assert_not_called()


def test_login_database_fault_is_safe_503(login_client, caplog):
    client, database = login_client
    marker = "private-test-login-database-exception"
    database.begin.side_effect = SQLAlchemyError(marker)
    response = client.post("/api/auth/login", json={
        "username": "unit_user", "password": PASSWORD
    })
    assert response.status_code == 503
    for secret in (PASSWORD, marker):
        assert secret not in response.text and secret not in caplog.text


def test_malformed_json_login_does_not_echo_password(login_client, caplog):
    client, database = login_client
    response = client.post("/api/auth/login", content=(
        '{"username":"unit_user","password":"' + PASSWORD + '",'
    ), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    database.begin.assert_not_called()


def test_late_unauthenticated_me_response_does_not_delete_newer_cookie(
    login_client, monkeypatch
):
    from app.api import auth as auth_api
    from app.services.login import InvalidSessionError

    client, _ = login_client

    def expired_session(*args):
        raise InvalidSessionError()

    monkeypatch.setattr(auth_api, "get_session_info", expired_session)
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
