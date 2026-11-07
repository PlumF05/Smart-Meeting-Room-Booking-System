"""两个角色各自的真实工作台上下文，不返回尚未实现的业务统计。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth_dependencies import require_admin, require_regular_user
from app.schemas.auth import CurrentUser
from app.schemas.workspace import WorkspaceInfo


router = APIRouter(prefix="/api/workspaces", tags=["工作台"])


@router.get("/admin", response_model=WorkspaceInfo, summary="获取管理员工作台身份")
def admin_workspace(user: Annotated[CurrentUser, Depends(require_admin)]) -> WorkspaceInfo:
    return WorkspaceInfo(workspace="admin", user=user)


@router.get("/user", response_model=WorkspaceInfo, summary="获取普通用户工作台身份")
def user_workspace(user: Annotated[CurrentUser, Depends(require_regular_user)]) -> WorkspaceInfo:
    return WorkspaceInfo(workspace="user", user=user)
