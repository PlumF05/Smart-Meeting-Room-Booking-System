import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { effectScope, type EffectScope } from 'vue'
import { assistantChatApi, ChatStreamError, type ChatRequest, type SubmissionResult } from '../src/api/assistantChat.ts'
import { assistantApi } from '../src/api/assistant.ts'
import { meetingAssistantApi, type AssistantConfirmation, type ConversationView } from '../src/api/meetingAssistant.ts'
import { useAssistantChatStore } from '../src/stores/assistantChat.ts'
import { useAuthStore } from '../src/stores/auth.ts'
import { useAssistantVoice } from '../src/voice/useAssistantVoice.ts'
import type { SpeechInput } from '../src/voice/speechRecognition.ts'
import type { SpeechOutput } from '../src/voice/speechSynthesis.ts'

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function until(check: () => boolean, message = '等待语音状态更新') {
  for (let count = 0; count < 100 && !check(); count++) await new Promise<void>(resolve => setImmediate(resolve))
  assert.ok(check(), message)
}
async function flush() { for (let count = 0; count < 4; count++) await new Promise<void>(resolve => setImmediate(resolve)) }

class FakeInput implements SpeechInput {
  supported = true
  automaticReady = true
  ignoreAbort = false
  live = false
  calls: Array<{ options: Parameters<SpeechInput['listen']>[0]; done: ReturnType<typeof deferred<string>> }> = []
  listen(options: Parameters<SpeechInput['listen']>[0]) {
    assert.equal(this.live, false, '不能同时打开两个识别轮次')
    this.live = true
    const done = deferred<string>()
    const abort = () => {
      this.live = false
      if (!this.ignoreAbort) done.reject(new DOMException('已停止', 'AbortError'))
    }
    options.signal.addEventListener('abort', abort, { once: true })
    this.calls.push({ options, done })
    if (this.automaticReady) options.onReady?.()
    return done.promise.finally(() => { this.live = false; options.signal.removeEventListener('abort', abort) })
  }
  final(text: string) { assert.ok(this.live, '测试应在麦克风轮次开启后输入'); this.calls.at(-1)!.done.resolve(text) }
}
class FakeOutput implements SpeechOutput {
  supported = true
  texts: string[] = []
  hold: ((text: string) => boolean) | null = null
  held: Array<{ signal: AbortSignal; done: ReturnType<typeof deferred<void>> }> = []
  readonly input: FakeInput
  constructor(input: FakeInput) { this.input = input }
  speak(text: string, signal: AbortSignal) {
    assert.equal(this.input.live, false, '朗读时麦克风必须关闭')
    this.texts.push(text)
    if (!this.hold?.(text)) return Promise.resolve()
    const done = deferred<void>()
    // 故意容许取消后迟到的 onend，以测试控制器的轮次隔离。
    this.held.push({ signal, done })
    return done.promise
  }
}

const originalStream = assistantChatApi.stream
const originalAskRules = assistantApi.askRules
const originalClose = meetingAssistantApi.close
const originalFetch = globalThis.fetch
let pinia: ReturnType<typeof createPinia>
let auth: ReturnType<typeof useAuthStore>
let chat: ReturnType<typeof useAssistantChatStore>
let scopes: EffectScope[]
const room = { id: 1, name: '研讨室101', location: '教学楼A座', capacity: 8, equipment: ['白板'], is_active: true }
const draft = { date: '2099-11-09', start_time: '14:00', end_time: '15:00', attendees: 6,
  title: '', purpose: '', equipment: [] }
