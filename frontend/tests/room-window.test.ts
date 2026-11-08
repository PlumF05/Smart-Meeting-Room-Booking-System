import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { effectScope, nextTick } from 'vue'
import type { EffectScope } from 'vue'

import { authApi } from '../src/api/auth.ts'
import { ApiError, http } from '../src/api/http.ts'
import { roomsApi } from '../src/api/rooms.ts'
import { buildRoomWindowQuery, roomTimeOptions } from '../src/composables/roomWindow.ts'
import { useRoomBrowser } from '../src/composables/useRoomBrowser.ts'
import { useAuthStore } from '../src/stores/auth.ts'
import type { CurrentUser } from '../src/types/auth.ts'
import type { RoomInfo, RoomOccupancy, RoomWindowCheck, RoomWindowQuery } from '../src/types/rooms.ts'

const query: RoomWindowQuery = { date: '2030-03-02', start_time: '10:00', end_time: '11:00' }
const emptyTime = { date: '', start_time: '', end_time: '' }
const original = { list: roomsApi.list, occupancy: roomsApi.occupancy, queryWindow: roomsApi.queryWindow, detail: roomsApi.detail, me: authApi.me, adapter: http.defaults.adapter }
let scopes: EffectScope[] = []

function rules(selected: RoomWindowQuery | null = null, serverNow = '2030-03-01T09:00:00+08:00'): RoomWindowCheck {
  return {
    timezone: 'Asia/Shanghai', server_now: serverNow, min_date: '2030-03-01', max_date: '2030-03-07',
    opens_at: '08:00', closes_at: '22:00', slot_minutes: 30, min_duration_minutes: 30, max_duration_minutes: 240,
    time_rules_passed: selected !== null, availability_checked: false,
    window: selected ? { ...selected, starts_at: `${selected.date}T${selected.start_time}:00+08:00`, ends_at: `${selected.date}T${selected.end_time}:00+08:00` } : null,
  }
}

function room(id = 1): RoomInfo {
  return { id, name: `合成会议室${id}`, location: '测试楼', capacity: 20, equipment: ['白板'], is_active: true }
}

function occupancy(id = 1): RoomOccupancy {
  return { room_id: id, available: true, is_active: true, occupancy: [], server_now: '2030-03-01T09:00:00+08:00', reserves_room: false }
}

function user(id: number): CurrentUser {
  return { id, username: `synthetic_${id}`, full_name: '时间测试用户', identity_type: 'student', role: 'user', is_active: true }
}

function createBrowser() {
  const auth = useAuthStore()
  auth.user = user(1)
  auth.status = 'authenticated'
  const scope = effectScope()
  scopes.push(scope)
  return { auth, state: scope.run(useRoomBrowser)!, scope }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((success, failure) => { resolve = success; reject = failure })
  return { promise, resolve, reject }
}

async function flush() {
  await new Promise<void>(resolve => setImmediate(resolve))
  await nextTick()
}

beforeEach(() => {
  setActivePinia(createPinia())
  scopes = []
  roomsApi.list = async () => [room()]
  roomsApi.occupancy = async () => [occupancy(1), occupancy(2)]
  roomsApi.queryWindow = async () => { throw new Error('测试未设置时间API mock，禁止网络') }
  roomsApi.detail = async () => { throw new Error('时间测试不应请求详情') }
  authApi.me = async () => { throw new Error('测试未设置身份API mock，禁止网络') }
  http.defaults.adapter = async () => { throw new Error('时间测试禁止真实网络') }
})

afterEach(() => {
  scopes.forEach(scope => scope.stop())
  roomsApi.list = original.list
  roomsApi.occupancy = original.occupancy
  roomsApi.queryWindow = original.queryWindow
  roomsApi.detail = original.detail
  authApi.me = original.me
  http.defaults.adapter = original.adapter
})

test('不填写时间继续查询资料，填写任一时间字段就必须完整', () => {
  assert.equal(buildRoomWindowQuery({}), null)
  assert.equal(buildRoomWindowQuery({ date: ' ', start_time: '', end_time: '' }), null)
  for (const partial of [
    { date: query.date }, { start_time: query.start_time }, { end_time: query.end_time },
    { date: query.date, start_time: query.start_time }, { date: query.date, end_time: query.end_time },
    { start_time: query.start_time, end_time: query.end_time },
  ]) {
    assert.throws(() => buildRoomWindowQuery(partial), (error: unknown) => error instanceof ApiError && error.message.includes('一起填写'))
  }
  assert.deepEqual(buildRoomWindowQuery({ date: ' 2030-03-02 ', start_time: '10:00 ', end_time: ' 11:00' }), query)
})

