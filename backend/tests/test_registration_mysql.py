"""注册的真实MySQL接口验收；只写共用fixture建立的独立临时实例。"""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.main import app
from app.models import User


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例测试，不能写入开发库",
    ),
]

RESPONSE_FIELDS = {"id", "username", "full_name", "identity_type", "role", "is_active"}
PASSWORD = "test-only-Password-123"


def registration_payload(base_username, **changes):
    payload = {
        "username": base_username,
        "full_name": "注册验证学生",
        "identity_type": "student",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    payload.update(changes)
    return payload


@pytest.fixture
def registration_client(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def isolated_db():
        with factory() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def user_count(engine):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(User))


@pytest.mark.parametrize("identity_type", ["teacher", "student"])
def test_registration_persists_hashed_password_and_only_safe_fields(
    engine, registration_client, identity_type, caplog
):
    username = f"register_{identity_type}"
    payload = registration_payload(f"  {username}  ", identity_type=identity_type,
                                   full_name="  注册验证用户  ")
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    result = response.json()
    assert set(result) == RESPONSE_FIELDS
    assert result["id"] > 0
    assert result["username"] == username
    assert result["full_name"] == "注册验证用户"
    assert result["identity_type"] == identity_type
    assert result["role"] == "user" and result["is_active"] is True
    assert "set-cookie" not in response.headers
    with Session(engine) as session:
        stored = session.get(User, result["id"])
        assert stored is not None and stored.username == username
        assert stored.role == "user" and stored.is_active
        assert stored.session_version == 1
        assert stored.password_hash.startswith("$argon2id$")
        assert stored.password_hash != payload["password"]
        assert verify_password(payload["password"], stored.password_hash)
        assert not verify_password("wrong-test-only-password", stored.password_hash)
        assert "confirm_password" not in stored.__table__.columns
        assert "password" not in stored.__table__.columns
        assert stored.password_hash not in response.text
        assert stored.password_hash not in caplog.text
    assert PASSWORD not in response.text and PASSWORD not in caplog.text


def test_exactly_eight_character_password_is_accepted(registration_client):
    response = registration_client.post("/api/auth/register", json=registration_payload(
        "eight_character_password", password="12345678", confirm_password="12345678"
    ))
    assert response.status_code == 201


def test_password_spaces_are_preserved(engine, registration_client):
    password = "  test password  "
    response = registration_client.post("/api/auth/register", json=registration_payload(
        "password_spaces", password=password, confirm_password=password
    ))
    assert response.status_code == 201
    with Session(engine) as session:
        stored = session.get(User, response.json()["id"])
        assert verify_password(password, stored.password_hash)
        assert not verify_password(password.strip(), stored.password_hash)


@pytest.mark.parametrize("changes", [
    {"username": "   "},
    {"full_name": "\t  "},
    {"username": "a" * 65},
    {"full_name": "名" * 101},
    {"password": "1234567", "confirm_password": "1234567"},
    {"password": "p" * 1025, "confirm_password": "p" * 1025},
    {"confirm_password": "different-test-password"},
    {"identity_type": "admin"},
    {"identity_type": "TEACHER"},
    {"username": 12345678},
    {"full_name": None},
    {"password": {"secret": "dictionary-test-password"}},
    {"confirm_password": ["list-test-password"]},
    {"role": "admin"},
    {"is_active": False},
    {"session_version": 99},
    {"password_hash": "must-not-be-accepted"},
])
def test_invalid_registration_creates_no_account(
    engine, registration_client, changes, caplog
):
    before = user_count(engine)
    payload = registration_payload("invalid_registration", **changes)
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 422
    assert user_count(engine) == before
    details = response.json()["detail"]
    assert details and all(set(error) == {"loc", "msg", "type"} for error in details)
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    for marker in ("different-test-password", "dictionary-test-password", "list-test-password"):
        assert marker not in response.text and marker not in caplog.text


@pytest.mark.parametrize("missing", [
    "username", "full_name", "identity_type", "password", "confirm_password",
])
def test_required_registration_fields_are_checked(
    engine, registration_client, missing
):
    payload = registration_payload("missing_registration_field")
    del payload[missing]
    before = user_count(engine)
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 422
    assert user_count(engine) == before


@pytest.mark.parametrize("first, second", [
    ("case_unique_account", "CASE_UNIQUE_ACCOUNT"),
    ("accent_unique_account", "áccent_unique_account"),
])
def test_collation_equivalent_username_returns_conflict(
    engine, registration_client, first, second, caplog
):
    initial = registration_client.post("/api/auth/register", json=registration_payload(first))
    assert initial.status_code == 201
    before = user_count(engine)
    response = registration_client.post("/api/auth/register", json=registration_payload(second))
    assert response.status_code == 409
    assert user_count(engine) == before
    assert "登录名" in response.json()["detail"]
    assert PASSWORD not in response.text and PASSWORD not in caplog.text


def test_existing_seeded_admin_username_cannot_be_registered(
    engine, registration_client
):
    with Session(engine) as session, session.begin():
        session.add(User(
            username="seeded_test_admin", password_hash=hash_password(PASSWORD),
            full_name="隔离测试管理员", role="admin", identity_type=None,
        ))
    response = registration_client.post("/api/auth/register", json=registration_payload(
        "seeded_test_admin"
    ))
    assert response.status_code == 409
    with Session(engine) as session:
        rows = session.scalars(select(User).where(User.username == "seeded_test_admin")).all()
        assert len(rows) == 1 and rows[0].role == "admin"


def test_two_independent_connections_cannot_register_the_same_username(
    engine, registration_client, monkeypatch
):
    # 两个请求在flush前各持有独立物理连接，避免仅验证顺序调用的结果。
    barrier = Barrier(2)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    original_flush = Session.flush
    connection_ids = set()

    def synchronized_flush(session, *args, **kwargs):
        if any(isinstance(item, User) and item.username == "concurrent_account" for item in session.new):
            physical = session.connection().connection.driver_connection
            connection_ids.add(physical.thread_id())
            barrier.wait(timeout=10)
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", synchronized_flush)

    def register_one():
        with TestClient(app) as client:
            return client.post("/api/auth/register", json=registration_payload("concurrent_account"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: register_one(), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(connection_ids) == 2
    with factory() as session:
        count = session.scalar(select(func.count()).select_from(User).where(User.username == "concurrent_account"))
        assert count == 1
