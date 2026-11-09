import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { assistantChatApi, ChatStreamError, type ChatRequest, type SubmissionResult } from '../api/assistantChat.ts'
import { meetingAssistantApi, type ConversationView } from '../api/meetingAssistant.ts'
import { getErrorMessage } from '../api/http.ts'
import { useAuthStore } from './auth.ts'

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  view?: ConversationView
  result?: SubmissionResult
}

const CONFIRM_WORDS = /^(确认提交|确认预约|确认提交申请)[。！!]?$/u
const IDLE_MS = 20 * 60 * 1000

export const useAssistantChatStore = defineStore('assistant-chat', () => {
  const auth = useAuthStore()
  const messages = ref<ChatMessage[]>([])
  const input = ref('')
  const view = ref<ConversationView | null>(null)
  const pending = ref(false)
  const statusText = ref('')
  const error = ref('')
  const outcomeUnknown = ref(false)
  const unverifiedPlan = ref(false)
  const canUse = computed(() => auth.isAuthenticated && auth.user?.role === 'user' && !auth.pending)
  let generation = 0
  let serial = 0
  let touchedAt = Date.now()
  let owner: { id: number; session: number; csrf: string } | null = null
  let controller: AbortController | null = null

  function release() {
    if (view.value && owner && auth.user?.id === owner.id && auth.sessionChangeCount === owner.session
      && auth.csrfToken === owner.csrf) {
      void meetingAssistantApi.close(view.value.conversation_id, owner.csrf).catch(() => {})
    }
  }

  function clear(remote = false) {
    generation += 1
    controller?.abort(); controller = null
    if (remote) release()
    messages.value = []; input.value = ''; view.value = null; owner = null
    pending.value = false; error.value = ''; statusText.value = ''; outcomeUnknown.value = false
    unverifiedPlan.value = false; touchedAt = Date.now()
  }

  function newConversation() {
    // 流断开不意味着事务回滚；核对实际结果后才放弃本地执行凭证。
    if (pending.value || outcomeUnknown.value) return
    clear(true)
  }

  function expireIdle() {
    if (!pending.value && !outcomeUnknown.value && Date.now() - touchedAt >= IDLE_MS && messages.value.length) {
      clear(true)
      error.value = '当前对话已闲置20分钟，请重新描述需求。'
    }
  }

  function isCurrentMessage(message: ChatMessage) {
    return !!message.view && message.view === view.value && !pending.value
  }

  function canConfirm(now = Date.now()) {
    const credential = view.value?.confirmation
    return canUse.value && !pending.value && !outcomeUnknown.value && !unverifiedPlan.value
      && credential?.state === 'pending' && new Date(credential.expires_at).getTime() > now
  }

  async function request(data: ChatRequest, userText: string) {
    if (!canUse.value || pending.value || !auth.csrfToken || !auth.user) return
    const currentOwner = { id: auth.user.id, session: auth.sessionChangeCount, csrf: auth.csrfToken }
    const version = ++generation
    const active = () => version === generation && canUse.value && auth.user?.id === currentOwner.id
      && auth.sessionChangeCount === currentOwner.session && auth.csrfToken === currentOwner.csrf
    const requestController = new AbortController()
    controller = requestController; owner = currentOwner; pending.value = true
    error.value = ''; statusText.value = '正在连接助手…'
    const submitting = data.action === 'confirm' || (data.action === 'message' && CONFIRM_WORDS.test(data.message?.trim() ?? ''))
    if (['message', 'select', 'all'].includes(data.action) && !submitting) unverifiedPlan.value = true
    const inputAtStart = input.value
    messages.value.push({ id: ++serial, role: 'user', text: userText })
    let messageReceived = false
    try {
      await assistantChatApi.stream(data, currentOwner.csrf, event => {
        if (!active()) return
        touchedAt = Date.now()
        if (event.type === 'status') { statusText.value = event.data.message; return }
        if (event.type === 'view') {
          if (data.conversation_id && event.data.conversation_id !== data.conversation_id) throw new Error('conversation_mismatch')
          view.value = event.data; unverifiedPlan.value = false
          messages.value.push({ id: ++serial, role: 'assistant', text: event.data.message, view: view.value })
        } else {
          if (data.confirmation_id && event.data.confirmation.confirmation_id !== data.confirmation_id) throw new Error('confirmation_mismatch')
          if (view.value) view.value = { ...view.value, confirmation: event.data.confirmation }
          outcomeUnknown.value = false
          messages.value.push({ id: ++serial, role: 'assistant', text: event.data.message, result: event.data,
            view: event.data.reservation ? undefined : view.value ?? undefined })
        }
        messageReceived = true
      }, requestController.signal)
      if (!active()) return
      if (input.value === inputAtStart) input.value = ''
      if (!messageReceived) throw new ChatStreamError(null, '没有收到完整结果，请重试。')
    } catch (failure) {
      if (!active()) return
      const knownRejection = failure instanceof ChatStreamError && failure.status !== null
        && failure.status >= 400 && failure.status < 500 && !failure.outcomeUnknown
      if ((submitting && !knownRejection) || data.action === 'check' || (failure instanceof ChatStreamError && failure.outcomeUnknown)) {
        outcomeUnknown.value = true
        error.value = '本次操作结果尚未核对。请点击“核对提交结果”，不要重复提交；也可在“我的预约”查看实际记录。'
      } else { unverifiedPlan.value = true; error.value = getErrorMessage(failure) }
      if (failure instanceof ChatStreamError && failure.status === 401) await auth.restoreSession(true).catch(() => {})
    } finally {
      if (active()) { pending.value = false; statusText.value = ''; controller = null }
    }
  }

  function currentData(): Pick<ChatRequest, 'conversation_id' | 'revision'> {
    return view.value ? { conversation_id: view.value.conversation_id, revision: view.value.revision } : {}
  }

  async function send() {
    if (outcomeUnknown.value) { error.value = '请先核对上一次提交结果，再继续对话。'; return }
    expireIdle()
    const text = input.value.trim()
    if (!text || Array.from(text).length > 500) { error.value = '请输入1至500个字符的预约需求。'; return }
    const explicitConfirm = CONFIRM_WORDS.test(text)
    if (explicitConfirm && !canConfirm()) {
      error.value = '请先核对当前完整摘要；摘要已失效时，发送补充条件重新生成后再确认。'
      return
    }
    const data: ChatRequest = { action: 'message', message: text, ...currentData() }
    if (explicitConfirm && view.value?.confirmation) data.confirmation_id = view.value.confirmation.confirmation_id
    await request(data, text)
  }

  async function selectRoom(roomId: number) {
    if (outcomeUnknown.value || !view.value?.candidates.some(room => room.id === roomId && room.is_active)) return
    await request({ action: 'select', room_id: roomId, ...currentData() }, `选择${view.value.candidates.find(room => room.id === roomId)!.name}`)
  }

  async function showAll() {
    if (outcomeUnknown.value || !view.value || view.value.total <= view.value.candidates.length) return
    await request({ action: 'all', ...currentData() }, '查看全部匹配会议室')
  }

  async function confirm() {
    if (!canConfirm() || !view.value?.confirmation) return
    await request({ action: 'confirm', ...currentData(), confirmation_id: view.value.confirmation.confirmation_id }, '确认提交申请')
  }

  async function check() {
    if (!view.value?.confirmation) return
    await request({ action: 'check', ...currentData(), confirmation_id: view.value.confirmation.confirmation_id }, '核对提交结果')
  }

  async function renewSummary() {
    if (outcomeUnknown.value || !view.value?.confirmation) return
    await request({ action: 'message', message: '重新生成摘要', ...currentData() }, '重新生成摘要')
  }

  // Pinia在路由切换时仍存活；账号、会话变化时同步清理，迟到响应不能串到新账号。
  watch([() => auth.user?.id, () => auth.user?.role, () => auth.status,
    () => auth.sessionChangeCount, () => auth.csrfToken, () => auth.pending], () => clear(), { flush: 'sync' })

  return { messages, input, view, pending, statusText, error, outcomeUnknown, unverifiedPlan, canUse,
    newConversation, expireIdle, isCurrentMessage, canConfirm, send, selectRoom, showAll, confirm, check, renewSummary }
})
