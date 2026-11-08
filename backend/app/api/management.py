"""管理员审批与资源维护入口；普通账号不能调用。"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.auth_dependencies import require_admin, require_trusted_origin
from app.core.auth_config import SESSION_COOKIE_NAME
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.management import DecisionInput, RoomUpdate
from app.schemas.rooms import RoomInfo, RoomSearch
from app.services.management import ManagementError, decide, list_applications, update_room
from app.services.rooms import list_rooms


router = APIRouter(prefix="/api/admin", tags=["审批与资源维护"],
                   dependencies=[Depends(require_trusted_origin)])
Admin = Annotated[CurrentUser, Depends(require_admin)]
Database = Annotated[Session, Depends(get_db)]
Identifier = Annotated[int, Path(ge=1, le=9223372036854775807)]
Csrf = Annotated[str | None, Header(alias="X-CSRF-Token")]


async def management_error_handler(request: Request, error: ManagementError):
    return JSONResponse(status_code=error.status, content={"detail": error.detail})


@router.get("/reservations", summary="按有效状态筛选与排序全部申请")
def applications(
    user: Admin, session: Database,
    status: Literal["pending", "approved", "rejected", "cancelled", "expired", "revoked", "finished", "all"] = "pending",
    room_id: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None,
    sort: Literal["starts_at", "submitted_at"] = "starts_at",
    direction: Literal["asc", "desc"] = "asc",
    user_id: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None,
):
    return list_applications(session, status, room_id, sort, direction, user_id)


@router.post("/reservations/{reservation_id}/decision", summary="人工批准、驳回或开始前撤销")
def decision(reservation_id: Identifier, data: DecisionInput, request: Request,
             user: Admin, session: Database, csrf_token: Csrf = None):
    return decide(session, reservation_id, data, actor_id=user.id,
                  raw_token=request.cookies.get(SESSION_COOKIE_NAME), csrf_token=csrf_token)


@router.get("/rooms", response_model=list[RoomInfo], summary="查看已有会议室维护资料")
def rooms(user: Admin, session: Database):
    return list_rooms(session, RoomSearch())


@router.patch("/rooms/{room_id}", response_model=RoomInfo, summary="维护已有会议室，保留历史快照")
def maintain(room_id: Identifier, data: RoomUpdate, request: Request,
             user: Admin, session: Database, csrf_token: Csrf = None):
    return update_room(session, room_id, data, actor_id=user.id,
                       raw_token=request.cookies.get(SESSION_COOKIE_NAME), csrf_token=csrf_token)
