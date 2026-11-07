from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.auth_dependencies import require_trusted_origin
from app.core.auth_config import COOKIE_SECURE, SESSION_COOKIE_NAME, SESSION_LIFETIME
from app.schemas.auth import LoginRequest, RegisteredUser, RegistrationRequest, SessionInfo
from app.services.auth import UsernameAlreadyExistsError, register_user
from app.services.login import get_session_info, login_user, logout_user


router = APIRouter(prefix="/api/auth", tags=["账号"])


@router.post(
    "/register",
    response_model=RegisteredUser,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_trusted_origin)],
    summary="注册已启用的普通用户，不自动登录",
    responses={
        409: {"description": "登录名已存在"},
        503: {"description": "数据库暂不可用"},
    },
)
def register(
    data: RegistrationRequest,
    session: Annotated[Session, Depends(get_db)],
) -> RegisteredUser:
    try:
        return register_user(session, data)
    except UsernameAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="登录名已存在，请更换登录名。"
        ) from None


@router.post(
    "/login", response_model=SessionInfo, summary="登录并建立服务器端会话",
    dependencies=[Depends(require_trusted_origin)],
    responses={401: {"description": "登录名、密码不正确或账号不可用"}},
)
def login(
    data: LoginRequest, request: Request, response: Response,
    session: Annotated[Session, Depends(get_db)],
) -> SessionInfo:
    result = login_user(session, data, request.cookies.get(SESSION_COOKIE_NAME))
    response.set_cookie(
        key=SESSION_COOKIE_NAME, value=result.token,
        max_age=int(SESSION_LIFETIME.total_seconds()), expires=result.info.expires_at,
        path="/", httponly=True, secure=COOKIE_SECURE, samesite="lax",
    )
    return result.info


@router.get(
    "/me", response_model=SessionInfo, summary="查询当前登录用户及CSRF令牌",
    responses={401: {"description": "未登录或登录已失效"}},
)
def me(request: Request, session: Annotated[Session, Depends(get_db)]) -> SessionInfo:
    return get_session_info(session, request.cookies.get(SESSION_COOKIE_NAME))


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response,
    summary="校验CSRF令牌后退出当前会话",
    dependencies=[Depends(require_trusted_origin)],
    responses={401: {"description": "登录已失效"}, 403: {"description": "CSRF校验失败"}},
)
def logout(
    request: Request, session: Annotated[Session, Depends(get_db)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    logout_user(session, request.cookies.get(SESSION_COOKIE_NAME), csrf_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/", httponly=True,
        secure=COOKIE_SECURE, samesite="lax",
    )
    return response
