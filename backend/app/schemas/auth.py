"""注册输入和输出；密码及确认密码不会成为响应字段。"""

from typing import Annotated, Literal, Self
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator


IdentityType = Literal["teacher", "student"]


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True
    )

    username: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    identity_type: IdentityType
    # 1024字符是输入处理上限，不增设复杂度规则；密码不去除空格或截断。
    password: SecretStr = Field(min_length=8, max_length=1024)
    confirm_password: SecretStr = Field(min_length=8, max_length=1024)

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        if self.password.get_secret_value() != self.confirm_password.get_secret_value():
            raise ValueError("两次输入的密码不一致")
        return self


class RegisteredUser(BaseModel):
    id: int
    username: str
    full_name: str
    identity_type: IdentityType
    role: Literal["user"]
    is_active: bool


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    username: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    password: SecretStr = Field(min_length=1, max_length=1024)


class CurrentUser(BaseModel):
    id: int
    username: str
    full_name: str
    identity_type: IdentityType | None
    role: Literal["user", "admin"]
    is_active: bool


class SessionInfo(BaseModel):
    user: CurrentUser
    csrf_token: str = Field(repr=False)
    expires_at: datetime
