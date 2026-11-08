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
import * as browserModule from '../src/composables/useRoomBrowser.ts'
import * as windowModule from '../src/composables/roomWindow.ts'
import { roomsApi } from '../src/api/rooms.ts'
import type { RoomWindowCheck } from '../src/types/rooms.ts'

const renderer = vue.createRenderer<Record<string, unknown>, Record<string, unknown>>({
  insert() {}, remove() {}, patchProp() {}, setText() {}, setElementText() {},
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  parentNode: () => null, nextSibling: () => null,
})
const imports: Record<string, unknown> = {
  vue, 'element-plus': elements, '../api/http.ts': api, '../stores/auth.ts': authModule,
  '../composables/useRoomBrowser.ts': browserModule, '../composables/roomWindow.ts': windowModule,
  '../components/BookingDialog.vue': { default: {} },
  'vue-router': { useRouter: () => ({ replace: async () => {}, push: async () => {} }) },
}
function loadView(name: string) {
  const filename = fileURLToPath(new URL(`../src/views/${name}.vue`, import.meta.url))
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const compiled = compileScript(descriptor, {
    id: `stage6-${name}`, fs: { fileExists: existsSync, readFile: path => readFileSync(path, 'utf8') },
  })
  const javascript = ts.transpileModule(compiled.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} as { default?: { setup: Function } } }
  new Function('require', 'module', 'exports', javascript)((name: string) => {
    assert.ok(name in imports, `测试禁止未声明导入：${name}`)
    return imports[name]
  }, module, module.exports)
  return module.exports.default!
}
const views = { rooms: loadView('RoomsView'), reservations: loadView('ReservationsView') }
let apps: vue.App[] = []
let auth: ReturnType<typeof authModule.useAuthStore>
let originalRestore: typeof auth.restoreSession
const original = { adapter: api.http.defaults.adapter, list: roomsApi.list, queryWindow: roomsApi.queryWindow }
const room = { id: 1, name: '测试会议室', location: '测试楼', capacity: 20, equipment: ['白板'], is_active: true }
const window = { date: '2030-03-02', start_time: '10:00', end_time: '11:00', starts_at: '2030-03-02T10:00:00+08:00', ends_at: '2030-03-02T11:00:00+08:00' }
const rules: RoomWindowCheck = {
  timezone: 'Asia/Shanghai', server_now: '2030-03-01T09:00:00+08:00', min_date: '2030-03-01', max_date: '2030-03-07',
  opens_at: '08:00', closes_at: '22:00', slot_minutes: 30, min_duration_minutes: 30, max_duration_minutes: 240,
  time_rules_passed: false, availability_checked: false, window: null,
}
const booking = {
  id: 1, title: '催办测试', purpose: '课程讨论', attendees: 10, status: 'pending', status_reason: null,
  starts_at: window.starts_at, ends_at: window.ends_at, can_cancel: true, can_remind: true, has_reminded: false,
  required_equipment: [], room_snapshot: room, current_room: room,
}
function response(config: any, data: unknown) {
  return { config, data, status: 200, statusText: 'OK', headers: new AxiosHeaders() }
}
async function flush() {
  await new Promise<void>(resolve => setImmediate(resolve))
  await vue.nextTick()
}
function page(name: keyof typeof views) {
  let state: any
  const original = views[name]
  const app = renderer.createApp({
    ...original,
    setup(props: unknown, context: unknown) { state = original.setup(props, context); return () => null },
  })
  app.mount({})
  apps.push(app)
  return { state, app }
}
beforeEach(() => {
  setActivePinia(createPinia())
  auth = authModule.useAuthStore()
  auth.user = { id: 1, username: 'synthetic', full_name: '测试用户', role: 'user', identity_type: 'student', is_active: true }
  auth.status = 'authenticated'; auth.csrfToken = 'synthetic-csrf'
  originalRestore = auth.restoreSession
  apps = []
  roomsApi.list = async () => [room, { ...room, id: 2 }]
  roomsApi.queryWindow = async () => rules
  api.http.defaults.adapter = async config => {
    assert.equal(config.method, 'get', '未设置mock的写请求不得访问服务')
    assert.equal(config.url, '/reservations')
    return response(config, [{ ...booking }])
  }
})
afterEach(() => {
  apps.forEach(app => app.unmount())
  auth.restoreSession = originalRestore
  api.http.defaults.adapter = original.adapter
  roomsApi.list = original.list; roomsApi.queryWindow = original.queryWindow
})