function credential(state: AssistantConfirmation['state'] = 'pending'): AssistantConfirmation {
  return { confirmation_id: 'credential', conversation_id: 'conversation', revision: 2, action: 'submit',
    summary: { ...draft, room_id: room.id, required_equipment: [] },
    created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 300_000).toISOString(), state, result: null }
}
function view(summary = false): ConversationView {
  return { conversation_id: 'conversation', revision: summary ? 2 : 1, phase: summary ? 'summary' : 'candidates',
    message: summary ? '请核对后确认。' : '找到匹配会议室，请选择。', draft: { ...draft }, candidates: [room],
    total: 1, checked_at: new Date().toISOString(), selected_room: summary ? room : null,
    confirmation: summary ? credential() : null }
}
function result(): SubmissionResult {
  return { confirmation: credential('consumed'), reservation: { id: 12, status: 'pending' }, message: '申请已提交，待审批。' }
}
function setupVoice(page: Document | null = null) {
  const input = new FakeInput(), output = new FakeOutput(input), scope = effectScope()
  scopes.push(scope)
  const voice = scope.run(() => useAssistantVoice(chat, { input, output, page }))!
  return { voice, input, output, scope }
}
async function seedSummary() {
  assistantChatApi.stream = async (_, __, emit) => emit({ type: 'view', data: view(true) })
  await chat.sendText('查询并选房')
}
beforeEach(() => {
  pinia = createPinia(); setActivePinia(pinia); scopes = []
  auth = useAuthStore()
  auth.user = { id: 1, username: 'synthetic', full_name: '测试学生', role: 'user', identity_type: 'student', is_active: true }
  auth.status = 'authenticated'; auth.csrfToken = 'S'.repeat(43)
  chat = useAssistantChatStore()
  globalThis.fetch = async () => { throw new Error('语音轮次测试不得连接真实网络') }
  assistantChatApi.stream = async () => { throw new Error('未配置接口替身') }
  assistantApi.askRules = async () => { throw new Error('未配置规则接口替身') }
  meetingAssistantApi.close = async () => {}
})
afterEach(() => {
  scopes.forEach(scope => scope.stop()); disposePinia(pinia)
  assistantChatApi.stream = originalStream; meetingAssistantApi.close = originalClose; globalThis.fetch = originalFetch
  assistantApi.askRules = originalAskRules
})

test('连续语音自动回答规则，朗读后可直接继续预约', async () => {
  const calls: string[] = []
  assistantApi.askRules = async question => {
    calls.push(question)
    return { answer: '预约需要管理员审批。', grounded: true, sources: [], knowledge_version: 'test' }
  }
  assistantChatApi.stream = async (data, _, emit) => {
    calls.push(data.message!)
    emit({ type: 'view', data: view() })
  }
  const { voice, input, output } = setupVoice()
  voice.start()
  await until(() => input.calls.length === 1)
  input.final('预约需要审批吗？')
  await until(() => input.calls.length === 2)
  assert.equal(output.texts.at(-1), '预约需要管理员审批。')
  input.final('明天十四点到十五点，六个人')
  await until(() => input.calls.length === 3)
  assert.match(output.texts.at(-1)!, /研讨室101/u)
  assert.deepEqual(calls, ['预约需要审批吗？', '明天十四点到十五点，六个人'])
})

test('模拟语音闭环：查询、选房、完整摘要、明确确认、实际待审批记录，完成后不再开启麦克风', async () => {
  const calls: ChatRequest[] = []
  assistantChatApi.stream = async (data, _, emit) => {
    calls.push(data)
    if (data.action === 'confirm') emit({ type: 'result', data: result() })
    else emit({ type: 'view', data: view(data.message === '选第一间') })
  }
  const { voice, input, output } = setupVoice()
  chat.input = '保留待发送的文字草稿'
  voice.start()
  await until(() => input.calls.length === 1)
  input.final('明天十四点到十五点，六个人')
  await until(() => input.calls.length === 2)
  assert.match(output.texts.at(-1)!, /第一间.*研讨室101/u)
  input.final('选第一间')
  await until(() => input.calls.length === 3)
  assert.match(output.texts.at(-1)!, /北京时间14点整至15点整/u)
  assert.match(output.texts.at(-1)!, /仍需管理员审批/u)
  assert.doesNotMatch(output.texts.at(-1)!, /主题|用途/u)
  input.final('确认提交。')
  await until(() => voice.phase.value === 'completed')
  assert.deepEqual(calls.map(call => call.action), ['message', 'message', 'confirm'])
  assert.equal(calls.at(-1)!.confirmation_id, 'credential')
  assert.equal(input.calls.length, 3); assert.equal(voice.active.value, false)
  assert.match(output.texts.at(-1)!, /预约编号12.*待管理员审批/u)
  assert.equal(chat.input, '保留待发送的文字草稿')
})

