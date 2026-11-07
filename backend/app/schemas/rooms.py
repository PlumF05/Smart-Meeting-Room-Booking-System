"""会议室资料查询；启用状态不代表某个时段一定可预约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


EquipmentName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]


class RoomSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    attendees: int | None = Field(
        default=None, ge=1, le=2147483647, description="预计人数，房间容量须不少于此人数；留空不筛选。",
    )
    equipment: list[EquipmentName] = Field(
        default_factory=list, max_length=10, description="所需设备，房间必须全部具备；可多次传入equipment参数。",
    )
    is_active: bool | None = Field(
        default=None, description="true仅启用，false仅停用；留空返回两者。这不是时段空闲状态。",
    )


class RoomInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    location: str
    capacity: int
    equipment: list[str]
    is_active: bool
