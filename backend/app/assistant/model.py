"""可选模型生成；只接收必要上下文，不访问业务数据库或保存模型内容。"""

import asyncio
import logging
from time import perf_counter

from app.core.ai_config import AISettings


class AIConnectionError(Exception):
    """只保存可公开的错误分类，不包含SDK异常、请求体或凭据。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _dependencies():
    # 放在实际调用处，使基础应用和离线配置检查不依赖AI包。
    try:
        from langchain_deepseek import ChatDeepSeek
        from langsmith import tracing_context
        from openai import APITimeoutError
        import httpx
    except (ImportError, OSError):
        raise AIConnectionError("dependencies") from None
    return ChatDeepSeek, tracing_context, httpx, APITimeoutError


async def generate_text(settings: AISettings, messages: list[tuple[str, str]], *,
                        max_tokens: int = 500, json_output: bool = False) -> str:
    if not settings.enabled:
        raise AIConnectionError("disabled")
    if not settings.is_configured:
        raise AIConnectionError("missing_key")
    create_model, tracing_context, httpx, sdk_timeout_error = _dependencies()
    # 即使本机配置了SDK调试日志，也不允许本次检查输出请求/响应正文。
    loggers = [logging.getLogger(name) for name in ("openai", "httpx", "httpcore", "langchain", "langsmith")]
    previous_levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.CRITICAL)

    async def invoke():
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=min(5, settings.timeout_seconds)),
        ) as client:
            model = create_model(
                model=settings.model,
                api_key=settings.api_key,
                base_url="https://api.deepseek.com",
                http_async_client=client,
                max_retries=0,
                timeout=settings.timeout_seconds,
                max_tokens=max_tokens,
                streaming=False,
                extra_body={"thinking": {"type": "disabled"}},
                model_kwargs={"response_format": {"type": "json_object"}} if json_output else {},
            )
            # 不发送LangSmith遥测，只使用调用方提供的必要上下文。
            with tracing_context(enabled=False):
                response = await model.ainvoke(
                    messages, config={"callbacks": []},
                )
            if not isinstance(response.content, str) or not response.content.strip():
                raise AIConnectionError("empty_response")
            return response.content

    try:
        result = await asyncio.wait_for(invoke(), timeout=settings.timeout_seconds)
    except AIConnectionError:
        raise
    except (TimeoutError, httpx.TimeoutException, sdk_timeout_error):
        raise AIConnectionError("timeout") from None
    except Exception as error:
        # SDK异常可能附带请求与响应；只映射状态码，不传播原始内容。
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            code = "credentials"
        elif status == 402:
            code = "balance"
        elif status == 429:
            code = "rate_limit"
        elif status in (400, 404, 422):
            code = "model_or_request"
        elif isinstance(status, int) and status >= 500:
            code = "provider"
        else:
            code = "connection"
        raise AIConnectionError(code) from None
    finally:
        for logger, level in zip(loggers, previous_levels):
            logger.setLevel(level)
    return result


async def check_connection(settings: AISettings) -> float:
    started = perf_counter()
    await generate_text(settings, [("human", "Reply with SMRBS_OK only.")], max_tokens=32)
    return perf_counter() - started