test('onReady之前不宣称开始采集，临时识别文本只预览，不提前提交', async () => {
  let requests = 0
  assistantChatApi.stream = async (_, __, emit) => { requests++; emit({ type: 'view', data: view() }) }
  const { voice, input } = setupVoice()
  input.automaticReady = false; voice.start()
  await until(() => input.calls.length === 1)
  assert.equal(voice.phase.value, 'preparing')
  input.calls[0]!.options.onInterim?.('明天下午')
  assert.equal(voice.preview.value, '明天下午'); assert.equal(requests, 0)
  input.calls[0]!.options.onReady?.()
  assert.equal(voice.phase.value, 'listening')
  input.final('明天下午六个人')
  await until(() => requests === 1)
})

test('SSE先到view仍等待完整请求结束，不提前朗读或再次识别', async () => {
  const finish = deferred<void>()
  assistantChatApi.stream = async (_, __, emit) => { emit({ type: 'view', data: view() }); await finish.promise }
  const { voice, input, output } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  input.final('明天下午六人')
  await until(() => chat.messages.length === 2)
  assert.equal(chat.pending, true); assert.equal(voice.phase.value, 'working')
  assert.equal(output.texts.length, 1); assert.equal(input.calls.length, 1)
  finish.resolve(); await until(() => input.calls.length === 2)
  assert.equal(output.texts.length, 2)
})

test('SSE的view之后报错，暂停并朗读错误，不把提前收到的候选或结果当成功', async () => {
  assistantChatApi.stream = async (_, __, emit) => {
    emit({ type: 'view', data: view() }); throw new ChatStreamError(503, '服务暂不可用，请重试。')
  }
  const { voice, input, output } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  input.final('明天下午六人')
  await until(() => voice.phase.value === 'paused')
  assert.equal(input.calls.length, 1); assert.match(voice.error.value, /服务暂不可用/u)
  assert.doesNotMatch(output.texts.join(''), /第一间|申请已提交/u)
})

test('摘要朗读中暂停、迟到onend不会开启麦克风；继续时必须重新完整朗读摘要', async () => {
  await seedSummary()
  let confirms = 0
  assistantChatApi.stream = async (data, _, emit) => { confirms++; assert.equal(data.action, 'confirm'); emit({ type: 'result', data: result() }) }
  const { voice, input, output } = setupVoice()
  output.hold = text => text.includes('请核对本次预约申请')
  voice.start(); await until(() => output.held.length === 1)
  voice.pause(); assert.equal(output.held[0]!.signal.aborted, true)
  output.held[0]!.done.resolve(); await flush()
  assert.equal(input.calls.length, 0); assert.equal(voice.phase.value, 'paused'); assert.equal(confirms, 0)
  voice.start(); await until(() => output.held.length === 2)
  assert.equal(input.calls.length, 0)
  output.held[1]!.done.resolve(); await until(() => input.calls.length === 1)
  input.final('确认提交'); await until(() => voice.phase.value === 'completed')
  assert.equal(confirms, 1)
})

test('摘要在朗读后过期，听到确认也不能发送写入请求', async () => {
  await seedSummary()
  let requests = 0
  assistantChatApi.stream = async () => { requests++ }
  const { voice, input, output } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  chat.view!.confirmation!.expires_at = new Date(Date.now() - 1).toISOString()
  input.final('确认提交'); await until(() => input.calls.length === 2)
  assert.equal(requests, 0); assert.match(output.texts.at(-1)!, /没有可以提交的有效摘要/u)
})

