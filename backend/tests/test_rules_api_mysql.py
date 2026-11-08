"""真实隔离MySQL会话和线程池复核；问答模型用替身，不访问开发库或外部API。"""
import pytest
from sqlalchemy.orm import Session
from test_reservation_submission_mysql import world,encoded_password,client,bind_user,counts
from app.api import assistant as api
from app.core.ai_config import AISettings
from app.models import User
from app.schemas.assistant import RuleAnswer

pytestmark=pytest.mark.mysql


def test_real_session_csrf_role_and_no_business_writes(engine,world,client,monkeypatch):
    calls=[]
    async def answer(*a):
        # 身份检查事务已结束，等待模型期间不占数据库连接。
        assert engine.pool.checkedout()==0
        calls.append(True)
        return RuleAnswer(answer='只读规则',grounded=False,sources=[],knowledge_version='test')
    monkeypatch.setattr(api,'ask_rules',answer)
    monkeypatch.setattr(api,'load_ai_settings',lambda:AISettings(enabled=True,api_key='synthetic-only'))
    headers=bind_user(client,world)
    response=client.post('/api/assistant/rules/ask',json={'question':'取消规则'},headers=headers)
    assert response.status_code==200 and calls==[True] and counts(engine)==(0,0,0)
    assert client.post('/api/assistant/rules/ask',json={'question':'规则'}).status_code==403
    headers=bind_user(client,world,'admin')
    assert client.post('/api/assistant/rules/ask',json={'question':'规则'},headers=headers).status_code==403
    assert calls==[True]


def test_disabled_account_cannot_receive_inflight_answer(engine,world,client,monkeypatch):
    async def answer(*a):
        with Session(engine) as db,db.begin():
            db.get(User,world['users']['student']['id']).is_active=False
        return RuleAnswer(answer='不可返回',grounded=False,sources=[],knowledge_version='test')
    monkeypatch.setattr(api,'ask_rules',answer)
    monkeypatch.setattr(api,'load_ai_settings',lambda:AISettings(enabled=True,api_key='synthetic-only'))
    response=client.post('/api/assistant/rules/ask',json={'question':'规则'},headers=bind_user(client,world))
    assert response.status_code==401 and '不可返回' not in response.text
    assert counts(engine)==(0,0,0)
