"""显式启用的独立MySQL实例测试；不读取开发凭据，不写入开发数据库。"""

import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
import pymysql
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DatabaseSettings
from app.db import session as database_session
from app.models import MeetingRoom, User


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
        reason="需显式启用独立MySQL实例测试，默认不启动数据库进程",
    ),
]

BACKEND = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = BACKEND.parent / "output" / "mysql-tests"


@pytest.fixture(scope="module")
def isolated_mysql_settings():
    executable_value = os.environ.get("SMRBS_TEST_MYSQLD")
    if not executable_value:
        pytest.fail("请用SMRBS_TEST_MYSQLD指定本机现有mysqld.exe，不使用开发库替代")
    executable = Path(executable_value).resolve(strict=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    instance_dir = Path(tempfile.mkdtemp(prefix="schema-", dir=OUTPUT_ROOT)).resolve()
    data_dir = instance_dir / "data"
    error_log = instance_dir / "mysql-error.log"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    common = [
        str(executable), "--no-defaults",
        f"--basedir={executable.parent.parent}", f"--datadir={data_dir}",
        f"--log-error={error_log}", "--innodb-buffer-pool-size=32M",
        "--innodb-redo-log-capacity=8M", "--skip-log-bin",
    ]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = None
    password = ""
    options = dict(host="127.0.0.1", port=port, user="root", connect_timeout=1,
                   read_timeout=3, write_timeout=3, autocommit=True, charset="utf8mb4")

    def confirm_instance(connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@datadir, @@port")
            actual_directory, actual_port = cursor.fetchone()
            assert Path(actual_directory).resolve() == data_dir.resolve()
            assert actual_port == port

    try:
        initialized = subprocess.run(
            [*common, "--initialize-insecure"], capture_output=True,
            creationflags=creation_flags, timeout=60,
        )
        assert initialized.returncode == 0, f"隔离MySQL初始化失败，检查本机日志：{error_log}"
        process = subprocess.Popen(
            [*common, "--bind-address=127.0.0.1", f"--port={port}", "--mysqlx=OFF"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert process.poll() is None, f"隔离MySQL提前退出，检查本机日志：{error_log}"
            try:
                connection = pymysql.connect(password="", **options)
            except pymysql.MySQLError:
                time.sleep(0.1)
                continue
            with connection:
                confirm_instance(connection)
                password = "Aa9!" + secrets.token_urlsafe(24)
                with connection.cursor() as cursor:
                    cursor.execute("ALTER USER CURRENT_USER() IDENTIFIED BY %s", (password,))
                    cursor.execute("CREATE DATABASE smrbs_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            break
        else:
            pytest.fail(f"隔离MySQL未按时就绪，检查本机日志：{error_log}")

        with pytest.MonkeyPatch.context() as patch:
            def forbid_development_configuration():
                raise AssertionError("独立实例测试不得读取开发数据库配置")

            patch.setattr(database_session, "load_database_settings", forbid_development_configuration)
            yield DatabaseSettings(host="127.0.0.1", port=port, name="smrbs_test", user="root", password=password)
    finally:
        if process is not None and process.poll() is None:
            try:
                with pymysql.connect(password=password, **options) as connection:
                    confirm_instance(connection)
                    with connection.cursor() as cursor:
                        cursor.execute("SHUTDOWN")
            except (pymysql.MySQLError, AssertionError):
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        if process is not None:
            with socket.socket() as stopped_probe:
                stopped_probe.settimeout(1)
                assert stopped_probe.connect_ex(("127.0.0.1", port)) != 0, (
                    f"临时端口仍在监听，保留本次数据目录供本机检查：{instance_dir}"
                )
        # 仅删除本fixture新建的随机目录，且必须在测试输出目录中；先停进程再清理。
        assert instance_dir.parent == OUTPUT_ROOT.resolve()
        assert instance_dir.name.startswith("schema-") and not instance_dir.is_symlink()
        shutil.rmtree(instance_dir)


def migration_config(connection):
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="module")
def engine(isolated_mysql_settings):
    database_engine = database_session.create_database_engine(isolated_mysql_settings)
    try:
        with database_engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        yield database_engine
    finally:
        database_engine.dispose()


def test_migrations_round_trip_and_metadata_match(engine):
    with engine.connect() as connection:
        config = migration_config(connection)
        assert MigrationContext.configure(connection).get_current_revision() == "0001"
        connection.rollback()
        command.current(config, check_heads=True)
        connection.rollback()
        command.check(config)
        connection.rollback()
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        connection.rollback()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names()) == {"alembic_version", "users", "meeting_rooms"}
        assert MigrationContext.configure(connection).get_current_revision() == "0001"
        connection.rollback()
        command.check(config)


def test_schema_storage_and_project_isolation_level(engine):
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT @@transaction_isolation")) == "READ-COMMITTED"
        rows = connection.execute(text(
            "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('users','meeting_rooms')"
        )).all()
        assert len(rows) == 2
        assert all(row.ENGINE == "InnoDB" and row.TABLE_COLLATION == "utf8mb4_0900_ai_ci" for row in rows)


def make_user(username, **changes):
    values = dict(username=username, password_hash="test-only-hash-not-login-credential", full_name="测试学生", identity_type="student")
    values.update(changes)
    return User(**values)


def test_orm_commit_persists_and_exception_rolls_back(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory.begin() as session:
        user = make_user("committed_user")
        room = MeetingRoom(name="事务验证会议室", location="测试楼", capacity=12)
        session.add_all([user, room])
        # MySQL不通过INSERT RETURNING返回这些默认字段，在Session关闭前显式读取。
        session.flush()
        session.refresh(user)
        session.refresh(room)
    assert user.id > 0 and room.id > 0
    assert user.role == "user" and user.is_active and user.session_version == 1
    assert room.equipment == [] and room.is_active
    assert user.created_at is not None and room.created_at is not None
    with factory() as session:
        assert session.scalar(select(User.username).where(User.id == user.id)) == "committed_user"
        assert session.get(MeetingRoom, room.id).equipment == []

    with pytest.raises(RuntimeError, match="intentional rollback"):
        with factory.begin() as session:
            session.add_all([make_user("rolled_back_user"), MeetingRoom(name="回滚会议室", location="测试楼", capacity=6)])
            session.flush()
            raise RuntimeError("intentional rollback")
    with factory() as session:
        assert session.scalar(select(User.id).where(User.username == "rolled_back_user")) is None
        assert session.scalar(select(MeetingRoom.id).where(MeetingRoom.name == "回滚会议室")) is None


def test_uncommitted_session_close_rolls_back(engine):
    with Session(engine) as session:
        session.add(make_user("uncommitted_user"))
        session.flush()
    with Session(engine) as session:
        assert session.scalar(select(User.id).where(User.username == "uncommitted_user")) is None


def test_get_db_dependency_closes_without_implicit_commit(engine, monkeypatch):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database_session, "get_session_factory", lambda: factory)
    dependency = database_session.get_db()
    session = next(dependency)
    session.add(make_user("dependency_uncommitted_user"))
    session.flush()
    dependency.close()
    with factory() as verification:
        assert verification.scalar(select(User.id).where(User.username == "dependency_uncommitted_user")) is None


def test_created_at_default_is_utc_even_with_beijing_connection_time_zone(engine):
    with engine.connect() as connection:
        original_time_zone = connection.scalar(text("SELECT @@session.time_zone"))
        try:
            connection.execute(text("SET SESSION time_zone='+08:00'"))
            connection.execute(User.__table__.insert().values(
                username="utc_default_user", password_hash="test-only-hash",
                full_name="时区验证学生", identity_type="student",
            ))
            created_at = connection.scalar(select(User.created_at).where(User.username == "utc_default_user"))
            utc_now = connection.scalar(text("SELECT UTC_TIMESTAMP(6)"))
            assert 0 <= (utc_now - created_at).total_seconds() < 5
        finally:
            connection.rollback()
            connection.execute(text("SET SESSION time_zone=:zone"), {"zone": original_time_zone})
            connection.rollback()


@pytest.mark.parametrize("kind", ["username", "room_name", "capacity", "role", "role_case", "role_accent", "identity", "identity_case", "session_version", "equipment"])
def test_database_constraints_reject_invalid_data(engine, kind):
    with Session(engine) as session, session.begin():
        if kind == "username":
            # 本期用户名采用现有ci排序规则，大小写不同也不能重复注册。
            session.add_all([make_user("SharedName"), make_user("sharedname")])
        elif kind == "room_name":
            session.add_all([MeetingRoom(name="重复会议室", location="测试楼", capacity=5), MeetingRoom(name="重复会议室", location="测试楼", capacity=5)])
        elif kind == "capacity":
            session.add(MeetingRoom(name="零容量会议室", location="测试楼", capacity=0))
        elif kind == "role":
            session.add(make_user("invalid_role", role="superadmin"))
        elif kind == "role_case":
            session.add(make_user("invalid_role_case", role="ADMIN"))
        elif kind == "role_accent":
            session.add(make_user("invalid_role_accent", role="usér"))
        elif kind == "identity":
            session.add(make_user("missing_identity", identity_type=None))
        elif kind == "identity_case":
            session.add(make_user("invalid_identity_case", identity_type="TEACHER"))
        elif kind == "session_version":
            session.add(make_user("invalid_session_version", session_version=0))
        else:
            session.add(MeetingRoom(name="错误设备类型会议室", location="测试楼", capacity=5, equipment={"projector": True}))
        with pytest.raises(DBAPIError) as error:
            session.flush()
        assert error.value.orig.args[0] == (1062 if kind in {"username", "room_name"} else 3819)
        session.rollback()


@pytest.mark.parametrize("identity_type", [None, "teacher", "student"])
def test_admin_identity_does_not_add_a_role_restriction(engine, identity_type):
    with Session(engine) as session:
        session.add(make_user(f"admin_{identity_type}", role="admin", identity_type=identity_type))
        session.flush()
        session.rollback()
