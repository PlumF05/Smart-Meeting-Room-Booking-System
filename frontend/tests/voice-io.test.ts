import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createBrowserSpeechInput, type BrowserSpeechRecognition } from '../src/voice/speechRecognition.ts'
import {
  createBrowserSpeechOutput, type BrowserSpeechSynthesis, type BrowserSpeechUtterance, type BrowserSpeechVoice,
} from '../src/voice/speechSynthesis.ts'

function recognitionEnvironment() {
  const instances: FakeRecognition[] = []
  class FakeRecognition implements BrowserSpeechRecognition {
    lang = ''
    continuous = false
    interimResults = false
    maxAlternatives = 0
    onaudiostart: BrowserSpeechRecognition['onaudiostart'] = null
    onresult: BrowserSpeechRecognition['onresult'] = null
    onerror: BrowserSpeechRecognition['onerror'] = null
    onend: BrowserSpeechRecognition['onend'] = null
    starts = 0
    stops = 0
    aborts = 0
    constructor() { instances.push(this) }
    start() { this.starts++ }
    stop() { this.stops++ }
    abort() { this.aborts++ }
    result(parts: Array<[string, boolean]>, resultIndex = 0) {
      this.onresult?.({ resultIndex, results: parts.map(([transcript, isFinal]) => ({ isFinal, 0: { transcript } })) })
    }
  }
  return { instances, Recognition: FakeRecognition, host: { SpeechRecognition: FakeRecognition } }
}

function synthesisEnvironment(voices: BrowserSpeechVoice[] = []) {
  class Utterance implements BrowserSpeechUtterance {
    text: string
    lang = ''
    voice: BrowserSpeechVoice | null = null
    onend: BrowserSpeechUtterance['onend'] = null
    onerror: BrowserSpeechUtterance['onerror'] = null
    constructor(text: string) { this.text = text }
  }
  const listeners = new Set<() => void>()
  const utterances: Utterance[] = []
  let cancelCalls = 0, voiceReads = 0
  const synthesis: BrowserSpeechSynthesis = {
    getVoices() { voiceReads++; return voices },
    speak(utterance) { utterances.push(utterance as Utterance) },
    cancel() { cancelCalls++ },
    addEventListener(_, listener) { listeners.add(listener) },
    removeEventListener(_, listener) { listeners.delete(listener) },
  }
  return {
    host: { speechSynthesis: synthesis, SpeechSynthesisUtterance: Utterance },
    utterances, listeners,
    get cancelCalls() { return cancelCalls },
    get voiceReads() { return voiceReads },
    setVoices(next: BrowserSpeechVoice[]) { voices = next },
    voicesChanged() { for (const listener of [...listeners]) listener() },
  }
}

const chineseVoice = { name: 'Local Chinese', lang: 'zh-CN', localService: true }
const englishVoice = { name: 'English', lang: 'en-US', localService: true }

test('Node导入无需window，不支持的浏览器给出可理解的输入输出错误', async () => {
  assert.equal(createBrowserSpeechInput().supported, false)
  assert.equal(createBrowserSpeechOutput().supported, false)
  const signal = new AbortController().signal
  await assert.rejects(createBrowserSpeechInput({}).listen({ signal }), /不支持语音输入/)
  await assert.rejects(createBrowserSpeechOutput({}).speak('你好', signal), /不支持语音播报/)
})

test('每轮新建识别器、支持webkit回退，只有audiostart通知一次录音就绪', async () => {
  const env = recognitionEnvironment()
  const input = createBrowserSpeechInput({ webkitSpeechRecognition: env.Recognition })
  assert.equal(input.supported, true)
  let ready = 0
  for (let turn = 0; turn < 2; turn++) {
    const result = input.listen({ signal: new AbortController().signal, onReady() { ready++ } })
    const recognition = env.instances[turn]!
    assert.equal(ready, turn)
    assert.equal(recognition.lang, 'zh-CN')
    assert.equal(recognition.continuous, true)
    assert.equal(recognition.interimResults, true)
    assert.equal(recognition.maxAlternatives, 1)
    assert.equal(recognition.starts, 1)
    recognition.onaudiostart!(); recognition.onaudiostart!()
    assert.equal(ready, turn + 1)
    recognition.result([['明天下午', true]])
    recognition.onend!()
    assert.equal(await result, '明天下午')
  }
  assert.notEqual(env.instances[0], env.instances[1])
})