test('日期和时间保持日历字符串，不接受不存在的日期或秒及偏移量，未来规则留给服务器', () => {
  for (const date of ['2030-02-29', '2030-04-31', '2030-13-01', '2030-00-01', '0000-01-01', '2030-3-2', '2030-03-02T00:00:00Z']) {
    assert.throws(() => buildRoomWindowQuery({ ...query, date }), ApiError)
  }
  for (const start_time of ['24:00', '10:60', '1:00', '10:00:00', '10:00+08:00']) {
    assert.throws(() => buildRoomWindowQuery({ ...query, start_time }), ApiError)
  }
  assert.equal(buildRoomWindowQuery({ ...query, date: '2000-02-29' })?.date, '2000-02-29')
})

test('时刻选项由服务器开放范围和步长生成，开始不包含闭馆时刻', () => {
  const changedRules = { ...rules(), opens_at: '09:00', closes_at: '10:00', slot_minutes: 15 }
  assert.deepEqual(roomTimeOptions(changedRules, 'start'), ['09:00', '09:15', '09:30', '09:45'])
  assert.deepEqual(roomTimeOptions(changedRules, 'end'), ['09:15', '09:30', '09:45', '10:00'])
  assert.deepEqual(roomTimeOptions(null, 'start'), [])
  assert.deepEqual(roomTimeOptions({ ...changedRules, slot_minutes: 0 }, 'start'), [])
})

test('读取或刷新规则不把元数据当作校验成功，也不自动增加列表请求', async () => {
  let lists = 0
  let checks = 0
  roomsApi.list = async () => { lists += 1; return [] }
  roomsApi.queryWindow = async selected => { assert.equal(selected, undefined); checks += 1; return rules(null, `2030-03-01T0${checks}:00:00+08:00`) }
  roomsApi.occupancy = async () => { assert.fail('未填时间不得请求占用') }
  const { state } = createBrowser()
  await flush()
  await state.loadWindowRules()
  await state.loadWindowRules()
  assert.equal(checks, 2)
  assert.equal(lists, 1)
  assert.equal(state.windowRules.value?.server_now, '2030-03-01T02:00:00+08:00')
  assert.equal(state.appliedWindow.value, null)
  assert.equal(state.rulesLoading.value, false)
})

test('规则读取失败保留错误供重试，不阻断不填时间的资料查询', async () => {
  const { state } = createBrowser()
  await flush()
  roomsApi.queryWindow = async () => { throw new ApiError(503, '无法读取服务器规则') }
  await state.loadWindowRules()
  assert.equal(state.rulesError.value, '无法读取服务器规则')
  assert.equal(state.rulesLoading.value, false)
  await state.search()
  assert.equal(state.hasLoaded.value, true)
  assert.equal(state.appliedWindow.value, null)
})

test('完整时间先校验再读资料和一次全量占用，只展示成功列表对应的时间快照', async () => {
  const { state } = createBrowser()
  await flush()
  const calls: string[] = []
  const listed = deferred<RoomInfo[]>()
  roomsApi.queryWindow = async selected => { calls.push('validate'); assert.deepEqual(selected, query); return rules(selected!) }
  roomsApi.list = filters => { calls.push('list'); assert.deepEqual(filters, { attendees: 12 }); return listed.promise }
  roomsApi.occupancy = async selected => { calls.push('occupancy'); assert.deepEqual(selected, query); return [occupancy(2)] }
  Object.assign(state.draft, query, { attendees: '12' })
  const pending = state.search()
  await flush()
  assert.deepEqual(calls, ['validate', 'list'])
  assert.equal(state.appliedWindow.value, null)
  assert.equal(state.hasLoaded.value, false)
  state.draft.date = '2030-03-03'
  listed.resolve([room(2)])
  await pending
  assert.deepEqual(calls, ['validate', 'list', 'occupancy'])
  assert.equal(state.appliedWindow.value?.date, query.date)
  assert.equal(state.windowRules.value?.availability_checked, false)
  assert.equal(state.hasLoaded.value, true)
  assert.equal(state.occupancyByRoom.value[2]?.available, true)
})

