"""短期LangGraph对话。检查点不含密钥/登录凭据，业务工具通过当前请求上下文提供。"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import json,os,secrets,sys,time
from typing import TypedDict
from starlette.concurrency import run_in_threadpool
from app.assistant.knowledge import BACKEND
from app.assistant.model import AIConnectionError
from app.schemas.meeting_assistant import Demand,ExtractedDemand,ConversationView
from app.services.booking_time import get_beijing_now,BookingTimeError
from app.services.login import token_digest


class ConversationError(Exception):
    def __init__(self,code,message):self.code,self.message=code,message;super().__init__(message)


@dataclass
class TurnContext:
    settings: object
    search: object


_context=ContextVar('assistant_turn')
_model_slots=asyncio.Semaphore(2)


async def extract_demand(message,draft,settings):
    if not settings.enabled or not settings.is_configured:raise AIConnectionError('disabled')
    process=None
    if _model_slots.locked():raise AIConnectionError('busy')
    await _model_slots.acquire()
    try:
        async with asyncio.timeout(45):
            environment={k:v for k,v in os.environ.items() if not k.startswith(
                ('SMRBS_','DEEPSEEK_','OPENAI_','LANGCHAIN_','LANGSMITH_'))}
            environment['PYTHONIOENCODING']='utf-8'
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.assistant.demand_worker',cwd=BACKEND,
                env=environment,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,creationflags=0x08000000 if sys.platform=='win32' else 0)
            config=settings.model_dump(mode='json');config['api_key']=settings.api_key.get_secret_value()
            payload=dict(message=message,draft=draft,settings=config,now=get_beijing_now().isoformat())
            output,_=await process.communicate(json.dumps(payload).encode())
            result=json.loads(output)
            if process.returncode!=0 or not result.get('ok'):raise AIConnectionError(result.get('code','invalid_demand'))
            return ExtractedDemand.model_validate(result['result'])
    except TimeoutError:raise AIConnectionError('timeout') from None
    except (OSError,ValueError,KeyError,TypeError):raise AIConnectionError('invalid_demand') from None
    finally:
        if process is not None and process.returncode is None:
            try:process.kill();await asyncio.wait_for(process.wait(),2)
            except (TimeoutError,ProcessLookupError):pass
        _model_slots.release()


class FlowState(TypedDict,total=False):
    draft:dict
    revision:int
    event:dict
    rooms:list
    selected_room:dict|None
    checked_at:str|None
    phase:str
    message:str
    ambiguities:list
    show_all:bool
    restore_only:bool


def build_graph():
    # 基础应用可在未安装LangGraph时启动；只有助手对话使用它。
    from langgraph.graph import StateGraph,START
    from langgraph.types import interrupt
    from langgraph.checkpoint.memory import InMemorySaver
    async def apply_event(state):
        event=state['event'];draft=state['draft'];selected=state.get('selected_room')
        ambiguities=state.get('ambiguities',[])
        if event['kind']=='message':
            parsed=await extract_demand(event['message'],draft,_context.get().settings)
            updates={k:v for k,v in parsed.updates.model_dump(mode='json',exclude_unset=True).items() if v is not None}
            cleared=set(parsed.clear_fields)
            # 歧义不得沿用旧条件成为完整方案。模型未指出字段时保守重新确认查询条件。
            if parsed.ambiguities and not cleared:
                cleared.update(('date','start_time','end_time','attendees'))
            updated=Demand.model_validate({**draft,**updates,**{k:None for k in cleared}}).model_dump()
            if any(updated[k]!=draft[k] for k in ('date','start_time','end_time','attendees','equipment')):selected=None
            draft=updated;ambiguities=parsed.ambiguities
        elif event['kind']=='select':selected=next(r for r in state['rooms'] if r['id']==event['room_id'])
        return dict(draft=draft,selected_room=selected,ambiguities=ambiguities,revision=state['revision']+1,
                    show_all=event['kind']=='all')
    async def resolve(state):
        missing=[label for key,label in (('date','日期'),('start_time','开始时间'),('end_time','结束时间'),('attendees','人数'))
                 if state['draft'][key] is None]
        if state['ambiguities'] or missing:
            return dict(phase='clarify',message='；'.join(state['ambiguities']) if state['ambiguities'] else '请补充'+ '、'.join(missing)+'。',rooms=[])
        try:found=await run_in_threadpool(_context.get().search,state['draft'])
        except (BookingTimeError,ValueError):
            return dict(phase='clarify',message='日期时段或人数不符合预约规则，请核对：未来7天、08:00—22:00、半小时对齐、30分钟至4小时。',rooms=[],selected_room=None)
        rooms=found['rooms'];selected=state.get('selected_room')
        if not rooms:
            message='你在所选时段已有其他预约，这是个人时间冲突，不代表其他会议室已被占用。' if found['reason']=='personal' else '没有同时满足人数、设备、资源状态和时段的会议室。可修改条件后重新查询，系统不会自动放宽条件。'
            return dict(phase='no_match',message=message,rooms=[],selected_room=None,checked_at=found['checked_at'])
        if selected is not None:
            selected=next((r for r in rooms if r['id']==selected['id']),None)
            if selected is None:
                return dict(phase='candidates',message='原选会议室当前不再满足条件，请重新选房；未自动更换。',rooms=rooms,selected_room=None,checked_at=found['checked_at'])
            missing=[label for key,label in (('title','会议主题'),('purpose','用途')) if state['draft'][key] is None]
            return dict(phase='clarify' if missing else 'summary',message='请补充'+'、'.join(missing)+'。' if missing else '方案资料完整。可生成五分钟有效的摘要；本阶段不会提交申请，也不会占位。',
                        rooms=rooms,selected_room=selected,checked_at=found['checked_at'])
        return dict(phase='candidates',message=f'找到{len(rooms)}间匹配会议室，请选择。查询不会占位。',rooms=rooms,checked_at=found['checked_at'])
    def wait_for_user(state):
        return {'event':interrupt({'phase':state['phase'],'message':state['message']})}
    graph=StateGraph(FlowState)
    graph.add_node('apply',apply_event);graph.add_node('resolve',resolve);graph.add_node('wait',wait_for_user)
    graph.add_conditional_edges(START,lambda s:'wait' if s.get('restore_only') else 'apply')
    graph.add_edge('apply','resolve');graph.add_edge('resolve','wait');graph.add_edge('wait','apply')
    saver=InMemorySaver()
    return graph.compile(checkpointer=saver),saver


class ConversationRuntime:
    def __init__(self):
        self.graph,self.saver=build_graph();self.entries={}
    def purge(self):
        for key,entry in list(self.entries.items()):
            if time.monotonic()-entry['touched']>1200 and not entry['lock'].locked():self.delete(key)
    def delete(self,key):
        self.entries.pop(key,None);self.saver.delete_thread(key)
    def entry(self,key,user_id,raw_token):
        self.purge();entry=self.entries.get(key)
        if entry is None:raise ConversationError('expired','对话已过期或服务已重启，请重新开始。')
        if entry['owner']!=(user_id,token_digest(raw_token)):raise ConversationError('owner','未找到本人的当前会话对话。')
        return entry
    async def start(self,user_id,raw_token,message,context):
        self.purge()
        if len(self.entries)>=50:raise ConversationError('busy','助手对话数量已达上限，请稍后重试。')
        key=secrets.token_urlsafe(24)
        entry=dict(owner=(user_id,token_digest(raw_token)),touched=time.monotonic(),lock=asyncio.Lock(),stable=None)
        self.entries[key]=entry
        try:return await self._run(key,entry,dict(draft=Demand().model_dump(),revision=0,event={'kind':'message','message':message}),context)
        except BaseException:self.delete(key);raise
    async def advance(self,key,user_id,raw_token,revision,event,context,*,before_advance=None):
        entry=self.entry(key,user_id,raw_token)
        if entry['lock'].locked():raise ConversationError('busy','对话正在处理，请等待本轮结果。')
        async with entry['lock']:
            stable=entry['stable']
            if stable['revision']!=revision:raise ConversationError('revision','对话版本已变化，请使用最新结果或重新开始。')
            if event['kind']=='select' and not any(r['id']==event['room_id'] for r in stable['rooms']):
                raise ConversationError('selection','请选择当前查询得到的候选会议室。')
            # 和版本校验共用会话锁，过时请求不能撤销新版本的凭证。
            if before_advance is not None:await before_advance()
            from langgraph.types import Command
            try:return await self._run(key,entry,Command(resume=event),context)
            except BaseException:
                # 失败回到上一个等待点，避免下一次重试落入未完成节点。
                self.saver.delete_thread(key)
                from langsmith import tracing_context
                with tracing_context(enabled=False):
                    self.graph.invoke({**stable,'restore_only':True},self.config(key))
                raise
    @staticmethod
    def config(key):return {'configurable':{'thread_id':key},'recursion_limit':20,'callbacks':[]}
    async def _run(self,key,entry,value,context):
        token=_context.set(context)
        try:
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                async with asyncio.timeout(55):await self.graph.ainvoke(value,self.config(key))
            entry['stable']=dict(self.graph.get_state(self.config(key)).values)
            entry['touched']=time.monotonic()
            return self.view(key)
        finally:_context.reset(token)
    def view(self,key):
        state=self.entries[key]['stable'];rooms=state['rooms']
        return ConversationView(conversation_id=key,revision=state['revision'],phase=state['phase'],message=state['message'],
            draft=state['draft'],candidates=rooms if state.get('show_all') else rooms[:3],total=len(rooms),
            checked_at=state.get('checked_at'),selected_room=state.get('selected_room'),confirmation=None)


_runtime=None
def runtime():
    global _runtime
    if _runtime is None:_runtime=ConversationRuntime()
    return _runtime
