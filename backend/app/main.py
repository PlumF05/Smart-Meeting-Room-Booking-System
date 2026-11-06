"""创建应用并注册本轮已实现的健康检查接口。"""

from fastapi import FastAPI

from app.api.health import router as health_router


app = FastAPI(
    title="SMRBS API",
    description="学院智能会议室预约系统：阶段1基础入口与健康检查。",
    version="0.1.0",
)
app.include_router(health_router)
