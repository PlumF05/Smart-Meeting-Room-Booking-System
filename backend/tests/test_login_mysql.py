"""登录会话的真实MySQL检查；只读写fixture创建的独立临时实例。"""

from datetime import UTC, datetime, timedelta
import hashlib
import os
import re
import secrets
from http.cookies import SimpleCookie

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import hash_password
from app.db.session import get_db
from app.main import app
from app.models import LoginSession, User
from app.services import login as login_service


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例测试，不能写入开发库",
    ),
]

COOKIE = "smrbs_session"
PASSWORD = "test-only-login-password-2026"
USER_FIELDS = {"id", "username", "full_name", "identity_type", "role", "is_active"}


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


def seed_user(engine, password_hash, **changes):
    values = {
        "username": "login_" + secrets.token_hex(8),
        "password_hash": password_hash,
        "full_name": "登录测试用户",
        "role": "user",
        "identity_type": "student",
        "is_active": True,
        "session_version": 1,
    }
    values.update(changes)
    with Session(engine) as database, database.begin():
        record = User(**values)
        database.add(record)
        database.flush()
        result = {"id": record.id, "username": record.username}
    return result


@pytest.fixture
def login_client(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def isolated_db():
        with factory() as database:
            yield database

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def login(client, username, **changes):
    data = {"username": username, "password": PASSWORD}
    data.update(changes)
    return client.post("/api/auth/login", json=data)


def replace_cookie(client, token):
    # 清空cookie jar，避免带域的真Cookie与手动重放Cookie同时存在。
    client.cookies.clear()
    client.cookies.set(COOKIE, token, domain="testserver.local", path="/")


def session_record(database, raw_token):
    digest = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    return database.scalar(select(LoginSession).where(LoginSession.token_hash == digest))


@pytest.mark.parametrize("role, identity", [
    ("user", "teacher"), ("user", "student"), ("admin", None),
])
def test_login_sets_cookie_and_only_returns_safe_session_info(
    engine, login_client, password_hash, role, identity, caplog
):
    user = seed_user(engine, password_hash, role=role, identity_type=identity)
    response = login(login_client, "  " + user["username"] + "  ")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert set(result) == {"user", "csrf_token", "expires_at"}
    assert set(result["user"]) == USER_FIELDS
    assert result["user"]["id"] == user["id"]
    assert result["user"]["role"] == role
    assert result["user"]["identity_type"] == identity
    assert result["user"]["is_active"] is True
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", result["csrf_token"])
    expires = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
    assert expires.utcoffset() == timedelta(0)
    assert timedelta(hours=7, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=8)

    raw_token = login_client.cookies.get(COOKIE)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token)
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert cookie[COOKIE]["httponly"]
    assert cookie[COOKIE]["samesite"].lower() == "lax"
    assert cookie[COOKIE]["path"] == "/"
    assert cookie[COOKIE]["max-age"] == "28800"
    # 本轮本机HTTP，Secure在将来的HTTPS部署中调整。
    assert not cookie[COOKIE]["secure"]
    with Session(engine) as database:
        record = session_record(database, raw_token)
        assert record is not None and record.user_id == user["id"]
        assert record.token_hash != raw_token
        assert record.token_hash == hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        assert record.csrf_token == result["csrf_token"]
        assert record.session_version == 1
        assert record.expires_at - record.created_at == timedelta(hours=8)
        assert record.revoked_at is None
    for secret in (PASSWORD, password_hash, raw_token):
        assert secret not in response.text and secret not in caplog.text
    current = login_client.get("/api/auth/me")
    assert current.status_code == 200
    assert current.headers["cache-control"] == "no-store"
    assert current.json() == result


def test_unknown_wrong_password_and_disabled_account_share_safe_401(
    engine, login_client, password_hash, caplog
):
    enabled = seed_user(engine, password_hash)
    disabled = seed_user(engine, password_hash, is_active=False)
    responses = [
        login(login_client, enabled["username"], password="wrong-private-test-password"),
        login(login_client, "not_existing_login_account"),
        login(login_client, disabled["username"]),
    ]
    assert [response.status_code for response in responses] == [401, 401, 401]
    assert responses[0].json() == responses[1].json() == responses[2].json()
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        for secret in (PASSWORD, password_hash, "wrong-private-test-password"):
            assert secret not in response.text and secret not in caplog.text
    with Session(engine) as database:
        count = database.scalar(select(func.count()).select_from(LoginSession).where(
            LoginSession.user_id.in_([enabled["id"], disabled["id"]])
        ))
        assert count == 0


