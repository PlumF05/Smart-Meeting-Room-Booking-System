"""可关闭的本机测试账号清单；数据库的密码哈希仍是登录验证依据。"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time

from dotenv import dotenv_values
from pwdlib.exceptions import UnknownHashError

from app.core.config import ENV_FILE
from app.core.security import verify_password


DEFAULT_LEDGER = ENV_FILE.parent / ".local" / "test-accounts.json"
_thread_lock = threading.RLock()
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalAccountSettings:
    enabled: bool
    path: Path = DEFAULT_LEDGER


@dataclass(frozen=True)
class KnownAccount:
    user_id: int
    username: str
    password_hash: str = field(repr=False)


def load_local_account_settings(
    env_file: Path = ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> LocalAccountSettings:
    values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
    environment = os.environ if environ is None else environ
    name = "SMRBS_RECORD_TEST_PASSWORDS"
    raw = environment.get(name, values.get(name) or "false").strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError("本机测试账号记录开关无效")
    return LocalAccountSettings(enabled=raw in {"true", "1"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    # 锁独立文件，避免JSON被原子替换后，其他进程锁到不同文件。
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + 5
        if os.name == "nt":
            import msvcrt

            def acquire() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release() -> None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release() -> None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("本机测试账号文件正在使用") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            release()


class AccountLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with _thread_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
                yield

    def _read(self) -> dict[int, dict]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict) or data.get("schema_version") != 1
            or not isinstance(data.get("accounts"), list)
        ):
            raise ValueError("本机测试账号文件格式无效")
        entries = {}
        for item in data["accounts"]:
            if (
                not isinstance(item, dict) or type(item.get("user_id")) is not int
                or item["user_id"] <= 0 or item["user_id"] in entries
                or not isinstance(item.get("username"), str)
                or not (item.get("password") is None or isinstance(item["password"], str))
                or item.get("password_status") not in {"known", "unknown"}
                or not (item.get("source") is None or isinstance(item["source"], str))
                or not (item.get("verified_at") is None or isinstance(item["verified_at"], str))
            ):
                raise ValueError("本机测试账号条目无效")
            entries[item["user_id"]] = item
        return entries

    def _write(self, entries: dict[int, dict]) -> None:
        document = {
            "schema_version": 1,
            "description": "仅供本机个人测试。password为明文；null表示原密码未知，成功登录后补齐。不要提交或分享此文件。",
            "updated_at": _now(),
            "accounts": [entries[key] for key in sorted(entries)],
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".test-accounts-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def record(self, user_id: int, username: str, password: str, source: str) -> None:
        with self._locked():
            entries = self._read()
            entries[user_id] = {
                "user_id": user_id, "username": username, "password": password,
                "password_status": "known", "source": source, "verified_at": _now(),
            }
            self._write(entries)

    def _sync_locked(
        self, accounts: list[KnownAccount], candidates: Mapping[int, str],
    ) -> dict[str, int]:
        previous = self._read()
        entries = {}
        for account in accounts:
            old = previous.get(account.user_id, {})
            password = None
            source = None
            choices = [(candidates.get(account.user_id), "verified_local_config"),
                       (old.get("password"), old.get("source"))]
            for candidate, candidate_source in choices:
                if candidate is None:
                    continue
                try:
                    valid = verify_password(candidate, account.password_hash)
                except (UnknownHashError, ValueError, TypeError):
                    valid = False
                if valid:
                    password, source = candidate, candidate_source
                    break
            entries[account.user_id] = {
                "user_id": account.user_id, "username": account.username,
                "password": password, "password_status": "known" if password is not None else "unknown",
                "source": source, "verified_at": _now() if password is not None else None,
            }
        self._write(entries)
        known = sum(entry["password"] is not None for entry in entries.values())
        return {"total": len(entries), "known": known, "unknown": len(entries) - known}

    def sync(
        self, accounts: list[KnownAccount], candidates: Mapping[int, str] | None = None,
    ) -> dict[str, int]:
        with self._locked():
            return self._sync_locked(accounts, candidates or {})

    def sync_from(
        self, loader: Callable[[], tuple[list[KnownAccount], Mapping[int, str]]],
    ) -> dict[str, int]:
        # 先锁清单再读取最新快照；业务钩子在提交后拿文件锁，不持有数据库锁等待。
        with self._locked():
            accounts, candidates = loader()
            return self._sync_locked(accounts, candidates)


def record_test_account(*, user_id: int, username: str, password: str, source: str) -> bool:
    try:
        settings = load_local_account_settings()
        if not settings.enabled:
            return False
        AccountLedger(settings.path).record(user_id, username, password, source)
        return True
    except Exception:
        # 本机辅助文件不能使已提交的注册/登录失败，也不能将异常中的密码写入日志。
        _logger.warning("本机测试账号文件未更新，请检查文件格式、权限或占用情况。")
        return False