test('部分时间本地拒绝，服务器拒绝过期开始时间也不继续读资料且保留表单', async () => {
  const { state } = createBrowser()
  await flush()
  let checks = 0
  roomsApi.queryWindow = async () => { checks += 1; throw new ApiError(422, '开始时间必须晚于服务器当前北京时间。') }
  roomsApi.list = async () => { assert.fail('时间无效不得查询列表') }
  state.draft.date = query.date
  await state.search()
  assert.equal(checks, 0)
  assert.ok(state.errorMessage.value.includes('一起填写'))
  Object.assign(state.draft, query)
  await state.search()
  assert.equal(checks, 1)
  assert.ok(state.errorMessage.value.includes('服务器当前北京时间'))
  assert.equal(state.draft.date, query.date)
  assert.equal(state.appliedWindow.value, null)
  assert.equal(state.hasLoaded.value, false)
})

test('日期时段每次提交重新校验，不缓存先前通过结果', async () => {
  const { state } = createBrowser()
  await flush()
  let checks = 0
  let lists = 0
  roomsApi.queryWindow = async selected => {
    checks += 1
    if (checks === 2) throw new ApiError(422, '开始时间已到，不能继续使用旧校验结果。')
    return rules(selected!)
  }
  roomsApi.list = async () => { lists += 1; return [] }
  Object.assign(state.draft, query)
  await state.search()
  assert.ok(state.appliedWindow.value)
  await state.search()
  assert.equal(checks, 2)
  assert.equal(lists, 1)
  assert.equal(state.appliedWindow.value, null)
  assert.ok(state.errorMessage.value.includes('旧校验'))
})

test('时间接口返回未通过或别的时段时，不展示错误校验或读取列表', async () => {
  const { state } = createBrowser()
  await flush()
  roomsApi.list = async () => { assert.fail('校验结果不匹配不得查询列表') }
  Object.assign(state.draft, query)
  for (const result of [rules(), rules({ ...query, start_time: '11:00' })]) {
    roomsApi.queryWindow = async () => result
    await state.search()
    assert.equal(state.appliedWindow.value, null)
    assert.ok(state.errorMessage.value.includes('不一致'))
  }
})

test('旧校验晚于新查询返回时，不能继续读列表或覆盖新时间', async () => {
  const { state } = createBrowser()
  await flush()
  const old = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = selected => selected?.date === query.date ? old.promise : Promise.resolve(rules(selected!))
  let lists = 0
  roomsApi.list = async () => { lists += 1; return [room(2)] }
  Object.assign(state.draft, query)
  const first = state.search()
  state.draft.date = '2030-03-03'
  await state.search()
  old.resolve(rules(query))
  await first
  assert.equal(lists, 1)
  assert.equal(state.appliedWindow.value?.date, '2030-03-03')
  assert.deepEqual(state.rooms.value.map(item => item.id), [2])
})

test('较早规则读取晚到时，不能覆盖新时间校验中的服务器时刻', async () => {
  const { state } = createBrowser()
  await flush()
  const pendingRules = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = selected => selected ? Promise.resolve(rules(selected, '2030-03-01T12:00:00+08:00')) : pendingRules.promise
  const metadata = state.loadWindowRules()
  Object.assign(state.draft, query)
  await state.search()
  pendingRules.resolve(rules(null, '2030-03-01T09:00:00+08:00'))
  await metadata
  assert.equal(state.windowRules.value?.server_now, '2030-03-01T12:00:00+08:00')
  assert.equal(state.rulesLoading.value, false)
})

test('较早时间校验晚到时，可完成对应资料查询但不能覆盖后发规则读取', async () => {
  const { state } = createBrowser()
  await flush()
  const earlyCheck = deferred<RoomWindowCheck>()
  const laterMetadata = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = selected => selected ? earlyCheck.promise : laterMetadata.promise
  Object.assign(state.draft, query)
  const searching = state.search()
  const metadata = state.loadWindowRules()
  laterMetadata.resolve(rules(null, '2030-03-01T10:01:00+08:00'))
  await metadata
  earlyCheck.resolve(rules(query, '2030-03-01T10:00:00+08:00'))
  await searching
  assert.equal(state.windowRules.value?.server_now, '2030-03-01T10:01:00+08:00')
  assert.equal(state.appliedWindow.value?.date, query.date)
  assert.equal(state.hasLoaded.value, true)
})

