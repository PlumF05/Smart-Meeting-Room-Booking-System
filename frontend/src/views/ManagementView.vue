<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElAlert, ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElOption,
  ElSelect, ElSwitch, ElTable, ElTableColumn, ElTabs, ElTabPane, ElTag,
} from 'element-plus'
import { ApiError, getErrorMessage, http } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'
import type { RoomInfo } from '../types/rooms.ts'

interface Booking {
  id: number; room_id: number; title: string; purpose: string; attendees: number
  starts_at: string; ends_at: string; submitted_at: string; status: string; status_reason: string | null
  can_approve: boolean; can_reject: boolean; can_revoke: boolean
  required_equipment: string[]
  applicant: { id: number; username: string; full_name: string; is_active: boolean }
  room_snapshot: Omit<RoomInfo, 'id' | 'is_active'>
  current_room: Omit<RoomInfo, 'id'>
}
type Action = 'approve' | 'reject' | 'revoke'
interface DecisionResult extends Booking { message: string; action_applied: boolean }
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const userFilter = computed(() => {
  const value = typeof route.query.user_id === 'string' ? Number(route.query.user_id) : NaN
  return Number.isSafeInteger(value) && value > 0 ? value : undefined
})
const tab = ref('reservations')
const bookings = ref<Booking[]>([])
const rooms = ref<RoomInfo[]>([])
const loadingBookings = ref(false)
const loadingRooms = ref(false)
const writing = ref(false)
const bookingError = ref('')
const roomsError = ref('')
const notice = ref('')
const noticeType = ref<'success' | 'warning'>('success')
const filters = reactive({ status: 'pending', room_id: undefined as number | undefined, sort: 'starts_at', direction: 'asc' })
const selected = ref<Booking | null>(null)
const decision = ref<{ booking: Booking; action: Action } | null>(null)
const decisionReason = ref('')
const decisionError = ref('')
const editingRoom = ref<RoomInfo | null>(null)
const roomError = ref('')
const roomDraft = reactive({ name: '', location: '', capacity: '', equipment: [] as string[], is_active: true, reason: '' })
const equipmentOptions = computed(() => [...new Set(rooms.value.flatMap(room => room.equipment))])
const labels: Record<string, string> = { pending: '未审批', approved: '已批准', rejected: '已驳回', cancelled: '已取消', expired: '已过期', revoked: '已撤销', finished: '已结束', all: '全部状态' }
const actionLabels: Record<Action, string> = { approve: '批准', reject: '驳回', revoke: '撤销' }
const statusLabel = (value: string) => labels[value] ?? value
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
let generation = 0
let bookingVersion = 0
let roomVersion = 0

function captureContext() {
  const started = generation
  const userId = auth.user?.id
  return () => started === generation && auth.isAuthenticated && auth.user?.id === userId && auth.user?.role === 'admin'
}

async function checkSession(failure: unknown, isCurrent: () => boolean) {
  if (!(failure instanceof ApiError) || ![401, 403].includes(failure.status ?? 0) || !isCurrent()) return
  const started = generation
  await auth.restoreSession(true).catch(() => {})
  if (started !== generation) return
  if (!auth.isAuthenticated) await router.replace({ name: 'login' })
  else if (auth.user?.role !== 'admin') await router.replace({ name: 'forbidden' })
}

async function loadBookings() {
  const version = ++bookingVersion
  const isContextCurrent = captureContext()
  const isCurrent = () => version === bookingVersion && isContextCurrent()
  if (!isCurrent()) return
  loadingBookings.value = true
  bookingError.value = ''
  try {
    const { data } = await http.get<Booking[]>('/admin/reservations', { params: { ...filters, ...(userFilter.value ? { user_id: userFilter.value } : {}) } })
    if (isCurrent()) bookings.value = data
  } catch (failure) {
    if (!isCurrent()) return
    bookingError.value = getErrorMessage(failure)
    await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) loadingBookings.value = false }
}

async function loadRooms() {
  const version = ++roomVersion
  const isContextCurrent = captureContext()
  const isCurrent = () => version === roomVersion && isContextCurrent()
  if (!isCurrent()) return
  loadingRooms.value = true
  roomsError.value = ''
  try {
    const { data } = await http.get<RoomInfo[]>('/admin/rooms')
    if (isCurrent()) rooms.value = data
  } catch (failure) {
    if (!isCurrent()) return
    roomsError.value = getErrorMessage(failure)
    await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) loadingRooms.value = false }
}

