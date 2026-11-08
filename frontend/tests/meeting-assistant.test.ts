import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, test } from 'node:test'
import * as vue from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import * as elements from 'element-plus'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { AxiosHeaders } from 'axios'
import * as api from '../src/api/http.ts'
import * as authModule from '../src/stores/auth.ts'
import * as meetingModule from '../src/api/meetingAssistant.ts'

const filename = fileURLToPath(new URL('../src/components/MeetingAssistant.vue', import.meta.url))
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
const compiled = compileScript(descriptor, {
  id: 'meeting-assistant-regression',
  fs: { fileExists: existsSync, readFile: path => readFileSync(path, 'utf8') },
})
const javascript = ts.transpileModule(compiled.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const moduleObject = { exports: {} as { default?: vue.Component } }
const imports: Record<string, unknown> = {
  vue, 'element-plus': elements, '../api/http.ts': api, '../stores/auth.ts': authModule,
  '../api/meetingAssistant.ts': meetingModule,
}
new Function('require', 'module', 'exports', javascript)(
  (name: string) => {
    assert.ok(name in imports, `禁止未声明导入：${name}`)
    return imports[name]
  }, moduleObject, moduleObject.exports,
)
const renderer = vue.createRenderer<Record<string, unknown>, Record<string, unknown>>({
  insert() {}, remove() {}, patchProp() {}, setText() {}, setElementText() {},
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  parentNode: () => null, nextSibling: () => null,
})
const room = { id: 1, name: '研讨室101', location: '教学楼', capacity: 8, equipment: ['白板'], is_active: true }
const draft = { date: '2026-11-09', start_time: '14:00', end_time: '15:00', attendees: 8, equipment: ['白板'], title: '毕业设计讨论', purpose: '讨论设计方案' }
function conversation(changes: Partial<meetingModule.ConversationView> = {}): meetingModule.ConversationView {
  return { conversation_id: 'synthetic-conversation', revision: 1, phase: 'candidates', message: '请选择会议室。',
    draft: structuredClone(draft), candidates: [structuredClone(room)], total: 4,
    checked_at: '2026-11-08T15:00:00+08:00', selected_room: null, confirmation: null, ...changes }
}
function confirmation(): meetingModule.AssistantConfirmation {
  return { confirmation_id: 'synthetic-confirmation', conversation_id: 'synthetic-conversation', revision: 2,
    action: 'submit', state: 'pending', result: null, created_at: '2026-11-08T15:00:00+08:00', expires_at: '2026-11-08T15:05:00+08:00',
    summary: { room_id: 1, date: draft.date, start_time: draft.start_time, end_time: draft.end_time,
      attendees: 8, required_equipment: ['白板'], title: draft.title, purpose: draft.purpose } }
}
const originalAdapter = api.http.defaults.adapter
const originalClose = meetingModule.meetingAssistantApi.close
let auth: ReturnType<typeof authModule.useAuthStore>
let originalRestore: typeof auth.restoreSession
let apps: vue.App[] = []
let closed: Array<[string, string]> = []
function response(config: any, data: unknown) {
  return { config, data, status: 200, statusText: 'OK', headers: new AxiosHeaders() }
}
async function flush() { await new Promise<void>(resolve => setImmediate(resolve)); await vue.nextTick() }
function panel() {
  let state: any
  const original = moduleObject.exports.default as { setup: Function }
  const app = renderer.createApp({ ...original, setup(props: unknown, context: unknown) {
    state = original.setup(props, context)
    return () => null
  } })
  app.mount({}); apps.push(app)
  return { state, app }
}
beforeEach(() => {
  setActivePinia(createPinia())
  auth = authModule.useAuthStore()
  auth.user = { id: 1, username: 'synthetic', full_name: '测试用户', role: 'user', identity_type: 'student', is_active: true }
  auth.status = 'authenticated'; auth.csrfToken = 'synthetic-csrf'
  originalRestore = auth.restoreSession
  apps = []; closed = []
  meetingModule.meetingAssistantApi.close = async (id, token) => { closed.push([id, token]) }
  api.http.defaults.adapter = async () => { throw new Error('测试不得访问真实服务') }
})
afterEach(async () => {
  for (const app of apps) app.unmount()
  await flush()
  auth.restoreSession = originalRestore
  meetingModule.meetingAssistantApi.close = originalClose
  api.http.defaults.adapter = originalAdapter
})

test('查找组件挂载不自动发请求，匿名和管理员不能发送需求', async () => {
  const { state } = panel()
  let calls = 0
  api.http.defaults.adapter = async config => { calls++; return response(config, conversation()) }
  await flush()
  assert.equal(calls, 0)
  auth.user!.role = 'admin'; state.message.value = '查找会议室'; await state.send()
  auth.status = 'anonymous'; await state.send()
  assert.equal(calls, 0)
  assert.equal(state.view.value, null)
})

test('首次只发送描述，后续补充带当前对话及revision，Cookie和65秒超时仅用于该请求', async () => {
  const { state } = panel()
  let calls = 0
  api.http.defaults.adapter = async config => {
    calls++
    assert.equal(config.method, 'post')
    assert.equal(config.withCredentials, true)
    assert.equal(config.timeout, 65_000)
    assert.equal(config.headers.get('X-CSRF-Token'), 'synthetic-csrf')
    assert.equal(config.url, calls === 1 ? '/assistant/conversations/start' : '/assistant/conversations/synthetic-conversation/message')
    assert.deepEqual(JSON.parse(config.data), calls === 1 ? { message: '明天下午8个人' } : { revision: 1, message: '两点到三点，需要白板' })
    return response(config, conversation({ revision: calls }))
  }
  state.message.value = ' 明天下午8个人 '; await state.send()
  state.message.value = '两点到三点，需要白板'; await state.send()
  assert.equal(calls, 2)
  assert.equal(state.view.value.revision, 2)
  assert.deepEqual(vue.toRaw(state.view.value.draft), draft)
  assert.equal(api.http.defaults.timeout, 10_000)
})

test('无效输入本地拒绝，处理中防重复且失败保留输入与旧条件、清除旧凭证', async () => {
  const { state } = panel()
  let calls = 0
  let reject!: (reason: unknown) => void
  state.view.value = conversation({ phase: 'summary', selected_room: room, confirmation: confirmation() })
  api.http.defaults.adapter = async () => { calls++; return new Promise((_, failed) => { reject = failed }) }
  for (const text of [' ', '问'.repeat(501)]) { state.message.value = text; await state.send() }
  assert.equal(calls, 0)
  state.message.value = '改成9个人'
  const request = state.send(); await flush(); await state.send()
  assert.equal(calls, 1)
  assert.equal(state.view.value.confirmation, null)
  reject(new api.ApiError(409, '方案已变化，请重新开始')); await request
  assert.equal(state.message.value, '改成9个人')
  assert.deepEqual(vue.toRaw(state.view.value.draft), draft)
  assert.equal(state.pending.value, false)
  assert.match(state.error.value, /方案已变化/)
})

test('只允许选择服务端返回的启用候选，选房与查看全部都不自动提交', async () => {
  const { state } = panel()
  state.view.value = conversation()
  const requests: Array<[string, unknown]> = []
  api.http.defaults.adapter = async config => {
    requests.push([config.url!, JSON.parse(config.data)])
    if (config.url!.endsWith('/select')) return response(config, conversation({ phase: 'summary', revision: 2, selected_room: room }))
    return response(config, conversation({ revision: 2, candidates: [room], total: 1 }))
  }
  await state.selectRoom(999)
  assert.equal(requests.length, 0)
  await state.selectRoom(1)
  assert.equal(state.view.value.selected_room.id, 1)
  await state.showAll()
  assert.deepEqual(requests, [
    ['/assistant/conversations/synthetic-conversation/select', { revision: 1, room_id: 1 }],
    ['/assistant/conversations/synthetic-conversation/candidates', { revision: 2, show_all: true }],
  ])
})

test('只有完整已选方案可生成凭证，prepare显示服务端有效期且不调用业务提交接口', async () => {
  const { state } = panel()
  const requests: string[] = []
  api.http.defaults.adapter = async config => {
    requests.push(config.url!)
    assert.deepEqual(JSON.parse(config.data), { revision: 2 })
    return response(config, conversation({ phase: 'summary', revision: 2, selected_room: room, confirmation: confirmation() }))
  }
  state.view.value = conversation(); await state.prepare()
  assert.equal(requests.length, 0)
  state.view.value = conversation({ phase: 'summary', revision: 2, selected_room: room })
  await state.prepare()
  assert.deepEqual(requests, ['/assistant/conversations/synthetic-conversation/prepare'])
  assert.equal(state.view.value.confirmation.expires_at, '2026-11-08T15:05:00+08:00')
  assert.equal(state.view.value.confirmation.state, 'pending')
  assert.equal(state.view.value.confirmation.result, null)
})

test('返回别的对话不得覆盖当前方案，重新开始只关闭已知对话且不自动发送新需求', async () => {
  const { state } = panel()
  api.http.defaults.adapter = async config => response(config, conversation())
  state.message.value = '查询'; await state.send()
  api.http.defaults.adapter = async config => response(config, conversation({ conversation_id: 'another-conversation' }))
  state.message.value = '补充'; await state.send()
  assert.equal(state.view.value.conversation_id, 'synthetic-conversation')
  assert.match(state.error.value, /不一致/)
  state.reset(); await flush()
  assert.deepEqual(closed, [['synthetic-conversation', 'synthetic-csrf']])
  assert.equal(state.view.value, null)
  assert.equal(state.message.value, '')
})

test('切换账号与同账号重登立即清空，旧成功或401不能污染新会话', async () => {
  const { state } = panel()
  let resolve!: (value: unknown) => void
  let reject!: (reason: unknown) => void
  let restored = 0; auth.restoreSession = async () => { restored++ }
  api.http.defaults.adapter = async config => new Promise(done => { resolve = data => done(response(config, data)) })
  state.message.value = '旧需求'; const old = state.send(); await flush()
  auth.user = { ...auth.user!, id: 2, username: 'next' }
  assert.equal(state.message.value, '')
  api.http.defaults.adapter = async config => response(config, conversation({ conversation_id: 'new-conversation' }))
  state.message.value = '新需求'; await state.send()
  resolve(conversation()); await old
  assert.equal(state.view.value.conversation_id, 'new-conversation')
  api.http.defaults.adapter = async () => new Promise((_, failed) => { reject = failed })
  state.message.value = '旧会话补充'; const request = state.send(); await flush()
  auth.sessionChangeCount++
  reject(new api.ApiError(401, '旧会话错误')); await request
  assert.equal(restored, 0)
  assert.equal(state.view.value, null)
  assert.equal(state.error.value, '')
})

test('当前401复核会话并保留输入，卸载中止请求且迟到响应不得恢复内容', async () => {
  const { state, app } = panel()
  let restored = 0; auth.restoreSession = async () => { restored++ }
  api.http.defaults.adapter = async () => { throw new api.ApiError(401, '登录已失效') }
  state.message.value = '人数8人'; await state.send()
  assert.equal(restored, 1)
  assert.equal(state.message.value, '人数8人')
  let signal: any
  let resolve!: (value: unknown) => void
  api.http.defaults.adapter = async config => {
    signal = config.signal
    return new Promise(done => { resolve = data => done(response(config, data)) })
  }
  const request = state.send(); await flush()
  app.unmount(); apps.splice(apps.indexOf(app), 1)
  assert.equal(signal.aborted, true)
  resolve(conversation()); await request
  assert.equal(state.view.value, null)
  assert.equal(state.message.value, '')
})

test('关闭使用独立DELETE请求及CSRF，不添加任何预约执行操作', async () => {
  api.http.defaults.adapter = async config => {
    assert.equal(config.method, 'delete')
    assert.equal(config.url, '/assistant/conversations/synthetic%2Fconversation')
    assert.equal(config.headers.get('X-CSRF-Token'), 'synthetic-csrf')
    assert.equal(config.timeout, 65_000)
    return response(config, null)
  }
  await originalClose('synthetic/conversation', 'synthetic-csrf')
})