test('旧时间校验完成不能停止较新规则请求的loading', async () => {
  const { state } = createBrowser()
  await flush()
  const earlyCheck = deferred<RoomWindowCheck>()
  const laterMetadata = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = selected => selected ? earlyCheck.promise : laterMetadata.promise
  Object.assign(state.draft, query)
  const searching = state.search()
  const metadata = state.loadWindowRules()
  earlyCheck.resolve(rules(query))
  await searching
  assert.equal(state.rulesLoading.value, true)
  assert.equal(state.windowRules.value, null)
  laterMetadata.resolve(rules())
  await metadata
  assert.equal(state.rulesLoading.value, false)
})

test('后发规则读取失败的提示不能被旧时间校验成功清除', async () => {
  const { state } = createBrowser()
  await flush()
  const earlyCheck = deferred<RoomWindowCheck>()
  const laterMetadata = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = selected => selected ? earlyCheck.promise : laterMetadata.promise
  Object.assign(state.draft, query)
  const searching = state.search()
  const metadata = state.loadWindowRules()
  laterMetadata.reject(new ApiError(503, '后发规则读取失败'))
  await metadata
  earlyCheck.resolve(rules(query))
  await searching
  assert.equal(state.rulesError.value, '后发规则读取失败')
  assert.equal(state.rulesLoading.value, false)
  assert.equal(state.windowRules.value, null)
  assert.equal(state.hasLoaded.value, true)
})

test('校验中重置会清时间并读取全部资料，旧校验不能再触发列表', async () => {
  const { state } = createBrowser()
  await flush()
  const old = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = () => old.promise
  Object.assign(state.draft, query)
  const pending = state.search()
  let lists = 0
  roomsApi.list = async filters => { lists += 1; assert.deepEqual(filters, {}); return [] }
  await state.resetFilters()
  old.resolve(rules(query))
  await pending
  assert.equal(lists, 1)
  assert.deepEqual({ date: state.draft.date, start_time: state.draft.start_time, end_time: state.draft.end_time }, emptyTime)
  assert.equal(state.appliedWindow.value, null)
})

test('换号或离开页面会丢弃仍在校验的时间响应', async () => {
  const { auth, state, scope } = createBrowser()
  await flush()
  const first = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = () => first.promise
  Object.assign(state.draft, query)
  const old = state.search()
  auth.user = user(2)
  auth.sessionChangeCount += 1
  await flush()
  first.resolve(rules(query))
  await old
  assert.equal(state.draft.date, '')
  assert.equal(state.appliedWindow.value, null)
  const second = deferred<RoomWindowCheck>()
  roomsApi.queryWindow = () => second.promise
  Object.assign(state.draft, query)
  const leaving = state.search()
  roomsApi.list = async () => { assert.fail('已离开页面不得继续请求列表') }
  scope.stop()
  second.resolve(rules(query))
  await leaving
  assert.equal(state.appliedWindow.value, null)
})

test('时间校验401复核失效时清空时间条件，不继续显示资料', async () => {
  const { auth, state } = createBrowser()
  await flush()
  Object.assign(state.draft, query)
  roomsApi.queryWindow = async () => { throw new ApiError(401, '登录已失效') }
  authApi.me = async () => { throw new ApiError(401, '服务器确认会话失效') }
  roomsApi.list = async () => { assert.fail('失效身份不能查询资料') }
  await state.search()
  await flush()
  assert.equal(auth.status, 'anonymous')
  assert.equal(state.draft.date, '')
  assert.equal(state.appliedWindow.value, null)
  assert.deepEqual(state.rooms.value, [])
})

test('时间通过但资料读取失败时，不把部分结果展示为已完成查询', async () => {
  const { state } = createBrowser()
  await flush()
  Object.assign(state.draft, query)
  roomsApi.queryWindow = async selected => rules(selected!)
  roomsApi.list = async () => { throw new ApiError(503, '会议室资料读取失败') }
  await state.search()
  assert.equal(state.hasLoaded.value, false)
  assert.equal(state.appliedWindow.value, null)
  assert.equal(state.draft.date, query.date)
  assert.equal(state.errorMessage.value, '会议室资料读取失败')
})

