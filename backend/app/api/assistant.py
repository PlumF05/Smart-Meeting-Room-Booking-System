from typing import Annotated
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.auth_dependencies import require_regular_user, require_trusted_origin
from app.assistant.model import AIConnectionError
from app.assistant.rules import ask_rules
from app.core.ai_config import load_ai_settings
from app.core.auth_config import SESSION_COOKIE_NAME
from app.db.session import get_db
from app.schemas.assistant import RuleAnswer, RuleQuestion
from app.schemas.auth import CurrentUser
from app.services.login import get_session_info, InvalidCsrfError, valid_token_format

router = APIRouter(prefix="/api/assistant", tags=["规则助手"], dependencies=[Depends(require_trusted_origin)])


@router.post("/rules/ask", response_model=RuleAnswer, summary="只读规则问答，不执行预约操作")
async def ask(data: RuleQuestion, request: Request,
    user: Annotated[CurrentUser, Depends(require_regular_user)],
    db: Annotated[Session, Depends(get_db)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    info = await run_in_threadpool(get_session_info, db, token)
    if not valid_token_format(csrf_token) or not secrets.compare_digest(info.csrf_token, csrf_token):
        raise InvalidCsrfError()
    try:
        settings = load_ai_settings()
        result = await ask_rules(data.question, settings)
    except (OSError, ValueError):
        raise HTTPException(503, "助手配置不可用，请检查本地AI配置。") from None
    except AIConnectionError as error:
        message = "规则助手暂不可用，请稍后重试；普通预约页面仍可使用。"
        status = 503
        if error.code == "busy":
            status, message = 429, "规则助手正在处理其他问题，请稍后重试。"
        elif error.code == "timeout":
            status, message = 504, "规则问答超时，请稍后重试；未执行任何预约操作。"
        elif error.code == "knowledge_text_length":
            status, message = 400, "问题过长，请缩短描述后重试。"
        elif error.code.startswith("knowledge_"):
            message = "规则知识或本地索引不可用，请先完成知识准备；普通页面仍可使用。"
        raise HTTPException(status, message) from None
    # 不在模型调用期间持有事务；返回前复核会话，避免向已失效会话返回结果。
    current = await run_in_threadpool(get_session_info, db, token)
    if current.user.id != user.id:
        raise HTTPException(401, "登录状态已变化，请重新登录。")
    return result