@pytest.mark.parametrize("token", [None, "short-token", "A" * 43])
def test_me_rejects_missing_malformed_and_unknown_cookie(login_client, token):
    if token is not None:
        replace_cookie(login_client, token)
    response = login_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert "user" not in response.json()


@pytest.mark.parametrize("csrf", [None, "A" * 43, "bad-format"])
def test_logout_rejects_missing_or_wrong_csrf_without_revoking_session(
    engine, login_client, password_hash, csrf
):
    user = seed_user(engine, password_hash)
    assert login(login_client, user["username"]).status_code == 200
    token = login_client.cookies.get(COOKIE)
    headers = {} if csrf is None else {"X-CSRF-Token": csrf}
    response = login_client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert login_client.get("/api/auth/me").status_code == 200
    with Session(engine) as database:
        assert session_record(database, token).revoked_at is None


def test_logout_revokes_session_and_replayed_cookie_stays_invalid(
    engine, login_client, password_hash, caplog
):
    user = seed_user(engine, password_hash)
    signed_in = login(login_client, user["username"])
    token = login_client.cookies.get(COOKIE)
    response = login_client.post("/api/auth/logout", headers={
        "X-CSRF-Token": signed_in.json()["csrf_token"]
    })
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert not login_client.cookies.get(COOKIE)
    assert token not in response.text and token not in caplog.text
    with Session(engine) as database:
        assert session_record(database, token).revoked_at is not None
    replace_cookie(login_client, token)
    assert login_client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("change", ["expired", "revoked", "version", "disabled"])
def test_me_rejects_server_side_invalid_session(
    engine, login_client, password_hash, change
):
    user = seed_user(engine, password_hash)
    assert login(login_client, user["username"]).status_code == 200
    token = login_client.cookies.get(COOKIE)
    with Session(engine) as database, database.begin():
        record = session_record(database, token)
        if change == "expired":
            now = datetime.now(UTC).replace(tzinfo=None)
            record.created_at = now - timedelta(hours=9)
            record.expires_at = now - timedelta(seconds=1)
        elif change == "revoked":
            record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        elif change == "version":
            database.get(User, user["id"]).session_version += 1
        else:
            database.get(User, user["id"]).is_active = False
    assert login_client.get("/api/auth/me").status_code == 401


def test_disable_then_reenable_does_not_restore_old_session(
    engine, login_client, password_hash
):
    user = seed_user(engine, password_hash)
    assert login(login_client, user["username"]).status_code == 200
    old_token = login_client.cookies.get(COOKIE)
    with Session(engine) as database, database.begin():
        database.get(User, user["id"]).is_active = False
        login_service.revoke_all_user_sessions(database, user["id"])
    assert login_client.get("/api/auth/me").status_code == 401
    with Session(engine) as database, database.begin():
        database.get(User, user["id"]).is_active = True
    replace_cookie(login_client, old_token)
    assert login_client.get("/api/auth/me").status_code == 401
    assert login(login_client, user["username"]).status_code == 200
    assert login_client.cookies.get(COOKIE) != old_token


def test_me_reads_current_role_from_database(engine, login_client, password_hash):
    user = seed_user(engine, password_hash)
    assert login(login_client, user["username"]).json()["user"]["role"] == "user"
    # 仅隔离测试直接变更资料，产品不增加角色变更入口。
    with Session(engine) as database, database.begin():
        database.get(User, user["id"]).role = "admin"
    response = login_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"


def test_logout_does_not_revoke_other_browser_session(
    engine, login_client, password_hash
):
    user = seed_user(engine, password_hash)
    first = login(login_client, user["username"])
    with TestClient(app) as second:
        assert login(second, user["username"]).status_code == 200
        first_token = login_client.cookies.get(COOKIE)
        second_token = second.cookies.get(COOKIE)
        assert first_token != second_token
        response = login_client.post("/api/auth/logout", headers={
            "X-CSRF-Token": first.json()["csrf_token"]
        })
        assert response.status_code == 204
        assert second.get("/api/auth/me").status_code == 200


def test_relogin_rotates_current_cookie_and_revokes_old_one(
    engine, login_client, password_hash
):
    user = seed_user(engine, password_hash)
    first = login(login_client, user["username"])
    old_token = login_client.cookies.get(COOKIE)
    second = login(login_client, user["username"])
    new_token = login_client.cookies.get(COOKIE)
    assert second.status_code == 200 and new_token != old_token
    assert second.json()["csrf_token"] != first.json()["csrf_token"]
    with Session(engine) as database:
        assert session_record(database, old_token).revoked_at is not None
        assert session_record(database, new_token).revoked_at is None
    with TestClient(app) as replay:
        replace_cookie(replay, old_token)
        assert replay.get("/api/auth/me").status_code == 401
    assert login_client.get("/api/auth/me").status_code == 200


