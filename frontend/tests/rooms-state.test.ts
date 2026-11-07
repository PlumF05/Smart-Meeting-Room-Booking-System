import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { effectScope, nextTick } from 'vue'
import type { EffectScope } from 'vue'

import { authApi } from '../src/api/auth.ts'
import { ApiError, http } from '../src/api/http.ts'
import { roomsApi } from '../src/api/rooms.ts'
import { buildRoomFilters, useRoomBrowser } from '../src/composables/useRoomBrowser.ts'
import type { RoomFilterDraft } from '../src/composables/useRoomBrowser.ts'
import { useAuthStore } from '../src/stores/auth.ts'
import type { SessionInfo } from '../src/types/auth.ts'
import type { RoomFilters, RoomInfo } from '../src/types/rooms.ts'

const originals = {
  list: roomsApi.list, detail: roomsApi.detail, me: authApi.me,
  login: authApi.login, adapter: http.defaults.adapter,
}
let scopes: EffectScope[] = []

function room(id: number, equipment = ['白板'], isActive = true): RoomInfo {
  return {
    id, name: `隔离测试房间${id}`, location: `测试楼${id}`, capacity: 20,
    equipment, is_active: isActive,
  }
}

function sessionInfo(id: number): SessionInfo {
  return {
    user: {
      id, username: `synthetic_student_${id}`, full_name: '状态测试学生',
      identity_type: 'student', role: 'user', is_active: true,
    },
    csrf_token: `synthetic-csrf-${id}`, expires_at: '2099-01-01T00:00:00Z',
  }
}

function authenticate(id = 1) {
  const auth = useAuthStore()
  auth.user = sessionInfo(id).user
  auth.status = 'authenticated'
  return auth
}

function browser() {
  const scope = effectScope()
  scopes.push(scope)
  const state = scope.run(useRoomBrowser)!
  return { state, scope }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((success, failure) => { resolve = success; reject = failure })
  return { promise, resolve, reject }
}

async function flush() {
  // 完成当前微任务和Vue watcher队列，不依赖计时等待或真实网络。
  await new Promise<void>(resolve => setImmediate(resolve))
  await nextTick()
}

beforeEach(() => {
  setActivePinia(createPinia())
  scopes = []
  roomsApi.list = async () => { throw new Error('禁止未设置mock的列表请求') }
  roomsApi.detail = async () => { throw new Error('禁止未设置mock的详情请求') }
  authApi.me = async () => { throw new Error('禁止未设置mock的会话请求') }
  authApi.login = async () => { throw new Error('禁止未设置mock的登录请求') }
  http.defaults.adapter = async () => { throw new Error('状态测试禁止访问真实服务') }
})

afterEach(() => {
  for (const scope of scopes) scope.stop()
  roomsApi.list = originals.list
  roomsApi.detail = originals.detail
  authApi.me = originals.me
  authApi.login = originals.login
  http.defaults.adapter = originals.adapter
})

test('筛选把人数转为整数、设备去空白去重，并保留停用false', () => {
  assert.deepEqual(buildRoomFilters({
    attendees: ' 020 ', equipment: [' 投影仪 ', '白板', '投影仪'], state: 'inactive',
  }), { attendees: 20, equipment: ['投影仪', '白板'], is_active: false })
  assert.deepEqual(buildRoomFilters({ attendees: ' ', equipment: [], state: 'all' }), {})
  assert.deepEqual(buildRoomFilters({ attendees: '1', equipment: [], state: 'active' }), {
    attendees: 1, is_active: true,
  })
})

test('人数拒绝零、负数、小数、指数、非数字和数据库整数上界之外的值', () => {
  for (const attendees of ['0', '-1', '1.5', '+2', '2e3', 'abc', '2147483648', '9999999999999999999']) {
    assert.throws(
      () => buildRoomFilters({ attendees, equipment: [], state: 'all' }),
      (error: unknown) => error instanceof ApiError && error.status === 422,
      attendees,
    )
  }
  assert.equal(buildRoomFilters({ attendees: '2147483647', equipment: [], state: 'all' }).attendees, 2147483647)
})

