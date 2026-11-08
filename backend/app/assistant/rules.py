"""薄编排层：单次独立进程，总预算58秒并限制并行，不让模型加载阻塞业务线程。"""
import asyncio
import json
import os
import sys

from app.core.ai_config import AISettings
from app.assistant.knowledge import BACKEND
from app.assistant.model import AIConnectionError
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
        process = None
        try:
            async with asyncio.timeout(ROUND_TIMEOUT):
                environment = {k:v for k,v in os.environ.items() if not k.startswith(
                    ("SMRBS_", "DEEPSEEK_", "OPENAI_", "LANGCHAIN_", "LANGSMITH_", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"))}
                environment["PYTHONIOENCODING"] = "utf-8"
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.assistant.rules_worker",
                    cwd=BACKEND, env=environment, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, creationflags=0x08000000 if sys.platform=="win32" else 0)
                configuration = settings.model_dump(mode="json")
                configuration["api_key"] = settings.api_key.get_secret_value()
                data = json.dumps(dict(question=question, settings=configuration)).encode("utf-8")
                output, _ = await process.communicate(data)
                if process.returncode != 0:
                    raise AIConnectionError("knowledge_unavailable")
                response = json.loads(output)
                if response.get("ok") is not True:
                    raise AIConnectionError(response.get("code", "knowledge_unavailable"))
                return RuleAnswer.model_validate(response["result"])
        except TimeoutError:
            raise AIConnectionError("timeout") from None
        except (OSError, ValueError, KeyError, TypeError):
            raise AIConnectionError("knowledge_unavailable") from None
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=2)
                except (TimeoutError, ProcessLookupError):
                    pass