function openDecision(id: number, action: Action) {
  if (writing.value) return
  const booking = bookings.value.find(item => item.id === id)
  if (!booking) return
  decision.value = { booking, action }
  decisionReason.value = ''
  decisionError.value = ''
}

async function submitDecision() {
  if (!decision.value || writing.value) return
  const { booking, action } = decision.value
  const reason = decisionReason.value.trim()
  if (action !== 'approve' && !reason) { decisionError.value = '请填写驳回或撤销的原因。'; return }
  const isCurrent = captureContext()
  if (!isCurrent()) return
  writing.value = true
  decisionError.value = ''
  notice.value = ''
  try {
    const { data } = await http.post<DecisionResult>(`/admin/reservations/${booking.id}/decision`, { action, reason: reason || null }, { headers: { 'X-CSRF-Token': auth.csrfToken } })
    if (!isCurrent()) return
    notice.value = `${data.message} 当前状态：${statusLabel(data.status)}。`
    noticeType.value = data.action_applied ? 'success' : 'warning'
    if (selected.value?.id === data.id) selected.value = data
    decision.value = null
    void loadBookings()
  } catch (failure) {
    if (!isCurrent()) return
    decisionError.value = getErrorMessage(failure)
    await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) writing.value = false }
}

function editRoom(id: number) {
  if (writing.value) return
  const room = rooms.value.find(item => item.id === id)
  if (!room) return
  editingRoom.value = room
  Object.assign(roomDraft, { name: room.name, location: room.location, capacity: String(room.capacity), equipment: [...room.equipment], is_active: room.is_active, reason: '' })
  roomError.value = ''
}

async function saveRoom() {
  if (!editingRoom.value || writing.value) return
  const capacity = Number(roomDraft.capacity)
  const equipment = roomDraft.equipment.map(item => item.trim())
  if (!roomDraft.name.trim() || !roomDraft.location.trim()) { roomError.value = '请填写会议室名称和地点。'; return }
  if (!/^\d+$/.test(roomDraft.capacity) || !Number.isInteger(capacity) || capacity < 1 || capacity > 2147483647) { roomError.value = '容量须为 1 至 2147483647 的整数。'; return }
  if (equipment.length > 10 || equipment.some(item => !item || [...item].length > 50)) { roomError.value = '设备最多 10 项，每项须为 1 至 50 个字符。'; return }
  const isCurrent = captureContext()
  if (!isCurrent()) return
  const roomId = editingRoom.value.id
  writing.value = true
  roomError.value = ''
  notice.value = ''
  try {
    const { data } = await http.patch<RoomInfo>(`/admin/rooms/${roomId}`, {
      name: roomDraft.name.trim(), location: roomDraft.location.trim(), capacity,
      equipment, is_active: roomDraft.is_active, reason: roomDraft.reason.trim() || null,
    }, { headers: { 'X-CSRF-Token': auth.csrfToken } })
    if (!isCurrent()) return
    rooms.value = rooms.value.map(room => room.id === data.id ? data : room)
    notice.value = `已保存“${data.name}”，当前为${data.is_active ? '启用' : '停用'}状态。`
    noticeType.value = 'success'
    editingRoom.value = null
    void loadBookings()
  } catch (failure) {
    if (!isCurrent()) return
    roomError.value = getErrorMessage(failure)
    await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) writing.value = false }
}

