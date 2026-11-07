"""创建应用，注册健康检查、账号接口及安全的错误响应。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from app.api.auth import router as auth_router
from app.api.errors import (
    database_unavailable_error_handler,
    invalid_credentials_error_handler,
    invalid_csrf_error_handler,
    invalid_session_error_handler,
    request_validation_error_handler,
)
from app.api.health import router as health_router
from app.api.workspace import router as workspace_router
from app.core.errors import DatabaseUnavailableError
from app.services.login import InvalidCredentialsError, InvalidCsrfError, InvalidSessionError


app = FastAPI(
    title="SMRBS API",
    description="学院智能会议室预约系统：健康检查、注册与数据库登录会话。",
    version="0.1.0",
)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.add_exception_handler(DatabaseUnavailableError, database_unavailable_error_handler)
app.add_exception_handler(InvalidCredentialsError, invalid_credentials_error_handler)
app.add_exception_handler(InvalidSessionError, invalid_session_error_handler)
app.add_exception_handler(InvalidCsrfError, invalid_csrf_error_handler)


@app.middleware("http")
async def prevent_auth_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/workspaces/")):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(workspace_router)
