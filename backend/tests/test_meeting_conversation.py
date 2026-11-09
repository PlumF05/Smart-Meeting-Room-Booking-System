"""真实 LangGraph 的中断/恢复流程；模型与只读查询使用固定替身，无网络请求。"""

import asyncio
from copy import deepcopy
import time

import pytest

pytest.importorskip("langgraph.graph")

from app.assistant import conversation as flow
from app.assistant.model import AIConnectionError
from app.schemas.meeting_assistant import ExtractedDemand


OWNER = 7
TOKEN = "isolated-graph-session-token"
CORE = {
    "date": "2026-11-09", "start_time": "10:00", "end_time": "11:00",
    "attendees": 8, "equipment": ["白板"],
}
COMPLETE = {**CORE, "title": "课程设计", "purpose": "讨论课程设计分工"}
ROOMS = [
    {"id": index, "name": f"会议室{index}", "location": f"教学楼{index}层",
     "capacity": index * 10, "equipment": ["白板"], "is_active": True}
    for index in range(1, 5)
]


class SearchStub:
    def __init__(self):
        self.rooms = deepcopy(ROOMS)
        self.reason = "available"
        self.calls = []
        self.error = None

    def __call__(self, draft):
        self.calls.append(deepcopy(draft))
        if self.error is not None:
            raise self.error
        return {"rooms": deepcopy(self.rooms), "reason": self.reason,
                "checked_at": "2026-11-08T09:00:00+08:00"}


def fake_model(monkeypatch, *responses):
    pending = list(responses)
    calls = []

    async def extract(message, draft, settings):
        calls.append((message, deepcopy(draft)))
        assert pending, "出现了未预期的额外模型调用"
        item = pending.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ExtractedDemand.model_validate(item)

    monkeypatch.setattr(flow, "extract_demand", extract)
    return calls


def patch(updates=None, **extra):
    return {"updates": updates or {}, **extra}


async def advance(runtime, view, context, kind="message", **event):
    return await runtime.advance(view.conversation_id, OWNER, TOKEN, view.revision,
                                 {"kind": kind, **event}, context)


