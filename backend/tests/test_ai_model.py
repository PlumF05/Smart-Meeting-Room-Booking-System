"""实际LangChain/SDK配模拟HTTP；不使用本地密钥，不连接外部服务。"""
import asyncio
from contextlib import contextmanager
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.assistant import model as service
from app.core.ai_config import AISettings

pytest.importorskip("langchain_deepseek", reason="仅安装基础依赖时不运行AI集成检查")
from langchain_deepseek import ChatDeepSeek
from langsmith import tracing_context
from openai import APITimeoutError

SETTINGS = AISettings(enabled=True, api_key="synthetic-secret-never-live")


def mocked_http(monkeypatch, handler):
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    adapter = SimpleNamespace(AsyncClient=client, Timeout=httpx.Timeout, TimeoutException=httpx.TimeoutException)
    monkeypatch.setattr(service, "_dependencies", lambda: (ChatDeepSeek, tracing_context, adapter, APITimeoutError))


def answer(content="SMRBS_OK"):
    return {"id": "synthetic", "object": "chat.completion", "model": "deepseek-flash", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


def test_real_sdk_sends_only_fixed_probe_and_no_tracing(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-secret-never-live"
        data = json.loads(request.content)
        assert data["model"] == "deepseek-flash"
        assert data["max_tokens"] == 32 and data["thinking"] == {"type": "disabled"}
        assert data["messages"] == [{"role": "user", "content": "Reply with SMRBS_OK only."}]
        assert not data.get("tools") and not data.get("stream")
        return httpx.Response(200, json=answer())
    mocked_http(monkeypatch, handler)
    # 即使外部开启追踪，调用模型时也必须处于禁用上下文。
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    from langsmith import run_helpers
    @contextmanager
    def checked_trace(**kwargs):
        with tracing_context(**kwargs):
            assert run_helpers.get_tracing_context()["enabled"] is False
            yield
    original = service._dependencies
    monkeypatch.setattr(service, "_dependencies", lambda: (ChatDeepSeek, checked_trace, original()[2], APITimeoutError))
    # 上面的工厂必须持有原适配器，而不是递归读取已替换工厂。
    elapsed = asyncio.run(service.check_connection(SETTINGS))
    assert elapsed >= 0 and len(requests) == 1


@pytest.mark.parametrize("status,code", [(401,"credentials"),(403,"credentials"),(402,"balance"),
    (429,"rate_limit"),(400,"model_or_request"),(404,"model_or_request"),(422,"model_or_request"),(503,"provider")])
def test_sdk_errors_are_safe_and_never_retry(monkeypatch, status, code):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "synthetic-secret-never-live", "type": "test"}})
    mocked_http(monkeypatch, handler)
    levels = {name: logging.getLogger(name).level for name in ("openai","httpx","httpcore","langchain","langsmith")}
    with pytest.raises(service.AIConnectionError) as raised:
        asyncio.run(service.check_connection(SETTINGS))
    assert raised.value.code == code and str(raised.value) == code
    assert len(requests) == 1
    assert all(logging.getLogger(name).level == level for name, level in levels.items())


@pytest.mark.parametrize("failure,code", [(httpx.ReadTimeout,"timeout"),(httpx.ConnectError,"connection")])
def test_wrapped_transport_failures(monkeypatch, failure, code):
    calls = []
    def handler(request):
        calls.append(True)
        raise failure("synthetic-secret-never-live", request=request)
    mocked_http(monkeypatch, handler)
    with pytest.raises(service.AIConnectionError) as raised:
        asyncio.run(service.check_connection(SETTINGS))
    assert raised.value.code == code and len(calls) == 1


def test_empty_reply_is_not_success(monkeypatch):
    mocked_http(monkeypatch, lambda request: httpx.Response(200, json=answer("")))
    with pytest.raises(service.AIConnectionError, match="empty_response"):
        asyncio.run(service.check_connection(SETTINGS))


def test_disabled_or_missing_key_does_not_load_ai(monkeypatch):
    def forbidden():
        pytest.fail("未启用或缺密钥时不能加载模型")
    monkeypatch.setattr(service, "_dependencies", forbidden)
    for settings, code in ((AISettings(), "disabled"), (AISettings(enabled=True), "missing_key")):
        with pytest.raises(service.AIConnectionError, match=code):
            asyncio.run(service.check_connection(settings))


def test_total_deadline_bounds_a_slow_call(monkeypatch):
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json=answer())
    mocked_http(monkeypatch, handler)
    with pytest.raises(service.AIConnectionError, match="timeout"):
        asyncio.run(service.check_connection(SETTINGS.model_copy(update={"timeout_seconds": .05})))
