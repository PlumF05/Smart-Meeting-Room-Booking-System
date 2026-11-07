"""同步命令的隔离检查，不读取开发配置或真实账号清单。"""

import json
import os
import secrets

import pytest
from sqlalchemy.orm import Session

from app.cli import sync_test_accounts as cli
from app.core.admin_config import AdminSeedSettings
from app.core.local_accounts import AccountLedger, LocalAccountSettings
from app.core.security import hash_password
from app.models import User


def test_disabled_cli_does_not_read_database_or_admin_config(monkeypatch, tmp_path, capsys):
    path = tmp_path / "disabled" / "accounts.json"
    monkeypatch.setattr(cli, "load_local_account_settings", lambda: LocalAccountSettings(False, path))

    def forbidden():
        pytest.fail("关闭开关时不能读取管理员密码或连接数据库")

    monkeypatch.setattr(cli, "load_admin_settings", forbidden)
    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    assert cli.main([]) == 1
    assert not path.parent.exists()
    assert "尚未开启" in capsys.readouterr().out


def test_cli_rejects_arguments_without_echoing_them(capsys):
    secret = "synthetic-not-a-real-password"
    assert cli.main(["--password", secret]) == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err


def test_cli_does_not_expose_configuration_exception(monkeypatch, capsys):
    secret = "synthetic-sensitive-configuration"

    def fail():
        raise ValueError(secret)

    monkeypatch.setattr(cli, "load_local_account_settings", fail)
    assert cli.main([]) == 1
    output = capsys.readouterr()
    assert "同步失败" in output.err
    assert secret not in output.out + output.err


@pytest.mark.mysql
@pytest.mark.skipif(
    os.environ.get("SMRBS_RUN_MYSQL_SCHEMA_TEST") != "1",
    reason="必须使用独立MySQL测试实例",
)
def test_sync_lists_all_users_and_only_accepts_verified_passwords(engine, monkeypatch, tmp_path, capsys):
    password = "synthetic-sync-password"
    encoded = hash_password(password)
    prefix = "sync_" + secrets.token_hex(5)
    with Session(engine) as database, database.begin():
        admin = User(
            username=prefix + "_admin", full_name="同步测试管理员", password_hash=encoded,
            role="admin", identity_type=None, is_active=True, session_version=1,
        )
        regular = User(
            username=prefix + "_user", full_name="同步测试用户", password_hash=encoded,
            role="user", identity_type="student", is_active=True, session_version=1,
        )
        database.add_all([admin, regular])
        database.flush()
        admin_id, regular_id = admin.id, regular.id
    path = tmp_path / "accounts.json"
    ledger = AccountLedger(path)
    ledger.record(regular_id, prefix + "_user", "synthetic-stale-password", "login")
    settings = AdminSeedSettings(username=prefix + "_admin", password=password)
    monkeypatch.setattr(cli, "load_local_account_settings", lambda: LocalAccountSettings(True, path))
    monkeypatch.setattr(cli, "load_admin_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_database_engine", lambda: engine)
    assert cli.main([]) == 0
    accounts = {item["user_id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["accounts"]}
    assert accounts[admin_id]["password"] == password
    assert accounts[regular_id]["password"] is None
    assert password not in capsys.readouterr().out
    # 旧的管理员配置密码若不再有效，不能被当成当前密码写回清单。
    settings = AdminSeedSettings(username=prefix + "_admin", password="synthetic-wrong-password")
    path.unlink()
    assert cli.main([]) == 0
    accounts = {item["user_id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["accounts"]}
    assert accounts[admin_id]["password"] is None
