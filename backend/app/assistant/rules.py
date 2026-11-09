"""薄编排层：单次独立进程，总预算58秒并限制并行，不让模型加载阻塞业务线程。"""
import asyncio

from app.core.ai_config import AISettings
from app.assistant.model import AIConnectionError
from app.assistant.worker_transport import run_worker
from app.schemas.assistant import RuleAnswer

ROUND_TIMEOUT = 58
_busy = asyncio.Lock()


async def ask_rules(question: str, settings: AISettings) -> RuleAnswer:
    if not settings.enabled:
        raise AIConnectionError("disabled")
    if not settings.is_configured:
        raise AIConnectionError("missing_key")
    if _busy.locked():
        raise AIConnectionError("busy")
    async with _busy:
        try:
            configuration = settings.model_dump(mode="json")
            configuration["api_key"] = settings.api_key.get_secret_value()
            response = await run_worker(
                "app.assistant.rules_worker", dict(question=question, settings=configuration), ROUND_TIMEOUT,
            )
            return RuleAnswer.model_validate(response["result"])
        except (ValueError, KeyError, TypeError):
            raise AIConnectionError("knowledge_unavailable") from None