test('无法核实提交结果时不重复写入，只允许只读check恢复实际记录', async () => {
  await seedSummary()
  const calls: string[] = []
  assistantChatApi.stream = async (data, _, emit) => {
    calls.push(data.action)
    if (data.action === 'confirm') throw new ChatStreamError(null, '连接中断')
    assert.equal(data.action, 'check'); emit({ type: 'result', data: result() })
  }
  const { voice, input } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  input.final('确认提交'); await until(() => voice.phase.value === 'paused')
  assert.equal(chat.outcomeUnknown, true)
  voice.start(); await until(() => input.calls.length === 2)
  input.final('确认提交'); await until(() => input.calls.length === 3)
  assert.deepEqual(calls, ['confirm'])
  input.final('核对提交结果'); await until(() => voice.phase.value === 'completed')
  assert.deepEqual(calls, ['confirm', 'check']); assert.equal(chat.outcomeUnknown, false)
})

test('否定句及模糊回答不会被前端改写成确认提交', async () => {
  await seedSummary()
  const calls: ChatRequest[] = []
  assistantChatApi.stream = async (data, _, emit) => { calls.push(data); emit({ type: 'view', data: view(true) }) }
  const { voice, input } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  input.final('不要确认提交'); await until(() => input.calls.length === 2)
  input.final('好的'); await until(() => input.calls.length === 3)
  assert.deepEqual(calls.map(call => [call.action, call.message, call.confirmation_id]),
    [['message', '不要确认提交', undefined], ['message', '好的', undefined]])
})

test('已发送确认后停止语音不会取消HTTP事务，迟到的实际预约仍保存在聊天中且不重开麦克风', async () => {
  await seedSummary()
  const finish = deferred<void>()
  let serverSignal: AbortSignal | undefined
  assistantChatApi.stream = async (data, _, emit, signal) => {
    assert.equal(data.action, 'confirm'); serverSignal = signal
    await finish.promise; emit({ type: 'result', data: result() })
  }
  const { voice, input, output } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  input.final('确认提交'); await until(() => chat.pending)
  voice.stop()
  assert.equal(serverSignal!.aborted, false)
  finish.resolve(); await until(() => !chat.pending); await flush()
  assert.equal(chat.messages.at(-1)!.result!.reservation!.id, 12)
  assert.equal(voice.phase.value, 'idle'); assert.equal(input.calls.length, 1)
  assert.doesNotMatch(output.texts.join(''), /预约编号12/u)
})

test('识别失败时暂停且保留文字操作，不自动反复拉起麦克风', async () => {
  let requests = 0
  assistantChatApi.stream = async () => { requests++ }
  const { voice, input } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  input.calls[0]!.done.reject(new Error('没有识别到完整语音'))
  await until(() => voice.phase.value === 'paused')
  assert.equal(requests, 0); assert.equal(input.calls.length, 1)
  assert.equal(chat.canUse, true); assert.match(voice.error.value, /完整语音/u)
})

test('暂停时重听摘要只朗读，不自动开启麦克风或发送请求', async () => {
  await seedSummary()
  let requests = 0
  assistantChatApi.stream = async () => { requests++ }
  const { voice, input, output } = setupVoice()
  voice.replay(); await until(() => voice.phase.value === 'paused')
  assert.match(output.texts[0]!, /请核对本次预约申请/u)
  assert.equal(input.calls.length, 0); assert.equal(requests, 0)
})

test('语音自动发送不覆盖或清空尚未发送的文字草稿', async () => {
  assistantChatApi.stream = async (_, __, emit) => emit({ type: 'view', data: view() })
  const { voice, input } = setupVoice()
  chat.input = '尚未发送的手写条件'
  voice.start(); await until(() => input.calls.length === 1)
  input.final('明天下午六人'); await until(() => input.calls.length === 2)
  assert.equal(chat.input, '尚未发送的手写条件')
})

test('页面确认按钮只发送当前凭证，不清除没有发送的文字草稿', async () => {
  await seedSummary()
  chat.input = '仍需保留的手写条件'
  assistantChatApi.stream = async (data, _, emit) => {
    assert.equal(data.action, 'confirm'); emit({ type: 'result', data: result() })
  }
  await chat.confirm()
  assert.equal(chat.input, '仍需保留的手写条件')
})

