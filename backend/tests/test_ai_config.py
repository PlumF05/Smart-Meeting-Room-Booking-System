"""AI配置公开行为：只用临时文件和合成密钥，不读取开发配置、不访问网络。"""

import os

from pydantic import ValidationError
import pytest

from app.core.ai_config import AISettings, load_ai_settings


def test_missing_ai_file_keeps_feature_disabled_and_does_not_create_file(tmp_path):
    path = tmp_path / ".env.ai"
    settings = load_ai_settings(path, environ={})
    assert settings.enabled is False
    assert settings.is_configured is False
    assert settings.model == "deepseek-flash"
    assert settings.timeout_seconds == 30
    assert not path.exists()


def test_ai_file_supports_bom_and_literal_key_without_changing_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SMRBS_SYNTHETIC_LITERAL", "must-not-replace")
    before = dict(os.environ)
    path = tmp_path / ".env.ai"
    secret = "synthetic-${SMRBS_SYNTHETIC_LITERAL}-#literal"
    path.write_text(
        "SMRBS_AI_ENABLED=true\n"
        f"DEEPSEEK_API_KEY='{secret}'\n"
        "SMRBS_AI_MODEL=deepseek-flash\n"
        "SMRBS_AI_TIMEOUT_SECONDS=12.5\n",
        encoding="utf-8-sig",
    )
    settings = load_ai_settings(path, environ={})
    assert settings.enabled is True
    assert settings.api_key.get_secret_value() == secret
    assert settings.timeout_seconds == 12.5
    assert settings.is_configured is True
    assert dict(os.environ) == before


def test_process_environment_overrides_file_and_keeps_other_file_fields(tmp_path):
    path = tmp_path / ".env.ai"
    path.write_text(
        "SMRBS_AI_ENABLED=false\nDEEPSEEK_API_KEY='synthetic-file-key'\n"
        "SMRBS_AI_MODEL=deepseek-flash\nSMRBS_AI_TIMEOUT_SECONDS=30\n",
        encoding="utf-8",
    )
    settings = load_ai_settings(path, environ={
        "SMRBS_AI_ENABLED": " TRUE ",
        "DEEPSEEK_API_KEY": "synthetic-environment-key",
        "SMRBS_AI_MODEL": "future-safe-model:2",
    })
    assert settings.enabled is True
    assert settings.api_key.get_secret_value() == "synthetic-environment-key"
    assert settings.model == "future-safe-model:2"
    assert settings.timeout_seconds == 30


def test_explicit_empty_environment_key_overrides_file_secret(tmp_path):
    path = tmp_path / ".env.ai"
    path.write_text("DEEPSEEK_API_KEY='synthetic-file-key'\n", encoding="utf-8")
    settings = load_ai_settings(path, environ={"DEEPSEEK_API_KEY": ""})
    assert settings.is_configured is False
    assert settings.api_key.get_secret_value() == ""


def test_enabled_switch_is_separate_from_key_presence():
    assert AISettings(enabled=False, api_key="synthetic-key").is_configured is True
    assert AISettings(enabled=True, api_key="").is_configured is False


@pytest.mark.parametrize("value", ["yes", "1", "maybe", ""])
def test_invalid_enabled_value_is_rejected_without_loose_truthiness(tmp_path, value):
    with pytest.raises(ValidationError):
        load_ai_settings(tmp_path / "missing", environ={"SMRBS_AI_ENABLED": value})


@pytest.mark.parametrize("model", ["", "  ", "deepseek flash", "bad\nmodel", "x" * 101])
def test_unsafe_or_missing_model_identifier_is_rejected(model):
    with pytest.raises(ValidationError):
        AISettings(model=model)


@pytest.mark.parametrize("timeout", [0, 46, float("nan"), float("inf"), "invalid"])
def test_invalid_or_unbounded_timeout_is_rejected(timeout):
    with pytest.raises(ValidationError):
        AISettings(timeout_seconds=timeout)


def test_secret_is_hidden_from_repr_json_and_validation_error_text():
    secret = "synthetic-secret-not-for-output"
    settings = AISettings(api_key=secret)
    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert secret not in settings.model_dump_json()
    with pytest.raises(ValidationError) as captured:
        AISettings(api_key=secret, model="invalid model")
    assert secret not in str(captured.value)
    assert "invalid model" not in str(captured.value)
