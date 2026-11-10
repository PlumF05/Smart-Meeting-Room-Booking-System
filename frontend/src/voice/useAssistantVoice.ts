import { computed, onScopeDispose, ref, watch } from 'vue'
import type { useAssistantChatStore } from '../stores/assistantChat.ts'
import { createBrowserSpeechInput, type SpeechInput } from './speechRecognition.ts'
import { createBrowserSpeechOutput, type SpeechOutput } from './speechSynthesis.ts'
import { buildSpokenReply } from './spokenReply.ts'

type ChatStore = ReturnType<typeof useAssistantChatStore>
export type VoicePhase = 'idle' | 'preparing' | 'listening' | 'working' | 'speaking' | 'paused' | 'completed'
interface VoiceOptions { input?: SpeechInput; output?: SpeechOutput; page?: Document | null }
const CONFIRM = /^(确认提交|确认预约|确认提交申请)$/u
const INTRO = '语音预约已开始。请告诉我预约日期、具体开始和结束时间、参会人数。每轮回答结束后，我会再次开启麦克风；请等页面显示麦克风已开始采集再说话。可以说暂停语音或停止语音。'
const UNKNOWN = '提交结果尚未核对，请说核对提交结果，或使用页面上的核对按钮。请不要重复提交。'

// 仅协调语音轮次；查询、确认凭证、权限和写入仍由原聊天接口处理。
export function useAssistantVoice(chat: ChatStore, options: VoiceOptions = {}) {
  const input = options.input ?? createBrowserSpeechInput()
  const output = options.output ?? createBrowserSpeechOutput()
  const page = options.page === undefined ? (typeof document === 'undefined' ? null : document) : options.page
  const supported = input.supported && output.supported
  const readSupported = output.supported
  const autoRead = ref(false)
  const readout = ref(false)
  const phase = ref<VoicePhase>('idle')
  const active = ref(false)
  const preview = ref('')
  const error = ref('')
  const hint = ref('')
  const status = computed(() => hint.value || ({ idle: '语音未开启', preparing: '正在等待麦克风开始采集…',
    listening: '麦克风已开始采集，现在请说', working: '正在处理预约，请稍候…', speaking: '正在朗读，麦克风已关闭',
    paused: '语音已暂停', completed: '预约申请已办理，语音已结束' }[phase.value]))
  let epoch = 0
  let controller: AbortController | null = null
  let ownRequest = false
  let lastSpoken = 0
  let spokenConfirmationId: string | undefined
  let requestBaseline = 0
  let requestResetVersion = chat.resetVersion
  const reading = computed(() => active.value && readout.value)
  const canRead = computed(() => readSupported && chat.canUse && !chat.pending && !chat.error
    && !chat.outcomeUnknown && !chat.unverifiedPlan && chat.messages.some(message => message.role === 'assistant'))

  function interrupt() {
    epoch++
    controller?.abort(); controller = null
    active.value = false
    readout.value = false
    spokenConfirmationId = undefined
  }
  function pause(reason = '') {
    if (!active.value) return
    interrupt(); phase.value = 'paused'; hint.value = reason
  }
  function stop() {
    interrupt(); phase.value = 'idle'; preview.value = ''; hint.value = ''; error.value = ''
  }
  const latest = () => [...chat.messages].reverse().find(message => message.role === 'assistant')

  function waitForRequest(signal: AbortSignal) {
    if (!chat.pending) return Promise.resolve()
    return new Promise<void>((resolve, reject) => {
      const finish = (failure?: Error) => {
        clearTimeout(timer); unwatch(); signal.removeEventListener('abort', abort)
        if (failure) reject(failure); else resolve()
      }
      const abort = () => finish(new DOMException('语音已停止', 'AbortError'))
      const unwatch = watch(() => chat.pending, pending => { if (!pending) finish() }, { flush: 'sync' })
      const timer = setTimeout(() => finish(new Error('请求仍在处理中，请稍后查看页面结果。')), 70_000)
      signal.addEventListener('abort', abort, { once: true })
      if (signal.aborted) abort()
    })
  }

  function begin(readOnly = false, forceRead = false, readoutEndPhase: VoicePhase = 'paused') {
    if (!(readOnly ? readSupported : supported) || !chat.canUse || page?.hidden) return
    interrupt()
    const version = epoch
    const current = new AbortController()
    controller = current; active.value = true; readout.value = readOnly
    error.value = ''; hint.value = ''; preview.value = ''
    const valid = () => version === epoch && !current.signal.aborted && chat.canUse
    const say = async (text: string) => {
      if (!valid()) return
      phase.value = 'speaking'; hint.value = ''
      await output.speak(text, current.signal)
    }
    const read = async () => {
      const message = latest()
      if (!message) { await say(INTRO); return false }
      if (chat.outcomeUnknown) { await say(UNKNOWN); return false }
      if (chat.unverifiedPlan) {
        await say('当前条件尚未重新核实，请补充预约条件，重新查询后再确认。')
        return false
      }
      const spoken = buildSpokenReply(message)
      spokenConfirmationId = undefined
      await say(spoken.text)
      if (!valid()) return false
      lastSpoken = message.id
      // 播放完成后才认可“听完摘要”；中止、过期或条件变化都不能留下确认资格。
      if (spoken.confirmationId === chat.view?.confirmation?.confirmation_id && chat.canConfirm()) {
        spokenConfirmationId = spoken.confirmationId
      }
      if (spoken.completed) {
        active.value = false; phase.value = readOnly ? readoutEndPhase : 'completed'; controller = null
        return true
      }
      return false
    }

    void (async () => {
      try {
        phase.value = 'working'
        await waitForRequest(current.signal)
        if (!valid()) return
        const message = latest()
        if (chat.outcomeUnknown) await say(UNKNOWN)
        else if (forceRead || !message || message.id !== lastSpoken || !!chat.view?.confirmation) {
          if (await read()) return
        }
        if (readOnly && valid()) { active.value = false; phase.value = readoutEndPhase; controller = null; return }
        while (valid()) {
          phase.value = 'preparing'; hint.value = ''; preview.value = ''
          const text = (await input.listen({ signal: current.signal,
            onReady: () => { if (valid()) phase.value = 'listening' },
            onInterim: value => { if (valid()) preview.value = value },
          })).trim()
          if (!valid()) return
          preview.value = text
          const command = text.replace(/\s/gu, '').replace(/[。！？!?，,；;]+$/u, '')
          if (['停止语音', '退出语音'].includes(command)) { stop(); return }
          if (command === '暂停语音') { pause(); return }
          if (['重听回复', '重听摘要', '重听候选'].includes(command)) { if (await read()) return; continue }
          if (chat.outcomeUnknown && command !== '核对提交结果') { await say(UNKNOWN); continue }
          if (CONFIRM.test(command)) {
            if (!chat.canConfirm()) {
              await say('当前没有可以提交的有效摘要。请说重新生成摘要，或补充预约条件。')
              continue
            }
            if (spokenConfirmationId !== chat.view?.confirmation?.confirmation_id) {
              await say('请先听完当前预约摘要，再明确确认。')
              if (await read()) return
              continue
            }
          }
          phase.value = 'working'
          spokenConfirmationId = undefined
          ownRequest = true
          try {
            if (CONFIRM.test(command)) await chat.confirm()
            else if (command === '核对提交结果') await chat.check()
            else if (command === '查看全部') await chat.showAll()
            else if (command === '重新生成摘要') await chat.renewSummary()
            else await chat.sendText(text)
          } finally { ownRequest = false }
          if (!valid()) return
          // SSE可能先发view、后报错；等请求完整结束后再朗读业务结果。
          if (chat.error || chat.outcomeUnknown) {
            const message = chat.outcomeUnknown ? UNKNOWN : chat.error
            await say(message)
            if (!valid()) return
            error.value = message; active.value = false; phase.value = 'paused'; controller = null
            return
          }
          if (await read()) return
        }
      } catch (failure) {
        if (!valid()) return
        current.abort(); controller = null; active.value = false; phase.value = 'paused'
        spokenConfirmationId = undefined
        error.value = failure instanceof Error ? failure.message : '语音暂不可用，请继续使用文字或重试。'
      }
    })()
  }

  function start() { if (!active.value || reading.value) begin() }
  function replay() { if (!chat.pending && latest()) begin(!active.value || reading.value, true) }
  function readLatest() { if (canRead.value) begin(true, true, 'idle') }
  function stopReading() { if (reading.value) stop() }
  watch(autoRead, enabled => { if (!enabled) stopReading() }, { flush: 'sync' })
  watch(() => chat.pending, (pending, wasPending) => {
    if (pending) {
      requestBaseline = latest()?.id ?? 0
      requestResetVersion = chat.resetVersion
      if (active.value && !ownRequest) pause('页面操作进行中；完成后可继续语音。')
    } else if (wasPending && autoRead.value && !ownRequest && !active.value && canRead.value
      && requestResetVersion === chat.resetVersion && (latest()?.id ?? 0) > requestBaseline) {
      // 完整请求结束且业务结果可信后才自动读；连续语音已自行朗读，不重复播放。
      readLatest()
    }
  }, { flush: 'sync' })
  watch([() => chat.resetVersion, () => chat.canUse], () => {
    stop(); lastSpoken = 0; autoRead.value = false; requestBaseline = 0
  }, { flush: 'sync' })
  const visibility = () => { if (page?.hidden) pause('页面已转入后台，语音已暂停。') }
  page?.addEventListener('visibilitychange', visibility)
  onScopeDispose(() => { stop(); page?.removeEventListener('visibilitychange', visibility) })

  return { supported, readSupported, autoRead, reading, canRead, phase, active, preview, error, status,
    start, pause, stop, replay, readLatest, stopReading }
}
