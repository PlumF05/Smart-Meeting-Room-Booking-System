import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { AxiosError, AxiosHeaders } from 'axios'
import { ApiError, getErrorMessage, http } from '../src/api/http.ts'

const originalAdapter = http.defaults.adapter
afterEach(() => { http.defaults.adapter = originalAdapter })

test('预约字段校验失败应说明具体字段，不回显用户输入', async () => {
  const privateInput = 'private-meeting-purpose'
  http.defaults.adapter = async config => {
    throw new AxiosError('transport failure', 'ERR_BAD_RESPONSE', config, undefined, {
      config, status: 422, statusText: '422', headers: new AxiosHeaders(),
      data: { detail: [
        { loc: ['body', 'title'], type: 'string_too_short', msg: privateInput, input: privateInput },
        { loc: ['body', 'attendees'], type: 'int_type', msg: privateInput },
      ] },
    })
  }
  await assert.rejects(http.post('/reservations', {}), (error: unknown) => {
    const message = getErrorMessage(error)
    assert.match(message, /会议主题不能为空/)
    assert.match(message, /预计人数/)
    assert.ok(!message.includes(privateInput))
    assert.ok(error instanceof ApiError)
    return true
  })
})

test('预约取消原因校验失败应指出原因字段', async () => {
  http.defaults.adapter = async config => {
    throw new AxiosError('transport failure', 'ERR_BAD_RESPONSE', config, undefined, {
      config, status: 422, statusText: '422', headers: new AxiosHeaders(),
      data: { detail: [{ loc: ['body', 'reason'], type: 'string_too_long' }] },
    })
  }
  await assert.rejects(http.post('/reservations/1/cancel', {}), (error: unknown) => {
    assert.match(getErrorMessage(error), /取消原因超过允许长度/)
    return true
  })
})

test('预约409缺少说明时不能误报登录名冲突', async () => {
  http.defaults.adapter = async config => {
    throw new AxiosError('transport failure', 'ERR_BAD_RESPONSE', config, undefined, {
      config, status: 409, statusText: '409', headers: new AxiosHeaders(), data: {},
    })
  }
  await assert.rejects(http.post('/reservations', {}), (error: unknown) => {
    assert.ok(!getErrorMessage(error).includes('登录名'))
    assert.match(getErrorMessage(error), /冲突|状态/)
    return true
  })
})
