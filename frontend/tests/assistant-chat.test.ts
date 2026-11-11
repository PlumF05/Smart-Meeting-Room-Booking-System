import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { assistantChatApi, ChatStreamError, type ChatEvent, type ChatRequest, type SubmissionResult } from '../src/api/assistantChat.ts'
import { http } from '../src/api/http.ts'
import { meetingAssistantApi, type AssistantConfirmation, type ConversationView } from '../src/api/meetingAssistant.ts'
import { useAssistantChatStore } from '../src/stores/assistantChat.ts'
import { useAuthStore } from '../src/stores/auth.ts'

const originalStream = assistantChatApi.stream
const originalFetch = globalThis.fetch
const originalClose = meetingAssistantApi.close
const originalNow = Date.now
let pinia: ReturnType<typeof createPinia>
let auth: ReturnType<typeof useAuthStore>
let chat: ReturnType<typeof useAssistantChatStore>

const room = { id: 1, name: '研讨室101', location: '教学楼A座', capacity: 8, equipment: ['白板'], is_active: true }
const draft = { date: '2099-11-09', start_time: '14:00', end_time: '15:00', attendees: 6,
  title: '课程讨论', purpose: '讨论设计任务', equipment: ['白板'] }
function confirmation(state: AssistantConfirmation['state'] = 'pending'): AssistantConfirmation {
  return { confirmation_id: 'credential', conversation_id: 'conversation', revision: 2, action: 'submit',
    summary: { ...draft, required_equipment: draft.equipment, room_id: 1 },
    created_at: '2099-11-08T10:00:00+08:00', expires_at: '2099-11-08T10:05:00+08:00', state, result: null }
}
function view(summary = false): ConversationView {
  return { conversation_id: 'conversation', revision: summary ? 2 : 1, phase: summary ? 'summary' : 'candidates',
    message: summary ? '请核对后确认。' : '找到匹配会议室，请选择。', draft: { ...draft },
    candidates: [room], total: 4, checked_at: '2099-11-08T10:00:00+08:00', selected_room: summary ? room : null,
    confirmation: summary ? confirmation() : null }
}
function result(reservation: Record<string, unknown> | null = { id: 12, status: 'pending' }): SubmissionResult {
  return { confirmation: confirmation(reservation ? 'consumed' : 'pending'), reservation, message: '申请已提交，待管理员审批。' }
}
function emitView(onEvent: (e: ChatEvent) => void, summary = false) { onEvent({ type: 'view', data: view(summary) }) }
function sse(events: Array<[string, unknown]>, eol = '\n') {
  return events.map(([event, data]) => `event: ${event}${eol}data: ${JSON.stringify(data)}${eol}${eol}`).join('')
}
function response(text: string, chunkSizes = [7, 1, 2, 31]) {
  const bytes = new TextEncoder().encode(text)
  let offset = 0, index = 0
  return new Response(new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= bytes.length) { controller.close(); return }
      const size = chunkSizes[index++ % chunkSizes.length]!
      controller.enqueue(bytes.slice(offset, offset + size)); offset += size
    },
  }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}
beforeEach(() => {
  pinia = createPinia(); setActivePinia(pinia)
  auth = useAuthStore()
  auth.user = { id: 1, username: 'synthetic', full_name: '测试学生', role: 'user', identity_type: 'student', is_active: true }
  auth.status = 'authenticated'; auth.csrfToken = 'S'.repeat(43)
  chat = useAssistantChatStore()
  globalThis.fetch = async () => { throw new Error('测试不得连接真实网络') }
  assistantChatApi.stream = async () => { throw new Error('测试未声明接口替身') }
  meetingAssistantApi.close = async () => {}
})
afterEach(() => {
  disposePinia(pinia)
  assistantChatApi.stream = originalStream; globalThis.fetch = originalFetch
  meetingAssistantApi.close = originalClose; Date.now = originalNow
})

