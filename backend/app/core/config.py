"""只读取项目后端的配置，不修改进程环境，也不输出配置值。"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
ENV_FIELDS = {
    "SMRBS_DB_HOST": "host",
    "SMRBS_DB_PORT": "port",
    "SMRBS_DB_NAME": "name",
    "SMRBS_DB_USER": "user",
    "SMRBS_DB_PASSWORD": "password",
}


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=3306, ge=1, le=65535)
    name: str = ""
    user: str = ""
    password: SecretStr = SecretStr("")

    @property
    def is_configured(self) -> bool:
        return bool(
            self.name.strip()
            and self.user.strip()
            and self.password.get_secret_value()
        )


def load_database_settings(
    env_file: Path = ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> DatabaseSettings:
    # 禁止${...}插值，避免密码中的字面字符被解释为环境变量。
    values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
    environment = os.environ if environ is None else environ
    selected = {}
    for env_name, field_name in ENV_FIELDS.items():
        if env_name in environment:
            selected[field_name] = environment[env_name]
        elif env_name in values:
            selected[field_name] = values[env_name] or ""
    return DatabaseSettings(**selected)
