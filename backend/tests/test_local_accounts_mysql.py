"""账号清单与真实MySQL业务的连接；只写fixture创建的独立临时实例。"""

import json
import os

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core import local_accounts
from app.core.admin_config import AdminSeedSettings
from app.core.errors import DatabaseUnavailableError
from app.core.local_accounts import AccountLedger, KnownAccount, LocalAccountSettings
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.main import app
from app.models import LoginSession, User
from app.services.bootstrap import seed_admin


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例测试，不能写入开发库",
    ),
]

PASSWORD = "ledger-mysql-only-Password-2026"
USER_FIELDS = {"id", "username", "full_name", "identity_type", "role", "is_active"}


@pytest.fixture(scope="module")
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def enabled_ledger(disable_local_account_recording, monkeypatch, tmp_path):
    path = tmp_path / ".local" / "test-accounts.json"
    monkeypatch.setattr(
        local_accounts, "load_local_account_settings",
        lambda: LocalAccountSettings(enabled=True, path=path),
    )
    return path


@pytest.fixture
def client(engine, enabled_ledger):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def isolated_db():
        with factory() as database:
            yield database

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = isolated_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def registration(username):
    return {
        "username": username, "full_name": "清单隔离测试用户", "identity_type": "student",
        "password": PASSWORD, "confirm_password": PASSWORD,
    }


def create_user(engine, encoded_password, username, **changes):
    values = {
        "username": username, "full_name": "清单隔离测试用户", "identity_type": "student",
        "password_hash": encoded_password, "role": "user", "is_active": True,
        "session_version": 1,
    }
    values.update(changes)
    with Session(engine) as database, database.begin():
        user = User(**values)
        database.add(user)
        database.flush()
        return user.id


