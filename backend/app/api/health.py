from fastapi import APIRouter, Response, status

from app.db.health import check_database
from app.schemas.health import HealthResponse


router = APIRouter(prefix="/api/health", tags=["健康检查"])


@router.get("", response_model=HealthResponse, summary="检查应用是否能响应")
def application_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        component="application",
        code="running",
        message="应用正在运行；此接口不检查数据库连接。",
    )


@router.get(
    "/db",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "数据库未配置或暂不可用"}},
    summary="只读检查指定MySQL数据库的连接",
)
def database_health(response: Response) -> HealthResponse:
    result = check_database()
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    messages = {
        "connected": "已连接配置指定的数据库，SELECT 1 检查通过。",
        "not_configured": "数据库尚未配置，请在本地填写数据库名、账号和密码。",
        "invalid_configuration": "数据库配置无效，请检查本地配置文件及端口格式。",
        "unavailable": "数据库连接检查失败，请在本地核对服务、数据库名、账号和密码。",
    }
    return HealthResponse(
        status="ok" if result.ready else "error",
        component="database",
        code=result.code,
        message=messages[result.code],
    )