test('连续final逐段汇总且重复结果不重复添加，interim仅用于完整预览', async () => {
  const env = recognitionEnvironment(), preview: string[] = []
  const result = createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal, onInterim: text => preview.push(text) })
  const recognition = env.instances[0]!
  recognition.result([['明天下午', true], ['两', false]])
  recognition.result([['明天下午', true], ['两点到三点，', true], ['六', false]], 1)
  recognition.result([['明天下午', true], ['两点到三点，', true], ['六个人', true]], 2)
  recognition.result([['明天下午', true], ['两点到三点，', true], ['六个人', true]], 2)
  const lateEnd = recognition.onend!
  lateEnd(); lateEnd()
  assert.equal(await result, '明天下午两点到三点，六个人')
  assert.deepEqual(preview, ['明天下午两', '明天下午两点到三点，六', '明天下午两点到三点，六个人', '明天下午两点到三点，六个人'])
  assert.equal(recognition.onend, null)
  assert.equal(recognition.onresult, null)
})

test('空结果、只有interim或空白final结束均reject，不发送临时识别文字', async () => {
  for (const parts of [[], [['我还没说完', false]], [['  ', true]]] as Array<Array<[string, boolean]>>) {
    const env = recognitionEnvironment()
    const result = createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal })
    env.instances[0]!.result(parts)
    env.instances[0]!.onend!()
    await assert.rejects(result, /没有识别到完整语音/)
  }
  const env = recognitionEnvironment()
  const result = createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal })
  env.instances[0]!.result([['已确认片段', true], ['未确认尾句', false]])
  env.instances[0]!.onend!()
  assert.equal(await result, '已确认片段')
})

test('预览回调内abort也不再安排静默计时器', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const schedule = context.mock.method(globalThis, 'setTimeout')
  const env = recognitionEnvironment(), controller = new AbortController()
  const result = createBrowserSpeechInput(env.host).listen({ signal: controller.signal, onInterim() { controller.abort() } })
  const scheduledBefore = schedule.mock.callCount()
  env.instances[0]!.result([['片段', true]])
  await assert.rejects(result, { name: 'AbortError' })
  assert.equal(schedule.mock.callCount(), scheduledBefore)
  context.mock.timers.tick(100_000)
  assert.equal(env.instances[0]!.stops, 0)
})

test('末次result静默2秒才stop，等待stop后的末次final和正常end', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = recognitionEnvironment()
  const result = createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal })
  const recognition = env.instances[0]!
  recognition.result([['明天', true]])
  context.mock.timers.tick(1_900)
  recognition.result([['明天', true], ['下午', false]], 1)
  context.mock.timers.tick(1_999)
  assert.equal(recognition.stops, 0)
  context.mock.timers.tick(1)
  assert.equal(recognition.stops, 1)
  recognition.result([['明天', true], ['下午', true]], 1)
  recognition.onend!()
  assert.equal(await result, '明天下午')
  context.mock.timers.tick(100_000)
  assert.equal(recognition.stops, 1)
  assert.equal(recognition.aborts, 0)
})

test('单轮40秒停止录音，没有end时有界失败且不自动重启', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = recognitionEnvironment()
  const result = createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal })
  const recognition = env.instances[0]!
  context.mock.timers.tick(39_999)
  assert.equal(recognition.stops, 0)
  context.mock.timers.tick(1)
  assert.equal(recognition.stops, 1)
  context.mock.timers.tick(1_500)
  await assert.rejects(result, /未正常结束/)
  assert.equal(recognition.aborts, 1)
  context.mock.timers.tick(100_000)
  assert.equal(env.instances.length, 1)
  assert.equal(recognition.starts, 1)
})

test('识别原生错误reject并清理，迟到end和结果不转为成功', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  for (const [code, expected] of [['not-allowed', /麦克风/], ['no-speech', /没有听清/], ['network', /连接失败/], ['unknown', /语音识别失败/]] as const) {
    const env = recognitionEnvironment()
    let previews = 0
    const result = createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal, onInterim() { previews++ } })
    const recognition = env.instances[0]!, lateResult = recognition.onresult!, lateEnd = recognition.onend!
    recognition.result([['部分final', true]])
    recognition.onerror!({ error: code })
    lateEnd(); lateResult({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: '迟到结果' } }] })
    await assert.rejects(result, expected)
    assert.equal(recognition.aborts, 1)
    assert.equal(previews, 1)
    context.mock.timers.tick(100_000)
    assert.equal(recognition.stops, 0)
  }
})

