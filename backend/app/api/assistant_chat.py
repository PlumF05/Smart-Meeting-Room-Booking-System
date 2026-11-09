"""会议室查询与显式确认提交；SSE只传实际进度和校验后的完整卡片。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.meeting_assistant import User, Database, Csrf, authorize, recheck
from app.api.auth_dependencies import require_trusted_origin
from fastapi import Depends
from app.assistant import conversation as flow
from app.assistant.model import AIConnectionError
from app.core.ai_config import load_ai_settings
from app.core.errors import DatabaseUnavailableError
from app.schemas.meeting_assistant import ChatInput
from app.services import assistant_confirmations as confirmations
from app.services.assistant_search import find_candidates
from app.services.booking_time import BookingTimeError
from app.services.login import InvalidSessionError, InvalidCsrfError, get_session_info
from app.services.reservations import (
    ReservationPermissionError, ReservationRuleError, RoomConflictError,
    PersonalConflictError, RoomInactiveError,
)
from app.services.rooms import RoomNotFoundError

router = APIRouter(prefix='/api/assistant', tags=['聊天助手'],
                   dependencies=[Depends(require_trusted_origin)])
CONFIRM_WORDS = {'确认提交', '确认预约', '确认提交申请'}
RENEW_WORDS = {'重新生成摘要', '重新确认'}


def public_error(error):
    if isinstance(error, flow.ConversationError):
        return {'expired':410, 'owner':404, 'busy':429}.get(error.code,409), error.message
    if isinstance(error, confirmations.ConfirmationError):
        return 404 if error.code == 'not_found' else 409, error.message
    if isinstance(error, InvalidSessionError): return 401, '登录已失效，请重新登录。'
    if isinstance(error, (InvalidCsrfError, ReservationPermissionError)): return 403, '当前登录信息无效或无办理权限，请重新连接。'
    if isinstance(error, RoomNotFoundError): return 404, '会议室不存在，请重新查询。'
    if isinstance(error, RoomInactiveError): return 409, '会议室已停用，请重新查询和选房。'
    if isinstance(error, PersonalConflictError): return 409, '你在该时段已有其他预约，请核对我的预约。'
    if isinstance(error, RoomConflictError): return 409, '所选会议室已被占用，请重新查询和选房。'
    if isinstance(error, (BookingTimeError, ReservationRuleError)): return 409, str(error)
    if isinstance(error, (TimeoutError, AIConnectionError)):
        code = getattr(error, 'code', 'timeout')
        return (504 if code == 'timeout' else 429 if code == 'busy' else 503), '本轮处理未完成，请稍后重试；普通预约页面仍可使用。'
    if isinstance(error, DatabaseUnavailableError): return 503, '数据库暂不可用。若刚才正在提交，请先核对办理结果。'
    return 503, '助手本轮处理失败，请重试或重新开始；普通预约页面仍可使用。'


def validate_action(data):
    if data.action == 'message' and not data.message:
        raise HTTPException(422, '请输入预约需求。')
    if data.action != 'message' or data.conversation_id is not None:
        if data.conversation_id is None or data.revision is None:
            raise HTTPException(422, '缺少当前对话及方案版本，请重新开始。')
    if data.action == 'select' and data.room_id is None:
        raise HTTPException(422, '请选择会议室。')
    if data.action in ('confirm','check') and data.confirmation_id is None:
        raise HTTPException(422, '请先核对完整摘要。')


@router.post('/chat', summary='流式查询会议室与确认提交申请')
async def chat(data: ChatInput, request: Request, user: User, db: Database, csrf: Csrf = None):
    token = await authorize(db, request, user, csrf)
    validate_action(data)
    engine = db.get_bind()
    queue = asyncio.Queue()
    execution_started = False

    # 每次业务调用独立拥有Session，网络断开也不会关闭另一个线程中的事务。
    def service(function, **kwargs):
        with Session(engine, autoflush=False, expire_on_commit=False) as working:
            return function(working, **kwargs)

    actor = dict(user_id=user.id, raw_token=token, csrf_token=csrf)

    def emit(message): queue.put_nowait(('status', {'message':message}))

    async def invalidate(identifier, before_revision=None):
        await run_in_threadpool(service, confirmations.invalidate_conversation,
                                **actor, conversation_id=identifier,before_revision=before_revision)

    async def current_room(view):
        found = await run_in_threadpool(service, find_candidates, user_id=user.id, draft=view.draft.model_dump())
        chosen = next((room for room in found['rooms'] if room['id'] == view.selected_room.id), None)
        if chosen != view.selected_room.model_dump():
            await invalidate(view.conversation_id)
            raise flow.ConversationError('changed', '会议室资料或占用情况已变化，请重新查询和选房。')

    async def attach_confirmation(rt, view):
        if view.phase != 'summary' or view.selected_room is None: return view
        entry = rt.entry(view.conversation_id, user.id, token)
        async with entry['lock']:
            if rt.view(view.conversation_id).revision != view.revision:
                raise flow.ConversationError('revision', '方案已变化，请使用最新消息。')
            emit('正在核对完整方案…')
            await current_room(view)
            draft = view.draft
            payload = dict(room_id=view.selected_room.id, date=draft.date, start_time=draft.start_time,
                end_time=draft.end_time, attendees=draft.attendees, title=draft.title,
                purpose=draft.purpose, required_equipment=draft.equipment)
            view.confirmation = await run_in_threadpool(service, confirmations.prepare_confirmation,
                **actor, conversation_id=view.conversation_id, revision=view.revision, payload=payload)
        return view

    async def work():
        nonlocal execution_started
        try:
            async with asyncio.timeout(59):
                rt = flow.runtime()
                normalized = data.message.strip().rstrip('。！!') if data.message else ''
                action = 'confirm' if data.action == 'message' and normalized in CONFIRM_WORDS else data.action
                if action in ('confirm','check'):
                    from app.services.assistant_submission import submit_confirmed, inspect_submission
                    if not data.confirmation_id:
                        raise flow.ConversationError('confirmation', '请先选房并核对完整摘要，再明确确认提交。')
                    arguments = dict(**actor, conversation_id=data.conversation_id,
                        confirmation_id=data.confirmation_id, revision=data.revision)
                    if action == 'check':
                        emit('正在核对实际办理结果…')
                        result = await run_in_threadpool(service, inspect_submission, **arguments)
                    else:
                        entry = rt.entry(data.conversation_id, user.id, token)
                        if entry['lock'].locked(): raise flow.ConversationError('busy','当前对话正在处理，请稍后核对结果。')
                        async with entry['lock']:
                            view = rt.view(data.conversation_id)
                            if view.revision != data.revision or view.phase != 'summary' or view.selected_room is None:
                                raise flow.ConversationError('revision','方案已变化，请先核对最新摘要。')
                            existing = await run_in_threadpool(service, inspect_submission, **arguments)
                            if existing['confirmation']['state'] != 'consumed': await current_room(view)
                            emit('正在提交预约申请…')
                            execution_started = True
                            result = await run_in_threadpool(service, submit_confirmed, **arguments)
                    await run_in_threadpool(service,get_session_info,raw_token=token)
                    result['message'] = '申请已提交，待管理员审批。' if result['reservation'] and result['reservation']['status'] == 'pending' else '已核对实际预约状态。' if result['reservation'] else '该方案尚未提交；请核对有效期后明确确认。'
                    queue.put_nowait(('result',result))
                else:
                    context = flow.TurnContext(load_ai_settings(),
                        lambda draft: service(find_candidates,user_id=user.id,draft=draft), emit)
                    if data.conversation_id is None:
                        if normalized in CONFIRM_WORDS: raise flow.ConversationError('confirmation','请先描述需求、选房并核对完整摘要。')
                        view = await rt.start(user.id,token,data.message,context)
                    else:
                        event = {'kind':'select','room_id':data.room_id} if action == 'select' else {'kind':'all'} if action == 'all' else {'kind':'renew'} if normalized in RENEW_WORDS else {'kind':'message','message':data.message}
                        view = await rt.advance(data.conversation_id,user.id,token,data.revision,event,context)
                        if view.revision != data.revision: await invalidate(data.conversation_id,view.revision)
                    view = await attach_confirmation(rt,view)
                    await recheck(db,token)
                    queue.put_nowait(('view',view.model_dump(mode='json')))
        except Exception as error:
            status,message = public_error(error)
            queue.put_nowait(('error',dict(status=status,message=message,outcome_unknown=execution_started)))
        finally:
            queue.put_nowait(('done',{}))

    async def events():
        task = asyncio.create_task(work())
        try:
            while True:
                kind,payload = await queue.get()
                yield f'event: {kind}\ndata: {json.dumps(jsonable_encoder(payload),ensure_ascii=False)}\n\n'
                if kind == 'done': break
        finally:
            if not task.done():
                if execution_started:
                    # 写事务自行收尾并保存凭证结果；断开不等于取消业务。
                    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
                else:
                    task.cancel()
                    try: await task
                    except asyncio.CancelledError: pass

    return StreamingResponse(events(),media_type='text/event-stream',
        headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
