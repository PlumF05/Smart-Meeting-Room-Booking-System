import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { AssistantConfirmation, ConversationView } from '../src/api/meetingAssistant.ts'
import type { ChatMessage } from '../src/stores/assistantChat.ts'
import { buildSpokenReply } from '../src/voice/spokenReply.ts'

const now = Date.parse('2026-11-09T02:00:00Z')
const room = { id: 8, name: '研讨室101', location: '教学楼A座三层', capacity: 12, equipment: ['白板', '投影仪'], is_active: true }

function confirmation(overrides: Partial<AssistantConfirmation> = {}): AssistantConfirmation {
  return {
    confirmation_id: 'current-confirmation', conversation_id: 'current-conversation', revision: 3, action: 'submit',
    summary: { room_id: 8, date: '2026-11-10', start_time: '14:00', end_time: '15:30', attendees: 6,
      required_equipment: ['白板'], title: '', purpose: '' },
    created_at: '2026-11-09T10:00:00+08:00', expires_at: '2026-11-09T10:05:00+08:00', state: 'pending', result: null,
    ...overrides,
  }
}

function view(overrides: Partial<ConversationView> = {}): ConversationView {
  return {
    conversation_id: 'current-conversation', revision: 3, phase: 'summary', message: '请核对后确认。',
    draft: { date: '2099-01-01', start_time: '08:00', end_time: '09:00', attendees: 999,
      equipment: ['草稿设备'], title: '旧草稿主题', purpose: '旧草稿用途' },
    candidates: [room], total: 1, checked_at: '2026-11-09T02:00:00Z', selected_room: { ...room },
    confirmation: confirmation(), ...overrides,
  }
}

function message(currentView = view()): ChatMessage {
  return { id: 2, role: 'assistant', text: currentView.message, view: currentView }
}

test('完整摘要只读服务端确认字段，说明北京时间、设备与审批，并返回当前凭证', () => {
  const reply = buildSpokenReply(message(), now)
  assert.equal(reply.confirmationId, 'current-confirmation')
  assert.equal(reply.completed, false)
  for (const text of ['研讨室101', '教学楼A座三层', '2026年11月10日', '北京时间14点整至15点30分', '参会人数6人', '所需设备白板',
    '确认后提交申请，仍需管理员审批', '信息正确请说确认提交，需要修改请直接说明']) assert.ok(reply.text.includes(text), text)
  for (const text of ['2099', '999', '草稿设备', '旧草稿主题', '旧草稿用途', '投影仪', 'confirmation_id', '{']) assert.ok(!reply.text.includes(text), text)
})

test('空的选填主题和用途不朗读、不追问；有值才读出', () => {
  const current = view()
  current.confirmation!.summary.title = '   '
  current.confirmation!.summary.purpose = '\n'
  const empty = buildSpokenReply(message(current), now)
  assert.equal(empty.confirmationId, 'current-confirmation')
  assert.doesNotMatch(empty.text, /主题|用途|请补充|请提供/u)
  current.confirmation!.summary.title = '课程讨论'
  current.confirmation!.summary.purpose = '讨论设计任务'
  const filled = buildSpokenReply(message(current), now)
  assert.match(filled.text, /主题课程讨论。用途讨论设计任务。/u)
})

test('摘要按北京时间日历值朗读，不因运行环境时区改变日期和时刻', () => {
  const originalTimezone = process.env.TZ
  try {
    process.env.TZ = 'America/Los_Angeles'
    const current = view()
    current.confirmation!.summary.date = '2026-11-10'
    current.confirmation!.summary.start_time = '00:00'
    current.confirmation!.summary.end_time = '00:30'
    const reply = buildSpokenReply(message(current), now)
    assert.match(reply.text, /2026年11月10日，北京时间0点整至0点30分/u)
    assert.equal(reply.confirmationId, 'current-confirmation')
  } finally {
    if (originalTimezone === undefined) delete process.env.TZ
    else process.env.TZ = originalTimezone
  }
})

test('失效、被替代、刚到期和非法到期时间均不开放确认，并指导重新生成摘要', () => {
  for (const current of [
    confirmation({ state: 'expired' }), confirmation({ state: 'superseded' }),
    confirmation({ expires_at: '2026-11-09T02:00:00Z' }), confirmation({ expires_at: 'not-a-date' }),
    confirmation({ expires_at: '2026-11-09T10:05:00' }),
  ]) {
    const reply = buildSpokenReply(message(view({ confirmation: current })), now)
    assert.equal(reply.confirmationId, undefined)
    assert.equal(reply.completed, false)
    assert.match(reply.text, /重新生成摘要/u)
    assert.doesNotMatch(reply.text, /信息正确请说确认提交/u)
  }
})

test('已使用的凭证没有实际预约记录时不能宣称已提交或预约成功', () => {
  const current = message(view({ confirmation: confirmation({ state: 'consumed', result: { reservation_id: 90, status: 'approved' } }) }))
  const reply = buildSpokenReply(current, now)
  assert.equal(reply.confirmationId, undefined)
  assert.equal(reply.completed, false)
  assert.match(reply.text, /核对提交结果/u)
  assert.doesNotMatch(reply.text, /预约成功|已批准|申请已提交/u)
})

