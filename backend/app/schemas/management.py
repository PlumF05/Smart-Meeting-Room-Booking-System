"""管理员审批和既有资源维护的输入边界。"""
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.rooms import EquipmentName


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    action: Literal["approve", "reject", "revoke"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @model_validator(mode="after")
    def required_reason(self) -> Self:
        self.reason = self.reason or None
        if self.action in ("reject", "revoke") and not self.reason:
            raise ValueError("驳回和撤销必须填写原因")
        return self


class RoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    location: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    capacity: int = Field(ge=1, le=2147483647)
    equipment: list[EquipmentName] = Field(max_length=10)
    is_active: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @field_validator("equipment")
    @classmethod
    def unique_equipment(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))
