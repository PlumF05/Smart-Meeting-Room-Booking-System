"""规则接口权限、成本边界及短时会话复核；不读取配置、不访问MySQL或模型。"""
from types import SimpleNamespace
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api import assistant as api
from app.api.auth_dependencies import get_current_user
from app.assistant import rules
from app.assistant.model import AIConnectionError
from app.core.ai_config import AISettings
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.assistant import RuleAnswer
from app.services.login import InvalidSessionError


@pytest.fixture
def world(monkeypatch):
    user = CurrentUser(id=7, username="synthetic",full_name="测试",identity_type="student",role="user",is_active=True)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(api,"get_session_info",lambda *a:SimpleNamespace(user=user,csrf_token="S" * 43))
    monkeypatch.setattr(api,"load_ai_settings",lambda:AISettings(enabled=True,api_key="synthetic-only"))
    calls = []
    async def answer(question,settings):
        calls.append(question)
        return RuleAnswer(answer="没有足够依据",grounded=False,sources=[],knowledge_version="test")
    monkeypatch.setattr(api,"ask_rules",answer)
    with TestClient(app) as client:
        yield client,user,calls
    app.dependency_overrides.clear()


def test_regular_user_gets_structured_non_cached_answer(world):
    client,_,calls=world
    response=client.post('/api/assistant/rules/ask',json={'question':'取消规则是什么'},headers={'X-CSRF-Token':'S' * 43})
    assert response.status_code==200 and response.json()['sources']==[]
    assert response.headers['cache-control']=='no-store' and calls==['取消规则是什么']


@pytest.mark.parametrize('role', ['admin','anonymous'])
def test_unauthorized_requests_do_not_call_ai(world,monkeypatch,role):
    client,user,calls=world
    if role=='admin': user.role='admin'
    else:
        def reject(): raise InvalidSessionError()
        app.dependency_overrides[get_current_user]=reject
    response=client.post('/api/assistant/rules/ask',json={'question':'规则'},headers={'X-CSRF-Token':'S' * 43})
    assert response.status_code==(403 if role=='admin' else 401) and not calls


@pytest.mark.parametrize('body', [{'question':''},{'question':'x'*501},{'question':'规则','user_id':9}])
def test_invalid_input_never_reaches_model(world,body):
    client,_,calls=world
    assert client.post('/api/assistant/rules/ask',json=body,headers={'X-CSRF-Token':'S' * 43}).status_code==422
    assert not calls


def test_missing_csrf_or_cross_origin_does_not_spend_api(world):
    client,_,calls=world
    assert client.post('/api/assistant/rules/ask',json={'question':'规则'}).status_code==403
    assert client.post('/api/assistant/rules/ask',json={'question':'规则'},headers={
        'Origin':'https://untrusted.invalid','X-CSRF-Token':'S' * 43}).status_code==403
    assert not calls


@pytest.mark.parametrize('code,status',[('timeout',504),('busy',429),('knowledge_index',503),('credentials',503),('knowledge_text_length',400)])
def test_failures_are_safe_and_leave_health_working(world,monkeypatch,code,status):
    client,_,_=world
    async def failed(*a): raise AIConnectionError(code)
    monkeypatch.setattr(api,'ask_rules',failed)
    response=client.post('/api/assistant/rules/ask',json={'question':'规则'},headers={'X-CSRF-Token':'S' * 43})
    assert response.status_code==status and 'synthetic-only' not in response.text
    assert client.get('/api/health').status_code==200


def test_session_invalidated_during_generation_cannot_receive_answer(world,monkeypatch):
    client,user,calls=world
    checked=[]
    def session(*a):
        checked.append(True)
        if len(checked)>1: raise InvalidSessionError()
        return SimpleNamespace(user=user,csrf_token='S' * 43)
    monkeypatch.setattr(api,'get_session_info',session)
    assert client.post('/api/assistant/rules/ask',json={'question':'规则'},headers={'X-CSRF-Token':'S' * 43}).status_code==401
    assert len(calls)==1


def test_worker_timeout_kills_process_and_releases_busy_slot(monkeypatch):
    class Process:
        returncode=None
        killed=False
        async def communicate(self,data):
            assert b'synthetic-only' in data
            await asyncio.sleep(5)
        def kill(self): self.killed=True; self.returncode=-1
        async def wait(self): return self.returncode
    process=Process()
    async def spawn(*a,**kwargs):
        assert 'synthetic-only' not in str(a) and kwargs['stderr']==asyncio.subprocess.DEVNULL
        return process
    monkeypatch.setattr(rules.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(rules,'ROUND_TIMEOUT',.03)
    with pytest.raises(AIConnectionError,match='timeout'):
        asyncio.run(rules.ask_rules('规则',AISettings(enabled=True,api_key='synthetic-only')))
    assert process.killed and not rules._busy.locked()


def test_worker_startup_is_also_inside_deadline(monkeypatch):
    async def slow_spawn(*a,**k):
        await asyncio.sleep(5)
    monkeypatch.setattr(rules.asyncio,'create_subprocess_exec',slow_spawn)
    monkeypatch.setattr(rules,'ROUND_TIMEOUT',.03)
    with pytest.raises(AIConnectionError,match='timeout'):
        asyncio.run(rules.ask_rules('规则',AISettings(enabled=True,api_key='synthetic-only')))
    assert not rules._busy.locked()
