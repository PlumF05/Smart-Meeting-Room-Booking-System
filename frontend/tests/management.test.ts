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

const filename = fileURLToPath(new URL('../src/views/ManagementView.vue', import.meta.url))
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
const compiled = compileScript(descriptor, {
  id: 'management-regression',
  fs: { fileExists: existsSync, readFile: path => readFileSync(path, 'utf8') },
})
const javascript = ts.transpileModule(compiled.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const moduleObject = { exports: {} as { default?: vue.Component } }
const redirects: unknown[] = []
const routeState = vue.reactive({ query: {} as Record<string, string> })
const imports: Record<string, unknown> = {
  vue, 'element-plus': elements, '../api/http.ts': api, '../stores/auth.ts': authModule,
  'vue-router': { useRoute: () => routeState, useRouter: () => ({ replace: async (route: unknown) => { redirects.push(route) } }) },
}
new Function('require', 'module', 'exports', javascript)(
  (name: string) => {
    assert.ok(name in imports, `测试禁止未声明导入：${name}`)
    return imports[name]
  }, moduleObject, moduleObject.exports,
)

// 执行实际SFC的setup，使用Vue内存renderer管理生命周期，不访问浏览器或真实服务。
const renderer = vue.createRenderer<Record<string, unknown>, Record<string, unknown>>({
  insert() {}, remove() {}, patchProp() {}, setText() {}, setElementText() {},
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  parentNode: () => null, nextSibling: () => null,
})
const originalAdapter = api.http.defaults.adapter
let auth: ReturnType<typeof authModule.useAuthStore>
let originalRestore: typeof auth.restoreSession
let apps: vue.App[] = []
let handle: (config: any) => any

const room = { id: 1, name: '测试房间', location: '测试楼', capacity: 20, equipment: ['白板'], is_active: true }
const booking = {
  id: 1, room_id: 1, title: '审批测试', purpose: '讨论', attendees: 10,
  starts_at: '2099-01-01T10:00:00+08:00', ends_at: '2099-01-01T11:00:00+08:00',
  submitted_at: '2098-12-31T10:00:00+08:00', status: 'pending', status_reason: null,
  can_approve: true, can_reject: true, can_revoke: false, required_equipment: [],
  applicant: { id: 10, username: 'student', full_name: '学生', is_active: true },
  room_snapshot: room, current_room: room,
}

function response(config: any, data: unknown) {
  return { config, data, status: 200, statusText: 'OK', headers: new AxiosHeaders() }
}

async function flush() {
  await new Promise<void>(resolve => setImmediate(resolve))
  await vue.nextTick()
}

function page() {
  let state: any
  const original = moduleObject.exports.default as { setup: Function }
  const app = renderer.createApp({
    ...original,
    setup(props: unknown, context: unknown) {
      state = original.setup(props, context)
      return () => null
    },
  })
  app.mount({})
  apps.push(app)
  return { state, app }
}

beforeEach(() => {
  setActivePinia(createPinia())
  auth = authModule.useAuthStore()
  auth.user = { id: 1, username: 'synthetic_admin', full_name: '测试管理员', role: 'admin', identity_type: null, is_active: true }
  auth.status = 'authenticated'
  auth.csrfToken = 'synthetic-csrf'
  originalRestore = auth.restoreSession
  apps = []
  redirects.length = 0
  routeState.query = {}
  handle = config => {
    assert.equal(config.method, 'get', '未设置mock的写请求不得访问真实服务')
    assert.ok(['/admin/rooms', '/admin/reservations'].includes(config.url))
    return response(config, config.url === '/admin/rooms' ? [room] : [booking])
  }
  api.http.defaults.adapter = async config => handle(config)
})

afterEach(() => {
  for (const app of apps) app.unmount()
  auth.restoreSession = originalRestore
  api.http.defaults.adapter = originalAdapter
})

test('管理员页面默认读取未审批预约和已有会议室', async () => {
  const requests: any[] = []
  const initial = handle
  handle = config => { requests.push(config); return initial(config) }
  const { state } = page()
  await flush()
  assert.equal(state.bookings.value[0].id, booking.id)
  assert.equal(state.rooms.value[0].id, room.id)
  const query = requests.find(config => config.url === '/admin/reservations')
  assert.deepEqual(query.params, { status: 'pending', room_id: undefined, sort: 'starts_at', direction: 'asc' })
  assert.equal(state.loadingBookings.value, false)
  assert.equal(state.loadingRooms.value, false)
})

test('审批未实际执行时展示服务端消息和实际状态，并发送当前CSRF', async () => {
  const { state } = page()
  await flush()
  let posts = 0
  handle = config => {
    if (config.method === 'post') {
      posts++
      assert.equal(config.headers.get('X-CSRF-Token'), 'synthetic-csrf')
      assert.equal(JSON.parse(config.data).action, 'approve')
      return response(config, { ...booking, status: 'expired', message: '预约已过期，未批准', action_applied: false })
    }
    return response(config, [])
  }
  state.openDecision(booking.id, 'approve')
  await state.submitDecision()
  await flush()
  assert.equal(posts, 1)
  assert.match(state.notice.value, /未批准.*已过期/)
  assert.equal(state.noticeType.value, 'warning')
  assert.equal(state.decision.value, null)
})

test('驳回或撤销未填写原因时保留弹窗且不发送写请求', async () => {
  const { state } = page()
  await flush()
  let writes = 0
  handle = () => { writes++; throw new Error('不应发送写请求') }
  for (const action of ['reject', 'revoke']) {
    state.openDecision(booking.id, action)
    state.decisionReason.value = '   '
    await state.submitDecision()
    assert.match(state.decisionError.value, /原因/)
    assert.ok(state.decision.value)
  }
  assert.equal(writes, 0)
})

test('审批或房间保存冲突时保留编辑内容并显示需处置的编号', async () => {
  const { state } = page()
  await flush()
  handle = () => { throw new api.ApiError(409, '请先处理预约 #1') }
  state.openDecision(booking.id, 'reject')
  state.decisionReason.value = '需要补充用途'
  await state.submitDecision()
  assert.equal(state.decisionReason.value, '需要补充用途')
  assert.ok(state.decision.value)
  assert.match(state.decisionError.value, /#1/)
  state.decision.value = null
  state.editRoom(room.id)
  state.roomDraft.capacity = '8'
  await state.saveRoom()
  assert.ok(state.editingRoom.value)
  assert.equal(state.roomDraft.capacity, '8')
  assert.match(state.roomError.value, /#1/)
})

test('管理员写请求401复核登录并保留尚未提交的处理原因', async () => {
  const { state } = page()
  await flush()
  let restored = 0
  auth.restoreSession = async () => { restored++ }
  handle = () => { throw new api.ApiError(401, '登录已失效') }
  state.openDecision(booking.id, 'reject')
  state.decisionReason.value = '仍需保留'
  await state.submitDecision()
  assert.equal(restored, 1)
  assert.equal(state.decisionReason.value, '仍需保留')
  assert.ok(state.decision.value)
  assert.deepEqual(redirects, [])
})

test('管理员页面卸载后迟到的401不能再复核或跳转登录', async () => {
  const { state, app } = page()
  await flush()
  let restored = 0
  auth.restoreSession = async () => { restored++ }
  let rejectLate!: (reason: unknown) => void
  handle = () => new Promise((_, reject) => { rejectLate = reject })
  state.openDecision(booking.id, 'approve')
  const request = state.submitDecision()
  await flush()
  app.unmount()
  apps.splice(apps.indexOf(app), 1)
  rejectLate(new api.ApiError(401, '旧登录已失效'))
  await request
  assert.equal(restored, 0)
  assert.deepEqual(redirects, [])
})

test('换账号后旧审批响应不能写入新账号的列表或成功提示', async () => {
  const { state } = page()
  await flush()
  let resolveOld!: (data: unknown) => void
  handle = config => config.method === 'post'
    ? new Promise(resolve => { resolveOld = data => resolve(response(config, data)) })
    : response(config, config.url === '/admin/rooms' ? [room] : [{ ...booking, id: 2 }])
  state.openDecision(booking.id, 'approve')
  const request = state.submitDecision()
  await flush()
  auth.user = { ...auth.user!, id: 2, username: 'another_admin' }
  await flush()
  resolveOld({ ...booking, status: 'approved', message: '已批准', action_applied: true })
  await request
  assert.equal(state.notice.value, '')
  assert.equal(state.bookings.value[0].id, 2)
  assert.equal(state.writing.value, false)
  assert.equal(state.decision.value, null)
})


test('从账号处置入口进入审批时仅查询该账号全部状态的预约', async () => {
  routeState.query = { user_id: '10' }
  let params: any
  const original = handle
  handle = config => { if (config.url === '/admin/reservations') params = config.params; return original(config) }
  const { state } = page()
  await flush()
  assert.equal(state.userFilter.value, 10)
  assert.equal(params.user_id, 10)
  assert.equal(params.status, 'all')
})
