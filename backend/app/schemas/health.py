from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    component: Literal["application", "database"]
    code: Literal[
        "running", "connected", "not_configured", "invalid_configuration", "unavailable"
    ]
    message: str