test('空白预约草稿继承已应用时间人数设备，保留失败草稿并复制设备数组', async () => {
  const { state } = page('rooms')
  await flush()
  state.appliedWindow.value = window
  state.appliedFilters.value = { attendees: 12, equipment: ['白板'] }
  state.occupancyByRoom.value = { 1: { is_active: true, available: true }, 2: { is_active: true, available: true } }
  state.draft.attendees = '99'; state.draft.date = '2030-03-03'
  state.openBooking(1)
  assert.equal(state.bookingDraft.date, window.date)
  assert.equal(state.bookingDraft.start_time, window.start_time)
  assert.equal(state.bookingDraft.end_time, window.end_time)
  assert.equal(state.bookingDraft.attendees, 12)
  assert.deepEqual(state.bookingDraft.required_equipment, ['白板'])
  state.bookingDraft.title = '失败后保留'; state.bookingDraft.attendees = 25
  state.bookingDraft.required_equipment.push('投影仪')
  assert.deepEqual(state.appliedFilters.value.equipment, ['白板'])
  state.selectedRoom.value = null
  state.appliedWindow.value = { ...window, date: '2030-03-04' }
  state.openBooking(2)
  assert.equal(state.selectedRoom.value.id, 2)
  assert.equal(state.bookingDraft.title, '失败后保留')
  assert.equal(state.bookingDraft.date, window.date)
  assert.equal(state.bookingDraft.attendees, 25)
})

test('冲突或停用的行不能打开预约，无时段仍可打开基础资料中的启用房间', async () => {
  const { state } = page('rooms')
  await flush()
  state.appliedWindow.value = window
  for (const occupancy of [{ is_active: true, available: false }, { is_active: false, available: false }]) {
    state.occupancyByRoom.value = { 1: occupancy }
    assert.equal(state.canBookRoom(room), false)
    state.openBooking(1)
    assert.equal(state.selectedRoom.value, null)
  }
  assert.equal(state.canBookRoom({ ...room, is_active: false }), false)
  state.appliedWindow.value = null
  state.openBooking(1)
  assert.equal(state.selectedRoom.value.id, 1)
})

test('催办发送空JSON和当前CSRF，处理中阻止重复，显示服务端消息并刷新本人列表', async () => {
  const { state } = page('reservations')
  await flush()
  let writes = 0, reads = 0
  let resolveWrite!: (value: unknown) => void
  api.http.defaults.adapter = async config => {
    if (config.method === 'post') {
      writes++
      assert.equal(config.url, '/reservations/1/remind')
      assert.equal(config.headers.get('X-CSRF-Token'), 'synthetic-csrf')
      assert.deepEqual(JSON.parse(config.data), {})
      return new Promise(resolve => { resolveWrite = data => resolve(response(config, data)) })
    }
    reads++
    return response(config, [{ ...booking, has_reminded: true, can_remind: false }])
  }
  const request = state.remind(1)
  await flush()
  assert.equal(state.remindingId.value, 1)
  await state.remind(1)
  assert.equal(writes, 1)
  resolveWrite({ id: 1, has_reminded: true, message: '已催办，请勿重复发送' })
  await request
  assert.equal(state.notice.value, '已催办，请勿重复发送')
  assert.equal(reads, 1)
  assert.equal(state.rows.value[0].has_reminded, true)
  assert.equal(state.remindingId.value, null)
  await state.remind(1)
  assert.equal(writes, 1)
})

test('按钮权限以服务端can_remind及has_reminded为准，失败可重试并保留实际错误', async () => {
  const { state } = page('reservations')
  await flush()
  let writes = 0
  api.http.defaults.adapter = async () => { writes++; throw new api.ApiError(503, '催办暂不可用') }
  state.rows.value[0].can_remind = false
  await state.remind(1)
  state.rows.value[0].can_remind = true; state.rows.value[0].has_reminded = true
  await state.remind(1)
  assert.equal(writes, 0)
  state.rows.value[0].has_reminded = false
  await state.remind(1)
  assert.equal(writes, 1)
  assert.equal(state.error.value, '催办暂不可用')
  assert.equal(state.remindingId.value, null)
  await state.remind(1)
  assert.equal(writes, 2)
})

test('换号后迟到催办成功不得写入新账号提示或记录', async () => {
  const { state } = page('reservations')
  await flush()
  let resolveWrite!: (value: unknown) => void
  api.http.defaults.adapter = async config => config.method === 'post'
    ? new Promise(resolve => { resolveWrite = data => resolve(response(config, data)) })
    : response(config, [{ ...booking, id: 2 }])
  const request = state.remind(1)
  await flush()
  auth.user = { ...auth.user!, id: 2 }
  await flush()
  resolveWrite({ id: 1, has_reminded: true, message: '催办已发送' })
  await request
  assert.equal(state.notice.value, '')
  assert.equal(state.rows.value[0].id, 2)
  assert.equal(state.rows.value[0].has_reminded, false)
  assert.equal(state.remindingId.value, null)
})

test('离开页面后迟到催办401不得重新核对会话', async () => {
  const { state, app } = page('reservations')
  await flush()
  let rejectWrite!: (reason: unknown) => void
  let restored = 0
  auth.restoreSession = async () => { restored++ }
  api.http.defaults.adapter = () => new Promise((_, reject) => { rejectWrite = reject })
  const request = state.remind(1)
  await flush()
  app.unmount(); apps.splice(apps.indexOf(app), 1)
  rejectWrite(new api.ApiError(401, '旧会话已失效'))
  await request
  assert.equal(restored, 0)
  assert.equal(state.error.value, '')
})