test('abort立即停止识别、清理所有计时器，保存过的迟到回调也无效', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = recognitionEnvironment(), controller = new AbortController()
  let ready = 0, previews = 0
  const result = createBrowserSpeechInput(env.host).listen({ signal: controller.signal, onReady() { ready++ }, onInterim() { previews++ } })
  const recognition = env.instances[0]!, lateReady = recognition.onaudiostart!, lateResult = recognition.onresult!, lateEnd = recognition.onend!
  recognition.result([['旧语音', true]])
  controller.abort()
  lateReady(); lateEnd(); lateResult({ resultIndex: 0, results: [] })
  await assert.rejects(result, { name: 'AbortError' })
  assert.equal(recognition.aborts, 1)
  assert.equal(ready, 0)
  assert.equal(previews, 1)
  assert.equal(recognition.onaudiostart, null)
  assert.equal(recognition.onerror, null)
  context.mock.timers.tick(100_000)
  assert.equal(recognition.stops, 0)
})

test('已abort的signal不创建识别器；原生start抛错可理解且终止识别', async () => {
  const env = recognitionEnvironment(), controller = new AbortController()
  controller.abort()
  await assert.rejects(createBrowserSpeechInput(env.host).listen({ signal: controller.signal }), { name: 'AbortError' })
  assert.equal(env.instances.length, 0)
  env.Recognition.prototype.start = () => { throw new Error('synthetic native failure') }
  await assert.rejects(createBrowserSpeechInput(env.host).listen({ signal: new AbortController().signal }), /无法启动语音识别/)
  assert.equal(env.instances[0]!.aborts, 1)
})

test('朗读仅使用中文voice，优先本地Microsoft Kangkang，其次本地zh-CN，最后其它zh', async () => {
  const kangkang = { name: 'Microsoft Kangkang - Chinese (Simplified, PRC)', lang: 'zh-CN', localService: true }
  const taiwan = { name: 'Taiwan', lang: 'zh-TW', localService: false }
  const remoteKangkang = { ...kangkang, localService: false }
  for (const [voices, expected] of [
    [[englishVoice, remoteKangkang, chineseVoice, kangkang], kangkang],
    [[englishVoice, taiwan, remoteKangkang, chineseVoice], chineseVoice],
    [[englishVoice, taiwan], taiwan],
  ] as Array<[BrowserSpeechVoice[], BrowserSpeechVoice]>) {
    const env = synthesisEnvironment(voices)
    const result = createBrowserSpeechOutput(env.host).speak('  请核对预约信息。  ', new AbortController().signal)
    const utterance = env.utterances[0]!
    assert.equal(utterance.text, '请核对预约信息。')
    assert.equal(utterance.lang, 'zh-CN')
    assert.equal(utterance.voice, expected)
    const lateEnd = utterance.onend!
    lateEnd(); lateEnd()
    await result
    assert.equal(utterance.onend, null)
    assert.equal(utterance.onerror, null)
    assert.equal(env.listeners.size, 0)
    assert.equal(env.cancelCalls, 0)
  }
})

test('正常朗读等待原生onend后才resolve，清理超时计时器', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = synthesisEnvironment([chineseVoice])
  let completed = false
  const result = createBrowserSpeechOutput(env.host).speak('你好', new AbortController().signal).then(() => { completed = true })
  await Promise.resolve()
  assert.equal(completed, false)
  context.mock.timers.tick(5_000)
  assert.equal(completed, false)
  env.utterances[0]!.onend!()
  await result
  context.mock.timers.tick(200_000)
  assert.equal(env.cancelCalls, 0)
})

test('voiceschanged延迟加载中文声，事件监听和voice等待计时器随后清理', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = synthesisEnvironment([])
  const result = createBrowserSpeechOutput(env.host).speak('中文回复', new AbortController().signal)
  assert.equal(env.utterances.length, 0)
  assert.equal(env.listeners.size, 1)
  context.mock.timers.tick(500)
  env.setVoices([englishVoice, chineseVoice]); env.voicesChanged()
  assert.equal(env.utterances.length, 1)
  assert.equal(env.utterances[0]!.voice, chineseVoice)
  assert.equal(env.listeners.size, 0)
  const reads = env.voiceReads
  context.mock.timers.tick(3_000)
  assert.equal(env.voiceReads, reads)
  env.utterances[0]!.onend!()
  await result
})