test('不完整或不匹配的摘要不能给出可提交凭证', () => {
  const cases = [
    view({ selected_room: null }), view({ selected_room: { ...room, id: 42 } }),
    view({ revision: 4 }), view({ conversation_id: 'other-conversation' }),
    view({ selected_room: { ...room, is_active: false } }), view({ confirmation: null }),
  ]
  for (const current of cases) {
    const reply = buildSpokenReply(message(current), now)
    assert.equal(reply.confirmationId, undefined)
    assert.equal(reply.completed, false)
    assert.match(reply.text, /重新生成摘要/u)
  }
  const invalidDate = view()
  invalidDate.confirmation!.summary.date = '2026-02-31'
  assert.equal(buildSpokenReply(message(invalidDate), now).confirmationId, undefined)
})

test('候选逐间朗读序号、名称、地点、容量、设备与口头选择方法', () => {
  const current = view({ phase: 'candidates', confirmation: null, selected_room: null, total: 5,
    candidates: [room, { ...room, id: 9, name: '学术会议室', location: '科研楼二层', capacity: 30, equipment: [] }] })
  const reply = buildSpokenReply(message(current), now)
  for (const text of ['5间', '第一间，研讨室101', '教学楼A座三层', '容量12人', '设备有白板、投影仪',
    '第二间，学术会议室', '科研楼二层', '容量30人', '未列出设备', '选第一间', '查看全部']) assert.ok(reply.text.includes(text), text)
  assert.equal(reply.confirmationId, undefined)
  assert.equal(reply.completed, false)
})

test('澄清和无匹配结果直接朗读当前回复，不使用历史候选或摘要', () => {
  for (const phase of ['clarify', 'no_match'] as const) {
    const current = view({ phase, message: '请补充人数，或调整日期。' })
    const reply = buildSpokenReply(message(current), now)
    assert.deepEqual(reply, { text: current.message, completed: false })
  }
  assert.equal(buildSpokenReply(message(view({ phase: 'candidates', candidates: [], total: 0 })), now).confirmationId, undefined)
})

test('只有实际预约记录才完成，逐一读出实际编号和当前状态，不沿用旧收据状态', () => {
  const states = { pending: '待管理员审批', approved: '已批准', rejected: '已驳回', cancelled: '已取消', expired: '已过期', revoked: '已撤销', finished: '已结束' }
  for (const [status, label] of Object.entries(states)) {
    const current = message()
    current.result = { confirmation: confirmation({ state: 'consumed', result: { reservation_id: 999, status: 'pending' } }),
      reservation: { id: 37, status }, message: '过时的固定文案：申请已提交，待管理员审批。' }
    const reply = buildSpokenReply(current, now)
    assert.equal(reply.completed, true)
    assert.equal(reply.confirmationId, undefined)
    assert.match(reply.text, /预约编号37/u)
    assert.ok(reply.text.includes(`当前状态为${label}`))
    assert.doesNotMatch(reply.text, /999|过时/u)
    if (status === 'pending') assert.doesNotMatch(reply.text, /已批准|预约成功/u)
    else assert.doesNotMatch(reply.text, /待管理员审批/u)
  }
})

test('空结果或只有凭证收据时不宣布完成；待提交结果可继续补充条件', () => {
  for (const reservation of [null, {}, { status: 'pending' }]) {
    const current: ChatMessage = { id: 2, role: 'assistant', text: '预约成功', result: {
      confirmation: confirmation({ result: { reservation_id: 999, status: 'approved' } }), reservation, message: '预约成功',
    } }
    const reply = buildSpokenReply(current, now)
    assert.equal(reply.completed, false)
    assert.equal(reply.confirmationId, undefined)
    assert.match(reply.text, /尚未提交/u)
    assert.match(reply.text, /继续补充条件/u)
    assert.doesNotMatch(reply.text, /预约成功|已批准|999/u)
  }
})

test('核对尚未提交结果时，只有当前完整摘要读出后才可获得确认凭证', () => {
  const current = message()
  current.result = { confirmation: confirmation(), reservation: null, message: '尚未提交' }
  const reply = buildSpokenReply(current, now)
  assert.match(reply.text, /当前方案尚未提交/u)
  assert.match(reply.text, /2026年11月10日/u)
  assert.equal(reply.confirmationId, 'current-confirmation')
  current.result.confirmation = confirmation({ confirmation_id: 'different-confirmation' })
  assert.equal(buildSpokenReply(current, now).confirmationId, undefined)
})

test('实际记录的未知状态不推断批准，也不朗读原始内部值', () => {
  for (const status of ['future_state', '__proto__', 'constructor']) {
    const current = message()
    current.result = { confirmation: confirmation({ state: 'consumed' }), reservation: { id: 37, status }, message: '已批准' }
    const reply = buildSpokenReply(current, now)
    assert.equal(reply.completed, true)
    assert.match(reply.text, /当前状态待核对/u)
    assert.doesNotMatch(reply.text, /已批准|future_state|__proto__|constructor|object Object/u)
  }
})

test('用户消息不自动朗读；没有结构化视图的普通助手文本仍可朗读', () => {
  assert.deepEqual(buildSpokenReply({ id: 1, role: 'user', text: '明天下午开会' }, now), { text: '', completed: false })
  assert.deepEqual(buildSpokenReply({ id: 2, role: 'assistant', text: '请继续说明。' }, now), { text: '请继续说明。', completed: false })
})
