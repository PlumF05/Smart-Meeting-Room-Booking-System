import { ApiError } from './http.ts'
import type { AssistantConfirmation, ConversationView } from './meetingAssistant.ts'

export interface ChatRequest {
  action: 'message' | 'select' | 'all' | 'confirm' | 'check'
  message?: string
  conversation_id?: string
  revision?: number
  room_id?: number
  confirmation_id?: string
}

export interface SubmissionResult {
  confirmation: AssistantConfirmation
  reservation: Record<string, unknown> | null
  message: string
}

export type ChatEvent =
  | { type: 'status'; data: { message: string } }
  | { type: 'view'; data: ConversationView }
  | { type: 'result'; data: SubmissionResult }

export class ChatStreamError extends ApiError {
  readonly outcomeUnknown: boolean
  constructor(status: number | null, message: string, outcomeUnknown = false) {
    super(status, message)
    this.outcomeUnknown = outcomeUnknown
  }
}

/** POST流单独使用fetch，保留普通Axios接口的原有超时和错误处理。 */
async function stream(request: ChatRequest, csrfToken: string, onEvent: (event: ChatEvent) => void, signal?: AbortSignal) {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = setTimeout(abort, 65_000)
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  let finished = false
  let receivedResult = false
  let failure: ChatStreamError | null = null
  try {
    const response = await fetch('/api/assistant/chat', {
      method: 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-CSRF-Token': csrfToken },
      body: JSON.stringify(request),
    })
    if (!response.ok) {
      let message = '助手暂不可用，请稍后重试。'
      try {
        const body = await response.json()
        if (typeof body.detail === 'string' && body.detail.length <= 500) message = body.detail
      } catch { /* 非JSON响应不展示原始正文。 */ }
      throw new ChatStreamError(response.status, message)
    }
    if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
      throw new ChatStreamError(null, '助手响应格式异常，请重试。')
    }
    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let eventName = 'message'
    let lines: string[] = []
    let eventSize = 0
    const dispatch = () => {
      if (!lines.length || finished) { lines = []; eventName = 'message'; return }
      const name = eventName
      const data = JSON.parse(lines.join('\n'))
      lines = []; eventName = 'message'; eventSize = 0
      if (name === 'done') { finished = true; return }
      if (name === 'error') {
        failure = new ChatStreamError(typeof data.status === 'number' ? data.status : null,
          typeof data.message === 'string' ? data.message : '助手处理未完成，请重试。', data.outcome_unknown === true)
        return
      }
      if (failure) return
      if (name === 'status' && typeof data.message === 'string') onEvent({ type: 'status', data })
      else if (name === 'view' && typeof data.conversation_id === 'string' && Number.isInteger(data.revision)
        && typeof data.message === 'string' && Array.isArray(data.candidates) && data.draft) {
        receivedResult = true; onEvent({ type: 'view', data })
      } else if (name === 'result' && data.confirmation && typeof data.message === 'string') {
        receivedResult = true; onEvent({ type: 'result', data })
      }
    }
    const line = (value: string) => {
      if (!value) { dispatch(); return }
      if (value.startsWith('event:')) eventName = value.slice(6).trim()
      else if (value.startsWith('data:')) {
        eventSize += value.length
        if (eventSize > 524_288) throw new Error('event_limit')
        lines.push(value.slice(5).replace(/^ /, ''))
      }
    }
    while (!finished) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      if (buffer.length > 524_288) throw new Error('stream_limit')
      let end: number
      while ((end = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, end).replace(/\r$/, '')); buffer = buffer.slice(end + 1)
      }
      if (chunk.done) break
    }
    if (failure) throw failure
    if (!finished || !receivedResult) throw new ChatStreamError(null, '连接已中断，尚未收到完整结果。')
  } catch (error) {
    if (error instanceof ChatStreamError) throw error
    throw new ChatStreamError(null, controller.signal.aborted ? '请求已停止或超时，请核对结果后再试。' : '连接中断或响应异常，请稍后重试。')
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
    await reader?.cancel().catch(() => {})
  }
}

export const assistantChatApi = { stream }
