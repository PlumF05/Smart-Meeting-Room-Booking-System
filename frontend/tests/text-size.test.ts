import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createTextSizePreference } from '../src/accessibility/textSize.ts'

const key = 'smrbs-text-size'

function environment(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial))
  const attributes = new Map([['lang', 'zh-CN'], ['data-existing', 'unchanged']])
  const attributeWrites: string[] = []
  const reads: string[] = []
  const writes: Array<[string, string]> = []
  const root = {
    setAttribute(name: string, value: string) {
      attributeWrites.push(name)
      attributes.set(name, value)
    },
  }
  const storage = {
    getItem(name: string) {
      reads.push(name)
      return values.get(name) ?? null
    },
    setItem(name: string, value: string) {
      writes.push([name, value])
      values.set(name, value)
    },
  }
  return { values, attributes, attributeWrites, reads, writes, root, storage }
}

test('无字号记录时采用标准字体，并立即应用到页面根属性', () => {
  const env = environment()
  const preference = createTextSizePreference(env.root, () => env.storage)
  assert.equal(preference.size.value, 'standard')
  assert.equal(env.attributes.get('data-text-size'), 'standard')
})

test('已有大号偏好在初始化时直接恢复，无需用户重新点击', () => {
  const env = environment({ [key]: 'large' })
  const preference = createTextSizePreference(env.root, () => env.storage)
  assert.equal(preference.size.value, 'large')
  assert.equal(env.attributes.get('data-text-size'), 'large')
})

test('非法缓存回退标准字体，保留其他本地设置', () => {
  const env = environment({ [key]: 'unexpected', 'other-setting': 'keep' })
  const preference = createTextSizePreference(env.root, () => env.storage)
  assert.equal(preference.size.value, 'standard')
  assert.equal(env.attributes.get('data-text-size'), 'standard')
  assert.equal(env.values.get('other-setting'), 'keep')
})

test('标准与大号可以双向即时切换，并保存各次选择', () => {
  const env = environment()
  const preference = createTextSizePreference(env.root, () => env.storage)
  preference.setSize('large')
  assert.equal(preference.size.value, 'large')
  assert.equal(env.attributes.get('data-text-size'), 'large')
  assert.equal(env.values.get(key), 'large')
  preference.setSize('standard')
  assert.equal(preference.size.value, 'standard')
  assert.equal(env.attributes.get('data-text-size'), 'standard')
  assert.equal(env.values.get(key), 'standard')
  assert.deepEqual(env.writes, [[key, 'large'], [key, 'standard']])
})

test('重新创建偏好模块仍恢复上次选择，模拟刷新页面', () => {
  const env = environment()
  createTextSizePreference(env.root, () => env.storage).setSize('large')
  const nextRoot = environment()
  const restored = createTextSizePreference(nextRoot.root, () => env.storage)
  assert.equal(restored.size.value, 'large')
  assert.equal(nextRoot.attributes.get('data-text-size'), 'large')
})

test('取得存储或读取存储失败时仍显示标准字体，并可继续切换', () => {
  for (const failure of ['access', 'read']) {
    const env = environment()
    const unavailableStorage = {
      ...env.storage,
      getItem() { throw new Error('synthetic storage read failure') },
    }
    const getStorage = () => {
      if (failure === 'access') throw new Error('synthetic storage access failure')
      return unavailableStorage
    }
    const preference = createTextSizePreference(env.root, getStorage)
    assert.equal(preference.size.value, 'standard', failure)
    assert.equal(env.attributes.get('data-text-size'), 'standard', failure)
    assert.doesNotThrow(() => preference.setSize('large'), failure)
    assert.equal(preference.size.value, 'large', failure)
    assert.equal(env.attributes.get('data-text-size'), 'large', failure)
  }
})

test('保存失败不阻止当前页面改变字号，也不抛出未处理异常', () => {
  const env = environment()
  const storage = {
    ...env.storage,
    setItem() { throw new Error('synthetic storage write failure') },
  }
  const preference = createTextSizePreference(env.root, () => storage)
  assert.doesNotThrow(() => preference.setSize('large'))
  assert.equal(preference.size.value, 'large')
  assert.equal(env.attributes.get('data-text-size'), 'large')
  assert.doesNotThrow(() => preference.setSize('standard'))
  assert.equal(preference.size.value, 'standard')
  assert.equal(env.attributes.get('data-text-size'), 'standard')
})

test('只读写自己的存储键与字号属性，不改动其他设置或页面属性', () => {
  const env = environment({ 'other-setting': 'keep' })
  const preference = createTextSizePreference(env.root, () => env.storage)
  preference.setSize('large')
  preference.setSize('standard')
  assert.ok(env.reads.length > 0)
  assert.ok(env.reads.every(name => name === key))
  assert.ok(env.writes.every(([name]) => name === key))
  assert.ok(env.attributeWrites.every(name => name === 'data-text-size'))
  assert.equal(env.values.get('other-setting'), 'keep')
  assert.equal(env.attributes.get('lang'), 'zh-CN')
  assert.equal(env.attributes.get('data-existing'), 'unchanged')
})
