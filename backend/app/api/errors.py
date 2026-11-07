"""只返回必要错误信息，不回显请求正文、密码或数据库异常。"""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import DatabaseUnavailableError
from app.services.login import InvalidCredentialsError, InvalidCsrfError, InvalidSessionError


async def request_validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    # 默认校验错误的input或ctx可能带有密码，不能直接返回error.errors()。
    details = [
        {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": details},
    )


async def database_unavailable_error_handler(
    request: Request, error: DatabaseUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "数据库暂不可用，请稍后重试。"},
    )


async def invalid_credentials_error_handler(
    request: Request, error: InvalidCredentialsError
) -> JSONResponse:
    # 登录失败不覆盖已有Cookie，避免破坏当前有效登录。
    return JSONResponse(status_code=401, content={"detail": "登录名、密码不正确或账号不可用。"})


async def invalid_session_error_handler(
    request: Request, error: InvalidSessionError
) -> JSONResponse:
    # 旧查询的401可能晚于新登录到达，不能用删除头清掉浏览器的新Cookie。
    # 显式退出成功仍会删除Cookie；失效凭证本身始终由数据库拒绝。
    return JSONResponse(status_code=401, content={"detail": "未登录或登录已失效，请重新登录。"})


async def invalid_csrf_error_handler(
    request: Request, error: InvalidCsrfError
) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "CSRF校验失败，请刷新登录信息后重试。"})