test('关闭当前对话使用编码后的 DELETE 路径并携带 CSRF', async () => {
  const adapter = http.defaults.adapter
  try {
    http.defaults.adapter = async config => {
      assert.equal(config.method, 'delete')
      assert.equal(config.url, '/assistant/conversations/synthetic%2Fconversation')
      assert.equal(config.headers.get('X-CSRF-Token'), auth.csrfToken)
      assert.equal(config.timeout, 65_000)
      return { config, data: null, status: 204, statusText: 'No Content', headers: {} }
    }
    await originalClose('synthetic/conversation', auth.csrfToken)
  } finally {
    http.defaults.adapter = adapter
  }
})

test('SSE按分片及CRLF解析中文，POST携带Cookie/CSRF且收到done后才完成', async () => {
  assistantChatApi.stream = originalStream
  const received: ChatEvent[] = []
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/assistant/chat'); assert.equal(options?.method, 'POST')
    assert.equal(options?.credentials, 'same-origin')
    assert.equal((options?.headers as Record<string, string>)['X-CSRF-Token'], auth.csrfToken)
    assert.deepEqual(JSON.parse(String(options?.body)), { action: 'message', message: '明天下午6个人' })
    return response(sse([['status', { message: '正在查询…' }], ['view', view()], ['done', {}]], '\r\n'))
  }
  await assistantChatApi.stream({ action: 'message', message: '明天下午6个人' }, auth.csrfToken, e => received.push(e))
  assert.deepEqual(received.map(e => e.type), ['status', 'view'])
  assert.equal(received[0]!.data.message, '正在查询…')
  assert.equal((received[1]!.data as ConversationView).candidates[0]!.name, '研讨室101')
})

test('HTTP 200中的error仍视为失败；无done、无业务结果及非法JSON不能当成功', async () => {
  assistantChatApi.stream = originalStream
  for (const text of [sse([['view', view()]]), sse([['status', { message: '处理中' }], ['done', {}]]), 'event: view\ndata: {bad}\n\n']) {
    globalThis.fetch = async () => response(text)
    await assert.rejects(assistantChatApi.stream({ action: 'message', message: '需求' }, auth.csrfToken, () => {}), ChatStreamError)
  }
  globalThis.fetch = async () => response(sse([['error', { status: 409, message: '房间已被占用', outcome_unknown: false }], ['done', {}]]))
  await assert.rejects(assistantChatApi.stream({ action: 'message', message: '需求' }, auth.csrfToken, () => {}),
    (error: ChatStreamError) => error.status === 409 && error.message === '房间已被占用')
})

test('常规HTTP错误使用安全detail，非流式正文不展示到界面', async () => {
  assistantChatApi.stream = originalStream
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: '登录已失效' }), { status: 401 })
  await assert.rejects(assistantChatApi.stream({ action: 'message' }, auth.csrfToken, () => {}),
    (error: ChatStreamError) => error.status === 401 && error.message === '登录已失效')
  globalThis.fetch = async () => new Response('private upstream body', { status: 503 })
  await assert.rejects(assistantChatApi.stream({ action: 'message' }, auth.csrfToken, () => {}),
    (error: Error) => !error.message.includes('private'))
})

test('挂载store不发请求；匿名、管理员、空白与超长输入不请求', async () => {
  let calls = 0
  assistantChatApi.stream = async () => { calls++ }
  await nextTick(); assert.equal(calls, 0)
  for (const value of ['', ' '.repeat(2), 'x'.repeat(501)]) { chat.input = value; await chat.send() }
  chat.input = '明天8人'; auth.status = 'anonymous'; await chat.send()
  auth.status = 'authenticated'; auth.user!.role = 'admin'; chat.input = '明天8人'; await chat.send()
  assert.equal(calls, 0)
})

test('聊天保留往返记录，后续条件携带同一对话与版本，Pinia实例重取仍保留内容', async () => {
  const calls: ChatRequest[] = []
  assistantChatApi.stream = async (data, token, emit) => { calls.push(data); assert.equal(token, auth.csrfToken); emitView(emit) }
  chat.input = '明天下午6个人'; await chat.send()
  assert.equal(chat.input, '')
  chat.input = '两点到三点'; await chat.send()
  assert.deepEqual(calls, [{ action: 'message', message: '明天下午6个人' },
    { action: 'message', message: '两点到三点', conversation_id: 'conversation', revision: 1 }])
  assert.equal(chat.messages.length, 4)
  assert.equal(useAssistantChatStore().messages.length, 4)
})

