"""短期LangGraph对话。检查点不含密钥/登录凭据，业务工具通过当前请求上下文提供。"""

import asyncio
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypedDict

from starlette.concurrency import run_in_threadpool

from app.assistant.model import AIConnectionError
from app.schemas.meeting_assistant import ConversationView, Demand, ExtractedDemand
from app.services.booking_time import BookingTimeError, get_beijing_now
from app.services.login import token_digest


class ConversationError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass
class TurnContext:
    settings: object
    search: object
    emit: object = None


_context = ContextVar("assistant_turn")
_dialogue = ContextVar("assistant_dialogue", default={})
_model_slots = asyncio.Semaphore(2)


def booking_ambiguities(messages: list[str]) -> list[str]:
    """模型的自然语言提示不能将选填资料重新变成预约前置条件。"""
    optional_words = ("主题", "用途", "目的", "议题", "会议内容", "title", "purpose")
    required_words = (
        "日期",
        "时间",
        "时段",
        "几点",
        "人数",
        "多少人",
        "几人",
        "开始",
        "结束",
        "date",
        "time",
        "attendee",
        "people",
        "start",
        "end",
    )
    result = []
    for message in messages:
        text = message.lower()
        if any(word in text for word in optional_words):
            if any(word in text for word in required_words):
                result.append("预约日期、起止时间或人数尚有歧义，请明确这些条件。")
            continue
        result.append(message)
    return list(dict.fromkeys(result))


async def extract_demand(message, draft, settings):
    if not settings.enabled or not settings.is_configured:
        raise AIConnectionError("disabled")
    if _model_slots.locked():
        raise AIConnectionError("busy")
    await _model_slots.acquire()
    try:
        from app.assistant.worker_transport import run_worker

        config = settings.model_dump(mode="json")
        config["api_key"] = settings.api_key.get_secret_value()
        payload = dict(
            message=message,
            draft=draft,
            settings=config,
            now=get_beijing_now().isoformat(),
            dialogue=_dialogue.get(),
        )
        result = await run_worker("app.assistant.demand_worker", payload, 45)
        return ExtractedDemand.model_validate(result["result"])
    except (OSError, ValueError, KeyError, TypeError):
        raise AIConnectionError("invalid_demand") from None
    finally:
        _model_slots.release()


class FlowState(TypedDict, total=False):
    draft: dict
    revision: int
    event: dict
    rooms: list
    selected_room: dict | None
    checked_at: str | None
    phase: str
    message: str
    ambiguities: list
    show_all: bool
    restore_only: bool
    history: list
    requested_room_name: str | None
    notice: str | None