test('页面手动请求中断当前语音，手动请求仍能完成，继续语音后才开启下一轮', async () => {
  const finish = deferred<void>()
  assistantChatApi.stream = async (_, __, emit) => { await finish.promise; emit({ type: 'view', data: view() }) }
  const { voice, input } = setupVoice()
  voice.start(); await until(() => input.calls.length === 1)
  const manual = chat.sendText('手动查询')
  assert.equal(voice.phase.value, 'paused'); assert.equal(input.calls[0]!.options.signal.aborted, true)
  finish.resolve(); await manual; await flush()
  assert.equal(chat.messages.length, 2); assert.equal(input.calls.length, 1)
  voice.start(); await until(() => input.calls.length === 2)
})

for (const cleanup of ['stop', 'scope', 'account', 'conversation'] as const) {
  test(`${cleanup}清理忽略迟到的识别结果及事件，不能发送请求或重开麦克风`, async () => {
    let requests = 0
    assistantChatApi.stream = async () => { requests++ }
    const { voice, input, scope } = setupVoice()
    input.ignoreAbort = true
    voice.start(); await until(() => input.calls.length === 1)
    if (cleanup === 'stop') voice.stop()
    else if (cleanup === 'scope') scope.stop()
    else if (cleanup === 'account') auth.status = 'anonymous'
    else chat.newConversation()
    assert.equal(input.calls[0]!.options.signal.aborted, true)
    input.calls[0]!.options.onInterim?.('迟到的临时识别')
    input.calls[0]!.options.onReady?.()
    input.calls[0]!.done.resolve('确认提交')
    await flush()
    assert.equal(requests, 0); assert.equal(input.calls.length, 1)
    assert.equal(voice.phase.value, 'idle'); assert.equal(voice.preview.value, '')
  })
}

test('页面转入后台关闭麦克风，返回页面不会擅自继续识别', async () => {
  const page = Object.assign(new EventTarget(), { hidden: false })
  const { voice, input } = setupVoice(page as unknown as Document)
  voice.start(); await until(() => input.calls.length === 1)
  page.hidden = true; page.dispatchEvent(new Event('visibilitychange'))
  assert.equal(voice.phase.value, 'paused'); assert.equal(input.calls[0]!.options.signal.aborted, true)
  page.hidden = false; page.dispatchEvent(new Event('visibilitychange')); await flush()
  assert.equal(input.calls.length, 1)
})

test('独立朗读只要求TTS支持，默认关闭自动朗读，不启动识别、不请求接口，读完回到idle', async () => {
  await seedSummary()
  let requests = 0
  assistantChatApi.stream = async () => { requests++ }
  const input = new FakeInput(), output = new FakeOutput(input), scope = effectScope()
  input.supported = false; scopes.push(scope)
  const voice = scope.run(() => useAssistantVoice(chat, { input, output, page: null }))!
  output.hold = () => true
  assert.equal(voice.supported, false); assert.equal(voice.readSupported, true)
  assert.equal(voice.autoRead.value, false); assert.equal(voice.canRead.value, true)
  voice.readLatest(); await until(() => output.held.length === 1)
  assert.equal(voice.reading.value, true); assert.match(output.texts[0]!, /北京时间/u)
  assert.equal(input.calls.length, 0); assert.equal(requests, 0)
  output.held[0]!.done.resolve(); await until(() => voice.phase.value === 'idle')
  assert.equal(voice.reading.value, false); assert.equal(input.calls.length, 0); assert.equal(requests, 0)
  voice.readLatest(); await until(() => output.held.length === 2)
  voice.replay(); await until(() => output.held.length === 3)
  assert.equal(output.held[1]!.signal.aborted, true)
  output.held[1]!.done.resolve(); await flush()
  assert.equal(input.calls.length, 0)
  output.held[2]!.done.resolve(); await until(() => !voice.reading.value)
  assert.equal(input.calls.length, 0); assert.equal(requests, 0)
})

