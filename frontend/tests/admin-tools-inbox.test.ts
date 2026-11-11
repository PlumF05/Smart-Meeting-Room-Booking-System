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

const redirects: unknown[] = []
function component(name: string) {
  const filename = fileURLToPath(new URL(`../src/views/${name}.vue`, import.meta.url))
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const compiled = compileScript(descriptor, { id: name, fs: { fileExists: existsSync, readFile: path => readFileSync(path, 'utf8') } })
  const javascript = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const moduleObject = { exports: {} as { default?: vue.Component } }
  const imports: Record<string, unknown> = {
    vue, 'element-plus': elements, '../api/http.ts': api, '../stores/auth.ts': authModule,
    '../components/ListPagination.vue': { default: {} },
    'vue-router': { RouterLink: {}, useRouter: () => ({ replace: async (route: unknown) => { redirects.push(route) } }) },
  }
  new Function('require', 'module', 'exports', javascript)((key: string) => {
    assert.ok(key in imports, `禁止未声明导入：${key}`)
    return imports[key]
  }, moduleObject, moduleObject.exports)
  return moduleObject.exports.default as { setup: Function }
}
const inbox = component('InboxView')
const tools = component('AdminToolsView')
const renderer = vue.createRenderer<Record<string, unknown>, Record<string, unknown>>({
  insert() {}, remove() {}, patchProp() {}, setText() {}, setElementText() {},
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}), parentNode: () => null, nextSibling: () => null,
})
const user = { id: 8, username: 'student', full_name: '学生', identity_type: 'student', role: 'user', is_active: true, created_at: '2026-11-01T10:00:00+08:00', active_reservation_count: 2 }
const message = { id: 1, event_id: 11, title: '预约处理通知', content: '预约 #12 已批准', created_at: '2026-11-01T10:00:00+08:00', read_at: null }
let auth: ReturnType<typeof authModule.useAuthStore>
let originalRestore: typeof auth.restoreSession
const originalAdapter = api.http.defaults.adapter
let apps: vue.App[] = []
let handle: (config: any) => any
function response(config: any, data: unknown) { return { config, data, status: 200, statusText: 'OK', headers: new AxiosHeaders() } }
async function flush() { await new Promise<void>(resolve => setImmediate(resolve)); await vue.nextTick() }
function page(original: { setup: Function }) {
  let state: any
  const app = renderer.createApp({ ...original, setup(props: unknown, context: unknown) { state = original.setup(props, context); return () => null } })
  app.mount({}); apps.push(app)
  return { state, app }
}
beforeEach(() => {
  setActivePinia(createPinia())
  auth = authModule.useAuthStore()
  auth.user = { id: 1, username: 'admin', full_name: '管理员', role: 'admin', identity_type: null, is_active: true }
  auth.status = 'authenticated'; auth.csrfToken = 'test-csrf'
  originalRestore = auth.restoreSession; apps = []; redirects.length = 0
  handle = config => {
    assert.equal(config.method, 'get', '未mock的写请求不能执行')
    if (config.url === '/messages') return response(config, { items: [message], total: 1, unread_count: 1, page: 1, page_size: 20 })
    if (config.url === '/admin/users') return response(config, { items: [user, { ...user, id: 1, role: 'admin' }], total: 2, page: 1, page_size: 20 })
    if (config.url === '/admin/rooms') return response(config, [{ id: 3, name: '会议室3' }])
    if (config.url === '/admin/users/8/reservations') return response(config, [{ id: 12, status: 'approved' }])
    throw new Error(`未mock的请求：${config.url}`)
  }
  api.http.defaults.adapter = async config => handle(config)
})
afterEach(() => { for (const app of apps) app.unmount(); auth.restoreSession = originalRestore; api.http.defaults.adapter = originalAdapter })

