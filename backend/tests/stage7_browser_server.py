"""隔离浏览器验收服务；随机MySQL实例，受停止标记和最长运行时间约束。"""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
os.environ["SMRBS_RUN_MYSQL_SCHEMA_TEST"] = "1"
os.environ["SMRBS_TEST_MYSQLD"] = r"D:\MySQL\MySQL Server 8.0\bin\mysqld.exe"

from alembic import command
from sqlalchemy.orm import Session
import uvicorn
from conftest import isolated_mysql_settings, migration_config
from app.api import auth_dependencies
from app.core import local_accounts
from app.core.security import hash_password
from app.db.session import create_database_engine, get_db
from app.main import app
from app.models import User
from app.services.room_seed import seed_rooms


def main():
    output = BACKEND.parent / "output" / "stage7"
    output.mkdir(parents=True, exist_ok=True)
    stop = output / f"browser-stop-{secrets.token_hex(6)}"
    instance = isolated_mysql_settings.__wrapped__()
    settings = next(instance)
    engine = create_database_engine(settings)
    server = None
    thread = None
    try:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        with Session(engine) as db, db.begin():
            db.add(User(username="stage7_admin", full_name="阶段七隔离管理员", role="admin",
                        identity_type=None, is_active=True, password_hash=hash_password("Stage7-only-2026")))
        with Session(engine) as db:
            seed_rooms(db)
        def isolated_db():
            with Session(engine, autoflush=False, expire_on_commit=False) as db:
                yield db
        app.dependency_overrides[get_db] = isolated_db
        auth_dependencies.TRUSTED_ORIGINS = (*auth_dependencies.TRUSTED_ORIGINS, "http://127.0.0.1:5187")
        local_accounts.load_local_account_settings = lambda: local_accounts.LocalAccountSettings(
            enabled=False, path=output / "unused-ledger.json")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18007, log_level="warning"))
        thread = threading.Thread(target=server.run)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("浏览器验收服务未就绪")
            time.sleep(0.1)
        (output / "browser-runtime.json").write_text(json.dumps({"stop": str(stop), "api_port": 18007,
            "frontend_port": 5187, "isolated": True}), encoding="utf-8")
        print("STAGE7_ISOLATED_BROWSER_READY", flush=True)
        deadline = time.monotonic() + 2700
        while thread.is_alive() and not stop.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        engine.dispose()
        instance.close()
        print("STAGE7_ISOLATED_BROWSER_STOPPED", flush=True)


if __name__ == "__main__":
    main()
