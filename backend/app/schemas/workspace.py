from typing import Literal

from pydantic import BaseModel

from app.schemas.auth import CurrentUser


class WorkspaceInfo(BaseModel):
    workspace: Literal["admin", "user"]
    user: CurrentUser
