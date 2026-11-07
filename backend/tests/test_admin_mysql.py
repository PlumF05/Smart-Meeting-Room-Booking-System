"""管理员预置与角色边界；只使用fixture建立的独立MySQL实例。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import os
import secrets
from threading import Barrier

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.cli import seed_admin as seed_cli
from app.core.admin_config import AdminSeedSettings
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.main import app
from app.models import LoginSession, User
from app.services import bootstrap
from app.services.bootstrap import AdminPasswordRequiredError, AdminSeedConflictError, seed_admin


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例测试，不能写入开发库",
    ),
]

PASSWORD = "test-only-admin-password-2026"
USER_FIELDS = {"id", "username", "full_name", "identity_type", "role", "is_active"}


def unique_name(prefix):
    return prefix + "_" + secrets.token_hex(6)


def settings_for(username=None, **changes):
    values = dict(
        username=username or unique_name("seed_admin"),
        full_name="隔离测试管理员", password=PASSWORD,
    )
    values.update(changes)
    return AdminSeedSettings(**values)


@pytest.fixture(scope="module")
def regular_password_hash():
    return hash_password(PASSWORD)


def create_regular(engine, password_hash, identity="student", username=None):
    with Session(engine) as database, database.begin():
        user = User(
            username=username or unique_name("regular"), password_hash=password_hash,
            full_name="隔离测试普通用户", role="user", identity_type=identity,
            is_active=True, session_version=1,
        )
        database.add(user)
        database.flush()
        return user.id, user.username


@pytest.fixture
def client(engine):
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


def login(client, username):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return response


def test_seed_creates_only_active_admin_and_hashes_password(engine, caplog):
    settings = settings_for()
    with Session(engine) as database:
        result = seed_admin(database, settings)
    assert result.created is True and result.user_id > 0
    with Session(engine) as database:
        stored = database.get(User, result.user_id)
        assert stored.username == settings.username
        assert stored.full_name == settings.full_name
        assert stored.role == "admin" and stored.identity_type is None
        assert stored.is_active and stored.session_version == 1
        assert stored.password_hash.startswith("$argon2id$")
        assert stored.password_hash != PASSWORD
        assert verify_password(PASSWORD, stored.password_hash)
        assert not verify_password("wrong-test-only-password", stored.password_hash)
        assert stored.password_hash not in repr(result)
    assert PASSWORD not in repr(result) and PASSWORD not in caplog.text


def test_repeated_seed_does_not_reset_password_details_or_disabled_state(engine):
    initial = settings_for()
    with Session(engine) as database:
        result = seed_admin(database, initial)
    with Session(engine) as database, database.begin():
        user = database.get(User, result.user_id)
        user.full_name = "已维护的管理员资料"
        user.is_active = False
        user.session_version = 4
    with Session(engine) as database:
        before = tuple(database.execute(select(User.__table__).where(User.id == result.user_id)).one())
    with Session(engine) as database:
        repeated = seed_admin(database, settings_for(
            initial.username, full_name="不应覆盖的姓名", password="different-test-password"
        ))
    assert repeated.created is False and repeated.user_id == result.user_id
    with Session(engine) as database:
        after = tuple(database.execute(select(User.__table__).where(User.id == result.user_id)).one())
        assert before == after
        assert verify_password(PASSWORD, database.get(User, result.user_id).password_hash)
    # 已存在的管理员不需要再次提供密码。
    with Session(engine) as database:
        assert seed_admin(database, settings_for(initial.username, password=None)).created is False


def test_seed_cannot_promote_collation_equivalent_regular_user(
    engine, regular_password_hash
):
    username = unique_name("occupied")
    user_id, _ = create_regular(engine, regular_password_hash, username=username)
    with Session(engine) as database:
        before = tuple(database.execute(select(User.__table__).where(User.id == user_id)).one())
    with Session(engine) as database, pytest.raises(AdminSeedConflictError):
        seed_admin(database, settings_for(username.upper()))
    with Session(engine) as database:
        after = tuple(database.execute(select(User.__table__).where(User.id == user_id)).one())
        assert before == after
        assert database.get(User, user_id).role == "user"


def test_new_seed_requires_password_and_creates_no_account(engine):
    username = unique_name("no_password")
    with Session(engine) as database, pytest.raises(AdminPasswordRequiredError):
        seed_admin(database, settings_for(username, password=None))
    with Session(engine) as database:
        assert database.scalar(select(User.id).where(User.username == username)) is None


def test_concurrent_seed_creates_one_admin_and_other_command_reuses_it(engine, monkeypatch):
    settings = settings_for()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    ready = Barrier(2)
    original_hash = bootstrap.hash_password

    def synchronized_hash(password):
        encoded = original_hash(password)
        # 两个命令均查到不存在后，再同时进入真实数据库插入竞争。
        ready.wait(timeout=10)
        return encoded

    monkeypatch.setattr(bootstrap, "hash_password", synchronized_hash)

    def run_seed(_):
        with factory() as database:
            return seed_admin(database, settings)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run_seed, range(2)))
    assert sorted(result.created for result in results) == [False, True]
    assert len({result.user_id for result in results}) == 1
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(User).where(
            User.username == settings.username
        )) == 1


def test_ordinary_user_winning_seed_race_is_not_promoted(engine, monkeypatch):
    settings = settings_for()
    original_hash = bootstrap.hash_password

    def hash_then_register_ordinary(password):
        encoded = original_hash(password)
        create_regular(engine, encoded, username=settings.username)
        return encoded

    monkeypatch.setattr(bootstrap, "hash_password", hash_then_register_ordinary)
    with Session(engine) as database, pytest.raises(AdminSeedConflictError):
        seed_admin(database, settings)
    with Session(engine) as database:
        records = database.scalars(select(User).where(User.username == settings.username)).all()
        assert len(records) == 1 and records[0].role == "user"


def test_cli_uses_explicit_isolated_config_and_never_prints_password(
    engine, tmp_path, monkeypatch, capsys, caplog
):
    username = unique_name("cli_admin")
    config = tmp_path / ".env.admin"
    config.write_text(
        f"SMRBS_ADMIN_USERNAME={username}\nSMRBS_ADMIN_NAME=本地CLI测试管理员\n"
        f"SMRBS_ADMIN_PASSWORD='{PASSWORD}'\n", encoding="utf-8",
    )
    monkeypatch.setattr(seed_cli, "create_database_engine", lambda: engine)
    assert seed_cli.main(["--config", str(config)]) == 0
    first = capsys.readouterr()
    assert "已创建" in first.out
    assert PASSWORD not in first.out + first.err + caplog.text
    assert seed_cli.main(["--config", str(config)]) == 0
    repeated = capsys.readouterr()
    assert "已存在" in repeated.out
    assert PASSWORD not in repeated.out + repeated.err + caplog.text
    with Session(engine) as database:
        assert database.scalar(select(func.count()).select_from(User).where(
            User.username == username, User.role == "admin"
        )) == 1


@pytest.mark.parametrize("role, identity, own, other", [
    ("admin", None, "admin", "user"),
    ("user", "teacher", "user", "admin"),
    ("user", "student", "user", "admin"),
])
def test_role_workspace_allows_only_current_database_role(
    engine, client, regular_password_hash, role, identity, own, other, caplog
):
    if role == "admin":
        settings = settings_for()
        with Session(engine) as database:
            result = seed_admin(database, settings)
        user_id, username = result.user_id, result.username
    else:
        user_id, username = create_regular(engine, regular_password_hash, identity)
    signed_in = login(client, username)
    raw_token = client.cookies.get("smrbs_session")
    response = client.get(f"/api/workspaces/{own}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert set(result) == {"workspace", "user"}
    assert result["workspace"] == own
    assert set(result["user"]) == USER_FIELDS
    assert result["user"]["id"] == user_id
    assert result["user"]["role"] == role
    assert result["user"]["identity_type"] == identity
    for secret in (PASSWORD, regular_password_hash, raw_token, signed_in.json()["csrf_token"]):
        assert secret not in response.text and secret not in caplog.text
    rejected = client.get(f"/api/workspaces/{other}", params={"role": other, "user_id": user_id})
    assert rejected.status_code == 403
    assert rejected.headers["cache-control"] == "no-store"
    assert "user" not in rejected.json()


@pytest.mark.parametrize("workspace", ["admin", "user"])
def test_workspaces_require_login_even_if_request_claims_role(client, workspace):
    response = client.get(f"/api/workspaces/{workspace}", params={"role": workspace})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("change", ["expired", "revoked", "disabled", "version"])
def test_workspace_rechecks_server_side_session_validity(
    engine, client, regular_password_hash, change
):
    user_id, username = create_regular(engine, regular_password_hash)
    login(client, username)
    digest = hashlib.sha256(client.cookies.get("smrbs_session").encode("ascii")).hexdigest()
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as database, database.begin():
        record = database.scalar(select(LoginSession).where(LoginSession.token_hash == digest))
        if change == "expired":
            record.created_at = now - timedelta(hours=9)
            record.expires_at = now - timedelta(seconds=1)
        elif change == "revoked":
            record.revoked_at = now
        elif change == "disabled":
            database.get(User, user_id).is_active = False
        else:
            database.get(User, user_id).session_version += 1
    response = client.get("/api/workspaces/user")
    assert response.status_code == 401
    assert "user" not in response.json()


def test_workspace_role_is_read_from_database_not_old_cookie_or_query(
    engine, client, regular_password_hash
):
    user_id, username = create_regular(engine, regular_password_hash)
    login(client, username)
    assert client.get("/api/workspaces/admin", params={"role": "admin"}).status_code == 403
    # 直接改库仅用于隔离权限测试，产品不提供角色变更功能。
    with Session(engine) as database, database.begin():
        database.get(User, user_id).role = "admin"
    assert client.get("/api/workspaces/admin").status_code == 200
    assert client.get("/api/workspaces/user").status_code == 403


def test_public_registration_cannot_create_or_overwrite_admin(engine, client):
    settings = settings_for()
    with Session(engine) as database:
        result = seed_admin(database, settings)
    payload = dict(
        username=unique_name("public_admin"), full_name="非法角色测试",
        identity_type="teacher", password=PASSWORD, confirm_password=PASSWORD, role="admin",
    )
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422
    with Session(engine) as database:
        assert database.scalar(select(User.id).where(User.username == payload["username"])) is None
    del payload["role"]
    payload["username"] = settings.username
    assert client.post("/api/auth/register", json=payload).status_code == 409
    with Session(engine) as database:
        assert database.get(User, result.user_id).role == "admin"


def test_logged_out_cookie_cannot_reenter_workspace(engine, client, regular_password_hash):
    _, username = create_regular(engine, regular_password_hash)
    signed_in = login(client, username)
    token = client.cookies.get("smrbs_session")
    assert client.post("/api/auth/logout", headers={
        "X-CSRF-Token": signed_in.json()["csrf_token"]
    }).status_code == 204
    client.cookies.set("smrbs_session", token, domain="testserver.local", path="/")
    assert client.get("/api/workspaces/user").status_code == 401
