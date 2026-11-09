"""查房与摘要预览接口；本阶段没有执行确认/提交/取消接口。"""
from contextlib import asynccontextmanager
from typing import Annotated
import secrets
from fastapi import APIRouter,Depends,Header,HTTPException,Request,Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app.api.auth_dependencies import require_regular_user,require_trusted_origin
from app.assistant import conversation as flow
from app.assistant.model import AIConnectionError
from app.core.ai_config import load_ai_settings
from app.core.auth_config import SESSION_COOKIE_NAME
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.meeting_assistant import (StartConversation,ContinueConversation,SelectRoom,ShowCandidates,
                                          RevisionInput,ConversationView)
from app.services import assistant_confirmations as confirmations
from app.services.assistant_search import find_candidates
from app.services.booking_time import BookingTimeError
from app.services.login import get_session_info,InvalidCsrfError,valid_token_format

router=APIRouter(prefix='/api/assistant/conversations',tags=['会议室助手'],dependencies=[Depends(require_trusted_origin)])
User=Annotated[CurrentUser,Depends(require_regular_user)]
Database=Annotated[Session,Depends(get_db)]
Csrf=Annotated[str|None,Header(alias='X-CSRF-Token')]


async def authorize(db,request,user,csrf):
    token=request.cookies.get(SESSION_COOKIE_NAME)
    info=await run_in_threadpool(get_session_info,db,token)
    if not valid_token_format(csrf) or not secrets.compare_digest(info.csrf_token,csrf):raise InvalidCsrfError()
    return token


@asynccontextmanager
async def safe_errors():
    try:yield
    except flow.ConversationError as error:
        raise HTTPException({'expired':410,'owner':404,'busy':429}.get(error.code,409),error.message) from None
    except confirmations.ConfirmationError as error:
        raise HTTPException(404 if error.code=='not_found' else 409,error.message) from None
    except BookingTimeError as error:
        raise HTTPException(409,str(error)) from None
    except (AIConnectionError,ImportError) as error:
        status=504 if getattr(error,'code',None)=='timeout' else 429 if getattr(error,'code',None)=='busy' else 503
        raise HTTPException(status,'助手本轮未完成，请稍后重试；已填条件不会自动放宽，普通页面仍可使用。') from None
    except TimeoutError:
        raise HTTPException(504,'助手本轮超时，请重试或重新开始。') from None
    except (ValueError,OSError):
        raise HTTPException(503,'助手配置或输入不可用，请核对配置及条件。') from None


def context(db,user):
    return flow.TurnContext(settings=load_ai_settings(),search=lambda draft:find_candidates(db,user.id,draft))


async def recheck(db,token):
    await run_in_threadpool(get_session_info,db,token)


@router.post('/start',response_model=ConversationView)
async def start(data:StartConversation,request:Request,user:User,db:Database,csrf:Csrf=None):
    token=await authorize(db,request,user,csrf)
    async with safe_errors():
        result=await flow.runtime().start(user.id,token,data.message,context(db,user))
        await recheck(db,token)
        return result


async def advance(identifier,revision,event,request,user,db,csrf):
    token=await authorize(db,request,user,csrf)
    async with safe_errors():
        async def invalidate():
            await run_in_threadpool(confirmations.invalidate_conversation,db,user_id=user.id,raw_token=token,
                                    csrf_token=csrf,conversation_id=identifier)
        result=await flow.runtime().advance(identifier,user.id,token,revision,event,context(db,user),
                                           before_advance=invalidate)
        await recheck(db,token)
        return result


@router.post('/{identifier}/message',response_model=ConversationView)
async def message(identifier:str,data:ContinueConversation,request:Request,user:User,db:Database,csrf:Csrf=None):
    return await advance(identifier,data.revision,{'kind':'message','message':data.message},request,user,db,csrf)


@router.post('/{identifier}/select',response_model=ConversationView)
async def select_room(identifier:str,data:SelectRoom,request:Request,user:User,db:Database,csrf:Csrf=None):
    return await advance(identifier,data.revision,{'kind':'select','room_id':data.room_id},request,user,db,csrf)


@router.post('/{identifier}/candidates',response_model=ConversationView)
async def all_candidates(identifier:str,data:ShowCandidates,request:Request,user:User,db:Database,csrf:Csrf=None):
    return await advance(identifier,data.revision,{'kind':'all'},request,user,db,csrf)


@router.post('/{identifier}/prepare',response_model=ConversationView)
async def prepare(identifier:str,data:RevisionInput,request:Request,user:User,db:Database,csrf:Csrf=None):
    token=await authorize(db,request,user,csrf)
    async with safe_errors():
        rt=flow.runtime();entry=rt.entry(identifier,user.id,token)
        if entry['lock'].locked():raise flow.ConversationError('busy','对话正在处理，请稍后再生成摘要。')
        async with entry['lock']:
            view=rt.view(identifier)
            if view.revision!=data.revision or view.phase!='summary' or view.selected_room is None:
                raise flow.ConversationError('revision','请先补齐资料、选择会议室，并使用最新方案。')
            current=await run_in_threadpool(find_candidates,db,user.id,view.draft.model_dump())
            chosen=next((r for r in current['rooms'] if r['id']==view.selected_room.id),None)
            if chosen!=view.selected_room.model_dump():
                await run_in_threadpool(confirmations.invalidate_conversation,db,user_id=user.id,raw_token=token,
                                        csrf_token=csrf,conversation_id=identifier)
                raise flow.ConversationError('changed','所选会议室或时段情况已变化，请重新查询和选房。')
            draft=view.draft
            payload=dict(room_id=chosen['id'],date=draft.date,start_time=draft.start_time,end_time=draft.end_time,
                         attendees=draft.attendees,title=draft.title or "",purpose=draft.purpose or "",required_equipment=draft.equipment)
            view.confirmation=await run_in_threadpool(confirmations.prepare_confirmation,db,user_id=user.id,
                raw_token=token,csrf_token=csrf,conversation_id=identifier,revision=view.revision,payload=payload)
            await recheck(db,token)
            return view


@router.delete('/{identifier}',status_code=204)
async def close(identifier:str,request:Request,user:User,db:Database,csrf:Csrf=None):
    token=await authorize(db,request,user,csrf)
    async with safe_errors():
        rt=flow.runtime();entry=rt.entry(identifier,user.id,token)
        async with entry['lock']:
            await run_in_threadpool(confirmations.invalidate_conversation,db,user_id=user.id,raw_token=token,
                                    csrf_token=csrf,conversation_id=identifier)
            rt.delete(identifier)
    return Response(status_code=204)
