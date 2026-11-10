export interface SpeechOutput {
  readonly supported: boolean
  speak(text: string, signal: AbortSignal): Promise<void>
}

export interface BrowserSpeechVoice {
  readonly name: string
  readonly lang: string
  readonly localService: boolean
}

export interface BrowserSpeechUtterance {
  text: string
  lang: string
  voice: BrowserSpeechVoice | null
  onend: (() => void) | null
  onerror: ((event: { error: string }) => void) | null
}

export interface BrowserSpeechSynthesis {
  getVoices(): BrowserSpeechVoice[]
  speak(utterance: BrowserSpeechUtterance): void
  cancel(): void
  addEventListener(type: 'voiceschanged', listener: () => void): void
  removeEventListener(type: 'voiceschanged', listener: () => void): void
}

export interface SpeechSynthesisHost {
  speechSynthesis?: BrowserSpeechSynthesis
  SpeechSynthesisUtterance?: new (text: string) => BrowserSpeechUtterance
}

const VOICE_WAIT_MS = 3_000
const VOICE_POLL_MS = 100

function abortError(): Error {
  const error = new Error('已停止语音播报。')
  error.name = 'AbortError'
  return error
}

function selectChineseVoice(voices: BrowserSpeechVoice[]): BrowserSpeechVoice | undefined {
  const chinese = voices.filter(voice => /^zh(?:-|$)/i.test(voice.lang.replaceAll('_', '-')))
  const rank = (voice: BrowserSpeechVoice) => {
    if (voice.localService && /microsoft/i.test(voice.name) && /kangkang/i.test(voice.name)) return 0
    if (voice.localService && /^zh-cn$/i.test(voice.lang.replaceAll('_', '-'))) return 1
    return 2
  }
  return chinese.sort((a, b) => rank(a) - rank(b))[0]
}

export function createBrowserSpeechOutput(
  host: SpeechSynthesisHost | undefined = typeof window === 'undefined'
    ? undefined : window as unknown as SpeechSynthesisHost,
): SpeechOutput {
  const synthesis = host?.speechSynthesis
  const Utterance = host?.SpeechSynthesisUtterance
  return {
    supported: !!synthesis && typeof Utterance === 'function',
    speak(text, signal) {
      if (signal.aborted) return Promise.reject(abortError())
      if (!synthesis || !Utterance) return Promise.reject(new Error('当前浏览器不支持语音播报，请使用文字回复。'))
      if (!text.trim()) return Promise.reject(new Error('没有可播报的文字。'))

      return new Promise<void>((resolve, reject) => {
        let settled = false
        let utterance: BrowserSpeechUtterance | undefined
        let waiting = true
        let waitTimer: ReturnType<typeof setTimeout> | undefined
        let pollTimer: ReturnType<typeof setTimeout> | undefined
        let speechTimer: ReturnType<typeof setTimeout> | undefined
        const cleanupVoices = () => {
          waiting = false
          clearTimeout(waitTimer)
          clearTimeout(pollTimer)
          synthesis.removeEventListener('voiceschanged', tryVoices)
        }
        const finish = (error?: Error, cancel = false) => {
          if (settled) return
          settled = true
          cleanupVoices()
          clearTimeout(speechTimer)
          signal.removeEventListener('abort', onAbort)
          if (utterance) { utterance.onend = null; utterance.onerror = null }
          if (cancel) {
            try { synthesis.cancel() } catch { /* Preserve the original failure. */ }
          }
          if (error) reject(error)
          else resolve()
        }
        const onAbort = () => finish(abortError(), true)
        const tryVoices = () => {
          if (settled || !waiting) return
          let voice: BrowserSpeechVoice | undefined
          try { voice = selectChineseVoice(synthesis.getVoices()) }
          catch { finish(new Error('无法加载中文语音，请检查浏览器的语音设置。')); return }
          if (!voice) return
          cleanupVoices()
          try {
            utterance = new Utterance(text.trim())
            utterance.lang = 'zh-CN'
            utterance.voice = voice
            utterance.onend = () => finish()
            utterance.onerror = () => finish(new Error('语音播报失败，请查看文字回复或重新开始。'), true)
            // Native speech services can fail to emit either end or error. Keep the turn bounded.
            speechTimer = setTimeout(() => finish(new Error('语音播报超时，已停止播报。'), true),
              Math.min(120_000, Math.max(15_000, text.length * 350 + 10_000)))
            synthesis.speak(utterance)
          } catch { finish(new Error('无法开始语音播报，请查看文字回复。'), true) }
        }
        const pollVoices = () => {
          tryVoices()
          if (!settled && waiting) pollTimer = setTimeout(pollVoices, VOICE_POLL_MS)
        }

        signal.addEventListener('abort', onAbort, { once: true })
        if (signal.aborted) { onAbort(); return }
        synthesis.addEventListener('voiceschanged', tryVoices)
        waitTimer = setTimeout(() => {
          tryVoices()
          if (waiting) finish(new Error('没有可用的中文语音，请在系统中安装中文语音后重试。'))
        }, VOICE_WAIT_MS)
        pollVoices()
      })
    },
  }
}
