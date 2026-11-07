"""内部事件写入结构，不接受任意请求体或额外的敏感字段。"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.operation_enums import (
    OperationAction, OperationActor, OperationObject, OperationResult, OperationSource,
)


PositiveId = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]
StatusText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
ReasonText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class OperationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    event_key: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:_-]{1,64}$", min_length=1, max_length=64)]
    actor_kind: OperationActor
    actor_user_id: PositiveId | None = None
    object_type: OperationObject
    object_id: PositiveId
    action: OperationAction
    source: OperationSource
    before_status: StatusText | None = None
    after_status: StatusText | None = None
    result: OperationResult = OperationResult.SUCCESS
    reason: ReasonText | None = None

    @model_validator(mode="after")
    def actor_matches_source(self) -> Self:
        if self.actor_kind == OperationActor.SYSTEM:
            if self.actor_user_id is not None or self.source != OperationSource.SYSTEM:
                raise ValueError("系统事件不能绑定用户，且来源必须为system")
        elif self.actor_user_id is None or self.source == OperationSource.SYSTEM:
            raise ValueError("用户事件必须提供操作者，来源为page或assistant")
        return self


class MessageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    recipient_user_id: PositiveId
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