test('设备限制与后端字符计数一致，接受50个非BMP字符并拒绝51个', () => {
  const draft: RoomFilterDraft = { attendees: '', equipment: ['😀'.repeat(50)], state: 'all' }
  assert.deepEqual(buildRoomFilters(draft).equipment, draft.equipment)
  for (const equipment of [[' '], ['设'.repeat(51)], ['😀'.repeat(51)], Array.from({ length: 11 }, (_, i) => `设备${i}`)]) {
    assert.throws(() => buildRoomFilters({ ...draft, equipment }), ApiError)
  }
})

test('匿名状态不自动请求会议室，也不能发起详情请求', async () => {
  let requests = 0
  roomsApi.list = async () => { requests += 1; return [] }
  roomsApi.detail = async () => { requests += 1; return room(1) }
  const { state } = browser()
  await state.search()
  await state.openDetail(1)
  assert.equal(requests, 0)
  assert.equal(state.loading.value, false)
  assert.equal(state.detailOpen.value, false)
})

test('已登录时自动加载真实返回值，加载中与空结果有不同状态', async () => {
  authenticate()
  const pending = deferred<RoomInfo[]>()
  const calls: RoomFilters[] = []
  roomsApi.list = filters => { calls.push(filters ?? {}); return pending.promise }
  const { state } = browser()
  assert.equal(state.loading.value, true)
  assert.equal(state.hasLoaded.value, false)
  assert.deepEqual(calls, [{}])
  pending.resolve([room(1, ['白板', '投影仪']), room(2, ['白板'], false)])
  await flush()
  assert.deepEqual(state.rooms.value.map(item => item.id), [1, 2])
  assert.equal(state.rooms.value[1]?.is_active, false)
  assert.deepEqual(state.equipmentOptions.value, ['白板', '投影仪'])
  assert.equal(state.loading.value, false)
  assert.equal(state.hasLoaded.value, true)
  roomsApi.list = async () => []
  await state.search()
  assert.deepEqual(state.rooms.value, [])
  assert.equal(state.hasLoaded.value, true)
  assert.equal(state.errorMessage.value, '')
})

test('提交筛选使用条件快照，不把随后编辑的草稿显示成已执行条件', async () => {
  authenticate()
  roomsApi.list = async () => [room(1)]
  const { state } = browser()
  await flush()
  const pending = deferred<RoomInfo[]>()
  let received: RoomFilters | undefined
  roomsApi.list = filters => { received = filters; return pending.promise }
  state.draft.attendees = '20'
  state.draft.equipment = [' 投影仪 ', '白板']
  state.draft.state = 'inactive'
  const searching = state.search()
  state.draft.attendees = '30'
  state.draft.equipment.push('音响')
  assert.deepEqual(received, { attendees: 20, equipment: ['投影仪', '白板'], is_active: false })
  pending.resolve([room(2, ['投影仪', '白板'], false)])
  await searching
  assert.deepEqual(state.appliedFilters.value, received)
  assert.equal(state.draft.attendees, '30')
})

test('非法输入不请求接口，也不继续展示上一批结果', async () => {
  authenticate()
  let requests = 0
  roomsApi.list = async () => { requests += 1; return [room(1)] }
  const { state } = browser()
  await flush()
  state.draft.attendees = '0'
  await state.search()
  assert.equal(requests, 1)
  assert.deepEqual(state.rooms.value, [])
  assert.equal(state.hasLoaded.value, false)
  assert.ok(state.errorMessage.value.includes('人数'))
  assert.equal(state.draft.attendees, '0')
  assert.equal(state.loading.value, false)
})

