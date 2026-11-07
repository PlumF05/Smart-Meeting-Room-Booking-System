import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { AxiosError, AxiosHeaders } from 'axios'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'

import { authApi } from '../src/api/auth.ts'
import { ApiError, getErrorMessage, http } from '../src/api/http.ts'
import { useAuthStore } from '../src/stores/auth.ts'

const originalAdapter = http.defaults.adapter
const oldSession = sessionInfo(1, 'student_one')
const newSession = sessionInfo(2, 'student_two')
const loginInput = { username: 'student_one', password: 'test-only-password' }

function sessionInfo(id: number, username: string) {
  return {
    user: {
      id,
      username,
      full_name: '测试学生',
      identity_type: 'student' as const,
      role: 'user' as const,
      is_active: true,
    },
    csrf_token: `csrf-for-${id}`,
    expires_at: '2099-01-01T00:00:00Z',
  }
}

function response(config: InternalAxiosRequestConfig, data: unknown, status = 200): AxiosResponse {
  return { config, data, status, statusText: String(status), headers: new AxiosHeaders() }
}

function rejectResponse(config: InternalAxiosRequestConfig, status: number, data: unknown): never {
  throw new AxiosError('synthetic transport failure', 'ERR_BAD_RESPONSE', config, undefined,
    response(config, data, status))
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((finish) => { resolve = finish })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  http.defaults.adapter = async () => { throw new Error('测试未设置请求处理器，禁止访问真实服务') }
})

afterEach(() => {
  http.defaults.adapter = originalAdapter
})

test('恢复登录的401表示匿名，503保留未知状态并提供连接错误', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => rejectResponse(config, 401, { detail: '登录已失效' })
  await auth.restoreSession()
  assert.equal(auth.status, 'anonymous')
  assert.equal(auth.user, null)

  const fresh = useAuthStore(createPinia())
  http.defaults.adapter = async (config) => rejectResponse(config, 503, { detail: '数据库暂不可用，请稍后重试。' })
  await assert.rejects(fresh.restoreSession(), (error: unknown) => error instanceof ApiError && error.status === 503)
  assert.equal(fresh.status, 'unknown')
  assert.equal(fresh.isAuthenticated, false)
  assert.ok(fresh.restoreError)
})

test('同时恢复登录只发一次me请求', async () => {
  const auth = useAuthStore()
  const started = deferred<void>()
  const pending = deferred<AxiosResponse>()
  let requests = 0
  let request!: InternalAxiosRequestConfig
  http.defaults.adapter = (config) => {
    requests += 1
    request = config
    started.resolve(undefined)
    return pending.promise
  }
  const first = auth.restoreSession()
  const second = auth.restoreSession()
  await started.promise
  assert.equal(requests, 1)
  pending.resolve(response(request, oldSession))
  await Promise.all([first, second])
  assert.equal(auth.user?.id, oldSession.user.id)
  assert.equal(auth.status, 'authenticated')
})

test('旧的恢复请求晚于新登录返回时不能覆盖新账号', async () => {
  const auth = useAuthStore()
  const started = deferred<void>()
  const pending = deferred<AxiosResponse>()
  let oldRequest!: InternalAxiosRequestConfig
  http.defaults.adapter = (config) => {
    if (config.url?.endsWith('/me')) {
      oldRequest = config
      started.resolve(undefined)
      return pending.promise
    }
    return Promise.resolve(response(config, newSession))
  }
  const restoring = auth.restoreSession()
  await started.promise
  await auth.login({ username: newSession.user.username, password: 'test-only-password' })
  pending.resolve(response(oldRequest, oldSession))
  await restoring
  assert.equal(auth.user?.id, newSession.user.id)
  assert.equal(auth.status, 'authenticated')
})

test('外部换号通知会重新查询，旧的恢复结果不能覆盖新账号', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => response(config, oldSession)
  await auth.login(loginInput)
  const started = deferred<void>()
  const pending = deferred<AxiosResponse>()
  let oldRequest!: InternalAxiosRequestConfig
  let requests = 0
  http.defaults.adapter = (config) => {
    requests += 1
    if (requests === 1) {
      oldRequest = config
      started.resolve(undefined)
      return pending.promise
    }
    return Promise.resolve(response(config, newSession))
  }

  const oldRestoration = auth.restoreSession(true)
  await started.promise
  try {
    auth.sessionChangedExternally()
    await auth.restoreSession(true)
    assert.equal(requests, 2)
    assert.equal(auth.user?.id, newSession.user.id)
  } finally {
    pending.resolve(response(oldRequest, oldSession))
    await oldRestoration
  }
  assert.equal(auth.user?.id, newSession.user.id)
  assert.equal(auth.status, 'authenticated')
})

test('刷新连接失败和登录失败均不能假装退出已有账号', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => response(config, oldSession)
  await auth.login(loginInput)
  http.defaults.adapter = async (config) => rejectResponse(config, 503, { detail: '服务暂不可用' })
  await assert.rejects(auth.restoreSession(true))
  assert.equal(auth.user?.id, oldSession.user.id)
  assert.equal(auth.isAuthenticated, true)
  http.defaults.adapter = async (config) => rejectResponse(config, 401, { detail: '登录资料不正确' })
  await assert.rejects(auth.login({ ...loginInput, password: 'test-only-wrong-password' }))
  assert.equal(auth.user?.id, oldSession.user.id)
  assert.equal(auth.isAuthenticated, true)
})

