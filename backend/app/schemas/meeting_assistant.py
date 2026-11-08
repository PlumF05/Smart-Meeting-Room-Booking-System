"""助手草稿和显式交互事件；模型不能指定申请人、来源或审批状态。"""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from app.schemas.rooms import RoomInfo, EquipmentName

MessageText = Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=500)]
ShortText = Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=200)]


class Demand(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,hide_input_in_errors=True)
    date: str | None = Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')
    start_time: str | None = Field(default=None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end_time: str | None = Field(default=None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    attendees: int | None = Field(default=None,ge=1,le=2147483647)
    equipment: list[EquipmentName] = Field(default_factory=list,max_length=10)
    title: ShortText | None = None
    purpose: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=1000)] | None = None


class ExtractedDemand(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,hide_input_in_errors=True)
    updates: Demand
    ambiguities: list[ShortText] = Field(default_factory=list,max_length=5)
    clear_fields: list[Literal['date','start_time','end_time','attendees','title','purpose']] = Field(default_factory=list,max_length=6)


class StartConversation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message: MessageText


class RevisionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: int = Field(ge=1)


class ContinueConversation(RevisionInput):
    message: MessageText


class SelectRoom(RevisionInput):
    room_id: int = Field(ge=1)


class ShowCandidates(RevisionInput):
    show_all: Literal[True] = True


class ConversationView(BaseModel):
    conversation_id: str
    revision: int
    phase: Literal['clarify','candidates','no_match','summary']
    message: str
    draft: Demand
    candidates: list[RoomInfo]
    total: int
    checked_at: str | None = None
    selected_room: RoomInfo | None = None
    confirmation: dict | None = None
