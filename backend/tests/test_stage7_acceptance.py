"""阶段七：仅显式启用的隔离MySQL、真实HTTP、备份恢复及进程重启验收。

运行：SMRBS_RUN_MYSQL_SCHEMA_TEST=1，设置SMRBS_TEST_MYSQLD后执行
python -m pytest tests/test_stage7_acceptance.py -q -s。
所有账号、会话和SQL均为本测试生成；不读取开发配置，不覆盖开发库。
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
from threading import Barrier
import time

# 同一文件也作为独立Uvicorn进程入口；不通过TestClient测量性能。
BACKEND = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND))

import httpx
import pymysql
import pytest
from sqlalchemy import MetaData, func, select, text
from sqlalchemy.orm import Session

from app.core.auth_config import SESSION_COOKIE_NAME
from app.core.config import DatabaseSettings
from app.db.session import create_database_engine
from app.models import LoginSession, MeetingRoom, Notification, OperationLog, Reservation, User
from app.services.booking_time import get_beijing_now
from app.services.login import token_digest


pytestmark = pytest.mark.mysql
OUTPUT = BACKEND.parent / "output" / "stage7"
AI_MODULES = ("langchain", "langchain_core", "langchain_community", "langgraph", "chromadb")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def write_report(name, value):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def serve():
    """凭据只由stdin交付给子进程，配置入口全部指向fixture。"""
    config = json.loads(sys.stdin.readline())
    settings = DatabaseSettings(**config["database"])
    assert settings.name == "smrbs_test" and settings.host == "127.0.0.1"
    blocked = []
    if config["block_ai"]:
        class NoAIImports:
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in AI_MODULES:
                    raise ImportError("AI dependency disabled by acceptance test")
        sys.meta_path.insert(0, NoAIImports())
        for module in AI_MODULES:
            try:
                __import__(module)
            except ImportError:
                blocked.append(module)
        assert tuple(blocked) == AI_MODULES

    from app.core import config as database_config, local_accounts
    from app.db import session as database_session
    database_config.load_database_settings = lambda: settings
    database_session.load_database_settings = lambda: settings
    local_accounts.load_local_account_settings = lambda: local_accounts.LocalAccountSettings(
        enabled=False, path=Path(config["ready"]).with_suffix(".unused"))
    database_engine = create_database_engine(settings)
    from app.main import app
    import uvicorn

    def isolated_database():
        with Session(database_engine, autoflush=False, expire_on_commit=False) as database:
            yield database
    app.dependency_overrides[database_session.get_db] = isolated_database
    database_session.get_engine = lambda: database_engine
    Path(config["ready"]).write_text(json.dumps({"blocked_ai_imports": blocked}), encoding="utf-8")
    try:
        uvicorn.run(app, host="127.0.0.1", port=config["port"], log_level="critical", access_log=False)
    finally:
        database_engine.dispose()


@contextmanager
def live_server(settings, tmp_path, *, block_ai=False):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    ready = tmp_path / f"server-{secrets.token_hex(5)}.json"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SMRBS_")}
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve"], cwd=BACKEND,
        env=environment, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=NO_WINDOW,
    )
    payload = settings.model_dump(mode="json")
    payload["password"] = settings.password.get_secret_value()
    assert process.stdin is not None
    process.stdin.write(json.dumps(dict(database=payload, port=port, ready=str(ready), block_ai=block_ai)) + "\n")
    process.stdin.close()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        with httpx.Client(base_url=base_url, trust_env=False, timeout=1) as probe:
            while time.monotonic() < deadline:
                assert process.poll() is None, "隔离HTTP进程提前退出（凭据和原始stderr不输出）"
                try:
                    if probe.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("隔离HTTP进程未按时就绪")
        yield base_url, process.pid, json.loads(ready.read_text(encoding="utf-8"))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        assert process.poll() is not None
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", port)) != 0, "隔离HTTP进程停止后端口仍监听"


@contextmanager
def restarted_mysql(engine, settings):
    """接管fixture实例的重启与替换进程清理；只允许随机测试数据目录。"""
    import ctypes
    from ctypes import wintypes

    assert settings.host == "127.0.0.1" and settings.name == "smrbs_test"
    executable = Path(os.environ["SMRBS_TEST_MYSQLD"]).resolve(strict=True)
    options = dict(host=settings.host, port=settings.port, user=settings.user,
                   password=settings.password.get_secret_value(), database=settings.name,
                   connect_timeout=1, read_timeout=3, write_timeout=3, autocommit=True)
    with pymysql.connect(**options) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT @@datadir, @@port, @@pid_file, @@server_uuid")
        raw_directory, port, raw_pid_file, server_uuid = cursor.fetchone()
    candidate = Path(raw_directory)
    assert not candidate.is_symlink() and not candidate.parent.is_symlink()
    directory = candidate.resolve(strict=True)
    instance = directory.parent
    assert directory.name == "data" and instance.name.startswith("schema-")
    assert instance.parent == (OUTPUT.parent / "mysql-tests").resolve(strict=True)
    assert port == settings.port and 1024 < port <= 65535
    pid_file = Path(raw_pid_file).resolve(strict=True)
    assert pid_file.parent == directory and not Path(raw_pid_file).is_symlink()
    original_pid = int(pid_file.read_text(encoding="ascii").strip())
    assert original_pid > 0
    data_file = directory / "ibdata1"
    original_file_identity = data_file.stat().st_ino

    # 保留原进程句柄：端口关闭之外，还证明原mysqld已真正退出。
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, original_pid)
    assert handle, "无法核实原隔离MySQL进程"
    process = None
    details = dict(first_mysql_pid=original_pid, original_mysql_graceful_shutdown=False)

    def confirmed_shutdown():
        with pymysql.connect(**options) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT @@datadir, @@port, @@server_uuid")
            actual_directory, actual_port, actual_uuid = cursor.fetchone()
            assert Path(actual_directory).resolve(strict=True) == directory
            assert actual_port == port and actual_uuid == server_uuid
            cursor.execute("SHUTDOWN")

    def assert_port_closed():
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex((settings.host, port)) != 0, "隔离MySQL停止后端口仍监听"

    try:
        image_path, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        assert kernel.QueryFullProcessImageNameW(handle, 0, image_path, ctypes.byref(length))
        assert Path(image_path.value).resolve(strict=True) == executable
        engine.dispose()
        confirmed_shutdown()
        assert kernel.WaitForSingleObject(handle, 15000) == 0, "原隔离MySQL未按时正常退出"
        assert_port_closed()
        details["original_mysql_graceful_shutdown"] = True
        process = subprocess.Popen([
            # Windows默认的监控父进程会令Popen.pid不同于服务PID；禁用监控以直接持有服务。
            str(executable), "--no-defaults", "--no-monitor", f"--basedir={executable.parent.parent}",
            f"--datadir={directory}", f"--log-error={instance / 'mysql-error.log'}",
            "--innodb-buffer-pool-size=32M", "--innodb-redo-log-capacity=8M", "--skip-log-bin",
            "--bind-address=127.0.0.1", f"--port={port}", "--mysqlx=OFF",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert process.poll() is None, "重启的隔离MySQL提前退出（原始stderr不输出）"
            try:
                with pymysql.connect(**options) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT @@datadir, @@port, @@server_uuid")
                    actual_directory, actual_port, actual_uuid = cursor.fetchone()
            except pymysql.MySQLError:
                time.sleep(0.1)
                continue
            assert Path(actual_directory).resolve(strict=True) == directory and actual_port == port
            assert actual_uuid == server_uuid and int(pid_file.read_text(encoding="ascii").strip()) == process.pid
            break
        else:
            pytest.fail("重启的隔离MySQL未按时就绪")
        details.update(second_mysql_pid=process.pid, mysql_process_replaced=process.pid != original_pid,
                       datafiles_preserved=data_file.stat().st_ino == original_file_identity,
                       same_datadir=True, same_server_uuid=actual_uuid == server_uuid)
        assert details["mysql_process_replaced"] and details["datafiles_preserved"]
        yield details
    finally:
        kernel.CloseHandle(handle)
        # 原fixture只持有已退出的原进程；替换进程必须在fixture删目录前由此处清理。
        engine.dispose()
        if process is not None:
            if process.poll() is None:
                try:
                    confirmed_shutdown()
                except (pymysql.MySQLError, AssertionError):
                    # 身份确认失败时不再发SQL；仍须通过我们持有的Popen停止自建进程。
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
            details["replacement_mysql_graceful_shutdown"] = process.returncode == 0
            assert_port_closed()
            details["mysql_port_closed_after_cleanup"] = True


def populate(engine):
    now = datetime.now(UTC).replace(tzinfo=None)
    users, rooms = [], []
    with Session(engine) as database, database.begin():
        assert database.scalar(select(func.count()).select_from(User)) == 0
        for index in range(11):
            admin = index == 10
            user = User(username=f"stage7_{index}", password_hash="synthetic-unused-login-hash",
                        full_name=f"隔离验收{index}", role="admin" if admin else "user",
                        identity_type=None if admin else "student", is_active=True, session_version=1)
            database.add(user)
            database.flush()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            database.add(LoginSession(token_hash=token_digest(token), csrf_token=csrf, user_id=user.id,
                                      session_version=1, created_at=now - timedelta(minutes=1),
                                      expires_at=now + timedelta(hours=8)))
            users.append(dict(id=user.id, token=token, csrf=csrf))
        for index in range(10):
            room = MeetingRoom(name=f"隔离验收会议室{index}", location=f"测试楼{index}", capacity=20,
                               equipment=["投影仪"], is_active=True)
            database.add(room)
            database.flush()
            rooms.append(dict(id=room.id, name=room.name, location=room.location))
        for index in range(1000):
            owner, room = users[index % 10], rooms[index % 10]
            start = now - timedelta(days=30, hours=index)
            database.add(Reservation(user_id=owner["id"], room_id=room["id"], title=f"历史会议{index}",
                purpose="隔离数据量验收", attendees=5, starts_at=start, ends_at=start + timedelta(hours=1),
                status="finished", source="page", required_equipment=["投影仪"],
                room_name_snapshot=room["name"], room_location_snapshot=room["location"],
                room_capacity_snapshot=20, room_equipment_snapshot=["投影仪"], submitted_at=start - timedelta(days=1)))
    return users[:10], users[10], rooms


def client(base_url, user):
    return httpx.Client(base_url=base_url, trust_env=False, timeout=15,
                        cookies={SESSION_COOKIE_NAME: user["token"]},
                        headers={"X-CSRF-Token": user["csrf"]})


def booking(room, round_number=0):
    return dict(room_id=room["id"], title="真实HTTP验收会议", purpose="普通业务验收", attendees=5,
                date=(get_beijing_now().date() + timedelta(days=1)).isoformat(),
                start_time=f"{10 + round_number:02}:00", end_time=f"{11 + round_number:02}:00",
                required_equipment=["投影仪"])


def normalized(value):
    if isinstance(value, datetime):
        return {"datetime": value.isoformat(timespec="microseconds")}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    return value


def database_fingerprint(engine):
    metadata = MetaData()
    metadata.reflect(engine)
    result = {}
    with engine.connect() as connection:
        for table in sorted(metadata.tables.values(), key=lambda table: table.name):
            rows = connection.execute(select(table).order_by(*table.primary_key.columns)).all()
            # SQLAlchemy按反射类型解析JSON，DATETIME统一为六位微秒，覆盖每列和每行。
            encoded = json.dumps(dict(columns=list(table.columns.keys()), rows=normalized([list(row) for row in rows])),
                                 ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            result[table.name] = dict(rows=len(rows), sha256=hashlib.sha256(encoded).hexdigest())
    return result


def verify_backup(engine, settings, tmp_path):
    binary_dir = Path(os.environ["SMRBS_TEST_MYSQLD"]).resolve().parent
    dump, mysql = binary_dir / "mysqldump.exe", binary_dir / "mysql.exe"
    assert dump.is_file() and mysql.is_file()
    assert settings.name == "smrbs_test" and settings.host == "127.0.0.1"
    option_file = tmp_path / "synthetic-mysql-client.cnf"
    escape = lambda value: value.replace("\\", "\\\\").replace('"', '\\"')
    option_file.write_text('[client]\nhost=127.0.0.1\nprotocol=tcp\n'
        f'port={settings.port}\nuser="{escape(settings.user)}"\n'
        f'password="{escape(settings.password.get_secret_value())}"\ndefault-character-set=utf8mb4\n', encoding="utf-8")
    option_file.chmod(0o600)
    backup = OUTPUT / "synthetic-acceptance-backup.sql"
    target = "smrbs_restore_" + secrets.token_hex(6)
    source_fingerprint = database_fingerprint(engine)
    restored_engine = None
    try:
        with backup.open("wb") as output:
            dumped = subprocess.run([str(dump), f"--defaults-extra-file={option_file}", "--single-transaction",
                "--no-tablespaces", "--skip-add-locks", "--set-gtid-purged=OFF", settings.name],
                stdout=output, stderr=subprocess.PIPE, timeout=60, creationflags=NO_WINDOW)
        assert dumped.returncode == 0, f"隔离备份失败（exit={dumped.returncode}；原始stderr已隐藏）"
        # 不使用--databases，dump不会生成原库的CREATE DATABASE或USE语句。
        assert not any(line.lstrip().upper().startswith((b"USE ", b"CREATE DATABASE "))
                       for line in backup.read_bytes().splitlines())
        with engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
        with backup.open("rb") as source:
            restored = subprocess.run([str(mysql), f"--defaults-extra-file={option_file}", target],
                stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60, creationflags=NO_WINDOW)
        assert restored.returncode == 0, f"隔离恢复失败（exit={restored.returncode}；原始stderr已隐藏）"
        restored_engine = create_database_engine(settings.model_copy(update={"name": target}))
        restored_fingerprint = database_fingerprint(restored_engine)
        with engine.connect() as source, restored_engine.connect() as restored:
            versions = [source.scalar(text("SELECT version_num FROM alembic_version")),
                        restored.scalar(text("SELECT version_num FROM alembic_version"))]
        report = dict(timestamp_utc=datetime.now(UTC).isoformat(), isolated=True, source_database="smrbs_test",
            target_database=target, dump_file=backup.name, dump_bytes=backup.stat().st_size,
            comparison="all table columns and ordered rows; canonical JSON/DATETIME SHA-256",
            source=source_fingerprint, restored=restored_fingerprint, alembic_versions=versions,
            equal=source_fingerprint == restored_fingerprint, source_unchanged=source_fingerprint == database_fingerprint(engine))
        write_report("backup-restore.json", report)
        assert report["equal"] and report["source_unchanged"] and versions[0] == versions[1]
        assert all(report["source"][table]["rows"] > 0 for table in (
            "users", "login_sessions", "meeting_rooms", "reservations", "operation_logs", "notifications"))
    finally:
        if restored_engine is not None:
            restored_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{target}`"))
        option_file.unlink(missing_ok=True)


def machine_details(engine):
    import ctypes
    import winreg
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "avail_extended")]
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
        cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    with engine.connect() as connection:
        mysql_version = connection.scalar(text("SELECT VERSION()"))
    return dict(os=platform.platform(), cpu=cpu, logical_cpus=os.cpu_count(), ram_bytes=memory.total_phys,
        python=platform.python_version(), mysql=mysql_version,
        packages={name: importlib.metadata.version(name) for name in ("fastapi", "uvicorn", "SQLAlchemy", "PyMySQL", "httpx", "alembic")})


def performance(base_url, engine, users, admin, rooms):
    gate = os.environ.get("SMRBS_STAGE7_PERFORMANCE_GATE")
    if gate:
        print("STAGE7_PERFORMANCE_READY: waiting for coordinator gate", flush=True)
        deadline = time.monotonic() + 600
        while not Path(gate).exists():
            assert time.monotonic() < deadline, "性能协调等待超时"
            time.sleep(0.1)
    environment = machine_details(engine)
    before = database_fingerprint(engine)
    today = get_beijing_now().date()
    stats = f"/api/admin/statistics?start_date={today - timedelta(days=100)}&end_date={today}"
    clients = [(client(base_url, user), client(base_url, admin)) for user in users]
    barrier = Barrier(10)
    def worker(index):
        regular, administrator = clients[index]
        measurements = []
        for round_number in range(2):
            identifier = None
            for kind in ("rooms", "mine", "statistics", "submit", "cancel"):
                barrier.wait(timeout=30)
                start = time.perf_counter()
                if kind == "rooms":
                    response = regular.get("/api/rooms")
                elif kind == "mine":
                    response = regular.get("/api/reservations")
                elif kind == "statistics":
                    response = administrator.get(stats)
                elif kind == "submit":
                    response = regular.post("/api/reservations", json=booking(rooms[index], round_number))
                else:
                    response = regular.post(f"/api/reservations/{identifier}/cancel", json={"reason": "验收取消"})
                elapsed = time.perf_counter() - start
                measurements.append(dict(kind=kind, status=response.status_code, seconds=elapsed))
                assert response.status_code == (201 if kind == "submit" else 200), f"普通请求{kind}出现非预期状态{response.status_code}"
                if kind == "submit":
                    identifier = response.json()["id"]
                elif kind == "cancel":
                    assert response.json()["status"] == "cancelled"
        return measurements
    try:
        # 建立连接与认证探测不计入100次业务测量；各用户和管理员独立cookie jar。
        for regular, administrator in clients:
            assert regular.get("/api/rooms").status_code == 200
            assert administrator.get(stats).status_code == 200
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, index) for index in range(10)]
            results = [sample for future in futures for sample in future.result(timeout=60)]
        wall_seconds = time.perf_counter() - started
    finally:
        for pair in clients:
            for connection in pair:
                connection.close()
    durations = sorted(item["seconds"] for item in results)
    quantile = lambda percent: durations[math.ceil(percent * len(durations)) - 1]
    statuses = {str(status): sum(item["status"] == status for item in results) for status in sorted({item["status"] for item in results})}
    report = dict(timestamp_utc=datetime.now(UTC).isoformat(), isolated=True, transport="real localhost TCP HTTP / separate Uvicorn process",
        machine=environment, concurrency=10, request_count=len(results), rounds=2, warmup_requests_excluded=20,
        authentication="10 synthetic ordinary users, independent live database sessions; separate admin cookie jars; no auth bypass",
        data_before={table: item["rows"] for table, item in before.items()},
        mix={kind: sum(item["kind"] == kind for item in results) for kind in ("rooms", "mine", "statistics", "submit", "cancel")},
        status_counts=statuses, wall_seconds=wall_seconds, percentile_method="nearest-rank, ceil(p*N), perf_counter around full HTTP response",
        p50_seconds=quantile(.5), p95_seconds=quantile(.95), max_seconds=max(durations),
        threshold_p95_seconds=2, unexpected_errors=0, samples=results,
        limitation="Single local Windows machine and isolated 32MiB MySQL buffer pool; login/password hashing, AI requests, WAN latency and long endurance excluded")
    write_report("performance.json", report)
    assert len(results) == 100 and statuses == {"200": 80, "201": 20}
    assert report["p95_seconds"] <= 2


def persisted_views(base_url, user, admin, identifier):
    with client(base_url, user) as regular, client(base_url, admin) as administrator:
        responses = dict(reservation=regular.get(f"/api/reservations/{identifier}"),
                         messages=administrator.get("/api/messages?page_size=100"),
                         audit=administrator.get("/api/admin/operations?page_size=100"))
        assert all(response.status_code == 200 for response in responses.values())
        assert regular.get("/api/rooms").status_code == 200
        assert regular.get("/docs").status_code == 200
        assert regular.get("/api/health").status_code == 200
        return {name: response.json() for name, response in responses.items()}


def no_ai_business_flow(base_url, admin, room):
    """AI导入不可用期间，真实注册及登录后完成基础审批闭环（不计入压测）。"""
    username, password = "stage7_noai_" + secrets.token_hex(6), secrets.token_urlsafe(24)
    statuses = {}
    with httpx.Client(base_url=base_url, trust_env=False, timeout=15) as ordinary, client(base_url, admin) as administrator:
        response = ordinary.post("/api/auth/register", json=dict(username=username, full_name="无AI隔离用户",
            identity_type="student", password=password, confirm_password=password))
        statuses["register"] = response.status_code
        assert response.status_code == 201
        response = ordinary.post("/api/auth/login", json=dict(username=username, password=password))
        statuses["login"] = response.status_code
        assert response.status_code == 200 and response.json()["user"]["role"] == "user"
        ordinary.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        response = ordinary.post("/api/reservations", json=booking(room))
        statuses["submit"] = response.status_code
        assert response.status_code == 201
        identifier = response.json()["id"]
        response = administrator.post(f"/api/admin/reservations/{identifier}/decision", json=dict(action="approve"))
        statuses["approve"] = response.status_code
        assert response.status_code == 200 and response.json()["status"] == "approved" and response.json()["action_applied"]
        response = ordinary.get("/api/messages")
        statuses["messages"] = response.status_code
        assert response.status_code == 200 and response.json()["total"] == 1 and response.json()["unread_count"] == 1
        message_id = response.json()["items"][0]["id"]
        response = ordinary.post(f"/api/messages/{message_id}/read", json={})
        statuses["mark_read"] = response.status_code
        assert response.status_code == 200 and response.json()["read_at"] is not None
        response = ordinary.post(f"/api/reservations/{identifier}/cancel", json={})
        statuses["cancel"] = response.status_code
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
    return dict(http_statuses=statuses, all_steps_passed=True, excluded_from_performance=True)


def test_stage7_real_http_backup_restore_and_process_restart(engine, isolated_mysql_settings, tmp_path):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    users, admin, rooms = populate(engine)
    with live_server(isolated_mysql_settings, tmp_path) as (base_url, first_pid, _):
        with client(base_url, users[0]) as ordinary:
            created = ordinary.post("/api/reservations", json=booking(rooms[0]))
            assert created.status_code == 201
            identifier = created.json()["id"]
            assert ordinary.post(f"/api/reservations/{identifier}/cancel", json={}).status_code == 200
        before_restart = persisted_views(base_url, users[0], admin, identifier)
        assert before_restart["reservation"]["status"] == "cancelled"
        assert before_restart["messages"]["total"] == 2 and before_restart["audit"]["total"] == 2
        verify_backup(engine, isolated_mysql_settings, tmp_path)
    before_hash = database_fingerprint(engine)
    with restarted_mysql(engine, isolated_mysql_settings) as mysql_restart:
        with live_server(isolated_mysql_settings, tmp_path, block_ai=True) as (base_url, second_pid, startup):
            after_restart = persisted_views(base_url, users[0], admin, identifier)
            after_hash = database_fingerprint(engine)
            report = dict(timestamp_utc=datetime.now(UTC).isoformat(), isolated=True, first_pid=first_pid,
                second_pid=second_pid, process_replaced=first_pid != second_pid, persisted_views_equal=before_restart == after_restart,
                all_database_rows_unchanged=before_hash == after_hash, database_before=before_hash, database_after=after_hash,
                reservation_status=after_restart["reservation"]["status"], readable_messages=after_restart["messages"]["total"],
                readable_audit_entries=after_restart["audit"]["total"],
                blocked_ai_imports=startup["blocked_ai_imports"], ordinary_api_and_docs_http_status=200,
                restart_type="Uvicorn and isolated mysqld processes restarted, same datafiles; no OS reboot",
                limitation="AI import unavailability simulated; no AI capability or external provider call tested")
            assert report["process_replaced"] and report["persisted_views_equal"] and report["all_database_rows_unchanged"]
            assert tuple(report["blocked_ai_imports"]) == AI_MODULES
            report["no_ai_business_flow"] = no_ai_business_flow(base_url, admin, rooms[0])
            performance(base_url, engine, users, admin, rooms)
    report.update(mysql_restart)
    write_report("restart.json", report)
    assert report["replacement_mysql_graceful_shutdown"] and report["mysql_port_closed_after_cleanup"]


if __name__ == "__main__":
    assert sys.argv[1:] == ["--serve"]
    serve()
