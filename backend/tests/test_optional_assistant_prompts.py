"""选填主题、用途不能被模型歧义提示重新变成必填。"""
import asyncio
import pytest
from app.assistant import conversation as flow
from app.schemas.meeting_assistant import ExtractedDemand
from test_meeting_conversation import CORE, OWNER, TOKEN, SearchStub


@pytest.mark.parametrize('question', ['请补充会议主题和用途。', '主题不能为空，请提供主题', '请说明使用目的', 'Please provide title and purpose'])
def test_optional_detail_question_does_not_block_candidates_or_summary(monkeypatch,question):
    async def extract(*args):
        return ExtractedDemand(updates=CORE,ambiguities=[question])
    monkeypatch.setattr(flow,'extract_demand',extract)

    async def scenario():
        runtime=flow.ConversationRuntime()
        context=flow.TurnContext(None,SearchStub())
        view=await runtime.start(OWNER,TOKEN,'明天10点到11点，8人',context)
        assert view.phase=='candidates'
        assert view.draft.attendees==8 and view.draft.start_time=='10:00'
        view=await runtime.advance(view.conversation_id,OWNER,TOKEN,view.revision,{'kind':'select','room_id':1},context)
        assert view.phase=='summary' and '请补充' not in view.message
    asyncio.run(scenario())


def test_missing_required_fields_are_still_asked_instead_of_optional_details(monkeypatch):
    async def extract(*args):
        return ExtractedDemand(updates={'attendees':6},ambiguities=['请提供会议主题及用途'])
    monkeypatch.setattr(flow,'extract_demand',extract)

    async def scenario():
        view=await flow.ConversationRuntime().start(OWNER,TOKEN,'6人',flow.TurnContext(None,SearchStub()))
        assert view.phase=='clarify'
        assert '日期' in view.message and '开始时间' in view.message
        assert '主题' not in view.message and '用途' not in view.message
        assert view.draft.attendees==6
    asyncio.run(scenario())


def test_mixed_required_and_optional_ambiguity_still_requires_time_clarification(monkeypatch):
    async def extract(*args):
        return ExtractedDemand(updates=CORE,ambiguities=['开始时间不明确，还需补充主题和用途。'])
    monkeypatch.setattr(flow,'extract_demand',extract)

    async def scenario():
        view=await flow.ConversationRuntime().start(OWNER,TOKEN,'时间还没确定',flow.TurnContext(None,SearchStub()))
        assert view.phase=='clarify' and '时间' in view.message
        assert '主题' not in view.message and '用途' not in view.message
        assert not view.candidates
    asyncio.run(scenario())