def build_graph():
    # 基础应用可在未安装LangGraph时启动；只有助手对话使用它。
    from langgraph.graph import StateGraph, START
    from langgraph.types import interrupt
    from langgraph.checkpoint.memory import InMemorySaver

    async def apply_event(state):
        event = state["event"]
        draft = state["draft"]
        selected = state.get("selected_room")
        original_draft = draft.copy()
        original_selected = selected
        ambiguities = state.get("ambiguities", [])
        requested = state.get("requested_room_name")
        if event["kind"] == "message":
            if _context.get().emit:
                _context.get().emit("正在理解你的需求…")
            visible = (
                state.get("rooms", [])
                if state.get("show_all")
                else state.get("rooms", [])[:3]
            )
            marker = _dialogue.set(
                dict(
                    history=state.get("history", [])[-8:],
                    candidates=[{"id": r["id"], "name": r["name"]} for r in visible],
                    selected_room=selected,
                )
            )
            try:
                parsed = await extract_demand(
                    event["message"], draft, _context.get().settings
                )
            finally:
                _dialogue.reset(marker)
            if parsed.intent != "booking":
                return dict(
                    notice="我可以帮你查找会议室，并在你核对完整摘要后提交预约申请。请告诉我日期、时间和人数。"
                    if parsed.intent == "help"
                    else "当前助手提供会议室查询和申请提交。你可以继续补充预约需求；其他功能请使用对应业务页面。",
                    revision=max(1, state["revision"]),
                )
            updates = {
                k: v
                for k, v in parsed.updates.model_dump(
                    mode="json", exclude_unset=True
                ).items()
                if v is not None
            }
            cleared = set(parsed.clear_fields)
            ambiguities = booking_ambiguities(parsed.ambiguities)
            # 歧义不得沿用旧条件成为完整方案。模型未指出字段时保守重新确认查询条件。
            if ambiguities and not cleared:
                cleared.update(("date", "start_time", "end_time", "attendees"))
            updated = Demand.model_validate(
                {**draft, **updates, **{k: None for k in cleared}}
            ).model_dump()
            if any(
                updated[k] != draft[k]
                for k in ("date", "start_time", "end_time", "attendees", "equipment")
            ):
                selected = None
            draft = updated
            if parsed.room_name:
                requested = parsed.room_name
                selected = None
            if parsed.room_id is not None:
                selected = next((r for r in visible if r["id"] == parsed.room_id), None)
                if selected is None:
                    ambiguities = ["请从本次展示的候选会议室中选择。"]
                else:
                    requested = None
        elif event["kind"] == "select":
            selected = next(r for r in state["rooms"] if r["id"] == event["room_id"])
            requested = None
        changed = (
            draft != original_draft
            or selected != original_selected
            or requested != state.get("requested_room_name")
            or event["kind"] == "renew"
        )
        return dict(
            draft=draft,
            selected_room=selected,
            ambiguities=ambiguities,
            revision=max(1, state["revision"] + int(changed)),
            requested_room_name=requested,
            notice=None,
            show_all=event["kind"] == "all",
        )

    async def resolve(state):
        if state.get("notice"):
            return dict(message=state["notice"])
        missing = [
            label
            for key, label in (
                ("date", "日期"),
                ("start_time", "开始时间"),
                ("end_time", "结束时间"),
                ("attendees", "人数"),
            )
            if state["draft"][key] is None
        ]
        if state["ambiguities"] or missing:
            return dict(
                phase="clarify",
                message="；".join(state["ambiguities"])
                if state["ambiguities"]
                else "请补充" + "、".join(missing) + "。",
                rooms=[],
            )
        if _context.get().emit:
            _context.get().emit("正在查询符合条件的会议室…")
        try:
            found = await run_in_threadpool(_context.get().search, state["draft"])
        except (BookingTimeError, ValueError):
            return dict(
                phase="clarify",
                message="日期时段或人数不符合预约规则，请核对：未来7天、08:00—22:00、半小时对齐、30分钟至4小时。",
                rooms=[],
                selected_room=None,
            )
        rooms = found["rooms"]
        selected = state.get("selected_room")
        if state.get("requested_room_name"):
            matches = [r for r in rooms if r["name"] == state["requested_room_name"]]
            if len(matches) == 1:
                selected = matches[0]
            else:
                return dict(
                    phase="candidates" if rooms else "no_match",
                    message="指定会议室当前不满足条件或名称不唯一，请核对名称或从匹配候选中选择。",
                    rooms=rooms,
                    selected_room=None,
                    checked_at=found["checked_at"],
                )
        if not rooms:
            message = (
                "你在所选时段已有其他预约，这是个人时间冲突，不代表其他会议室已被占用。"
                if found["reason"] == "personal"
                else "没有同时满足人数、设备、资源状态和时段的会议室。可修改条件后重新查询，系统不会自动放宽条件。"
            )
            return dict(
                phase="no_match",
                message=message,
                rooms=[],
                selected_room=None,
                checked_at=found["checked_at"],
            )
        if selected is not None:
            selected = next((r for r in rooms if r["id"] == selected["id"]), None)
            if selected is None:
                return dict(
                    phase="candidates",
                    message="原选会议室当前不再满足条件，请重新选房；未自动更换。",
                    rooms=rooms,
                    selected_room=None,
                    checked_at=found["checked_at"],
                )
            return dict(
                phase="summary",
                message="请核对完整摘要，确认后提交申请。提交成功后仍需管理员审批。",
                rooms=rooms,
                selected_room=selected,
                checked_at=found["checked_at"],
            )
        return dict(
            phase="candidates",
            message=f"找到{len(rooms)}间匹配会议室，请选择。查询不会占位。",
            rooms=rooms,
            checked_at=found["checked_at"],
        )

    def wait_for_user(state):
        return {
            "event": interrupt({"phase": state["phase"], "message": state["message"]})
        }

    def remember(state):
        previous = state.get("history", [])
        if state["event"]["kind"] == "message":
            previous = previous + [
                {"role": "user", "content": state["event"]["message"]}
            ]
        return {
            "history": (
                previous + [{"role": "assistant", "content": state["message"]}]
            )[-10:]
        }

    graph = StateGraph(FlowState)
    graph.add_node("apply", apply_event)
    graph.add_node("resolve", resolve)
    graph.add_node("wait", wait_for_user)
    graph.add_node("remember", remember)
    graph.add_conditional_edges(
        START, lambda s: "wait" if s.get("restore_only") else "apply"
    )
    graph.add_edge("apply", "resolve")
    graph.add_edge("resolve", "remember")
    graph.add_edge("remember", "wait")
    graph.add_edge("wait", "apply")
    saver = InMemorySaver()
    return graph.compile(checkpointer=saver), saver


