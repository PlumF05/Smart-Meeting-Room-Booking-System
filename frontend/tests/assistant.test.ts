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
import * as assistantModule from '../src/api/assistant.ts'

const filename = fileURLToPath(new URL('../src/components/AssistantPanel.vue', import.meta.url))
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
const compiled = compileScript(descriptor, {
  id: 'assistant-regression',
  fs: { fileExists: existsSync, readFile: path => readFileSync(path, 'utf8') },
})
const javascript = ts.transpileModule(compiled.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const moduleObject = { exports: {} as { default?: vue.Component } }
const imports: Record<string, unknown> = {
  vue, 'element-plus': elements, '../api/http.ts': api, '../stores/auth.ts': authModule,
  '../api/assistant.ts': assistantModule,
  './MeetingAssistant.vue': { default: {} },
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
const sample = {
  answer: '开始后不能取消本人预约。', grounded: true, knowledge_version: 'V2.0-r6',
  sources: [{ id: 'BR05-1', section: 'BR05', title: '取消与改约', version: 'V2.0-r6', excerpt: '只可在开始前取消。' }],
}
const originalAdapter = api.http.defaults.adapter
let auth: ReturnType<typeof authModule.useAuthStore>
let originalRestore: typeof auth.restoreSession
let apps: vue.App[] = []
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
  apps = []
  api.http.defaults.adapter = async () => { throw new Error('测试不得访问真实服务') }
})
afterEach(() => {
  for (const app of apps) app.unmount()
  auth.restoreSession = originalRestore
  api.http.defaults.adapter = originalAdapter
})

test('助手默认关闭且不自动请求；匿名与管理员不能打开或提问', async () => {
  const { state } = panel()
  assert.equal(state.visible.value, false)
  await flush()
  for (const status of ['anonymous', 'admin']) {
    if (status === 'anonymous') auth.status = 'anonymous'
    else { auth.status = 'authenticated'; auth.user!.role = 'admin' }
    assert.equal(state.canUse.value, false)
    state.openPanel(); state.question.value = '可以取消吗？'; await state.ask()
    assert.equal(state.visible.value, false)
    assert.equal(state.reply.value, null)
  }
})

test('关闭总面板立即清空查找组件，不等待关闭动画与卸载', () => {
  const { state } = panel()
  let resets = 0
  state.meetingAssistant.value = { reset() { resets++ } }
  state.openPanel()
  state.activeTab.value = 'meeting'
  state.closePanel()
  assert.equal(resets, 1)
  assert.equal(state.visible.value, false)
  assert.equal(state.activeTab.value, 'rules')
})

test('仅当前问题和CSRF发给规则接口，65秒超时不改变公共HTTP设置，并保留真实来源', async () => {
  const { state } = panel()
  let count = 0
  api.http.defaults.adapter = async config => {
    count++
    assert.equal(config.method, 'post')
    assert.equal(config.url, '/assistant/rules/ask')
    assert.equal(config.withCredentials, true)
    assert.equal(config.timeout, 65_000)
    assert.equal(config.headers.get('X-CSRF-Token'), 'synthetic-csrf')
    assert.deepEqual(JSON.parse(config.data), { question: '开始后能取消吗？' })
    return response(config, sample)
  }
  state.openPanel(); state.question.value = '  开始后能取消吗？  '
  await state.ask()
  assert.equal(count, 1)
  assert.deepEqual(vue.toRaw(state.reply.value), sample)
  assert.equal(state.askedQuestion.value, '开始后能取消吗？')
  assert.equal(state.pending.value, false)
  assert.equal(api.http.defaults.timeout, 10_000)
})

test('空白与超长问题本地阻止；500个Unicode字符可以提交', async () => {
  const { state } = panel()
  let count = 0
  api.http.defaults.adapter = async config => { count++; return response(config, sample) }
  state.openPanel()
  for (const text of ['   ', '问'.repeat(501)]) {
    state.question.value = text; await state.ask()
    assert.match(state.error.value, /1至500/)
  }
  assert.equal(count, 0)
  state.question.value = '😀'.repeat(500)
  await state.ask()
  assert.equal(count, 1)
})

test('请求处理中阻止重复发送，失败保留问题且允许手动重试', async () => {
  const { state } = panel()
  let reject!: (reason: unknown) => void
  let count = 0
  api.http.defaults.adapter = async () => { count++; return new Promise((_, failed) => { reject = failed }) }
  state.openPanel(); state.question.value = '最多预约多久？'
  const first = state.ask(); await flush()
  await state.ask()
  assert.equal(count, 1)
  reject(new api.ApiError(503, '规则问答暂不可用')); await first
  assert.equal(state.question.value, '最多预约多久？')
  assert.equal(state.pending.value, false)
  assert.match(state.error.value, /暂不可用/)
  api.http.defaults.adapter = async config => response(config, sample)
  await state.ask()
  assert.equal(state.error.value, '')
  assert.deepEqual(vue.toRaw(state.reply.value), sample)
})

