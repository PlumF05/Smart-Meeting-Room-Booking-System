"""浏览器来源检查及未来业务接口复用的当前登录者依赖。"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth_config import SESSION_COOKIE_NAME, TRUSTED_ORIGINS
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.services.login import get_session_info


def require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if (
        request.headers.get("sec-fetch-site", "").lower() == "cross-site"
        or (origin is not None and origin not in TRUSTED_ORIGINS)
    ):
        raise HTTPException(status_code=403, detail="请求来源不被允许。")


def get_current_user(
    request: Request, session: Annotated[Session, Depends(get_db)]
) -> CurrentUser:
    return get_session_info(session, request.cookies.get(SESSION_COOKIE_NAME)).user


def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="当前账号无权访问该功能。")
    return user


def require_regular_user(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if user.role != "user":
        raise HTTPException(status_code=403, detail="当前账号无权访问该功能。")
    return user
