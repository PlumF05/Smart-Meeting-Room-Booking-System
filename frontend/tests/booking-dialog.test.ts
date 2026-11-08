import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { beforeEach, afterEach, test } from 'node:test'
import * as vue from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import * as elements from 'element-plus'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { renderToString } from '@vue/server-renderer'
import { AxiosHeaders } from 'axios'
import * as api from '../src/api/http.ts'
import * as authModule from '../src/stores/auth.ts'

const filename = fileURLToPath(new URL('../src/components/BookingDialog.vue', import.meta.url))
const source = readFileSync(filename, 'utf8')
const { descriptor } = parse(source, { filename })
const compiled = compileScript(descriptor, {
  id: 'booking-regression',
  fs: { fileExists: existsSync, readFile: path => readFileSync(path, 'utf8') },
})
const javascript = ts.transpileModule(compiled.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const moduleObject = { exports: {} as { default?: vue.Component } }
const imports: Record<string, unknown> = {
  vue, 'element-plus': elements, '../api/http.ts': api, '../stores/auth.ts': authModule,
}
new Function('require', 'module', 'exports', javascript)(
  (name: string) => {
    assert.ok(name in imports, `测试禁止未声明导入：${name}`)
    return imports[name]
  }, moduleObject, moduleObject.exports,
)

// 执行实际SFC的setup，使用Vue内存renderer管理挂载/卸载生命周期；不访问浏览器或真实服务。
const renderer = vue.createRenderer<Record<string, unknown>, Record<string, unknown>>({
  insert() {}, remove() {}, patchProp() {}, setText() {}, setElementText() {},
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  parentNode: () => null, nextSibling: () => null,
})
let apps: vue.App[] = []
const originalAdapter = api.http.defaults.adapter
beforeEach(() => {
  setActivePinia(createPinia())
  const auth = authModule.useAuthStore()
  auth.user = { id: 1, username: 'synthetic', full_name: '测试用户', role: 'user', identity_type: 'student', is_active: true }
  auth.status = 'authenticated'
  auth.csrfToken = 'synthetic-csrf'
  apps = []
  api.http.defaults.adapter = async () => { throw new Error('测试不得访问真实服务') }
})
afterEach(() => {
  for (const app of apps) app.unmount()
  api.http.defaults.adapter = originalAdapter
})

function dialog() {
  let state: any
  const original = moduleObject.exports.default as { setup: Function }
  const component = {
    ...original,
    setup(props: unknown, context: unknown) {
      state = original.setup(props, context)
      return () => null
    },
  }
  const app = renderer.createApp(component, {
    room: { id: 1, name: '小会议室', location: '测试楼', capacity: 8, equipment: [], is_active: true },
    draft: vue.reactive({ title: '课程讨论', purpose: '测试', attendees: 20, date: '2026-11-09', start_time: '10:00', end_time: '11:00', required_equipment: [] }),
  })
  app.mount({})
  apps.push(app)
  return { state, app }
}

function response(config: any, data: unknown) {
  return { config, data, status: 200, statusText: 'OK', headers: new AxiosHeaders() }
}

test('换到小会议室后，实际人数控件不得自动把20人缩减到8人', async () => {
  const inputTag = descriptor.template!.content.match(/<ElInputNumber\b[^>]*>/)![0]
  const maxExpression = inputTag.match(/:max="([^"]+)"/)![1]
  const max = new Function('room', `return (${maxExpression})`)({ capacity: 8 })
  const attendees = vue.ref(20)
  await renderToString(vue.createSSRApp({
    setup: () => () => vue.h(elements.ElInputNumber, {
      modelValue: attendees.value, min: 1, max, precision: 0,
      'onUpdate:modelValue': value => { attendees.value = value! },
    }),
  }))
  assert.equal(attendees.value, 20)
})

test('重新查询失败时清除上一轮无占位提示', async () => {
  const { state } = dialog()
  api.http.defaults.adapter = async config => response(config, { available: true, is_active: true, occupancy: [] })
  await state.checkOccupancy()
  assert.match(state.occupancy.value, /无房间占位/)
  api.http.defaults.adapter = async () => { throw new api.ApiError(503, '服务暂不可用') }
  await state.checkOccupancy()
  assert.equal(state.occupancy.value, '')
  assert.match(state.message.value, /服务暂不可用/)
})

test('提交遇到冲突时清除上一轮无占位提示并保留表单', async () => {
  const { state } = dialog()
  api.http.defaults.adapter = async config => response(config, { available: true, is_active: true, occupancy: [] })
  await state.checkOccupancy()
  api.http.defaults.adapter = async () => { throw new api.ApiError(409, '所选时段已有预约') }
  await state.submit()
  assert.equal(state.occupancy.value, '')
  assert.equal(state.form.title, '课程讨论')
  assert.match(state.message.value, /所选时段已有预约/)
})

test('离开弹窗后旧查询的401不得再触发会话恢复', async () => {
  const { state, app } = dialog()
  let rejectRequest!: (reason: unknown) => void
  api.http.defaults.adapter = () => new Promise((_, reject) => { rejectRequest = reject })
  const auth = authModule.useAuthStore()
  let restored = 0
  const restore = auth.restoreSession
  auth.restoreSession = async () => { restored++ }
  try {
    const request = state.checkOccupancy()
    await Promise.resolve()
    app.unmount()
    apps.splice(apps.indexOf(app), 1)
    rejectRequest(new api.ApiError(401, '旧请求未登录'))
    await request
    assert.equal(restored, 0)
  } finally { auth.restoreSession = restore }
})
