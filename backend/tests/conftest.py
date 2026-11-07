"""真实MySQL测试共用的隔离实例；默认不启动，不读取开发凭据。"""

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
import pymysql
import pytest

from app.core.config import DatabaseSettings
from app.db import session as database_session


BACKEND = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = BACKEND.parent / "output" / "mysql-tests"


@pytest.fixture(scope="module")
def isolated_mysql_settings():
    if os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1":
        pytest.skip("需显式启用独立MySQL实例测试")
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

