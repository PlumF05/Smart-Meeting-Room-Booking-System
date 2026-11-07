import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import { roomsApi } from '../api/rooms.ts'
import { ApiError, getErrorMessage } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'
import { buildRoomWindowQuery } from './roomWindow.ts'
import type { RoomWindowDraft } from './roomWindow.ts'
import type { RoomFilters, RoomInfo, RoomWindowCheck, RoomWindowQuery, ValidatedRoomWindow } from '../types/rooms.ts'

export interface RoomFilterDraft extends RoomWindowDraft {
  attendees: string
  equipment: string[]
  state: 'all' | 'active' | 'inactive'
}

export function buildRoomFilters(draft: RoomFilterDraft): RoomFilters {
  const filters: RoomFilters = {}
  const attendees = draft.attendees.trim()
  if (attendees) {
    const count = Number(attendees)
    if (!/^\d+$/.test(attendees) || !Number.isSafeInteger(count) || count < 1 || count > 2147483647) {
      throw new ApiError(422, '人数请输入1至2147483647之间的整数。')
    }
    filters.attendees = count
  }
  const equipment = [...new Set(draft.equipment.map(item => item.trim()))]
  if (equipment.length > 10 || equipment.some(item => !item || Array.from(item).length > 50)) {
    throw new ApiError(422, '最多选择10项设备，每项名称为1至50个字符。')
  }
  if (equipment.length) filters.equipment = equipment
  if (draft.state !== 'all') filters.is_active = draft.state === 'active'
  return filters
}