test('占用结果保留冲突和停用房间及同房间多个占位，未填时间不携带旧状态', async () => {
  const { state } = createBrowser()
  await flush()
  roomsApi.queryWindow = async selected => rules(selected!)
  roomsApi.list = async () => [room(1), room(2), { ...room(3), is_active: false }]
  const slots = [
    { status: 'pending' as const, starts_at: '2030-03-02T10:00:00+08:00', ends_at: '2030-03-02T10:30:00+08:00' },
    { status: 'approved' as const, starts_at: '2030-03-02T10:30:00+08:00', ends_at: '2030-03-02T11:00:00+08:00' },
  ]
  let calls = 0
  roomsApi.occupancy = async () => {
    calls++
    return [{ ...occupancy(1), available: false, occupancy: slots }, occupancy(2), { ...occupancy(3), available: false, is_active: false }]
  }
  Object.assign(state.draft, query)
  await state.search()
  assert.deepEqual(state.rooms.value.map(item => item.id), [1, 2, 3])
  assert.deepEqual(state.occupancyByRoom.value[1]?.occupancy, slots)
  assert.equal(state.occupancyByRoom.value[2]?.available, true)
  assert.equal(state.occupancyByRoom.value[3]?.is_active, false)
  state.clearWindowDraft()
  await state.search()
  assert.equal(calls, 1)
  assert.deepEqual(state.occupancyByRoom.value, {})
  assert.equal(state.appliedWindow.value, null)
})

test('占用请求失败或缺少列表房间时清空上一轮结果，保留时间供重试', async () => {
  const { state } = createBrowser()
  await flush()
  roomsApi.queryWindow = async selected => rules(selected!)
  Object.assign(state.draft, query)
  await state.search()
  assert.equal(state.occupancyByRoom.value[1]?.available, true)
  roomsApi.occupancy = async () => { throw new ApiError(503, '占用读取失败') }
  await state.search()
  assert.equal(state.errorMessage.value, '占用读取失败')
  assert.equal(state.draft.date, query.date)
  assert.deepEqual(state.occupancyByRoom.value, {})
  assert.deepEqual(state.rooms.value, [])
  assert.equal(state.appliedWindow.value, null)
  assert.equal(state.hasLoaded.value, false)
  roomsApi.occupancy = async () => []
  await state.search()
  assert.match(state.errorMessage.value, /不完整/)
  assert.equal(state.hasLoaded.value, false)
})

test('旧占用成功或失败不能覆盖重置或新查询结果', async () => {
  const { state } = createBrowser()
  await flush()
  roomsApi.queryWindow = async selected => rules(selected!)
  const old = deferred<RoomOccupancy[]>()
  roomsApi.occupancy = () => old.promise
  Object.assign(state.draft, query)
  const first = state.search()
  await flush()
  await state.resetFilters()
  old.resolve([{ ...occupancy(), available: false }])
  await first
  assert.deepEqual(state.occupancyByRoom.value, {})
  assert.equal(state.appliedWindow.value, null)
  const staleFailure = deferred<RoomOccupancy[]>()
  roomsApi.occupancy = selected => selected.date === query.date ? staleFailure.promise : Promise.resolve([occupancy()])
  Object.assign(state.draft, query)
  const second = state.search()
  await flush()
  state.draft.date = '2030-03-03'
  await state.search()
  staleFailure.reject(new ApiError(401, '旧占用失效'))
  await second
  assert.equal(state.appliedWindow.value?.date, '2030-03-03')
  assert.equal(state.occupancyByRoom.value[1]?.available, true)
  assert.equal(state.errorMessage.value, '')
})

test('换号或离开页面会丢弃迟到占用响应并清除占用状态', async () => {
  const { auth, state, scope } = createBrowser()
  await flush()
  roomsApi.queryWindow = async selected => rules(selected!)
  const old = deferred<RoomOccupancy[]>()
  roomsApi.occupancy = () => old.promise
  Object.assign(state.draft, query)
  const first = state.search()
  await flush()
  auth.user = user(2)
  auth.sessionChangeCount++
  await flush()
  old.resolve([occupancy()])
  await first
  assert.deepEqual(state.occupancyByRoom.value, {})
  assert.equal(state.appliedWindow.value, null)
  const late = deferred<RoomOccupancy[]>()
  roomsApi.occupancy = () => late.promise
  Object.assign(state.draft, query)
  const second = state.search()
  await flush()
  scope.stop()
  late.resolve([occupancy()])
  await second
  assert.deepEqual(state.occupancyByRoom.value, {})
})