test('开启自动朗读不读历史；手动请求收到view后仍等完整结束才读本轮最新回复', async () => {
  await seedSummary()
  const finish = deferred<void>()
  assistantChatApi.stream = async (_, __, emit) => { emit({ type: 'view', data: view() }); await finish.promise }
  const { voice, input, output } = setupVoice()
  voice.autoRead.value = true; await flush()
  assert.equal(output.texts.length, 0)
  const manual = chat.sendText('调整人数后重新查询')
  await until(() => chat.pending && chat.messages.length === 4)
  assert.equal(output.texts.length, 0); assert.equal(input.calls.length, 0)
  finish.resolve(); await manual
  await until(() => output.texts.length === 1 && voice.phase.value === 'idle')
  assert.match(output.texts[0]!, /第一间.*研讨室101/u)
  assert.equal(input.calls.length, 0); assert.equal(voice.autoRead.value, true)
})

test('自动朗读遇到SSE先view后error不播放提前收到的业务回复', async () => {
  assistantChatApi.stream = async (_, __, emit) => {
    emit({ type: 'view', data: view() }); throw new ChatStreamError(503, '服务暂不可用')
  }
  const { voice, input, output } = setupVoice()
  voice.autoRead.value = true
  await chat.sendText('明天下午六个人'); await flush()
  assert.equal(output.texts.length, 0); assert.equal(input.calls.length, 0)
  assert.equal(voice.canRead.value, false); assert.equal(voice.reading.value, false)
})

test('连续语音同时开启自动朗读时每轮只播放一次，不重复播放助手回复', async () => {
  assistantChatApi.stream = async (_, __, emit) => emit({ type: 'view', data: view() })
  const { voice, input, output } = setupVoice()
  voice.autoRead.value = true; voice.start()
  await until(() => input.calls.length === 1)
  input.final('明天下午六个人'); await until(() => input.calls.length === 2)
  await flush()
  assert.equal(output.texts.length, 2, '只朗读一次引导语和一次候选回复')
  assert.equal(output.texts.filter(text => text.includes('研讨室101')).length, 1)
  assert.equal(voice.reading.value, false); assert.equal(voice.active.value, true)
})

test('停止独立朗读中止当前音频且保留自动朗读开关，迟到结束不重播、仅下一次新回复自动朗读', async () => {
  await seedSummary()
  const { voice, input, output } = setupVoice()
  voice.autoRead.value = true; output.hold = () => true
  voice.readLatest(); await until(() => output.held.length === 1)
  voice.stopReading()
  assert.equal(output.held[0]!.signal.aborted, true)
  assert.equal(voice.autoRead.value, true); assert.equal(voice.reading.value, false)
  output.held[0]!.done.resolve(); await flush()
  assert.equal(output.texts.length, 1)
  assistantChatApi.stream = async (_, __, emit) => emit({ type: 'view', data: view() })
  await chat.sendText('继续使用文字预约'); await until(() => output.held.length === 2)
  assert.equal(output.texts.length, 2); assert.equal(input.calls.length, 0)
  output.held[1]!.done.resolve(); await until(() => voice.phase.value === 'idle')
})

test('账号变动中止独立朗读且关闭自动朗读，旧音频完成事件不能影响新会话', async () => {
  await seedSummary()
  const { voice, input, output } = setupVoice()
  voice.autoRead.value = true; output.hold = () => true
  voice.readLatest(); await until(() => output.held.length === 1)
  auth.status = 'anonymous'
  assert.equal(output.held[0]!.signal.aborted, true)
  assert.equal(voice.autoRead.value, false); assert.equal(voice.canRead.value, false)
  output.held[0]!.done.resolve(); await flush()
  assert.equal(voice.phase.value, 'idle'); assert.equal(voice.reading.value, false)
  assert.equal(output.texts.length, 1); assert.equal(input.calls.length, 0)
})

test('独立朗读实际预约结果只读真实编号与状态，读完回到idle而不继续监听', async () => {
  await seedSummary()
  assistantChatApi.stream = async (_, __, emit) => emit({ type: 'result', data: result() })
  await chat.confirm()
  const { voice, input, output } = setupVoice()
  voice.readLatest(); await until(() => output.texts.length === 1 && !voice.reading.value)
  assert.match(output.texts[0]!, /预约编号12.*待管理员审批/u)
  assert.equal(voice.phase.value, 'idle'); assert.equal(input.calls.length, 0)
})
