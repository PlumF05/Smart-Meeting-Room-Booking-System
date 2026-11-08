import { readonly, ref } from 'vue'

type TextSize = 'standard' | 'large'
type PreferenceStorage = Pick<Storage, 'getItem' | 'setItem'>
const STORAGE_KEY = 'smrbs-text-size'

// 只管理显示偏好；调用方提供DOM和存储，不依赖账号、路由或业务请求。
export function createTextSizePreference(
  root: Pick<HTMLElement, 'setAttribute'>,
  getStorage: () => PreferenceStorage,
) {
  const size = ref<TextSize>('standard')
  try {
    if (getStorage().getItem(STORAGE_KEY) === 'large') size.value = 'large'
  } catch {
    // 浏览器禁止本地存储时，仍允许本次页面切换字号。
  }
  root.setAttribute('data-text-size', size.value)

  function setSize(value: TextSize) {
    if (value !== 'standard' && value !== 'large') return
    size.value = value
    root.setAttribute('data-text-size', value)
    try {
      getStorage().setItem(STORAGE_KEY, value)
    } catch {
      // 保存失败不能中断页面操作。
    }
  }

  return { size: readonly(size), setSize }
}
