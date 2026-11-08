"""消息、催办、账号、统计和只读审计入口。"""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, Request
from app.api.auth_dependencies import get_current_user, require_regular_user, require_trusted_origin
from app.api.management import Admin, Csrf, Database, Identifier
from app.core.auth_config import SESSION_COOKIE_NAME
from app.schemas.administration import EmptyInput, StatisticsQuery, UserStateInput
from app.schemas.auth import CurrentUser
from app.services import administration, messages, reminders

router = APIRouter(prefix="/api", tags=["消息与管理收尾"], dependencies=[Depends(require_trusted_origin)])
Current = Annotated[CurrentUser, Depends(get_current_user)]
Regular = Annotated[CurrentUser, Depends(require_regular_user)]
Page = Annotated[int, Query(ge=1, le=1000000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/messages", summary="分页查询本人消息及未读数量")
def inbox(user: Current, session: Database, page: Page = 1, page_size: PageSize = 20, unread_only: bool = False):
    return messages.list_messages(session, user.id, page, page_size, unread_only)


@router.post("/messages/{identifier}/read", summary="标记本人消息已读")
def read(identifier: Identifier, data: EmptyInput, request: Request, user: Current, session: Database, csrf_token: Csrf = None):
    return messages.mark_read(session, user.id, identifier, request.cookies.get(SESSION_COOKIE_NAME), csrf_token)


@router.post("/reservations/{identifier}/remind", summary="单次催办本人待审批申请")
def remind(identifier: Identifier, data: EmptyInput, request: Request, user: Regular, session: Database, csrf_token: Csrf = None):
    return reminders.remind(session, user.id, identifier, request.cookies.get(SESSION_COOKIE_NAME), csrf_token)


@router.get("/admin/users", summary="搜索全部账号及未结束预约数量")
def users(user: Admin, session: Database, q: Annotated[str, Query(max_length=100)] = "", page: Page = 1, page_size: PageSize = 20):
    return administration.list_users(session, q.strip(), page, page_size)


@router.get("/admin/users/{identifier}/reservations", summary="查看账号关联预约以便逐条处理")
def user_reservations(identifier: Identifier, user: Admin, session: Database):
    return administration.related_reservations(session, identifier)


@router.patch("/admin/users/{identifier}/state", summary="启停普通账号；停用撤销旧会话，不取消预约")
def user_state(identifier: Identifier, data: UserStateInput, request: Request, user: Admin, session: Database, csrf_token: Csrf = None):
    return administration.set_user_state(session, identifier, data.is_active, actor_id=user.id,
                                         raw_token=request.cookies.get(SESSION_COOKIE_NAME), csrf_token=csrf_token)


@router.get("/admin/statistics", summary="按提交日期及当前有效状态统计")
def statistics(criteria: Annotated[StatisticsQuery, Query()], user: Admin, session: Database):
    return administration.statistics(session, criteria)


@router.get("/admin/operations", summary="分页只读操作记录")
def operations(user: Admin, session: Database, page: Page = 1, page_size: PageSize = 20,
               object_type: Literal["user", "meeting_room", "reservation"] | None = None,
               object_id: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None):
    return administration.list_operations(session, page, page_size, object_type, object_id)