test('历史候选不可操作；选房和全部候选只请求对应动作，不请求确认', async () => {
  assistantChatApi.stream = async (_, __, emit) => emitView(emit)
  chat.input = '查询'; await chat.send()
  const old = chat.messages.at(-1)!
  assert.equal(chat.isCurrentMessage(old), true)
  const calls: string[] = []
  assistantChatApi.stream = async (data, _, emit) => { calls.push(data.action); emitView(emit, data.action === 'select') }
  await chat.selectRoom(999)
  await chat.showAll(); assert.equal(chat.isCurrentMessage(old), false)
  await chat.selectRoom(1)
  assert.deepEqual(calls, ['all', 'select'])
  assert.ok(chat.view?.confirmation)
})

test('完整摘要自动展示，按钮确认带当前凭证；双击处理中不会重复请求', async () => {
  assistantChatApi.stream = async (_, __, emit) => emitView(emit, true)
  chat.input = '完整资料'; await chat.send()
  assert.equal(chat.canConfirm(), true)
  let finish!: () => void
  let calls = 0
  assistantChatApi.stream = async (data, _, emit) => {
    calls++; assert.equal(data.action, 'confirm'); assert.equal(data.confirmation_id, 'credential')
    await new Promise<void>(resolve => { finish = resolve })
    emit({ type: 'result', data: result() })
  }
  const waiting = chat.confirm(); await chat.confirm()
  assert.equal(calls, 1); assert.equal(chat.pending, true)
  chat.newConversation(); assert.ok(chat.view)
  finish(); await waiting
  assert.equal(chat.messages.at(-1)!.result!.reservation!.id, 12)
  assert.equal(chat.canConfirm(), false)
})

test('明确确认文字携带凭证；没有完整有效摘要时不发送确认', async () => {
  let calls: ChatRequest[] = []
  assistantChatApi.stream = async (data, _, emit) => { calls.push(data); emitView(emit, true) }
  chat.input = '确认提交'; await chat.send(); assert.equal(calls.length, 0)
  chat.input = '完整资料'; await chat.send()
  chat.input = '确认预约'; await chat.send()
  assert.equal(calls.at(-1)!.confirmation_id, 'credential')
  assert.equal(calls.at(-1)!.action, 'message')
  Date.now = () => new Date('2099-11-08T10:06:00+08:00').getTime()
  calls = []; await chat.confirm(); chat.input = '确认提交'; await chat.send()
  assert.equal(calls.length, 0)
})

test('确认连接中断不自动重试，先check核对实际结果；未核对时阻止继续提交与新对话', async () => {
  assistantChatApi.stream = async (_, __, emit) => emitView(emit, true)
  chat.input = '完整资料'; await chat.send()
  const calls: string[] = []
  assistantChatApi.stream = async data => { calls.push(data.action); throw new ChatStreamError(null, '断线') }
  await chat.confirm(); await chat.confirm(); chat.input = '继续'; await chat.send(); chat.newConversation()
  assert.deepEqual(calls, ['confirm']); assert.equal(chat.outcomeUnknown, true)
  assistantChatApi.stream = async (data, _, emit) => { calls.push(data.action); emit({ type: 'result', data: result() }) }
  await chat.check()
  assert.deepEqual(calls, ['confirm', 'check']); assert.equal(chat.outcomeUnknown, false)
  assert.equal(chat.messages.at(-1)!.result!.reservation!.id, 12)
})

test('明确的提交冲突显示实际原因，不误报成功或自动再次提交', async () => {
  assistantChatApi.stream = async (_, __, emit) => emitView(emit, true)
  chat.input = '完整资料'; await chat.send()
  let calls = 0
  assistantChatApi.stream = async () => { calls++; throw new ChatStreamError(409, '当前会议室已被占用，请修改条件。') }
  await chat.confirm(); await chat.confirm()
  assert.equal(calls, 1); assert.equal(chat.outcomeUnknown, false)
  assert.equal(chat.canConfirm(), false); assert.match(chat.error, /已被占用/)
  assert.equal(chat.messages.filter(m => m.result?.reservation).length, 0)
})