export function useRoomBrowser() {
  const auth = useAuthStore()
  const draft = reactive<RoomFilterDraft>({ attendees: '', equipment: [], state: 'all', date: '', start_time: '', end_time: '' })
  const rooms = ref<RoomInfo[]>([])
  const equipmentOptions = ref<string[]>([])
  const loading = ref(false)
  const errorMessage = ref('')
  const hasLoaded = ref(false)
  const appliedFilters = ref<RoomFilters>({})
  const appliedWindow = ref<ValidatedRoomWindow | null>(null)
  const windowRules = ref<RoomWindowCheck | null>(null)
  const rulesLoading = ref(false)
  const rulesError = ref('')
  const detailOpen = ref(false)
  const detail = ref<RoomInfo | null>(null)
  const detailId = ref<number | null>(null)
  const detailLoading = ref(false)
  const detailError = ref('')
  let listVersion = 0
  let detailVersion = 0
  let ruleVersion = 0
  let rulesRequested = false
  let disposed = false
  const identity = computed(() => auth.isAuthenticated && auth.user
    ? `${auth.user.id}:${auth.user.role}:${auth.sessionChangeCount}` : null)

  function closeDetail() {
    detailVersion += 1
    detailOpen.value = false
    detail.value = null
    detailId.value = null
    detailError.value = ''
    detailLoading.value = false
  }

  async function handleError(error: unknown, current: () => boolean, show: (message: string) => void) {
    if (!current()) return
    show(getErrorMessage(error))
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      try {
        // 重新核对Cookie身份，不能让较早请求的401直接清除后来建立的登录。
        await auth.restoreSession(true)
        if (current() && auth.isAuthenticated) show('登录信息已更新，请重新查询。')
      } catch {
        // 连接故障保留查询条件，公共会话提示由auth store负责。
      }
    }
  }

  async function loadWindowRules() {
    rulesRequested = true
    const version = ++ruleVersion
    const owner = identity.value
    const current = () => !disposed && version === ruleVersion && owner !== null && identity.value === owner
    if (!owner || disposed) return
    rulesError.value = ''
    rulesLoading.value = true
    try {
      const result = await roomsApi.queryWindow()
      if (!current()) return
      windowRules.value = result
    } catch (error) {
      await handleError(error, current, message => { rulesError.value = message })
    } finally {
      if (current()) rulesLoading.value = false
    }
  }

  async function search() {
    const version = ++listVersion
    const owner = identity.value
    const current = () => !disposed && version === listVersion && owner !== null && identity.value === owner
    closeDetail()
    rooms.value = []
    hasLoaded.value = false
    appliedWindow.value = null
    errorMessage.value = ''
    loading.value = false
    if (!owner || disposed) return
    let filters: RoomFilters
    let windowQuery: RoomWindowQuery | null
    try {
      filters = buildRoomFilters(draft)
      windowQuery = buildRoomWindowQuery(draft)
    } catch (error) {
      errorMessage.value = getErrorMessage(error)
      return
    }
    appliedFilters.value = filters
    loading.value = true
    try {
      let validatedWindow: ValidatedRoomWindow | null = null
      if (windowQuery) {
        // 无参数规则读取和带时间的校验共用发出序号，较早请求不能覆盖较新快照。
        const metadataVersion = ++ruleVersion
        rulesLoading.value = true
        rulesError.value = ''
        let check: RoomWindowCheck
        try {
          check = await roomsApi.queryWindow(windowQuery)
        } finally {
          if (!disposed && metadataVersion === ruleVersion && identity.value === owner) rulesLoading.value = false
        }
        if (!current()) return
        const selected = check.window
        if (!check.time_rules_passed || !selected || selected.date !== windowQuery.date
          || selected.start_time !== windowQuery.start_time || selected.end_time !== windowQuery.end_time) {
          throw new ApiError(null, '时间校验结果与本次条件不一致，请重新查询。')
        }
        if (metadataVersion === ruleVersion) windowRules.value = check
        validatedWindow = selected
      }
      const result = await roomsApi.list(filters)
      if (!current()) return
      rooms.value = result
      appliedWindow.value = validatedWindow
      equipmentOptions.value = [...new Set([...equipmentOptions.value, ...result.flatMap(room => room.equipment)])]
      hasLoaded.value = true
    } catch (error) {
      await handleError(error, current, message => { errorMessage.value = message })
    } finally {
      if (current()) loading.value = false
    }
  }

  async function openDetail(roomId: number) {
    const version = ++detailVersion
    const owner = identity.value
    const current = () => !disposed && version === detailVersion && owner !== null && identity.value === owner
    if (!owner || disposed) return
    detailId.value = roomId
    detailOpen.value = true
    detail.value = null
    detailError.value = ''
    detailLoading.value = true
    try {
      const result = await roomsApi.detail(roomId)
      if (!current()) return
      if (result.id !== roomId) throw new ApiError(null, '会议室信息不一致，请重新查询。')
      detail.value = result
    } catch (error) {
      await handleError(error, current, message => { detailError.value = message })
    } finally {
      if (current()) detailLoading.value = false
    }
  }

  function clearDraft() {
    draft.attendees = ''
    draft.equipment = []
    draft.state = 'all'
    clearWindowDraft()
  }

  function clearWindowDraft() {
    draft.date = ''
    draft.start_time = ''
    draft.end_time = ''
  }

  async function resetFilters() {
    clearDraft()
    await search()
  }

  watch(identity, () => {
    listVersion += 1
    closeDetail()
    clearDraft()
    rooms.value = []
    equipmentOptions.value = []
    appliedFilters.value = {}
    appliedWindow.value = null
    ruleVersion += 1
    windowRules.value = null
    rulesLoading.value = false
    rulesError.value = ''
    errorMessage.value = ''
    loading.value = false
    hasLoaded.value = false
    if (identity.value) {
      void search()
      if (rulesRequested) void loadWindowRules()
    }
  }, { immediate: true })

  onScopeDispose(() => {
    disposed = true
    listVersion += 1
    ruleVersion += 1
    closeDetail()
  })

  return {
    draft, rooms, equipmentOptions, loading, errorMessage, hasLoaded, appliedFilters,
    appliedWindow, windowRules, rulesLoading, rulesError,
    detailOpen, detail, detailId, detailLoading, detailError,
    search, resetFilters, openDetail, closeDetail, clearWindowDraft, loadWindowRules,
  }
}
