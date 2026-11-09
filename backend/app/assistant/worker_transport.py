"""可取消的助手子进程通信；兼容Windows开发服务器的Selector事件循环。"""

import asyncio
from concurrent.futures import Future, InvalidStateError
import json
import os
import re
import sys
import threading

from app.assistant.knowledge import BACKEND
from app.assistant.model import AIConnectionError


_PRIVATE_ENV_PREFIXES = (
    "SMRBS_", "DEEPSEEK_", "OPENAI_", "LANGCHAIN_", "LANGSMITH_",
    "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
)
_CLEANUP_SECONDS = 3


def _child_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(_PRIVATE_ENV_PREFIXES)
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


async def _invoke_worker(module: str, payload: dict, timeout: float | None) -> dict:
    process = None
    communication = None
    try:
        # 限时包括创建进程和通信，密钥只通过stdin传输，不进入命令行或日志。
        async with asyncio.timeout(timeout):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", module,
                cwd=BACKEND, env=_child_environment(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            communication = asyncio.create_task(process.communicate(json.dumps(payload).encode("utf-8")))
            # 取消调用者不能同时取消stdout读取；kill后必须继续排空管道，
            # 再关闭短命Proactor循环，否则process.wait的返回不保证管道已关闭。
            output, _ = await asyncio.shield(communication)
            if process.returncode != 0:
                raise AIConnectionError("worker_unavailable")
            response = json.loads(output)
            if not isinstance(response, dict):
                raise AIConnectionError("worker_unavailable")
            if response.get("ok") is not True:
                code = response.get("code")
                if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None:
                    code = "worker_unavailable"
                raise AIConnectionError(code)
            return response
    except TimeoutError:
        raise AIConnectionError("timeout") from None
    except (OSError, ValueError, TypeError):
        raise AIConnectionError("worker_unavailable") from None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        if communication is not None:
            try:
                await asyncio.wait_for(communication, timeout=2)
            except (TimeoutError, OSError, ValueError, TypeError):
                pass
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            stdin.close()
            try:
                await asyncio.wait_for(stdin.wait_closed(), timeout=2)
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                pass
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                pass


async def _run_on_proactor(module: str, payload: dict, timeout: float) -> dict:
    """仅在Selector不支持Windows子进程时启用一个短命Proactor线程。

    Process及其管道从创建到清理均属于该线程的循环。主循环取消请求时，
    跨线程取消对应任务并等待有界清理，不能仅取消一个to_thread等待者。
    """
    completion: Future = Future()
    cancelled = threading.Event()
    running = {}

    def cancel_worker() -> None:
        cancelled.set()
        loop, task = running.get("loop"), running.get("task")
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # 任务可能已经完成并关闭循环。
                pass

    async def invoke() -> dict:
        running["loop"] = asyncio.get_running_loop()
        running["task"] = asyncio.current_task()
        if cancelled.is_set():
            raise asyncio.CancelledError()
        # 主循环统一掌握期限，避免两个相同deadline先后cancel同一任务，
        # 第二次取消可能打断已经开始的管道清理。
        return await _invoke_worker(module, payload, None)

    def thread_main() -> None:
        result, error = None, None
        try:
            with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
                result = runner.run(invoke())
        except BaseException as caught:
            error = caught
        # 请求取消会同时取消wrap_future对应的Future，不能再覆盖取消结果。
        try:
            if error is None:
                completion.set_result(result)
            elif isinstance(error, asyncio.CancelledError):
                completion.cancel()
            else:
                completion.set_exception(error)
        except InvalidStateError:
            pass

    worker = threading.Thread(target=thread_main, name="smrbs-worker-proactor", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        raise AIConnectionError("worker_unavailable") from None
    try:
        # 主循环的期限也覆盖线程启动，避免线程尚未就绪时无限等待。
        async with asyncio.timeout(timeout):
            return await asyncio.wrap_future(completion)
    except TimeoutError:
        cancel_worker()
        await asyncio.to_thread(worker.join, _CLEANUP_SECONDS)
        raise AIConnectionError("timeout") from None
    except asyncio.CancelledError:
        cancel_worker()
        await asyncio.to_thread(worker.join, _CLEANUP_SECONDS)
        raise


async def run_worker(module: str, payload: dict, timeout: float) -> dict:
    """运行受信任的本项目worker，返回完整JSON；业务并发限额由调用者管理。"""
    if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module) is None:
        raise AIConnectionError("worker_unavailable")
    loop = asyncio.get_running_loop()
    if sys.platform == "win32" and isinstance(loop, asyncio.SelectorEventLoop):
        return await _run_on_proactor(module, payload, timeout)
    return await _invoke_worker(module, payload, timeout)
