import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { assistantApi, type RulesAnswer } from '../src/api/assistant.ts'
import { assistantChatApi } from '../src/api/assistantChat.ts'
import { ApiError, http } from '../src/api/http.ts'
import { useAssistantChatStore } from '../src/stores/assistantChat.ts'
import { isRuleQuestion } from '../src/stores/assistantIntent.ts'
import { useAuthStore } from '../src/stores/auth.ts'

const originalAsk = assistantApi.askRules
const originalStream = assistantChatApi.stream
const originalAdapter = http.defaults.adapter
const answer: RulesAnswer = {
  answer: '预约开始后不能取消。', grounded: true, knowledge_version: 'test',
  sources: [{ id: 'BR05-1', section: 'BR05', title: '取消', version: 'test', excerpt: '开始前可取消。' }],
}

test('自动区分通用规则、实时查房、预约条件和确认语句', () => {
  const examples = [
    ['预约开始后还能取消吗？', true],
    ['会议室几点开放？', true],
    ['预约最多可以用多久？', true],
    ['我想预约明天下午，主题和用途必须填写吗？', true],
    ['预约要审批吗', true],
    ['怎么催办', true],
    ['明天下午还有空闲会议室吗？', false],
    ['帮我订明天14点到15点的会议室，6个人，可以吗？', false],
    ['我想预约明天下午，主题是制度讨论', false],
    ['人数改成8人', false],
    ['第一间可以吗？', false],
    ['确认提交', false],
  ] as const
  for (const [text, expected] of examples) assert.equal(isRuleQuestion(text), expected, text)
})
let pinia: ReturnType<typeof createPinia>
let chat: ReturnType<typeof useAssistantChatStore>
let auth: ReturnType<typeof useAuthStore>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  auth = useAuthStore()
  auth.user = { id: 1, username: 'test', full_name: '测试用户', role: 'user', identity_type: 'student', is_active: true }
  auth.status = 'authenticated'
  auth.csrfToken = 'synthetic-csrf'
  chat = useAssistantChatStore()
  http.defaults.adapter = async () => { throw new Error('未声明的 HTTP 请求') }
  assistantChatApi.stream = async () => { assert.fail('规则提问不能进入预约接口') }
})

afterEach(() => {
  disposePinia(pinia)
  assistantApi.askRules = originalAsk
  assistantChatApi.stream = originalStream
  http.defaults.adapter = originalAdapter
})

test('规则问答进入同一聊天记录，保留来源、预约草稿和当前确认凭证', async () => {
  chat.view = {
    conversation_id: 'booking', revision: 3, phase: 'summary', message: '预约摘要',
    draft: { date: '2099-11-09', start_time: '14:00', end_time: '15:00', attendees: 6,
      title: '', purpose: '', equipment: [] },
    candidates: [], total: 1, checked_at: null, selected_room: null,
    confirmation: { confirmation_id: 'confirmation', conversation_id: 'booking', revision: 3,
      action: 'submit', state: 'pending', created_at: '2099-11-08T10:00:00+08:00',
      expires_at: '2099-11-08T10:05:00+08:00', result: null,
      summary: { room_id: 1, date: '2099-11-09', start_time: '14:00', end_time: '15:00',
        attendees: 6, title: '', purpose: '', required_equipment: [] } },
  }
  const booking = chat.view
  const calls: string[] = []
  http.defaults.adapter = async config => {
    calls.push(config.url!)
    assert.equal(config.method, 'post')
    assert.deepEqual(JSON.parse(config.data), { question: '开始后能取消吗？' })
    assert.equal(config.headers.get('X-CSRF-Token'), auth.csrfToken)
    assert.equal(config.timeout, 65_000)
    return { config, data: answer, status: 200, statusText: 'OK', headers: {} }
  }
  chat.input = '  开始后能取消吗？  '
  await chat.send()
  assert.deepEqual(calls, ['/assistant/rules/ask'])
  assert.deepEqual(chat.messages.map(message => message.kind), ['rules', 'rules'])
  assert.deepEqual(chat.messages.at(-1)?.rulesAnswer, answer)
  assert.equal(chat.view, booking)
  assert.equal(chat.canConfirm(), true)
  assert.equal(chat.input, '')
  assert.equal(chat.pending, false)
})

test('超长、空白、无权限及预约结果待核对时均不发送规则请求', async () => {
  let calls = 0
  assistantApi.askRules = async () => { calls++; return answer }
  for (const input of ['  ', '问'.repeat(501)]) {
    chat.input = input
    await chat.send()
    assert.match(chat.error, /1至500/)
  }
  chat.input = '预约规则'
  chat.outcomeUnknown = true
  await chat.send()
  chat.outcomeUnknown = false
  auth.user!.role = 'admin'
  chat.input = '预约规则'
  await chat.send()
  assert.equal(calls, 0)
})

test('规则错误保留问题，当前401复核登录，预约确认状态不被标为失效', async () => {
  let restored = 0
  auth.restoreSession = async () => { restored++ }
  assistantApi.askRules = async () => { throw new ApiError(401, '请重新登录') }
  chat.input = '规则问题'
  await chat.send()
  assert.equal(chat.input, '规则问题')
  assert.match(chat.error, /重新登录/)
  assert.equal(chat.unverifiedPlan, false)
  assert.equal(chat.outcomeUnknown, false)
  assert.equal(restored, 1)
})

test('规则请求防重复提交，换会话后中止并忽略迟到回复', async () => {
  let resolve!: (value: RulesAnswer) => void
  let signal: AbortSignal | undefined
  let calls = 0
  assistantApi.askRules = async (_, __, requestSignal) => {
    calls++
    signal = requestSignal
    return new Promise(done => { resolve = done })
  }
  chat.input = '规则问题'
  const first = chat.send()
  await chat.send()
  assert.equal(calls, 1)
  auth.sessionChangeCount++
  assert.equal(signal?.aborted, true)
  resolve(answer)
  await first
  assert.deepEqual(chat.messages, [])
  assert.equal(chat.pending, false)
})