test('消息读取使用筛选分页，标记已读发送CSRF且仅在服务端确认后变更', async () => {
  const { state } = page(inbox); await flush()
  const initial = handle
  let writes = 0
  handle = config => {
    if (config.method === 'post') {
      writes++; assert.equal(config.url, '/messages/1/read')
      assert.equal(config.headers.get('X-CSRF-Token'), 'test-csrf'); assert.deepEqual(JSON.parse(config.data), {})
      return response(config, { id: 1, read_at: '2026-11-08T10:00:00+08:00' })
    }
    assert.deepEqual(config.params, { page: 1, page_size: 20, unread_only: true })
    return response(config, { items: [], total: 0, unread_count: 0, page: 1, page_size: 20 })
  }
  state.unreadOnly.value = true; state.openMessage(1)
  await state.markRead(); await flush()
  assert.equal(writes, 1); assert.ok(state.selected.value.read_at); assert.equal(state.unreadCount.value, 0)
  assert.deepEqual(state.messages.value, [])
  handle = initial
})
test('已读失败保留消息内容；401复核登录；卸载后迟到响应不能复核', async () => {
  const { state, app } = page(inbox); await flush()
  let restored = 0; auth.restoreSession = async () => { restored++ }
  handle = () => { throw new api.ApiError(401, '请重新登录') }
  state.openMessage(1); await state.markRead()
  assert.equal(restored, 1); assert.equal(state.selected.value.content, message.content)
  assert.equal(state.selected.value.read_at, null); assert.match(state.readError.value, /重新登录/)
  let rejectLate!: (reason: unknown) => void
  handle = () => new Promise((_, reject) => { rejectLate = reject })
  const request = state.markRead(); await flush()
  app.unmount(); apps.splice(apps.indexOf(app), 1)
  rejectLate(new api.ApiError(401, '过期')); await request
  assert.equal(restored, 1); assert.deepEqual(redirects, [])
})
test('换号后迟到消息列表不能覆盖新账号消息', async () => {
  let resolveOld!: (value: unknown) => void
  handle = config => new Promise(resolve => { resolveOld = data => resolve(response(config, data)) })
  const { state } = page(inbox); await flush()
  handle = config => response(config, { items: [{ ...message, id: 2 }], total: 1, unread_count: 1, page: 1, page_size: 20 })
  auth.user = { ...auth.user!, id: 2 }; await flush()
  resolveOld({ items: [message], total: 1, unread_count: 1, page: 1, page_size: 20 }); await flush()
  assert.equal(state.messages.value[0].id, 2)
})
test('账号启停禁止管理员操作，失败保留确认内容与预约数', async () => {
  const { state } = page(tools); await flush()
  state.openStateChange(1); assert.equal(state.changingUser.value, null)
  state.openStateChange(8); assert.equal(state.changingUser.value.active_reservation_count, 2)
  handle = () => { throw new api.ApiError(409, '账号状态已变更') }
  await state.changeUserState()
  assert.equal(state.changingUser.value.id, 8); assert.equal(state.changingUser.value.is_active, true)
  assert.equal(state.notice.value, ''); assert.match(state.stateError.value, /已变更/)
})
test('停用成功发送CSRF，显示已有预约并保留预约编号供处置', async () => {
  const { state } = page(tools); await flush()
  const initial = handle
  handle = config => {
    if (config.method !== 'patch') return initial(config)
    assert.equal(config.url, '/admin/users/8/state'); assert.equal(config.headers.get('X-CSRF-Token'), 'test-csrf')
    assert.deepEqual(JSON.parse(config.data), { is_active: false })
    return response(config, { ...user, is_active: false })
  }
  state.openStateChange(8); await state.changeUserState(); await flush()
  assert.equal(state.changingUser.value, null); assert.equal(state.users.value[0].is_active, false)
  assert.match(state.notice.value, /已有预约未取消/); assert.equal(state.reservationUser.value.id, 8)
  assert.equal(state.reservations.value[0].id, 12)
})
test('换号后账号启停旧响应不能显示成功或覆盖新账号数据', async () => {
  const { state } = page(tools); await flush()
  const initial = handle
  let resolveOld!: (value: unknown) => void
  handle = config => config.method === 'patch' ? new Promise(resolve => { resolveOld = data => resolve(response(config, data)) }) : initial(config)
  state.openStateChange(8); const request = state.changeUserState(); await flush()
  auth.user = { ...auth.user!, id: 2, username: 'another_admin' }; await flush()
  resolveOld({ ...user, is_active: false }); await request
  assert.equal(state.notice.value, ''); assert.equal(state.changingUser.value, null)
  assert.equal(state.users.value[0].is_active, true); assert.equal(state.reservationUser.value, null)
})
test('统计按明确提交日期和房间请求；无效日期或对象编号不发送请求', async () => {
  const { state } = page(tools); await flush()
  let requests = 0
  handle = config => {
    requests++; assert.equal(config.method, 'get'); assert.equal(config.url, '/admin/statistics')
    assert.deepEqual(config.params, { start_date: '2026-11-01', end_date: '2026-11-08', room_id: 3 })
    return response(config, { total: 3, counts: { pending: 1, approved: 2 }, query_time: '2026-11-08T10:00:00+08:00', definition: '按提交日期筛选，按当前状态统计', start_date: '2026-11-01', end_date: '2026-11-08' })
  }
  Object.assign(state.statisticsFilter, { start_date: '2026-11-01', end_date: '2026-11-08', room_id: 3 })
  await state.loadStatistics(); assert.equal(state.statistics.value.total, 3); assert.equal(state.statisticsRoom.value, '会议室3')
  state.statisticsFilter.start_date = '2026-11-09'; await state.loadStatistics(); assert.match(state.statisticsError.value, /开始日期/)
  state.operationFilter.object_id = '1.5'; await state.loadOperations(); assert.match(state.operationsError.value, /正整数/)
  assert.equal(requests, 1)
})
test('操作记录仅GET按对象和分页筛选，保留服务端前后状态和原因', async () => {
  const { state } = page(tools); await flush()
  handle = config => {
    assert.equal(config.method, 'get'); assert.equal(config.url, '/admin/operations')
    assert.deepEqual(config.params, { page: 2, page_size: 20, object_type: 'user', object_id: 8 })
    return response(config, { items: [{ id: 31, actor_kind: 'user', actor_user_id: 1, object_type: 'user', object_id: 8, action: 'disable', source: 'page', before_status: 'active', after_status: 'inactive', result: 'success', reason: '核实后停用', created_at: message.created_at }], total: 21, page: 2, page_size: 20 })
  }
  Object.assign(state.operationFilter, { object_type: 'user', object_id: '8' }); state.operationPage.value = 2
  await state.loadOperations()
  assert.equal(state.operationTotal.value, 21); assert.equal(state.operations.value[0].before_status, 'active')
  assert.equal(state.operations.value[0].after_status, 'inactive'); assert.equal(state.operations.value[0].reason, '核实后停用')
})