test('查询失败保留草稿可重试，重置清除条件并重新读取全部资料', async () => {
  authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  state.draft.attendees = '12'
  state.draft.equipment = ['白板']
  state.draft.state = 'active'
  roomsApi.list = async () => { throw new ApiError(503, '服务暂不可用，请稍后重试。') }
  await state.search()
  assert.ok(state.errorMessage.value.includes('服务暂不可用'))
  assert.equal(state.draft.attendees, '12')
  assert.equal(state.hasLoaded.value, false)
  assert.equal(state.loading.value, false)
  const calls: RoomFilters[] = []
  roomsApi.list = async filters => { calls.push(filters ?? {}); return [room(3)] }
  await state.search()
  assert.equal(state.errorMessage.value, '')
  assert.deepEqual(calls[0], { attendees: 12, equipment: ['白板'], is_active: true })
  await state.resetFilters()
  assert.deepEqual(state.draft, { attendees: '', equipment: [], state: 'all', date: '', start_time: '', end_time: '' })
  assert.deepEqual(calls[1], {})
  assert.deepEqual(state.rooms.value.map(item => item.id), [3])
})

test('较早的列表成功响应不能覆盖更新查询的结果', async () => {
  authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  const old = deferred<RoomInfo[]>()
  const latest = deferred<RoomInfo[]>()
  roomsApi.list = filters => filters?.attendees === 10 ? old.promise : latest.promise
  state.draft.attendees = '10'
  const first = state.search()
  state.draft.attendees = '20'
  const second = state.search()
  latest.resolve([room(2)])
  await second
  old.resolve([room(1)])
  await first
  assert.deepEqual(state.rooms.value.map(item => item.id), [2])
  assert.equal(state.appliedFilters.value.attendees, 20)
  assert.equal(state.loading.value, false)
})

test('旧请求失败不能关闭新请求的加载状态或留下错误提示', async () => {
  authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  const old = deferred<RoomInfo[]>()
  const latest = deferred<RoomInfo[]>()
  let calls = 0
  roomsApi.list = () => ++calls === 1 ? old.promise : latest.promise
  const first = state.search()
  const second = state.search()
  old.reject(new ApiError(503, '旧查询失败'))
  await first
  assert.equal(state.loading.value, true)
  assert.equal(state.errorMessage.value, '')
  latest.resolve([room(2)])
  await second
  assert.equal(state.loading.value, false)
})

test('详情A晚于详情B返回时继续显示B', async () => {
  authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  const firstRoom = deferred<RoomInfo>()
  const secondRoom = deferred<RoomInfo>()
  roomsApi.detail = id => id === 1 ? firstRoom.promise : secondRoom.promise
  const first = state.openDetail(1)
  assert.equal(state.detailOpen.value, true)
  assert.equal(state.detailLoading.value, true)
  const second = state.openDetail(2)
  secondRoom.resolve(room(2, ['白板'], false))
  await second
  firstRoom.resolve(room(1))
  await first
  assert.equal(state.detail.value?.id, 2)
  assert.equal(state.detail.value?.is_active, false)
  assert.equal(state.detailId.value, 2)
  assert.equal(state.detailLoading.value, false)
})

test('关闭详情后迟到的结果不能重新打开或填充弹窗', async () => {
  authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  const pending = deferred<RoomInfo>()
  roomsApi.detail = () => pending.promise
  const request = state.openDetail(1)
  state.closeDetail()
  pending.resolve(room(1))
  await request
  assert.equal(state.detailOpen.value, false)
  assert.equal(state.detail.value, null)
  assert.equal(state.detailId.value, null)
  assert.equal(state.detailLoading.value, false)
})

test('详情失败保留重试目标，不改动列表筛选；重新请求成功可显示', async () => {
  authenticate()
  roomsApi.list = async () => [room(1)]
  const { state } = browser()
  await flush()
  state.draft.attendees = '8'
  roomsApi.detail = async () => { throw new ApiError(404, '会议室不存在。') }
  await state.openDetail(1)
  assert.equal(state.detailId.value, 1)
  assert.equal(state.detailLoading.value, false)
  assert.equal(state.detailError.value, '会议室不存在。')
  assert.equal(state.draft.attendees, '8')
  assert.equal(state.rooms.value.length, 1)
  roomsApi.detail = async id => room(id)
  await state.openDetail(state.detailId.value!)
  assert.equal(state.detail.value?.id, 1)
  assert.equal(state.detailError.value, '')
})