test('过期后明确重新生成摘要，取得新版本和凭证，不重复使用旧摘要', async () => {
  assistantChatApi.stream = async (_, __, emit) => emitView(emit, true)
  chat.input = '完整资料'; await chat.send()
  const old = chat.messages.at(-1)!
  Date.now = () => new Date('2099-11-08T10:06:00+08:00').getTime()
  assert.equal(chat.canConfirm(), false)
  assistantChatApi.stream = async (data, _, emit) => {
    assert.deepEqual(data, { action: 'message', message: '重新生成摘要', conversation_id: 'conversation', revision: 2 })
    const next = view(true)
    next.revision = 3
    next.confirmation = { ...confirmation(), confirmation_id: 'renewed', revision: 3, expires_at: '2099-11-08T10:11:00+08:00' }
    emit({ type: 'view', data: next })
  }
  await chat.renewSummary()
  assert.equal(chat.view!.confirmation!.confirmation_id, 'renewed')
  assert.equal(chat.canConfirm(), true); assert.equal(chat.isCurrentMessage(old), false)
  assert.equal(old.view!.confirmation!.confirmation_id, 'credential')
})

test('查询失败保留原输入与条件，旧凭证不再可确认', async () => {
  assistantChatApi.stream = async (_, __, emit) => emitView(emit, true)
  chat.input = '完整资料'; await chat.send()
  assistantChatApi.stream = async () => { throw new ChatStreamError(503, '模型不可用') }
  chat.input = '改为三点'; await chat.send()
  assert.equal(chat.input, '改为三点'); assert.equal(chat.view!.draft.attendees, 6)
  assert.equal(chat.canConfirm(), false); assert.match(chat.error, /模型不可用/)
})

test('新对话关闭旧后端会话并清空内容；普通路由复用store不触发关闭', async () => {
  const closed: string[] = []
  meetingAssistantApi.close = async id => { closed.push(id) }
  assistantChatApi.stream = async (_, __, emit) => emitView(emit)
  chat.input = '查询'; await chat.send()
  assert.equal(useAssistantChatStore(), chat); assert.deepEqual(closed, [])
  chat.newConversation()
  assert.deepEqual(closed, ['conversation']); assert.equal(chat.messages.length, 0); assert.equal(chat.view, null)
})

test('换号/同账号重新登录会abort并清空，迟到响应不能恢复旧内容', async () => {
  let emitLate!: (e: ChatEvent) => void, finish!: () => void
  let signal: AbortSignal | undefined
  assistantChatApi.stream = async (_, __, emit, requestSignal) => {
    emitLate = emit; signal = requestSignal
    await new Promise<void>(resolve => { finish = resolve })
  }
  chat.input = '查询'; const request = chat.send()
  auth.sessionChangeCount++
  assert.equal(signal?.aborted, true); assert.equal(chat.messages.length, 0)
  emitLate({ type: 'view', data: view() }); finish(); await request
  assert.equal(chat.messages.length, 0); assert.equal(chat.view, null)
})

test('退出开始立即清空；旧401响应不能复核新登录', async () => {
  let reject!: (e: Error) => void
  let restored = 0
  auth.restoreSession = async () => { restored++ }
  assistantChatApi.stream = async () => new Promise<void>((_, fail) => { reject = fail })
  chat.input = '查询'; const request = chat.send()
  auth.pending = true
  assert.equal(chat.messages.length, 0)
  auth.pending = false; auth.sessionChangeCount++
  reject(new ChatStreamError(401, '登录已失效')); await request
  assert.equal(restored, 0)
})

test('闲置20分钟清理普通草稿，结果待核对时保留执行凭证', async () => {
  const start = originalNow()
  Date.now = () => start
  assistantChatApi.stream = async (_, __, emit) => emitView(emit)
  chat.input = '查询'; await chat.send()
  Date.now = () => start + 20 * 60 * 1000
  chat.expireIdle(); assert.equal(chat.messages.length, 0)
  assert.match(chat.error, /闲置20分钟/)
  assistantChatApi.stream = async (_, __, emit) => emitView(emit, true)
  chat.input = '完整'; await chat.send()
  chat.outcomeUnknown = true
  Date.now = () => start + 40 * 60 * 1000
  chat.expireIdle(); assert.ok(chat.view?.confirmation)
})