def entries(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    return {item["user_id"]: item for item in document["accounts"]}


def test_registration_records_only_after_commit_and_still_stores_argon_hash(
    engine, client, enabled_ledger, monkeypatch, caplog,
):
    committed_ids = []
    original_record = AccountLedger.record

    def assert_committed(ledger, user_id, username, password, source):
        # 使用另一条连接查到用户，证明记录钩子在真实提交之后执行。
        with Session(engine) as database:
            stored = database.get(User, user_id)
            if stored is not None and stored.username == username:
                committed_ids.append(user_id)
        return original_record(ledger, user_id, username, password, source)

    monkeypatch.setattr(AccountLedger, "record", assert_committed)
    response = client.post("/api/auth/register", json=registration("ledger_registered"))
    assert response.status_code == 201
    result = response.json()
    assert set(result) == USER_FIELDS
    assert committed_ids == [result["id"]]
    record = entries(enabled_ledger)[result["id"]]
    assert record["username"] == "ledger_registered" and record["password"] == PASSWORD
    assert record["password_status"] == "known" and record["source"] == "register"
    with Session(engine) as database:
        stored = database.get(User, result["id"])
        assert stored.password_hash.startswith("$argon2id$")
        assert stored.password_hash != PASSWORD and verify_password(PASSWORD, stored.password_hash)
        assert "password" not in stored.__table__.columns
        assert stored.password_hash not in response.text and stored.password_hash not in caplog.text
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert str(enabled_ledger) not in response.text


def test_login_fills_unknown_password_after_session_commit_without_changing_hash(
    engine, client, enabled_ledger, encoded_password, monkeypatch, caplog,
):
    user_id = create_user(engine, encoded_password, "ledger_existing_user")
    AccountLedger(enabled_ledger).sync([
        KnownAccount(user_id, "ledger_existing_user", encoded_password),
    ])
    assert entries(enabled_ledger)[user_id]["password"] is None
    original_record = AccountLedger.record
    committed_sessions = []

    def observe_session_commit(ledger, recorded_id, username, password, source):
        with Session(engine) as database:
            committed_sessions.append(database.scalar(
                select(func.count()).select_from(LoginSession).where(LoginSession.user_id == user_id)
            ))
        return original_record(ledger, recorded_id, username, password, source)

    monkeypatch.setattr(AccountLedger, "record", observe_session_commit)
    response = client.post("/api/auth/login", json={
        "username": "  LEDGER_EXISTING_USER  ", "password": PASSWORD,
    })
    assert response.status_code == 200
    assert committed_sessions == [1]
    assert set(response.json()) == {"user", "csrf_token", "expires_at"}
    assert set(response.json()["user"]) == USER_FIELDS
    record = entries(enabled_ledger)[user_id]
    assert record["password"] == PASSWORD and record["source"] == "login"
    assert record["username"] == "ledger_existing_user"
    with Session(engine) as database:
        assert database.get(User, user_id).password_hash == encoded_password
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert encoded_password not in response.text and encoded_password not in caplog.text
    assert str(enabled_ledger) not in response.text


@pytest.mark.parametrize("case", ["wrong", "unknown", "disabled"])
def test_rejected_login_never_records_even_after_dummy_password_verification(
    engine, client, enabled_ledger, encoded_password, case,
):
    username = "ledger_rejected_" + case
    if case != "unknown":
        create_user(engine, encoded_password, username, is_active=case != "disabled")
    response = client.post("/api/auth/login", json={
        "username": username, "password": "wrong-synthetic-password" if case == "wrong" else PASSWORD,
    })
    assert response.status_code == 401
    assert not enabled_ledger.exists()


def test_duplicate_registration_does_not_replace_known_password(
    client, enabled_ledger,
):
    assert client.post("/api/auth/register", json=registration("ledger_duplicate")).status_code == 201
    before = enabled_ledger.read_bytes()
    payload = registration("LEDGER_DUPLICATE")
    payload.update(password="different-synthetic-password", confirm_password="different-synthetic-password")
    assert client.post("/api/auth/register", json=payload).status_code == 409
    assert enabled_ledger.read_bytes() == before


def test_failed_registration_commit_creates_neither_user_nor_ledger(
    engine, client, enabled_ledger,
):
    def fail_commit(database):
        raise SQLAlchemyError("synthetic commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        response = client.post("/api/auth/register", json=registration("ledger_register_rollback"))
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert response.status_code == 503
    assert not enabled_ledger.exists()
    with Session(engine) as database:
        assert database.scalar(select(User.id).where(User.username == "ledger_register_rollback")) is None


def test_failed_login_commit_does_not_record_password_or_create_session(
    engine, client, enabled_ledger, encoded_password,
):
    user_id = create_user(engine, encoded_password, "ledger_login_rollback")
    commits = []

    def fail_session_commit(database):
        commits.append(True)
        if len(commits) == 2:
            raise SQLAlchemyError("synthetic login commit failure")

    event.listen(Session, "before_commit", fail_session_commit)
    try:
        response = client.post("/api/auth/login", json={
            "username": "ledger_login_rollback", "password": PASSWORD,
        })
    finally:
        event.remove(Session, "before_commit", fail_session_commit)
    assert response.status_code == 503 and len(commits) == 2
    assert not enabled_ledger.exists()
    with Session(engine) as database:
        count = database.scalar(
            select(func.count()).select_from(LoginSession).where(LoginSession.user_id == user_id)
        )
        assert count == 0


@pytest.mark.parametrize("operation", ["register", "login"])
def test_file_write_failure_does_not_change_committed_api_success(
    engine, client, enabled_ledger, encoded_password, monkeypatch, caplog, operation,
):
    username = "ledger_write_failure_" + operation
    if operation == "login":
        create_user(engine, encoded_password, username)

    def unavailable_file(*args, **kwargs):
        raise PermissionError("synthetic file error with " + PASSWORD)

    monkeypatch.setattr(AccountLedger, "record", unavailable_file)
    payload = registration(username) if operation == "register" else {
        "username": username, "password": PASSWORD,
    }
    response = client.post("/api/auth/" + operation, json=payload)
    assert response.status_code == (201 if operation == "register" else 200)
    assert not enabled_ledger.exists()
    with Session(engine) as database:
        stored = database.scalar(select(User).where(User.username == username))
        assert stored is not None and verify_password(PASSWORD, stored.password_hash)
        if operation == "login":
            assert database.scalar(
                select(func.count()).select_from(LoginSession).where(LoginSession.user_id == stored.id)
            ) == 1
    assert caplog.records
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert "synthetic file error" not in response.text + caplog.text


def test_corrupt_ledger_is_preserved_while_registration_still_succeeds(
    client, enabled_ledger, caplog,
):
    enabled_ledger.parent.mkdir(parents=True)
    original = b'{invalid-but-preserve-this-document'
    enabled_ledger.write_bytes(original)
    response = client.post("/api/auth/register", json=registration("ledger_corrupt_file"))
    assert response.status_code == 201
    assert enabled_ledger.read_bytes() == original
    assert caplog.records and PASSWORD not in caplog.text


def test_new_admin_is_recorded_but_existing_admin_is_never_overwritten(
    engine, enabled_ledger,
):
    settings = AdminSeedSettings(username="ledger_seed_admin", password=PASSWORD)
    with Session(engine) as database:
        result = seed_admin(database, settings)
    assert result.created is True
    record = entries(enabled_ledger)[result.user_id]
    assert record["password"] == PASSWORD and record["source"] == "seed_admin"
    before = enabled_ledger.read_bytes()
    with Session(engine) as database:
        original_hash = database.get(User, result.user_id).password_hash
    with Session(engine) as database:
        repeated = seed_admin(database, AdminSeedSettings(
            username=settings.username, password="different-synthetic-admin-password",
        ))
    assert repeated.created is False and repeated.user_id == result.user_id
    assert enabled_ledger.read_bytes() == before
    with Session(engine) as database:
        assert database.get(User, result.user_id).password_hash == original_hash
        assert verify_password(PASSWORD, original_hash)


def test_failed_admin_commit_does_not_record_password(engine, enabled_ledger):
    settings = AdminSeedSettings(username="ledger_seed_rollback", password=PASSWORD)
    commits = []

    def fail_admin_commit(database):
        commits.append(True)
        # 首次事务只查已有管理员，第二次才创建新账号。
        if len(commits) == 2:
            raise SQLAlchemyError("synthetic admin commit failure")

    event.listen(Session, "before_commit", fail_admin_commit)
    try:
        with Session(engine) as database, pytest.raises(DatabaseUnavailableError):
            seed_admin(database, settings)
    finally:
        event.remove(Session, "before_commit", fail_admin_commit)
    assert len(commits) == 2 and not enabled_ledger.exists()
    with Session(engine) as database:
        assert database.scalar(select(User.id).where(User.username == settings.username)) is None
