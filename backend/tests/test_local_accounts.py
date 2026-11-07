"""本机测试账号清单的文件契约；只使用临时目录和虚构密码。"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.core import local_accounts
from app.core.local_accounts import (
    AccountLedger, KnownAccount, LocalAccountSettings, load_local_account_settings,
)
from app.core.security import hash_password


PASSWORD = "ledger-unit-only-Password-2026"
BACKEND = Path(__file__).resolve().parents[1]


def read_document(path):
    return json.loads(path.read_text(encoding="utf-8"))


def entries_by_id(path):
    return {item["user_id"]: item for item in read_document(path)["accounts"]}


def enable_recording(monkeypatch, path):
    monkeypatch.setattr(
        local_accounts, "load_local_account_settings",
        lambda: LocalAccountSettings(enabled=True, path=path),
    )


def test_recording_is_disabled_by_default_and_does_not_create_a_file(tmp_path):
    # 显式传入不存在的配置和空进程变量，不读取开发机.env。
    settings = load_local_account_settings(tmp_path / "missing-config", environ={})
    assert settings.enabled is False
    assert local_accounts.record_test_account(
        user_id=1, username="disabled_unit", password=PASSWORD, source="test",
    ) is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("configured, environment, expected", [
    ("true", {}, True), ("1", {}, True), ("FALSE", {}, False),
    ("true", {"SMRBS_RECORD_TEST_PASSWORDS": "false"}, False),
    ("false", {"SMRBS_RECORD_TEST_PASSWORDS": "  TRUE  "}, True),
])
def test_configuration_uses_explicit_file_and_process_override(
    tmp_path, configured, environment, expected,
):
    config = tmp_path / "unit-config"
    config.write_text(f"SMRBS_RECORD_TEST_PASSWORDS={configured}\n", encoding="utf-8-sig")
    assert load_local_account_settings(config, environ=environment).enabled is expected


def test_invalid_configuration_error_does_not_echo_input(tmp_path):
    with pytest.raises(ValueError) as error:
        load_local_account_settings(
            tmp_path / "missing-config", environ={"SMRBS_RECORD_TEST_PASSWORDS": PASSWORD},
        )
    assert PASSWORD not in str(error.value)


def test_record_preserves_password_whitespace_and_replaces_same_user(tmp_path):
    path = tmp_path / ".local" / "accounts.json"
    ledger = AccountLedger(path)
    ledger.record(2, "unit_second", PASSWORD, "registration")
    ledger.record(1, "unit_first", "  unit password with spaces  ", "login")
    ledger.record(2, "unit_second_renamed", "unit-new-password", "login")
    document = read_document(path)
    assert document["schema_version"] == 1
    assert document["description"] and document["updated_at"]
    assert [item["user_id"] for item in document["accounts"]] == [1, 2]
    entries = entries_by_id(path)
    assert entries[1]["password"] == "  unit password with spaces  "
    assert entries[2]["username"] == "unit_second_renamed"
    assert entries[2]["password"] == "unit-new-password"
    assert all(item["password_status"] == "known" and item["verified_at"]
               for item in entries.values())
    assert entries[2]["source"] == "login"
    assert not list(path.parent.glob("*.tmp"))


def test_threaded_writers_do_not_lose_other_users(tmp_path):
    path = tmp_path / ".local" / "accounts.json"

    def record_one(user_id):
        # 每次建立不同对象，不能仅靠某个对象内部的锁使测试通过。
        AccountLedger(path).record(user_id, f"thread_{user_id}", PASSWORD, "thread_test")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record_one, range(1, 41)))
    assert set(entries_by_id(path)) == set(range(1, 41))


def test_independent_processes_do_not_lose_other_users(tmp_path):
    path = tmp_path / ".local" / "accounts.json"
    script = (
        "from pathlib import Path; import sys; "
        "from app.core.local_accounts import AccountLedger; "
        "ledger=AccountLedger(Path(sys.argv[1])); base=int(sys.argv[2]); "
        "[ledger.record(base+i, f'process_{base+i}', 'process-test-only-password', "
        "'process_test') for i in range(1, 9)]"
    )
    children = []
    try:
        for base in (0, 10, 20, 30):
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(path), str(base)], cwd=BACKEND,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ))
        assert [child.wait(timeout=30) for child in children] == [0, 0, 0, 0]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert set(entries_by_id(path)) == {
        base + number for base in (0, 10, 20, 30) for number in range(1, 9)
    }


@pytest.mark.parametrize("original", [
    b"{invalid JSON", b"[]", b'{"schema_version":2,"accounts":[]}',
    b'{"schema_version":1,"accounts":[{"user_id":-1}]}',
])
def test_invalid_existing_document_is_never_overwritten(tmp_path, original):
    path = tmp_path / "accounts.json"
    path.write_bytes(original)
    with pytest.raises(ValueError):
        AccountLedger(path).record(1, "unit_new", PASSWORD, "registration")
    assert path.read_bytes() == original
    with pytest.raises(ValueError):
        AccountLedger(path).sync([])
    assert path.read_bytes() == original


def test_recording_failure_is_sanitized_and_does_not_raise(tmp_path, monkeypatch, caplog):
    path = tmp_path / "accounts.json"
    enable_recording(monkeypatch, path)

    def fail_with_secret(*args, **kwargs):
        raise OSError("exception-with-secret-" + PASSWORD)

    monkeypatch.setattr(AccountLedger, "record", fail_with_secret)
    assert local_accounts.record_test_account(
        user_id=1, username="unit_new", password=PASSWORD, source="registration",
    ) is False
    assert caplog.records
    assert PASSWORD not in caplog.text
    assert "exception-with-secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert not path.exists()


def test_enabled_hook_records_successfully(tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    enable_recording(monkeypatch, path)
    assert local_accounts.record_test_account(
        user_id=1, username="unit_new", password=PASSWORD, source="registration",
    ) is True
    assert entries_by_id(path)[1]["password"] == PASSWORD


def test_sync_checks_current_hash_preserves_valid_and_clears_stale_passwords(tmp_path):
    path = tmp_path / "accounts.json"
    ledger = AccountLedger(path)
    encoded = hash_password(PASSWORD)
    ledger.record(1, "old_name", PASSWORD, "login")
    ledger.record(2, "stale_password", "no-longer-current-password", "login")
    ledger.record(99, "deleted_account", PASSWORD, "login")
    accounts = [KnownAccount(number, f"current_{number}", encoded) for number in range(1, 5)]
    assert encoded not in repr(accounts[0])
    counts = ledger.sync(accounts, candidates={3: PASSWORD, 4: "wrong-candidate-password"})
    assert counts == {"total": 4, "known": 2, "unknown": 2}
    entries = entries_by_id(path)
    assert set(entries) == {1, 2, 3, 4}
    assert entries[1]["username"] == "current_1" and entries[1]["password"] == PASSWORD
    assert entries[1]["source"] == "login"
    assert entries[3]["password"] == PASSWORD
    for user_id in (2, 4):
        assert entries[user_id]["password"] is None
        assert entries[user_id]["password_status"] == "unknown"
        assert entries[user_id]["verified_at"] is None
    assert encoded not in path.read_text(encoding="utf-8")


def test_sync_rejects_bad_candidate_without_discarding_current_known_password(tmp_path):
    path = tmp_path / "accounts.json"
    ledger = AccountLedger(path)
    ledger.record(1, "unit_current", PASSWORD, "login")
    counts = ledger.sync(
        [KnownAccount(1, "unit_current", hash_password(PASSWORD))],
        candidates={1: "different-unverified-candidate"},
    )
    assert counts == {"total": 1, "known": 1, "unknown": 0}
    assert entries_by_id(path)[1]["password"] == PASSWORD


def test_sync_from_does_not_overwrite_when_snapshot_loader_fails(tmp_path):
    path = tmp_path / "accounts.json"
    ledger = AccountLedger(path)
    ledger.record(1, "unit_current", PASSWORD, "login")
    before = path.read_bytes()

    def failed_loader():
        raise RuntimeError("synthetic snapshot failure")

    with pytest.raises(RuntimeError):
        ledger.sync_from(failed_loader)
    assert path.read_bytes() == before


def test_sync_from_uses_loader_snapshot_and_candidates(tmp_path):
    path = tmp_path / "accounts.json"
    encoded = hash_password(PASSWORD)
    counts = AccountLedger(path).sync_from(
        lambda: ([KnownAccount(1, "unit_snapshot", encoded)], {1: PASSWORD}),
    )
    assert counts == {"total": 1, "known": 1, "unknown": 0}
    assert entries_by_id(path)[1]["password"] == PASSWORD
