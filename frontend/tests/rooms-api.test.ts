import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { AxiosError, AxiosHeaders } from 'axios'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'

import { ApiError, http } from '../src/api/http.ts'
import { roomsApi } from '../src/api/rooms.ts'
import type { RoomInfo } from '../src/types/rooms.ts'

const originalAdapter = http.defaults.adapter
const room: RoomInfo = {
  id: 6, name: '会议室302', location: '测试教学楼302', capacity: 40,
  equipment: ['投影仪', '白板'], is_active: false,
}

function response(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { config, data, status, statusText: String(status), headers: new AxiosHeaders() }
}

beforeEach(() => {
  http.defaults.adapter = async () => { throw new Error('测试禁止访问真实服务') }
})

afterEach(() => {
  http.defaults.adapter = originalAdapter
})

test('列表缺省条件不发送筛选参数，复用带Cookie的GET请求', async () => {
  http.defaults.adapter = async (config) => {
    assert.equal(config.method, 'get')
    assert.equal(config.withCredentials, true)
    assert.equal(http.getUri(config), '/api/rooms')
    return response(config, [room])
  }
  assert.deepEqual(await roomsApi.list(), [room])
  assert.deepEqual(await roomsApi.list({ equipment: [] }), [room])
})

test('设备使用重复查询键并保留特殊字符，false不会当作未填写丢弃', async () => {
  const special = '带"引号\\字符%_装置'
  const equipment = ['投影仪', special, '投影仪']
  http.defaults.adapter = async (config) => {
    const url = new URL(http.getUri(config), 'http://test.invalid')
    assert.equal(url.pathname, '/api/rooms')
    assert.deepEqual([...url.searchParams.keys()], ['attendees', 'equipment', 'equipment', 'is_active'])
    assert.equal(url.searchParams.get('attendees'), '20')
    assert.deepEqual(url.searchParams.getAll('equipment'), ['投影仪', special])
    assert.equal(url.searchParams.get('is_active'), 'false')
    return response(config, [])
  }
  assert.deepEqual(await roomsApi.list({ attendees: 20, equipment, is_active: false }), [])
  assert.deepEqual(equipment, ['投影仪', special, '投影仪'])
})

test('仅指定启用状态时不自动补充人数或设备条件', async () => {
  http.defaults.adapter = async (config) => {
    const url = new URL(http.getUri(config), 'http://test.invalid')
    assert.deepEqual([...url.searchParams], [['is_active', 'true']])
    return response(config, [])
  }
  await roomsApi.list({ is_active: true })
})

test('详情通过稳定编号读取，停用状态原样保留', async () => {
  http.defaults.adapter = async (config) => {
    assert.equal(config.method, 'get')
    assert.equal(http.getUri(config), '/api/rooms/6')
    return response(config, room)
  }
  assert.deepEqual(await roomsApi.detail(6), room)
})

test('接口失败仍使用公共ApiError，不向页面传播Axios请求配置', async () => {
  const marker = 'synthetic-sensitive-request-marker'
  http.defaults.adapter = async (config) => {
    config.headers.set('X-Synthetic-Test', marker)
    throw new AxiosError(marker, 'ERR_BAD_RESPONSE', config, undefined,
      response(config, { detail: '未登录或登录已失效，请重新登录。' }, 401))
  }
  await assert.rejects(roomsApi.list(), (error: unknown) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 401)
    assert.equal(error.message, '未登录或登录已失效，请重新登录。')
    assert.equal('config' in error, false)
    assert.equal('request' in error, false)
    assert.equal(JSON.stringify(error).includes(marker), false)
    return true
  })
})

test('详情不存在时保留后端404和说明，供页面提供重试或关闭操作', async () => {
  http.defaults.adapter = async (config) => {
    throw new AxiosError('synthetic missing room', 'ERR_BAD_RESPONSE', config, undefined,
      response(config, { detail: '会议室不存在。' }, 404))
  }
  await assert.rejects(roomsApi.detail(999), (error: unknown) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 404)
    assert.equal(error.message, '会议室不存在。')
    return true
  })
})

test('时间规则读取使用静态query-window地址，无参数不夹带浏览器当前时刻', async () => {
  http.defaults.adapter = async config => {
    assert.equal(config.method, 'get')
    assert.equal(config.withCredentials, true)
    assert.equal(http.getUri(config), '/api/rooms/query-window')
    return response(config, { time_rules_passed: false, window: null, availability_checked: false })
  }
  const result = await roomsApi.queryWindow()
  assert.equal(result.time_rules_passed, false)
  assert.equal(result.window, null)
  assert.equal(result.availability_checked, false)
})

test('日期时段单独发给时间校验接口，不加入资料查询或传入now覆盖服务器时钟', async () => {
  const query = { date: '2030-03-02', start_time: '10:00', end_time: '11:30' }
  http.defaults.adapter = async config => {
    const url = new URL(http.getUri(config), 'http://test.invalid')
    assert.equal(url.pathname, '/api/rooms/query-window')
    assert.deepEqual([...url.searchParams], Object.entries(query))
    return response(config, { time_rules_passed: true, availability_checked: false, window: query })
  }
  assert.equal((await roomsApi.queryWindow(query)).availability_checked, false)
})

test('列表占用仅用一次带完整时段的bulk GET，不夹带人数设备或写请求', async () => {
  const query = { date: '2030-03-02', start_time: '10:00', end_time: '11:30' }
  const result = [{ room_id: 6, available: false, is_active: false, occupancy: [], server_now: '2030-03-01T10:00:00+08:00', reserves_room: false }]
  let calls = 0
  http.defaults.adapter = async config => {
    calls++
    assert.equal(config.method, 'get')
    assert.equal(config.withCredentials, true)
    const url = new URL(http.getUri(config), 'http://test.invalid')
    assert.equal(url.pathname, '/api/rooms/occupancy')
    assert.deepEqual([...url.searchParams], Object.entries(query))
    return response(config, result)
  }
  assert.deepEqual(await roomsApi.occupancy(query), result)
  assert.equal(calls, 1)
})

test('服务器拒绝已过开始时间时，保留安全中文说明用于表单修改', async () => {
  http.defaults.adapter = async config => {
    throw new AxiosError('synthetic request metadata', 'ERR_BAD_RESPONSE', config, undefined,
      response(config, { detail: '开始时间必须晚于服务器当前北京时间。' }, 422))
  }
  await assert.rejects(
    roomsApi.queryWindow({ date: '2030-03-02', start_time: '10:00', end_time: '11:00' }),
    (error: unknown) => error instanceof ApiError && error.status === 422
      && error.message === '开始时间必须晚于服务器当前北京时间。' && !('config' in error),
  )
})
