"""仅本机预置命令读取的配置，Web应用不加载管理员初始密码。"""

from pathlib import Path
from typing import Annotated

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from app.core.config import ENV_FILE


ADMIN_ENV_FILE = ENV_FILE.parent / ".env.admin"


class AdminSeedSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid", strict=True)

    username: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ] = "smrbs_admin"
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = "系统管理员"
    password: SecretStr | None = Field(default=None, min_length=8, max_length=1024)


def load_admin_settings(path: Path = ADMIN_ENV_FILE) -> AdminSeedSettings:
    values = dotenv_values(path, encoding="utf-8-sig", interpolate=False)
    fields = {}
    for key, field in {
        "SMRBS_ADMIN_USERNAME": "username",
        "SMRBS_ADMIN_NAME": "full_name",
    }.items():
        if key in values:
            fields[field] = values[key] or ""
    if values.get("SMRBS_ADMIN_PASSWORD"):
        fields["password"] = values["SMRBS_ADMIN_PASSWORD"]
    return AdminSeedSettings(**fields)