class ConversationRuntime:
    def __init__(self):
        self.graph, self.saver = build_graph()
        self.entries = {}

    def purge(self):
        for key, entry in list(self.entries.items()):
            if (
                time.monotonic() - entry["touched"] > 1200
                and not entry["lock"].locked()
            ):
                self.delete(key)

    def delete(self, key):
        self.entries.pop(key, None)
        self.saver.delete_thread(key)

    def entry(self, key, user_id, raw_token):
        self.purge()
        entry = self.entries.get(key)
        if entry is None:
            raise ConversationError("expired", "对话已过期或服务已重启，请重新开始。")
        if entry["owner"] != (user_id, token_digest(raw_token)):
            raise ConversationError("owner", "未找到本人的当前会话对话。")
        return entry

    async def start(self, user_id, raw_token, message, context):
        self.purge()
        if len(self.entries) >= 50:
            raise ConversationError("busy", "助手对话数量已达上限，请稍后重试。")
        key = secrets.token_urlsafe(24)
        entry = dict(
            owner=(user_id, token_digest(raw_token)),
            touched=time.monotonic(),
            lock=asyncio.Lock(),
            stable=None,
        )
        self.entries[key] = entry
        try:
            return await self._run(
                key,
                entry,
                dict(
                    draft=Demand().model_dump(),
                    revision=0,
                    event={"kind": "message", "message": message},
                    rooms=[],
                    phase="clarify",
                    message="",
                    history=[],
                ),
                context,
            )
        except BaseException:
            self.delete(key)
            raise

    async def advance(
        self, key, user_id, raw_token, revision, event, context, *, before_advance=None
    ):
        entry = self.entry(key, user_id, raw_token)
        if entry["lock"].locked():
            raise ConversationError("busy", "对话正在处理，请等待本轮结果。")
        async with entry["lock"]:
            stable = entry["stable"]
            if stable["revision"] != revision:
                raise ConversationError(
                    "revision", "对话版本已变化，请使用最新结果或重新开始。"
                )
            if event["kind"] == "select" and not any(
                r["id"] == event["room_id"] for r in stable["rooms"]
            ):
                raise ConversationError("selection", "请选择当前查询得到的候选会议室。")
            # 和版本校验共用会话锁，过时请求不能撤销新版本的凭证。
            if before_advance is not None:
                await before_advance()
            from langgraph.types import Command

            try:
                return await self._run(key, entry, Command(resume=event), context)
            except BaseException:
                # 失败回到上一个等待点，避免下一次重试落入未完成节点。
                self.saver.delete_thread(key)
                from langsmith import tracing_context

                with tracing_context(enabled=False):
                    self.graph.invoke(
                        {**stable, "restore_only": True}, self.config(key)
                    )
                raise

    @staticmethod
    def config(key):
        return {
            "configurable": {"thread_id": key},
            "recursion_limit": 20,
            "callbacks": [],
        }

    async def _run(self, key, entry, value, context):
        token = _context.set(context)
        try:
            from langsmith import tracing_context

            with tracing_context(enabled=False):
                async with asyncio.timeout(55):
                    await self.graph.ainvoke(value, self.config(key))
            entry["stable"] = dict(self.graph.get_state(self.config(key)).values)
            entry["touched"] = time.monotonic()
            return self.view(key)
        finally:
            _context.reset(token)

    def view(self, key):
        state = self.entries[key]["stable"]
        rooms = state["rooms"]
        return ConversationView(
            conversation_id=key,
            revision=state["revision"],
            phase=state["phase"],
            message=state["message"],
            draft=state["draft"],
            candidates=rooms if state.get("show_all") else rooms[:3],
            total=len(rooms),
            checked_at=state.get("checked_at"),
            selected_room=state.get("selected_room"),
            confirmation=None,
        )


_runtime = None


def runtime():
    global _runtime
    if _runtime is None:
        _runtime = ConversationRuntime()
    return _runtime