test('漏发voiceschanged时有界轮询仍能加载中文声', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = synthesisEnvironment([])
  const result = createBrowserSpeechOutput(env.host).speak('中文回复', new AbortController().signal)
  env.setVoices([chineseVoice])
  context.mock.timers.tick(100)
  assert.equal(env.utterances.length, 1)
  env.utterances[0]!.onend!()
  await result
})

test('只有英文声或没有声等待3秒后reject，绝不交给默认英文声朗读', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  for (const voices of [[], [englishVoice]]) {
    const env = synthesisEnvironment(voices)
    const result = createBrowserSpeechOutput(env.host).speak('中文回复', new AbortController().signal)
    context.mock.timers.tick(3_000)
    await assert.rejects(result, /没有可用的中文语音/)
    assert.equal(env.utterances.length, 0)
    assert.equal(env.listeners.size, 0)
    const reads = env.voiceReads
    context.mock.timers.tick(100_000)
    assert.equal(env.voiceReads, reads)
  }
})

test('加载voice中abort取消队列并清理监听及计时器，迟到voice不再朗读', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = synthesisEnvironment([]), controller = new AbortController()
  const result = createBrowserSpeechOutput(env.host).speak('中文回复', controller.signal)
  const lateVoices = [...env.listeners][0]!
  controller.abort()
  await assert.rejects(result, { name: 'AbortError' })
  assert.equal(env.cancelCalls, 1)
  assert.equal(env.listeners.size, 0)
  env.setVoices([chineseVoice]); lateVoices()
  context.mock.timers.tick(200_000)
  assert.equal(env.utterances.length, 0)
})

test('朗读中abort或原生error只reject一次，取消队列后迟到end不能完成', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  for (const mode of ['abort', 'error']) {
    const env = synthesisEnvironment([chineseVoice]), controller = new AbortController()
    const result = createBrowserSpeechOutput(env.host).speak('中文回复', controller.signal)
    const utterance = env.utterances[0]!, lateEnd = utterance.onend!, lateError = utterance.onerror!
    if (mode === 'abort') controller.abort()
    else utterance.onerror!({ error: 'synthesis-failed' })
    lateEnd(); lateError({ error: 'canceled' })
    await assert.rejects(result, mode === 'abort' ? { name: 'AbortError' } : /播报失败/)
    assert.equal(env.cancelCalls, 1)
    assert.equal(utterance.onend, null)
    assert.equal(utterance.onerror, null)
    context.mock.timers.tick(200_000)
    assert.equal(env.cancelCalls, 1)
  }
})

test('原生朗读没有end或error时有界超时并cancel', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const env = synthesisEnvironment([chineseVoice])
  const result = createBrowserSpeechOutput(env.host).speak('你好', new AbortController().signal)
  context.mock.timers.tick(15_000)
  await assert.rejects(result, /播报超时/)
  assert.equal(env.cancelCalls, 1)
  assert.equal(env.utterances[0]!.onend, null)
})

test('已abort、空文字、voice读取失败和原生speak抛错均不误报朗读完成', async () => {
  const env = synthesisEnvironment([chineseVoice]), controller = new AbortController()
  controller.abort()
  const output = createBrowserSpeechOutput(env.host)
  await assert.rejects(output.speak('你好', controller.signal), { name: 'AbortError' })
  await assert.rejects(output.speak('  ', new AbortController().signal), /没有可播报/)
  assert.equal(env.utterances.length, 0)
  env.host.speechSynthesis.speak = () => { throw new Error('synthetic speak failure') }
  await assert.rejects(output.speak('你好', new AbortController().signal), /无法开始语音播报/)
  assert.equal(env.cancelCalls, 1)
  env.host.speechSynthesis.getVoices = () => { throw new Error('synthetic voice failure') }
  await assert.rejects(output.speak('你好', new AbortController().signal), /无法加载中文语音/)
  assert.equal(env.listeners.size, 0)
})
