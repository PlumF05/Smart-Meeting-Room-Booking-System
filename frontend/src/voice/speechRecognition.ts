export interface SpeechInput {
  readonly supported: boolean
  listen(options: {
    signal: AbortSignal
    onInterim?: (text: string) => void
    onReady?: () => void
  }): Promise<string>
}

interface RecognitionResult {
  readonly isFinal: boolean
  readonly [index: number]: { readonly transcript: string } | undefined
}

interface RecognitionEvent {
  readonly resultIndex: number
  readonly results: ArrayLike<RecognitionResult>
}

export interface BrowserSpeechRecognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  onaudiostart: (() => void) | null
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

export interface SpeechRecognitionHost {
  SpeechRecognition?: new () => BrowserSpeechRecognition
  webkitSpeechRecognition?: new () => BrowserSpeechRecognition
}

const SILENCE_MS = 2_000
const MAX_LISTEN_MS = 40_000
const STOP_GRACE_MS = 1_500

function abortError(): Error {
  const error = new Error('已停止语音输入。')
  error.name = 'AbortError'
  return error
}

function recognitionError(code: string): Error {
  const messages: Record<string, string> = {
    'not-allowed': '无法使用麦克风，请允许浏览器访问麦克风后重试。',
    'service-not-allowed': '浏览器未允许语音识别，请检查浏览器设置。',
    'audio-capture': '未找到可用的麦克风，请检查麦克风连接和权限。',
    'no-speech': '没有听清语音，请点击麦克风后重新说一次。',
    network: '语音识别连接失败，请检查网络后重试。',
    aborted: '语音识别已中断，请重新开始。',
    'language-not-supported': '浏览器暂不支持中文语音识别。',
  }
  return new Error(messages[code] ?? '语音识别失败，请重新开始或使用文字输入。')
}

/** Read browser APIs at construction time so importing this module also works in Node/SSR. */
export function createBrowserSpeechInput(
  host: SpeechRecognitionHost | undefined = typeof window === 'undefined'
    ? undefined : window as unknown as SpeechRecognitionHost,
): SpeechInput {
  const Recognition = host?.SpeechRecognition ?? host?.webkitSpeechRecognition
  return {
    supported: typeof Recognition === 'function',
    listen({ signal, onInterim, onReady }) {
      if (signal.aborted) return Promise.reject(abortError())
      if (!Recognition) return Promise.reject(new Error('当前浏览器不支持语音输入，请使用 Microsoft Edge 或文字输入。'))

      return new Promise<string>((resolve, reject) => {
        let recognition: BrowserSpeechRecognition
        try {
          recognition = new Recognition()
        } catch {
          reject(new Error('无法启动语音识别，请检查浏览器和麦克风设置。'))
          return
        }

        const finals = new Map<number, string>()
        let settled = false
        let stopping = false
        let ready = false
        let silenceTimer: ReturnType<typeof setTimeout> | undefined
        let maximumTimer: ReturnType<typeof setTimeout> | undefined
        let stopTimer: ReturnType<typeof setTimeout> | undefined
        const finalText = () => [...finals.entries()].sort(([a], [b]) => a - b).map(([, text]) => text).join('').trim()
        const finish = (error?: Error, abort = false) => {
          if (settled) return
          settled = true
          clearTimeout(silenceTimer)
          clearTimeout(maximumTimer)
          clearTimeout(stopTimer)
          signal.removeEventListener('abort', onAbort)
          recognition.onaudiostart = null
          recognition.onresult = null
          recognition.onerror = null
          recognition.onend = null
          if (abort) {
            try { recognition.abort() } catch { /* The native service may already have ended. */ }
          }
          if (error) reject(error)
          else {
            const text = finalText()
            if (text) resolve(text)
            else reject(new Error('没有识别到完整语音，请点击麦克风后重新说一次。'))
          }
        }
        const onAbort = () => finish(abortError(), true)
        const stop = () => {
          if (settled || stopping) return
          stopping = true
          clearTimeout(silenceTimer)
          clearTimeout(maximumTimer)
          // stop() may deliver one last final result before end. Do not submit interim text.
          stopTimer = setTimeout(() => finish(new Error('语音识别未正常结束，请重新开始。'), true), STOP_GRACE_MS)
          try { recognition.stop() }
          catch { finish(new Error('无法结束语音识别，请重新开始。'), true) }
        }

        recognition.onaudiostart = () => {
          if (settled || stopping || ready) return
          ready = true
          onReady?.()
        }
        recognition.onresult = event => {
          if (settled) return
          // Results contain the accumulated final segments; their indices prevent duplicates.
          for (let index = event.resultIndex; index < event.results.length; index++) {
            const result = event.results[index]
            if (result?.isFinal) finals.set(index, result[0]?.transcript ?? '')
          }
          let interim = ''
          for (let index = 0; index < event.results.length; index++) {
            const result = event.results[index]
            if (result && !result.isFinal) interim += result[0]?.transcript ?? ''
          }
          onInterim?.((finalText() + interim).trim())
          if (!settled && !stopping) {
            clearTimeout(silenceTimer)
            silenceTimer = setTimeout(stop, SILENCE_MS)
          }
        }
        recognition.onerror = event => finish(recognitionError(event.error), true)
        recognition.onend = () => finish()
        signal.addEventListener('abort', onAbort, { once: true })
        if (signal.aborted) { onAbort(); return }
        maximumTimer = setTimeout(stop, MAX_LISTEN_MS)
        try {
          recognition.lang = 'zh-CN'
          recognition.continuous = true
          recognition.interimResults = true
          recognition.maxAlternatives = 1
          recognition.start()
        }
        catch { finish(new Error('无法启动语音识别，请检查麦克风权限后重试。'), true) }
      })
    },
  }
}