test('注册成功提示返回登录，但不建立前端登录状态', async () => {
  const auth = useAuthStore()
  const payload = {
    username: 'registered_student', full_name: '注册测试学生', identity_type: 'student' as const,
    password: 'test-only-password', confirm_password: 'test-only-password',
  }
  const requests: string[] = []
  http.defaults.adapter = async (config) => {
    requests.push(config.url ?? '')
    return response(config, { ...oldSession.user, username: payload.username }, 201)
  }
  await auth.register(payload)
  assert.equal(auth.user, null)
  assert.equal(auth.isAuthenticated, false)
  assert.equal(requests.length, 1)
  assert.ok(requests[0]?.endsWith('/register'))
  assert.ok(auth.notice)
  assert.equal(auth.takeRegistrationUsername(), payload.username)
  assert.equal(auth.takeRegistrationUsername(), '')
})

test('退出发送当前CSRF且成功后清除内存身份', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => response(config, oldSession)
  await auth.login(loginInput)
  let logoutRequest: InternalAxiosRequestConfig | undefined
  http.defaults.adapter = async (config) => {
    logoutRequest = config
    return response(config, undefined, 204)
  }
  await auth.logout()
  assert.equal(logoutRequest?.headers.get('X-CSRF-Token'), oldSession.csrf_token)
  assert.equal(logoutRequest?.withCredentials, true)
  assert.equal(logoutRequest?.baseURL, '/api')
  assert.equal(auth.status, 'anonymous')
  assert.equal(auth.user, null)
})

test('退出时401视为已经失效，503仍保留身份', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => response(config, oldSession)
  await auth.login(loginInput)
  http.defaults.adapter = async (config) => rejectResponse(config, 503, { detail: '服务暂不可用' })
  await assert.rejects(auth.logout())
  assert.equal(auth.user?.id, oldSession.user.id)
  http.defaults.adapter = async (config) => rejectResponse(config, 401, { detail: '登录已失效' })
  await auth.logout()
  assert.equal(auth.status, 'anonymous')
  assert.equal(auth.user, null)
})

test('退出的CSRF失效会更新当前身份并要求确认，不自动退出另一个账号', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => response(config, oldSession)
  await auth.login(loginInput)
  let logoutCalls = 0
  http.defaults.adapter = async (config) => {
    if (config.url?.endsWith('/logout')) {
      logoutCalls += 1
      return rejectResponse(config, 403, { detail: 'CSRF校验失败' })
    }
    return response(config, newSession)
  }
  await assert.rejects(auth.logout(), (error: unknown) => error instanceof Error && /确认.*重试/.test(error.message))
  assert.equal(logoutCalls, 1)
  assert.equal(auth.user?.id, newSession.user.id)
  assert.equal(auth.status, 'authenticated')
})

test('退出前发出的恢复请求晚到时不能恢复已经退出的身份', async () => {
  const auth = useAuthStore()
  http.defaults.adapter = async (config) => response(config, oldSession)
  await auth.login(loginInput)
  const started = deferred<void>()
  const pending = deferred<AxiosResponse>()
  let pendingRequest!: InternalAxiosRequestConfig
  http.defaults.adapter = (config) => {
    if (config.url?.endsWith('/me')) {
      pendingRequest = config
      started.resolve(undefined)
      return pending.promise
    }
    return Promise.resolve(response(config, undefined, 204))
  }
  const restoring = auth.restoreSession(true)
  await started.promise
  await auth.logout()
  pending.resolve(response(pendingRequest, oldSession))
  await restoring
  assert.equal(auth.status, 'anonymous')
  assert.equal(auth.user, null)
})

test('接口校验异常保留有用字段错误，不保留含密码的Axios请求对象', async () => {
  const marker = 'synthetic-request-password-never-display'
  http.defaults.adapter = async (config) => rejectResponse(config, 422, {
    detail: [{ loc: ['body', 'password'], type: 'too_short', msg: 'Value should have at least 8 items', input: marker }],
  })
  await assert.rejects(authApi.login({ username: 'student', password: marker }), (error: unknown) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 422)
    assert.ok(error.fieldErrors.password)
    assert.ok(!(error instanceof AxiosError))
    assert.ok(!JSON.stringify(error).includes(marker))
    assert.ok(!getErrorMessage(error).includes(marker))
    assert.equal((error as Error & { cause?: unknown }).cause, undefined)
    assert.equal((error as Error & { config?: unknown }).config, undefined)
    return true
  })
})

test('连接异常不展示原始传输错误及其中的请求信息', async () => {
  const marker = 'synthetic-private-transport-detail'
  http.defaults.adapter = async (config) => { throw new AxiosError(marker, 'ERR_NETWORK', config) }
  await assert.rejects(authApi.me(), (error: unknown) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, null)
    assert.ok(getErrorMessage(error))
    assert.ok(!getErrorMessage(error).includes(marker))
    return true
  })
  assert.equal(http.defaults.timeout, 10_000)
})
