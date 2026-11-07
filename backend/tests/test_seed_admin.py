"""独立配置与CLI输入保密；默认不读取开发凭据或连接数据库。"""

from io import StringIO
from unittest.mock import MagicMock
import warnings

from pydantic import ValidationError
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cli import seed_admin as seed_cli
from app.core.admin_config import AdminSeedSettings, load_admin_settings
from app.core.errors import DatabaseUnavailableError
from app.services.bootstrap import seed_admin


PASSWORD = "test-only@admin:/?#${UNCHANGED_PASSWORD}"


def forbid_database():
    raise AssertionError("此测试不能读取开发连接配置或访问数据库")


def test_admin_configuration_defaults_do_not_supply_a_default_password(tmp_path):
    settings = load_admin_settings(tmp_path / "absent-admin-config")
    assert settings.username == "smrbs_admin"
    assert settings.full_name == "系统管理员"
    assert settings.password is None


def test_admin_file_keeps_bom_special_password_and_ignores_process_environment(
    tmp_path, monkeypatch
):
    config = tmp_path / ".env.admin"
    config.write_text(
        "SMRBS_ADMIN_USERNAME='  local_admin  '\nSMRBS_ADMIN_NAME='  本地管理员  '\n"
        f"SMRBS_ADMIN_PASSWORD='{PASSWORD}'\n", encoding="utf-8-sig",
    )
    monkeypatch.setenv("SMRBS_ADMIN_USERNAME", "must_not_override_file")
    monkeypatch.setenv("SMRBS_ADMIN_PASSWORD", "must_not_override_password")
    monkeypatch.setenv("UNCHANGED_PASSWORD", "must_not_interpolate")
    settings = load_admin_settings(config)
    assert settings.username == "local_admin" and settings.full_name == "本地管理员"
    assert settings.password.get_secret_value() == PASSWORD
    assert PASSWORD not in repr(settings)


@pytest.mark.parametrize("changes", [
    {"username": "  "}, {"username": "a" * 65}, {"full_name": "\t "},
    {"full_name": "名" * 101}, {"password": "1234567"}, {"password": "p" * 1025},
])
def test_admin_settings_reject_invalid_values_without_echoing_input(changes):
    values = dict(username="unit_admin", full_name="单元管理员", password=PASSWORD)
    values.update(changes)
    with pytest.raises(ValidationError) as error:
        AdminSeedSettings(**values)
    assert "input_value=" not in str(error.value)
    assert PASSWORD not in str(error.value)


def test_cli_rejects_command_line_password_without_echoing_it(monkeypatch, capsys):
    monkeypatch.setattr(seed_cli, "create_database_engine", forbid_database)
    marker = "must-not-be-printed-command-line-password"
    with pytest.raises(SystemExit) as error:
        seed_cli.main(["--password", marker])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err


def test_invalid_local_config_stops_before_database_and_does_not_print_password(
    tmp_path, monkeypatch, capsys
):
    config = tmp_path / ".env.admin"
    marker = "short77"
    config.write_text(f"SMRBS_ADMIN_PASSWORD='{marker}'\n", encoding="utf-8")
    monkeypatch.setattr(seed_cli, "create_database_engine", forbid_database)
    assert seed_cli.main(["--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err


def test_cli_database_failure_is_sanitized(tmp_path, monkeypatch, capsys):
    config = tmp_path / ".env.admin"
    config.write_text(f"SMRBS_ADMIN_PASSWORD='{PASSWORD}'\n", encoding="utf-8")
    marker = "private-database-configuration-error-marker"
    factory = MagicMock(side_effect=DatabaseUnavailableError(marker))
    monkeypatch.setattr(seed_cli, "create_database_engine", factory)
    assert seed_cli.main(["--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    assert PASSWORD not in captured.out + captured.err


def test_noninteractive_password_input_does_not_fall_back_to_echo(monkeypatch):
    monkeypatch.setattr(seed_cli.sys, "stdin", StringIO())
    prompt = MagicMock(side_effect=AssertionError("非TTY不得调用密码提示"))
    monkeypatch.setattr(seed_cli.getpass, "getpass", prompt)
    with pytest.raises(ValueError):
        seed_cli.read_password(AdminSeedSettings())
    prompt.assert_not_called()


def test_interactive_confirmation_is_required_and_password_not_trimmed(monkeypatch):
    input_stream = MagicMock()
    input_stream.isatty.return_value = True
    monkeypatch.setattr(seed_cli.sys, "stdin", input_stream)
    password = "  local password  "
    monkeypatch.setattr(seed_cli.getpass, "getpass", MagicMock(side_effect=[password, password]))
    settings = seed_cli.read_password(AdminSeedSettings())
    assert settings.password.get_secret_value() == password
    monkeypatch.setattr(seed_cli.getpass, "getpass", MagicMock(side_effect=[password, "different-password"]))
    with pytest.raises(ValueError):
        seed_cli.read_password(AdminSeedSettings())


def test_interactive_input_stops_if_getpass_cannot_hide_characters(monkeypatch):
    input_stream = MagicMock()
    input_stream.isatty.return_value = True
    monkeypatch.setattr(seed_cli.sys, "stdin", input_stream)

    def unsafe_prompt(*args):
        warnings.warn("cannot hide input", seed_cli.getpass.GetPassWarning)
        raise AssertionError("必须在回显输入前停止")

    monkeypatch.setattr(seed_cli.getpass, "getpass", unsafe_prompt)
    with pytest.raises(seed_cli.getpass.GetPassWarning):
        seed_cli.read_password(AdminSeedSettings())


def test_seed_database_exception_does_not_escape_original_details():
    database = MagicMock(spec=Session)
    marker = "private-seed-database-error-marker"
    database.begin.side_effect = SQLAlchemyError(marker)
    with pytest.raises(DatabaseUnavailableError) as error:
        seed_admin(database, AdminSeedSettings(password=PASSWORD))
    assert marker not in str(error.value)