watch([() => auth.user?.id, () => auth.user?.role, () => auth.status, () => auth.sessionChangeCount, () => userFilter.value], () => {
  generation += 1
  bookings.value = []; rooms.value = []; selected.value = null; decision.value = null; editingRoom.value = null
  writing.value = false; loadingBookings.value = false; loadingRooms.value = false
  notice.value = ''; bookingError.value = ''; roomsError.value = ''; decisionError.value = ''; roomError.value = ''
  decisionReason.value = ''
  Object.assign(filters, { status: userFilter.value ? 'all' : 'pending', room_id: undefined, sort: 'starts_at', direction: 'asc' })
  Object.assign(roomDraft, { name: '', location: '', capacity: '', equipment: [], is_active: true, reason: '' })
  if (auth.isAuthenticated && auth.user?.role === 'admin') { void loadBookings(); void loadRooms() }
  else void router.replace({ name: auth.isAuthenticated ? 'forbidden' : 'login' })
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <section class="management-page" aria-labelledby="management-title">
    <div class="account-heading">
      <div><h1 id="management-title">审批与资源维护</h1><p class="account-subtitle">办理预约审批，维护学院已有会议室。时间均为北京时间。</p></div>
    </div>
    <ElAlert v-if="notice" class="page-notice" :title="notice" :type="noticeType" show-icon @close="notice = ''" />
    <p v-if="userFilter" class="section-note">当前查看账号 #{{ userFilter }} 的预约。<ElButton link @click="router.replace({ name: 'management' })">清除账号筛选</ElButton></p>
    <ElTabs v-model="tab">
      <ElTabPane label="预约审批" name="reservations">
        <ElForm class="management-filters" inline :disabled="loadingBookings || writing" @submit.prevent="loadBookings">
          <ElFormItem label="状态"><ElSelect v-model="filters.status" class="status-filter"><ElOption v-for="(label, value) in labels" :key="value" :label="label" :value="value" /></ElSelect></ElFormItem>
          <ElFormItem label="会议室"><ElSelect v-model="filters.room_id" clearable placeholder="全部会议室" class="room-filter"><ElOption v-for="room in rooms" :key="room.id" :label="room.name" :value="room.id" /></ElSelect></ElFormItem>
          <ElFormItem label="排序"><ElSelect v-model="filters.sort" class="sort-filter"><ElOption label="使用开始时间" value="starts_at" /><ElOption label="提交时间" value="submitted_at" /></ElSelect></ElFormItem>
          <ElFormItem><ElSelect v-model="filters.direction" aria-label="排序方向" class="direction-filter"><ElOption label="从早到晚" value="asc" /><ElOption label="从晚到早" value="desc" /></ElSelect></ElFormItem>
          <ElButton native-type="submit" type="primary" :loading="loadingBookings" :disabled="writing">查询 / 刷新</ElButton>
        </ElForm>
        <ElAlert v-if="bookingError" class="page-notice" :title="bookingError" type="error" :closable="false" show-icon />
        <p class="section-note" role="status">{{ loadingBookings ? '正在读取预约…' : `当前列表 ${bookings.length} 条；点击详情核对用途、完整时段、容量和设备。` }}</p>
        <ElTable :data="bookings" row-key="id" class="management-table" aria-label="管理员预约列表" :aria-busy="loadingBookings" empty-text="暂无符合条件的预约">
          <ElTableColumn prop="id" label="编号" width="70" />
          <ElTableColumn label="主题 / 申请人" min-width="175"><template #default="{ row }"><strong>{{ row.title }}</strong><span class="table-secondary">{{ row.applicant.full_name }}（{{ row.applicant.username }}）</span></template></ElTableColumn>
          <ElTableColumn prop="room_snapshot.name" label="申请时会议室" min-width="130" />
          <ElTableColumn label="使用时段" min-width="175"><template #default="{ row }">{{ formatTime(row.starts_at) }}<span class="table-secondary">至 {{ formatTime(row.ends_at) }}</span></template></ElTableColumn>
          <ElTableColumn label="人数 / 当时容量" width="130"><template #default="{ row }">{{ row.attendees }} / {{ row.room_snapshot.capacity }} 人</template></ElTableColumn>
          <ElTableColumn label="状态" width="95"><template #default="{ row }"><ElTag :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : 'info'" effect="plain">{{ statusLabel(row.status) }}</ElTag></template></ElTableColumn>
          <ElTableColumn label="操作" width="180"><template #default="{ row }">
            <ElButton link type="primary" @click="selected = bookings.find(item => item.id === row.id) ?? null">详情</ElButton>
            <ElButton v-if="row.can_approve" link type="primary" :disabled="writing || loadingBookings" @click="openDecision(row.id, 'approve')">批准</ElButton>
            <ElButton v-if="row.can_reject" link type="danger" :disabled="writing || loadingBookings" @click="openDecision(row.id, 'reject')">驳回</ElButton>
            <ElButton v-if="row.can_revoke" link type="danger" :disabled="writing || loadingBookings" @click="openDecision(row.id, 'revoke')">撤销</ElButton>
          </template></ElTableColumn>
        </ElTable>
      </ElTabPane>
      <ElTabPane label="会议室维护" name="rooms">
        <div class="section-heading"><p class="section-note">维护名称、地点、容量、设备及启停状态。</p><ElButton :loading="loadingRooms" :disabled="writing" @click="loadRooms">刷新会议室</ElButton></div>
        <ElAlert v-if="roomsError" class="page-notice" :title="roomsError" type="error" :closable="false" show-icon />
        <ElTable :data="rooms" row-key="id" class="management-table" aria-label="会议室维护列表" :aria-busy="loadingRooms" empty-text="暂无会议室">
          <ElTableColumn prop="id" label="编号" width="75" /><ElTableColumn prop="name" label="会议室" min-width="170" /><ElTableColumn prop="location" label="地点" min-width="180" />
          <ElTableColumn label="容量" width="90"><template #default="{ row }">{{ row.capacity }} 人</template></ElTableColumn>
          <ElTableColumn label="设备" min-width="200"><template #default="{ row }">{{ row.equipment.join('、') || '无' }}</template></ElTableColumn>
          <ElTableColumn label="资源状态" width="100"><template #default="{ row }"><ElTag :type="row.is_active ? 'success' : 'info'" effect="plain">{{ row.is_active ? '启用' : '停用' }}</ElTag></template></ElTableColumn>
          <ElTableColumn label="操作" width="115"><template #default="{ row }"><ElButton link type="primary" :disabled="writing || loadingRooms" @click="editRoom(row.id)">编辑 / 启停</ElButton></template></ElTableColumn>
        </ElTable>
      </ElTabPane>
    </ElTabs>

    <ElDialog :model-value="!!selected" title="预约详情" width="700px" @update:model-value="value => { if (!value) selected = null }">
      <template v-if="selected">
        <h3 class="detail-title">#{{ selected.id }} {{ selected.title }}</h3>
        <dl class="detail-fields">
          <div><dt>申请人</dt><dd>{{ selected.applicant.full_name }}（{{ selected.applicant.username }}）{{ selected.applicant.is_active ? '' : ' / 账号已停用' }}</dd></div>
          <div><dt>提交时间</dt><dd>{{ formatTime(selected.submitted_at) }}</dd></div>
          <div class="detail-wide"><dt>用途</dt><dd>{{ selected.purpose }}</dd></div>
          <div class="detail-wide"><dt>使用时段</dt><dd>{{ formatTime(selected.starts_at) }} 至 {{ formatTime(selected.ends_at) }}</dd></div>
          <div><dt>预计人数</dt><dd>{{ selected.attendees }} 人</dd></div>
          <div><dt>状态</dt><dd>{{ statusLabel(selected.status) }}</dd></div>
          <div class="detail-wide"><dt>状态原因</dt><dd>{{ selected.status_reason || '无' }}</dd></div>
          <div class="detail-wide"><dt>所需设备</dt><dd>{{ selected.required_equipment.join('、') || '无' }}</dd></div>
        </dl>
        <div class="room-comparison"><section><h3>申请时资料</h3><p>{{ selected.room_snapshot.name }} / {{ selected.room_snapshot.location }}</p><p>容量：{{ selected.room_snapshot.capacity }} 人</p><p>设备：{{ selected.room_snapshot.equipment.join('、') || '无' }}</p></section><section><h3>当前资料</h3><p>{{ selected.current_room.name }} / {{ selected.current_room.location }}</p><p>容量：{{ selected.current_room.capacity }} 人；{{ selected.current_room.is_active ? '启用' : '停用' }}</p><p>设备：{{ selected.current_room.equipment.join('、') || '无' }}</p></section></div>
      </template>
      <template #footer><ElButton @click="selected = null">关闭</ElButton></template>
    </ElDialog>

    <ElDialog :model-value="!!decision" :title="decision ? `确认${actionLabels[decision.action]}预约` : '处理预约'" width="540px" :close-on-click-modal="false" :close-on-press-escape="!writing" :show-close="!writing" @update:model-value="value => { if (!value && !writing) decision = null }">
      <template v-if="decision">
        <ElAlert v-if="decisionError" class="page-notice" :title="decisionError" type="error" :closable="false" show-icon />
        <p>确认{{ actionLabels[decision.action] }} #{{ decision.booking.id }}“{{ decision.booking.title }}”？</p>
        <p class="section-note">{{ decision.action === 'approve' ? '批准前请核对用途、人数及当前会议室资料。' : decision.action === 'reject' ? '驳回后将通知申请人并释放预约时段。' : '撤销已批准的预约后将通知申请人并释放时段。' }}</p>
        <ElForm label-position="top"><ElFormItem :label="decision.action === 'approve' ? '审批备注（可选）' : '处理原因（必填）'"><ElInput v-model="decisionReason" type="textarea" :rows="4" maxlength="1000" show-word-limit :disabled="writing" /></ElFormItem></ElForm>
      </template>
      <template #footer><ElButton :disabled="writing" @click="decision = null">返回</ElButton><ElButton :type="decision?.action === 'approve' ? 'primary' : 'danger'" :loading="writing" :disabled="writing" @click="submitDecision">确认{{ decision ? actionLabels[decision.action] : '处理' }}</ElButton></template>
    </ElDialog>

    <ElDialog :model-value="!!editingRoom" title="编辑会议室" width="620px" :close-on-click-modal="false" :close-on-press-escape="!writing" :show-close="!writing" @update:model-value="value => { if (!value && !writing) editingRoom = null }">
      <ElAlert v-if="roomError" class="page-notice" :title="roomError" type="error" :closable="false" show-icon />
      <ElForm label-position="top" :disabled="writing" @submit.prevent="saveRoom">
        <div class="room-edit-grid"><ElFormItem label="会议室名称（必填）"><ElInput v-model="roomDraft.name" maxlength="100" /></ElFormItem><ElFormItem label="容量（人，必填）"><ElInput v-model="roomDraft.capacity" inputmode="numeric" /></ElFormItem></div>
        <ElFormItem label="地点（必填）"><ElInput v-model="roomDraft.location" maxlength="255" /></ElFormItem>
        <ElFormItem label="设备（最多 10 项，每项不超过 50 字）"><ElSelect v-model="roomDraft.equipment" multiple filterable allow-create default-first-option :reserve-keyword="false" :multiple-limit="10" placeholder="输入设备名称后回车" no-data-text="输入设备名称后回车"><ElOption v-for="equipment in equipmentOptions" :key="equipment" :label="equipment" :value="equipment" /></ElSelect></ElFormItem>
        <ElFormItem label="资源状态"><ElSwitch v-model="roomDraft.is_active" active-text="启用" inactive-text="停用" /></ElFormItem>
        <ElFormItem label="变更原因（可选）"><ElInput v-model="roomDraft.reason" type="textarea" :rows="2" maxlength="1000" show-word-limit /></ElFormItem>
        <p class="section-note">如停用、变更地点或容量影响已有预约，系统会提示需先处理的预约编号。设备可直接更新，已提交预约保留申请时资料。</p>
      </ElForm>
      <template #footer><ElButton :disabled="writing" @click="editingRoom = null">返回</ElButton><ElButton type="primary" :loading="writing" :disabled="writing" @click="saveRoom">保存修改</ElButton></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.management-page { min-width: 1020px; }
.page-notice { margin-bottom: 18px; }
.management-page :deep(.el-alert__title) { overflow-wrap: anywhere; }
.management-filters { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; padding: 20px; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
.management-filters :deep(.el-form-item) { margin: 0; }
.status-filter { width: 110px; }.room-filter { width: 165px; }.sort-filter { width: 145px; }.direction-filter { width: 120px; }
.section-note { margin: 14px 0; color: var(--muted); font-size: 13px; line-height: 1.8; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 14px; }
.management-table { --el-table-header-bg-color: #eaf1f3; --el-table-header-text-color: #536a76; --el-table-border-color: #d8e2e6; border: 1px solid var(--border); border-radius: 8px; }
.management-table :deep(td.el-table__cell) { padding-block: 15px; }.management-table :deep(.cell) { overflow-wrap: anywhere; }
.table-secondary { display: block; color: var(--muted); font-size: 12px; line-height: 1.7; margin-top: 5px; }
.detail-title { margin: 4px 0 22px; overflow-wrap: anywhere; }.detail-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 0 0 24px; }.detail-fields dt { color: var(--muted); font-size: 13px; margin-bottom: 7px; }.detail-fields dd { margin: 0; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }.detail-wide { grid-column: 1 / -1; }
.room-comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding-top: 10px; border-top: 1px solid var(--border); }.room-comparison h3 { font-size: 15px; }.room-comparison p { line-height: 1.8; overflow-wrap: anywhere; font-size: 14px; }
.room-edit-grid { display: grid; grid-template-columns: 1fr 150px; gap: 20px; }.room-edit-grid + .el-form-item { margin-top: 0; }
</style>