def test_switching_account_revokes_previous_browser_session(
    engine, login_client, password_hash
):
    first = seed_user(engine, password_hash)
    second = seed_user(engine, password_hash)
    assert login(login_client, first["username"]).status_code == 200
    old_token = login_client.cookies.get(COOKIE)
    response = login(login_client, second["username"])
    assert response.status_code == 200
    assert response.json()["user"]["id"] == second["id"]
    with TestClient(app) as replay:
        replace_cookie(replay, old_token)
        assert replay.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:8000.evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "cross-site"},
])
def test_login_rejects_untrusted_browser_origin(
    engine, login_client, password_hash, headers
):
    user = seed_user(engine, password_hash)
    response = login_client.post("/api/auth/login", json={
        "username": user["username"], "password": PASSWORD
    }, headers=headers)
    assert response.status_code == 403
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(LoginSession).where(
            LoginSession.user_id == user["id"]
        )) == 0


def test_trusted_origin_is_allowed_and_csrf_is_bound_to_session(
    engine, login_client, password_hash
):
    user = seed_user(engine, password_hash)
    response = login_client.post("/api/auth/login", json={
        "username": user["username"], "password": PASSWORD
    }, headers={"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200
    with TestClient(app) as second:
        other = login(second, user["username"])
        assert other.status_code == 200
        response = login_client.post("/api/auth/logout", headers={
            "X-CSRF-Token": other.json()["csrf_token"]
        })
        assert response.status_code == 403
    assert login_client.get("/api/auth/me").status_code == 200


def test_logout_rejects_untrusted_origin_even_with_correct_csrf(
    engine, login_client, password_hash
):
    user = seed_user(engine, password_hash)
    first = login(login_client, user["username"])
    response = login_client.post("/api/auth/logout", headers={
        "Origin": "https://attacker.example", "X-CSRF-Token": first.json()["csrf_token"]
    })
    assert response.status_code == 403
    assert login_client.get("/api/auth/me").status_code == 200


def test_account_disabled_during_password_verification_cannot_get_session(
    engine, login_client, password_hash, monkeypatch
):
    user = seed_user(engine, password_hash)
    original_verify = login_service.verify_password

    def verify_then_disable(password, encoded):
        verified = original_verify(password, encoded)
        with Session(engine) as database, database.begin():
            database.get(User, user["id"]).is_active = False
            login_service.revoke_all_user_sessions(database, user["id"])
        return verified

    monkeypatch.setattr(login_service, "verify_password", verify_then_disable)
    response = login(login_client, user["username"])
    assert response.status_code == 401
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(LoginSession).where(
            LoginSession.user_id == user["id"]
        )) == 0


def test_revoke_all_reads_latest_version_and_preserves_pending_disable(
    engine, password_hash
):
    user = seed_user(engine, password_hash)
    with Session(engine, autoflush=False, expire_on_commit=False) as cached:
        with cached.begin():
            cached_user = cached.get(User, user["id"])
            assert cached_user.session_version == 1
        with Session(engine) as another, another.begin():
            login_service.revoke_all_user_sessions(another, user["id"])
        # cached仍持有版本1，但数据库已是版本2；停用标记尚未flush。
        assert cached_user.session_version == 1
        with cached.begin():
            cached_user.is_active = False
            login_service.revoke_all_user_sessions(cached, user["id"])
            assert cached_user.session_version == 3
            assert cached_user.is_active is False
    with Session(engine) as verification:
        stored = verification.get(User, user["id"])
        assert stored.session_version == 3 and stored.is_active is False


def test_logout_rechecks_revocation_despite_cached_orm_objects(
    engine, login_client, password_hash
):
    user = seed_user(engine, password_hash)
    signed_in = login(login_client, user["username"])
    token = login_client.cookies.get(COOKIE)
    csrf = signed_in.json()["csrf_token"]
    with Session(engine, autoflush=False, expire_on_commit=False) as cached:
        with cached.begin():
            cached_user = cached.get(User, user["id"])
            cached_session = session_record(cached, token)
            assert cached_session.revoked_at is None
        assert login_client.post("/api/auth/logout", headers={
            "X-CSRF-Token": csrf
        }).status_code == 204
        with pytest.raises(login_service.InvalidSessionError):
            login_service.logout_user(cached, token, csrf)
        # 保留引用，确保上面的复查不能依赖对象已被垃圾回收。
        assert cached_user.id == user["id"]
