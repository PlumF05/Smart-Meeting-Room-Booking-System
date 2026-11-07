"""普通用户提交预约；最终权限、占位和资源规则在同一业务事务中重验。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Path, status
from sqlalchemy.orm import Session

from app.api.auth_dependencies import require_regular_user, require_trusted_origin
from app.core.auth_config import SESSION_COOKIE_NAME
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.reservations import ReservationCreate, ReservationCreated
from app.services.booking_time import BookingTimeError
from app.services.reservations import (
    PersonalConflictError,
    ReservationPermissionError,
    ReservationRuleError,
    RoomConflictError,
    RoomInactiveError,
    create_reservation,
)
from app.services.rooms import RoomNotFoundError


router = APIRouter(
    prefix="/api/reservations", tags=["预约"],
    dependencies=[Depends(require_trusted_origin)],
)


@router.post(
    "", response_model=ReservationCreated, status_code=status.HTTP_201_CREATED,
    summary="提交会议室预约申请",
    description=(
        "仅普通用户可提交。校验CSRF令牌及最新时间、资源和占位规则后，"
        "将未审批预约、会议室快照、操作记录及管理员消息一起保存；提交成功不代表审批通过。"
    ),
    responses={
        401: {"description": "未登录或登录已失效"},
        403: {"description": "无预约权限、CSRF校验失败或来源不被允许"},
        404: {"description": "会议室不存在"},
        409: {"description": "会议室已停用，或会议室/本人已有重叠预约"},
        422: {"description": "输入、时间、人数或设备不符合要求"},
        503: {"description": "数据库暂不可用"},
    },
)
def submit_reservation(
    data: ReservationCreate, request: Request,
    user: Annotated[CurrentUser, Depends(require_regular_user)],
    session: Annotated[Session, Depends(get_db)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> ReservationCreated:
    try:
        return create_reservation(
            session, data, user_id=user.id,
            raw_token=request.cookies.get(SESSION_COOKIE_NAME), csrf_token=csrf_token,
        )
    except ReservationPermissionError:
        raise HTTPException(status_code=403, detail="当前账号无权提交预约。") from None
    except RoomNotFoundError:
        raise HTTPException(status_code=404, detail="会议室不存在。") from None
    except RoomInactiveError:
        raise HTTPException(status_code=409, detail="会议室已停用，无法提交预约。") from None
    except PersonalConflictError:
        raise HTTPException(status_code=409, detail="您在所选时段已有其他预约。") from None
    except RoomConflictError:
        raise HTTPException(status_code=409, detail="当前会议室在所选时段已有预约。") from None
    except (BookingTimeError, ReservationRuleError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


from pydantic import BaseModel, ConfigDict, Field
from app.services.my_reservations import read_mine, cancel_mine, ReservationNotFoundError, CancellationConflictError


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str | None = Field(default=None, max_length=1000)


@router.get("", summary="查询本人预约")
def my_list(user: Annotated[CurrentUser, Depends(require_regular_user)], session: Annotated[Session, Depends(get_db)]):
    return read_mine(session, user.id)


@router.get("/{reservation_id}", summary="查询本人预约详情")
def my_detail(reservation_id: Annotated[int, Path(ge=1, le=9223372036854775807)], user: Annotated[CurrentUser, Depends(require_regular_user)], session: Annotated[Session, Depends(get_db)]):
    try:
        return read_mine(session, user.id, reservation_id)
    except ReservationNotFoundError:
        raise HTTPException(404, "预约不存在。") from None


@router.post("/{reservation_id}/cancel", summary="开始前取消本人预约")
def cancel(reservation_id: Annotated[int, Path(ge=1, le=9223372036854775807)], data: CancelRequest, request: Request,
           user: Annotated[CurrentUser, Depends(require_regular_user)], session: Annotated[Session, Depends(get_db)],
           csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    try:
        return cancel_mine(session, user.id, reservation_id, request.cookies.get(SESSION_COOKIE_NAME), csrf_token, data.reason.strip() or None if data.reason else None)
    except ReservationNotFoundError:
        raise HTTPException(404, "预约不存在。") from None
    except CancellationConflictError:
        raise HTTPException(409, "仅可在开始前取消未审批或已批准的本人预约。") from None
    except ReservationPermissionError:
        raise HTTPException(403, "当前账号无权取消预约。") from None
