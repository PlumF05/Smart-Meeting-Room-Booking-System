"""创建应用，注册健康检查、账号接口及安全的错误响应。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from app.api.auth import router as auth_router
from app.api.assistant import router as assistant_router
from app.api.meeting_assistant import router as meeting_assistant_router
from app.api.assistant_chat import router as assistant_chat_router
from app.api.errors import (
    database_unavailable_error_handler,
    invalid_credentials_error_handler,
    invalid_csrf_error_handler,
    invalid_session_error_handler,
    request_validation_error_handler,
)
from app.api.health import router as health_router
from app.api.followup import router as followup_router
from app.api.management import router as management_router, management_error_handler
from app.api.reservations import router as reservations_router
from app.api.rooms import router as rooms_router
from app.api.workspace import router as workspace_router
from app.core.errors import DatabaseUnavailableError
from app.services.login import InvalidCredentialsError, InvalidCsrfError, InvalidSessionError
from app.services.management import ManagementError


app = FastAPI(
    title="SMRBS API",
    description="学院智能会议室预约系统：注册登录、查询预约、人工审批与会议室维护。",
    version="0.1.0",
)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.add_exception_handler(DatabaseUnavailableError, database_unavailable_error_handler)
app.add_exception_handler(InvalidCredentialsError, invalid_credentials_error_handler)
app.add_exception_handler(InvalidSessionError, invalid_session_error_handler)
app.add_exception_handler(InvalidCsrfError, invalid_csrf_error_handler)
app.add_exception_handler(ManagementError, management_error_handler)


@app.middleware("http")
async def prevent_private_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/workspaces/", "/api/rooms", "/api/reservations", "/api/admin/", "/api/messages", "/api/assistant")):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(assistant_router)
app.include_router(meeting_assistant_router)
app.include_router(assistant_chat_router)
app.include_router(workspace_router)
app.include_router(rooms_router)
app.include_router(reservations_router)
app.include_router(management_router)
app.include_router(followup_router)
