"""可选AI配置；不修改环境、不读取数据库配置，也不初始化模型。"""

from collections.abc import Mapping
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

AI_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.ai"
ENV_FIELDS = {
    "SMRBS_AI_ENABLED": "enabled",
    "DEEPSEEK_API_KEY": "api_key",
    "SMRBS_AI_MODEL": "model",
    "SMRBS_AI_TIMEOUT_SECONDS": "timeout_seconds",
}


class AISettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    enabled: bool = False
    api_key: SecretStr = Field(default=SecretStr(""), repr=False, exclude=True)
    model: str = Field(default="deepseek-flash", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    timeout_seconds: float = Field(default=30, ge=1, le=45, allow_inf_nan=False)

    @field_validator("enabled", mode="before")
    @classmethod
    def explicit_boolean(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError("AI开关只接受true或false")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.get_secret_value().strip())


def load_ai_settings(
    env_file: Path = AI_ENV_FILE, environ: Mapping[str, str] | None = None,
) -> AISettings:
    values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
    environment = os.environ if environ is None else environ
    selected = {}
    for name, field in ENV_FIELDS.items():
        if name in environment:
            selected[field] = environment[name]
        elif name in values:
            selected[field] = values[name] or ""
    return AISettings(**selected)