test('详情接口返回其他编号时不展示错误房间', async () => {
  authenticate()
  roomsApi.list = async () => []
  roomsApi.detail = async () => room(2)
  const { state } = browser()
  await flush()
  await state.openDetail(1)
  assert.equal(state.detail.value, null)
  assert.ok(state.detailError.value.includes('信息不一致'))
})

test('新查询会关闭旧详情并丢弃尚未到达的详情响应', async () => {
  authenticate()
  roomsApi.list = async () => [room(1)]
  const { state } = browser()
  await flush()
  const pending = deferred<RoomInfo>()
  roomsApi.detail = () => pending.promise
  const opening = state.openDetail(1)
  await state.search()
  pending.resolve(room(1))
  await opening
  assert.equal(state.detailOpen.value, false)
  assert.equal(state.detail.value, null)
})

test('换账号清除旧条件和详情，旧响应不能污染新账号的列表', async () => {
  const auth = authenticate()
  roomsApi.list = async () => [room(1, ['旧设备'])]
  const { state } = browser()
  await flush()
  state.draft.attendees = '12'
  state.draft.equipment = ['旧设备']
  state.draft.state = 'inactive'
  const oldList = deferred<RoomInfo[]>()
  const newList = deferred<RoomInfo[]>()
  const oldDetail = deferred<RoomInfo>()
  roomsApi.list = () => auth.user?.id === 1 ? oldList.promise : newList.promise
  roomsApi.detail = () => oldDetail.promise
  const searching = state.search()
  const opening = state.openDetail(1)
  auth.user = sessionInfo(2).user
  auth.sessionChangeCount += 1
  oldList.resolve([room(99)])
  oldDetail.resolve(room(99))
  await Promise.all([searching, opening])
  await nextTick()
  assert.deepEqual(state.draft, { attendees: '', equipment: [], state: 'all', date: '', start_time: '', end_time: '' })
  assert.deepEqual(state.rooms.value, [])
  assert.deepEqual(state.equipmentOptions.value, [])
  assert.equal(state.detailOpen.value, false)
  assert.equal(state.detail.value, null)
  assert.equal(state.loading.value, true)
  newList.resolve([room(2, ['新设备'])])
  await flush()
  assert.deepEqual(state.rooms.value.map(item => item.id), [2])
  assert.deepEqual(state.equipmentOptions.value, ['新设备'])
})

test('同账号重新登录也清理上一登录的条件和详情', async () => {
  const auth = authenticate()
  roomsApi.list = async () => [room(1)]
  roomsApi.detail = async () => room(1)
  const { state } = browser()
  await flush()
  state.draft.attendees = '8'
  await state.openDetail(1)
  authApi.login = async () => sessionInfo(1)
  await auth.login({ username: 'synthetic_student_1', password: 'synthetic-test-password' })
  await flush()
  assert.deepEqual(state.draft, { attendees: '', equipment: [], state: 'all', date: '', start_time: '', end_time: '' })
  assert.equal(state.detailOpen.value, false)
  assert.equal(state.detail.value, null)
})

test('离开页面销毁scope后，迟到列表和详情均不能写回状态', async () => {
  authenticate()
  const pendingList = deferred<RoomInfo[]>()
  const pendingDetail = deferred<RoomInfo>()
  roomsApi.list = () => pendingList.promise
  roomsApi.detail = () => pendingDetail.promise
  const { state, scope } = browser()
  const opening = state.openDetail(1)
  scope.stop()
  pendingList.resolve([room(1)])
  pendingDetail.resolve(room(1))
  await opening
  await flush()
  assert.deepEqual(state.rooms.value, [])
  assert.equal(state.hasLoaded.value, false)
  assert.equal(state.detailOpen.value, false)
  assert.equal(state.detail.value, null)
})