def test_partial_demand_only_asks_missing_slots_and_all_candidates_does_not_call_model(monkeypatch):
    calls = fake_model(monkeypatch, patch({"date": CORE["date"], "attendees": 8}),
                       patch({"start_time": "10:00", "end_time": "11:00", "equipment": ["白板"]}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        first = await runtime.start(OWNER, TOKEN, "明天8人", context)
        assert first.phase == "clarify" and first.revision == 1
        assert "开始时间" in first.message and "结束时间" in first.message
        assert "人数" not in first.message and "日期" not in first.message
        assert search.calls == []
        second = await advance(runtime, first, context, message="10点到11点，需要白板")
        assert second.draft.model_dump() == {**CORE, "title": None, "purpose": None}
        assert second.phase == "candidates" and second.revision == 2
        assert [room.id for room in second.candidates] == [1, 2, 3] and second.total == 4
        assert "不会占位" in second.message and second.selected_room is None
        expanded = await advance(runtime, second, context, "all")
        assert len(expanded.candidates) == 4 and expanded.revision == second.revision
        assert len(calls) == 2 and len(search.calls) == 2
        assert calls[1][1]["date"] == CORE["date"] and calls[1][1]["attendees"] == 8

    asyncio.run(scenario())


def test_select_room_then_only_ask_missing_title_and_purpose_preserves_selection(monkeypatch):
    calls = fake_model(monkeypatch, patch(CORE), patch({"title": COMPLETE["title"]}),
                       patch({"purpose": COMPLETE["purpose"]}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "完整查询条件", context)
        view = await advance(runtime, view, context, "select", room_id=2)
        assert view.phase == "summary" and view.selected_room.id == 2
        assert "确认后提交" in view.message
        view = await advance(runtime, view, context, message="主题课程设计")
        assert view.phase == "summary"
        assert view.selected_room.id == 2 and view.draft.date == CORE["date"]
        view = await advance(runtime, view, context, message="讨论课程设计分工")
        assert view.phase == "summary" and view.selected_room.id == 2
        assert view.draft.model_dump() == COMPLETE and view.revision == 4
        assert view.confirmation is None and "确认后提交" in view.message
        assert len(calls) == 3 and len(search.calls) == 4
        state = runtime.graph.get_state(runtime.config(view.conversation_id)).values
        assert state["phase"] == "summary"
        assert TOKEN not in repr(state) and "settings" not in state and "csrf" not in state

    asyncio.run(scenario())


def test_complete_first_message_skips_redundant_clarification_but_requires_explicit_room_choice(monkeypatch):
    calls = fake_model(monkeypatch, patch(COMPLETE))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        assert view.phase == "candidates" and view.selected_room is None
        view = await advance(runtime, view, context, "select", room_id=1)
        assert view.phase == "summary" and view.selected_room.id == 1 and len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("updates", [
    {"start_time": "11:00", "end_time": "12:00"}, {"attendees": 9},
    {"date": "2026-11-10"}, {"equipment": []},
])
def test_changing_search_conditions_clears_explicit_room_selection(monkeypatch, updates):
    fake_model(monkeypatch, patch(COMPLETE), patch(updates))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        view = await advance(runtime, view, context, "select", room_id=1)
        view = await advance(runtime, view, context, message="修改查询条件")
        assert view.phase == "candidates" and view.selected_room is None
        assert view.draft.title == COMPLETE["title"]
        for field, value in updates.items():
            assert getattr(view.draft, field) == value

    asyncio.run(scenario())


def test_omitted_or_null_fields_preserve_old_values_but_explicit_clear_removes_them(monkeypatch):
    fake_model(monkeypatch, patch(COMPLETE), patch({"date": None}),
               patch(clear_fields=["date", "title"]))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        view = await advance(runtime, view, context, "select", room_id=1)
        view = await advance(runtime, view, context, message="未修改日期")
        assert view.draft.model_dump() == COMPLETE and view.selected_room.id == 1
        before = len(search.calls)
        view = await advance(runtime, view, context, message="日期和主题还没确定")
        assert view.draft.date is None and view.draft.title is None
        assert view.draft.purpose == COMPLETE["purpose"] and view.selected_room is None
        assert view.phase == "clarify" and "日期" in view.message
        assert len(search.calls) == before

    asyncio.run(scenario())


def test_ambiguity_requires_answer_and_cannot_be_bypassed_by_view_all(monkeypatch):
    calls = fake_model(monkeypatch,
                       patch(CORE, ambiguities=["请确认是上午还是下午。"],
                             clear_fields=["start_time", "end_time"]),
                       patch({"start_time": "10:00", "end_time": "11:00"}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "时间有歧义", context)
        assert view.phase == "clarify" and "上午" in view.message
        view = await advance(runtime, view, context, "all")
        assert view.phase == "clarify" and search.calls == [] and len(calls) == 1
        assert view.draft.date == CORE["date"]
        view = await advance(runtime, view, context, message="上午")
        assert view.phase == "candidates" and len(search.calls) == 1

    asyncio.run(scenario())


def test_unspecified_ambiguity_clears_search_fields_and_cannot_be_resolved_by_title_only(monkeypatch):
    fake_model(monkeypatch, patch(COMPLETE),
               patch({"start_time": "14:00"}, ambiguities=["请确认预约的日期时段。"]),
               patch({"title": "新的课程讨论"}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        view = await advance(runtime, view, context, "select", room_id=1)
        previous_queries = len(search.calls)
        view = await advance(runtime, view, context, message="时间有歧义")
        assert view.phase == "clarify" and view.selected_room is None
        assert all(getattr(view.draft, field) is None
                   for field in ("date", "start_time", "end_time", "attendees"))
        assert view.draft.equipment == CORE["equipment"]
        view = await advance(runtime, view, context, message="仅修改主题")
        assert view.phase == "clarify" and view.selected_room is None
        assert "日期" in view.message and "人数" in view.message
        assert len(search.calls) == previous_queries

    asyncio.run(scenario())


def test_explicit_clear_has_priority_over_contradictory_model_update(monkeypatch):
    fake_model(monkeypatch, patch(CORE), patch({"date": "2026-11-10"}, clear_fields=["date"]))

    async def scenario():
        runtime = flow.ConversationRuntime()
        context = flow.TurnContext(None, SearchStub())
        view = await runtime.start(OWNER, TOKEN, "完整查询条件", context)
        view = await advance(runtime, view, context, message="日期还未确定")
        assert view.phase == "clarify" and view.draft.date is None

    asyncio.run(scenario())


@pytest.mark.parametrize("reason,fragment", [("conditions", "不会自动放宽条件"), ("personal", "个人时间冲突")])
def test_no_match_keeps_constraints_and_distinguishes_personal_conflict(monkeypatch, reason, fragment):
    fake_model(monkeypatch, patch(CORE))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        search.rooms, search.reason = [], reason
        view = await runtime.start(OWNER, TOKEN, "查询条件", flow.TurnContext(None, search))
        assert view.phase == "no_match" and fragment in view.message
        assert view.candidates == [] and view.selected_room is None
        assert view.draft.model_dump() == {**CORE, "title": None, "purpose": None}
        assert search.calls[0]["equipment"] == ["白板"]

    asyncio.run(scenario())


def test_previously_selected_room_disappearing_does_not_auto_choose_a_replacement(monkeypatch):
    fake_model(monkeypatch, patch(COMPLETE), patch({"title": "新的主题"}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        view = await advance(runtime, view, context, "select", room_id=1)
        search.rooms = search.rooms[1:]
        view = await advance(runtime, view, context, message="仅修改主题")
        assert view.phase == "candidates" and view.selected_room is None
        assert "未自动更换" in view.message and [r.id for r in view.candidates] == [2, 3, 4]
        assert view.draft.title == "新的主题"

    asyncio.run(scenario())


def test_owner_token_revision_selection_and_busy_checks_do_not_advance_state(monkeypatch):
    calls = fake_model(monkeypatch, patch(COMPLETE))

    async def scenario():
        runtime = flow.ConversationRuntime()
        context = flow.TurnContext(None, SearchStub())
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        for owner, token in ((OWNER + 1, TOKEN), (OWNER, TOKEN + "new-login")):
            with pytest.raises(flow.ConversationError) as error:
                runtime.entry(view.conversation_id, owner, token)
            assert error.value.code == "owner"
        for revision, event, expected in (
            (view.revision + 1, {"kind": "all"}, "revision"),
            (view.revision, {"kind": "select", "room_id": 999}, "selection"),
        ):
            with pytest.raises(flow.ConversationError) as error:
                await runtime.advance(view.conversation_id, OWNER, TOKEN, revision, event, context)
            assert error.value.code == expected
        lock = runtime.entries[view.conversation_id]["lock"]
        async with lock:
            with pytest.raises(flow.ConversationError) as error:
                await advance(runtime, view, context, "all")
            assert error.value.code == "busy"
        assert runtime.view(view.conversation_id).model_dump() == view.model_dump()
        assert len(calls) == 1

    asyncio.run(scenario())


def test_expiration_removes_checkpoints_but_does_not_purge_an_active_turn(monkeypatch):
    fake_model(monkeypatch, patch(CORE))

    async def scenario():
        runtime = flow.ConversationRuntime()
        context = flow.TurnContext(None, SearchStub())
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        key = view.conversation_id
        runtime.entries[key]["touched"] = time.monotonic() - 1201
        async with runtime.entries[key]["lock"]:
            runtime.purge()
            assert key in runtime.entries
        with pytest.raises(flow.ConversationError) as error:
            runtime.entry(key, OWNER, TOKEN)
        assert error.value.code == "expired"
        assert key not in runtime.entries
        assert runtime.saver.get_tuple(runtime.config(key)) is None

    asyncio.run(scenario())


def test_before_advance_runs_inside_lock_only_after_revision_and_selection_checks(monkeypatch):
    fake_model(monkeypatch, patch(COMPLETE))

    async def scenario():
        runtime = flow.ConversationRuntime()
        context = flow.TurnContext(None, SearchStub())
        view = await runtime.start(OWNER, TOKEN, "完整需求", context)
        lock = runtime.entries[view.conversation_id]["lock"]
        effects = []

        async def invalidate():
            assert lock.locked()
            effects.append("invalidated")

        async def attempt(revision, event):
            return await runtime.advance(view.conversation_id, OWNER, TOKEN, revision, event,
                                         context, before_advance=invalidate)

        for revision, event, expected in (
            (view.revision + 1, {"kind": "all"}, "revision"),
            (view.revision, {"kind": "select", "room_id": 999}, "selection"),
        ):
            with pytest.raises(flow.ConversationError) as error:
                await attempt(revision, event)
            assert error.value.code == expected and effects == []
        async with lock:
            with pytest.raises(flow.ConversationError) as error:
                await attempt(view.revision, {"kind": "all"})
            assert error.value.code == "busy" and effects == []
        latest = await attempt(view.revision, {"kind": "select", "room_id": 1})
        assert effects == ["invalidated"] and latest.revision == view.revision + 1
        with pytest.raises(flow.ConversationError) as error:
            await attempt(view.revision, {"kind": "all"})
        assert error.value.code == "revision" and effects == ["invalidated"]

    asyncio.run(scenario())


def test_failed_initial_extraction_cleans_up_unpublished_conversation(monkeypatch):
    fake_model(monkeypatch, AIConnectionError("timeout"))

    async def scenario():
        runtime = flow.ConversationRuntime()
        with pytest.raises(AIConnectionError):
            await runtime.start(OWNER, TOKEN, "查询", flow.TurnContext(None, SearchStub()))
        assert runtime.entries == {} and list(runtime.saver.list(None)) == []

    asyncio.run(scenario())


def test_failed_model_turn_restores_waitpoint_and_retry_applies_once(monkeypatch):
    calls = fake_model(monkeypatch, patch(CORE), AIConnectionError("timeout"), patch({"attendees": 9}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        first = await runtime.start(OWNER, TOKEN, "完整查询条件", context)
        with pytest.raises(AIConnectionError):
            await advance(runtime, first, context, message="改成9人")
        assert runtime.view(first.conversation_id).model_dump() == first.model_dump()
        retry = await advance(runtime, first, context, message="改成9人")
        assert retry.revision == first.revision + 1 and retry.draft.attendees == 9
        assert retry.draft.date == CORE["date"] and len(calls) == 3
        assert len(search.calls) == 2

    asyncio.run(scenario())


def test_failed_search_turn_restores_last_confirmed_draft_before_retry(monkeypatch):
    calls = fake_model(monkeypatch, patch(CORE), patch({"attendees": 9}), patch({"attendees": 9}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        first = await runtime.start(OWNER, TOKEN, "完整查询条件", context)
        search.error = RuntimeError("synthetic temporary search failure")
        with pytest.raises(RuntimeError):
            await advance(runtime, first, context, message="改成9人")
        assert runtime.view(first.conversation_id).model_dump() == first.model_dump()
        search.error = None
        retry = await advance(runtime, first, context, message="改成9人")
        assert retry.revision == first.revision + 1 and retry.draft.attendees == 9
        assert calls[2][1]["attendees"] == CORE["attendees"]

    asyncio.run(scenario())


def test_booking_rule_rejection_becomes_clarification_and_can_be_corrected(monkeypatch):
    fake_model(monkeypatch, patch(CORE), patch({"start_time": "11:00", "end_time": "12:00"}))

    async def scenario():
        runtime, search = flow.ConversationRuntime(), SearchStub()
        context = flow.TurnContext(None, search)
        search.error = ValueError("synthetic invalid booking window")
        view = await runtime.start(OWNER, TOKEN, "原时间不符合规则", context)
        assert view.phase == "clarify" and "半小时" in view.message
        assert view.candidates == [] and view.selected_room is None
        search.error = None
        view = await advance(runtime, view, context, message="11点到12点")
        assert view.phase == "candidates" and view.draft.start_time == "11:00"

    asyncio.run(scenario())