test('grounded=false结果不伪造成有依据，新的提问不携带上一轮回答', async () => {
  const { state } = panel()
  const ungrounded = { answer: '当前没有相关规则依据。', grounded: false, sources: [], knowledge_version: 'V2.0-r6' }
  let count = 0
  api.http.defaults.adapter = async config => {
    count++
    assert.deepEqual(Object.keys(JSON.parse(config.data)), ['question'])
    return response(config, count === 1 ? sample : ungrounded)
  }
  state.openPanel(); state.question.value = '取消规则？'; await state.ask()
  state.question.value = '明天会下雨吗？'; await state.ask()
  assert.equal(state.reply.value.grounded, false)
  assert.deepEqual(vue.toRaw(state.reply.value.sources), [])
  assert.equal(state.reply.value.answer, ungrounded.answer)
})

test('换号立即清空关闭，旧回复不能覆盖新账号的问题与结果', async () => {
  const { state } = panel()
  let resolve!: (value: unknown) => void
  api.http.defaults.adapter = async config => new Promise(done => { resolve = data => done(response(config, data)) })
  state.openPanel(); state.question.value = '上一账号问题'
  const old = state.ask(); await flush()
  auth.user = { ...auth.user!, id: 2, username: 'next' }
  assert.equal(state.visible.value, false)
  assert.equal(state.question.value, '')
  assert.equal(state.reply.value, null)
  api.http.defaults.adapter = async config => response(config, { ...sample, answer: '新账号结果' })
  state.openPanel(); state.question.value = '新账号问题'; await state.ask()
  resolve(sample); await old
  assert.equal(state.question.value, '新账号问题')
  assert.equal(state.reply.value.answer, '新账号结果')
})

test('同一账号重新登录也清除内容；旧401不能复核新会话', async () => {
  const { state } = panel()
  let reject!: (reason: unknown) => void
  let restored = 0; auth.restoreSession = async () => { restored++ }
  api.http.defaults.adapter = async () => new Promise((_, failed) => { reject = failed })
  state.openPanel(); state.question.value = '旧会话问题'
  const request = state.ask(); await flush()
  auth.sessionChangeCount++
  assert.equal(state.question.value, '')
  assert.equal(state.visible.value, false)
  reject(new api.ApiError(401, '会话已过期')); await request
  assert.equal(restored, 0)
  assert.equal(state.error.value, '')
})

test('当前401复核会话，身份仍有效时保留问题供重试', async () => {
  const { state } = panel()
  let restored = 0; auth.restoreSession = async () => { restored++ }
  api.http.defaults.adapter = async () => { throw new api.ApiError(401, '请重新登录') }
  state.openPanel(); state.question.value = '需要管理员批准吗？'
  await state.ask()
  assert.equal(restored, 1)
  assert.equal(state.question.value, '需要管理员批准吗？')
  assert.match(state.error.value, /重新登录/)
  assert.equal(state.pending.value, false)
})

test('开始退出登录就关闭助手，组件卸载后迟到401不能复核或恢复内容', async () => {
  const { state, app } = panel()
  let reject!: (reason: unknown) => void
  let restored = 0; auth.restoreSession = async () => { restored++ }
  api.http.defaults.adapter = async () => new Promise((_, failed) => { reject = failed })
  state.openPanel(); state.question.value = '未完成问题'
  const request = state.ask(); await flush()
  auth.pending = true
  assert.equal(state.visible.value, false)
  assert.equal(state.question.value, '')
  app.unmount(); apps.splice(apps.indexOf(app), 1)
  reject(new api.ApiError(401, '旧错误')); await request
  assert.equal(restored, 0)
  assert.equal(state.reply.value, null)
})

test('手动关闭中止请求并清空问题和答案，旧响应不会重新打开面板', async () => {
  const { state } = panel()
  let resolve!: (value: unknown) => void
  let signal: any
  api.http.defaults.adapter = async config => {
    signal = config.signal
    return new Promise(done => { resolve = data => done(response(config, data)) })
  }
  state.openPanel(); state.question.value = '规则问题'
  const request = state.ask(); await flush()
  state.closePanel()
  assert.equal(signal.aborted, true)
  resolve(sample); await request
  assert.equal(state.visible.value, false)
  assert.equal(state.question.value, '')
  assert.equal(state.reply.value, null)
})