test('查询401先重新核对会话，确认失效后清理账号和查询条件', async () => {
  const auth = authenticate()
  roomsApi.list = async () => [room(1)]
  const { state } = browser()
  await flush()
  state.draft.attendees = '12'
  let meCalls = 0
  roomsApi.list = async () => { throw new ApiError(401, '登录已失效') }
  authApi.me = async () => { meCalls += 1; throw new ApiError(401, '会话已失效') }
  await state.search()
  await flush()
  assert.equal(meCalls, 1)
  assert.equal(auth.status, 'anonymous')
  assert.equal(auth.user, null)
  assert.deepEqual(state.draft, { attendees: '', equipment: [], state: 'all', date: '', start_time: '', end_time: '' })
  assert.deepEqual(state.rooms.value, [])
  assert.equal(state.loading.value, false)
})

test('查询401但当前会话有效时保留身份和条件，提示重新查询', async () => {
  const auth = authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  state.draft.attendees = '12'
  roomsApi.list = async () => { throw new ApiError(401, '旧登录信息已失效') }
  authApi.me = async () => sessionInfo(1)
  await state.search()
  assert.equal(auth.isAuthenticated, true)
  assert.equal(state.draft.attendees, '12')
  assert.ok(state.errorMessage.value.includes('重新查询'))
})

test('新账号登录后才返回的旧查询401不会再次核对或清除新登录', async () => {
  const auth = authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  const oldList = deferred<RoomInfo[]>()
  roomsApi.list = () => auth.user?.id === 1 ? oldList.promise : Promise.resolve([room(2)])
  let meCalls = 0
  authApi.me = async () => { meCalls += 1; throw new ApiError(401, '不该发生的旧会话查询') }
  const oldRequest = state.search()
  authApi.login = async () => sessionInfo(2)
  await auth.login({ username: 'synthetic_student_2', password: 'synthetic-test-password' })
  oldList.reject(new ApiError(401, '旧查询已失效'))
  await oldRequest
  await flush()
  assert.equal(auth.user?.id, 2)
  assert.equal(auth.isAuthenticated, true)
  assert.equal(meCalls, 0)
  assert.deepEqual(state.rooms.value.map(item => item.id), [2])
  assert.equal(state.errorMessage.value, '')
})

test('401触发的会话核对晚于新登录失败时，也不能清除新账号', async () => {
  const auth = authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  const pendingMe = deferred<SessionInfo>()
  roomsApi.list = async () => {
    if (auth.user?.id === 1) throw new ApiError(401, '旧会话已失效')
    return [room(2)]
  }
  authApi.me = () => pendingMe.promise
  const oldRequest = state.search()
  await flush()
  assert.equal(auth.restoring, true)
  authApi.login = async () => sessionInfo(2)
  await auth.login({ username: 'synthetic_student_2', password: 'synthetic-test-password' })
  pendingMe.reject(new ApiError(401, '旧会话核对失败'))
  await oldRequest
  await flush()
  assert.equal(auth.user?.id, 2)
  assert.equal(auth.isAuthenticated, true)
  assert.deepEqual(state.rooms.value.map(item => item.id), [2])
  assert.equal(state.errorMessage.value, '')
})

test('会话核对连接失败时保留已知身份和查询条件供重试', async () => {
  const auth = authenticate()
  roomsApi.list = async () => []
  const { state } = browser()
  await flush()
  state.draft.attendees = '12'
  roomsApi.list = async () => { throw new ApiError(401, '登录信息待确认') }
  authApi.me = async () => { throw new ApiError(503, '暂时无法核对会话') }
  await state.search()
  assert.equal(auth.user?.id, 1)
  assert.equal(auth.isAuthenticated, true)
  assert.equal(state.draft.attendees, '12')
  assert.equal(state.loading.value, false)
  assert.ok(auth.restoreError.includes('暂时无法核对会话'))
})
