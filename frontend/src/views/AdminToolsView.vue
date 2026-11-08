<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElAlert, ElButton, ElDatePicker, ElDialog, ElForm, ElFormItem, ElInput, ElOption, ElPagination, ElSelect, ElTable, ElTableColumn, ElTabs, ElTabPane, ElTag } from 'element-plus'
import { ApiError, getErrorMessage, http } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'
import type { RoomInfo } from '../types/rooms.ts'

interface UserAccount {
  id: number; username: string; full_name: string; identity_type: string | null; role: string
  is_active: boolean; created_at: string; active_reservation_count: number
}
interface Page<T> { items: T[]; total: number; page: number; page_size: number }
interface UserReservation {
  id: number; title: string; purpose: string; starts_at: string; ends_at: string; submitted_at: string
  status: string; status_reason: string | null; room_snapshot: { name: string }
}
interface Statistics { total: number; counts: Record<string, number>; query_time: string; definition: string; start_date: string; end_date: string }
interface Operation {
  id: number; actor_kind: string; actor_user_id: number | null; created_at: string
  object_type: string; object_id: number; action: string; source: string
  before_status: string | null; after_status: string | null; result: string; reason: string | null
}
const auth = useAuthStore()
const router = useRouter()
const tab = ref('users')
const users = ref<UserAccount[]>([])
const userQuery = ref('')
const userPage = ref(1)
const userTotal = ref(0)
const usersLoading = ref(false)
const usersError = ref('')
const changingUser = ref<UserAccount | null>(null)
const writing = ref(false)
const stateError = ref('')
const notice = ref('')
const reservationUser = ref<UserAccount | null>(null)
const reservations = ref<UserReservation[]>([])
const reservationsLoading = ref(false)
const reservationsError = ref('')
const rooms = ref<RoomInfo[]>([])
const roomsError = ref('')
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const statisticsFilter = reactive({ start_date: `${today().slice(0, 7)}-01`, end_date: today(), room_id: undefined as number | undefined })
const statistics = ref<Statistics | null>(null)
const statisticsRoom = ref('全部会议室')
const statisticsLoading = ref(false)
const statisticsError = ref('')
const operations = ref<Operation[]>([])
const operationFilter = reactive({ object_type: '', object_id: '' })
const operationPage = ref(1)
const operationTotal = ref(0)
const operationsLoading = ref(false)
const operationsError = ref('')
const selectedOperation = ref<Operation | null>(null)
const statuses: Record<string, string> = { pending: '未审批', approved: '已批准', rejected: '已驳回', cancelled: '已取消', expired: '已过期', revoked: '已撤销', finished: '已结束' }
const objectTypes: Record<string, string> = { user: '账号', meeting_room: '会议室', reservation: '预约' }
const actionLabels: Record<string, string> = { register: '注册', submit: '提交', approve: '批准', reject: '驳回', cancel: '取消', revoke: '撤销', expire: '过期', finish: '结束', remind: '催办', edit: '编辑', disable: '停用', enable: '启用' }
const sourceLabels: Record<string, string> = { page: '页面', assistant: '预约助手', system: '系统' }
const resultLabels: Record<string, string> = { success: '成功', failure: '失败' }
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const statusLabel = (value: string | null) => value ? (statuses[value] ?? ({ active: '启用', inactive: '停用' } as Record<string, string>)[value] ?? value) : '—'
let generation = 0
const versions = { users: 0, reservations: 0, statistics: 0, operations: 0 }

function captureContext() {
  const started = generation
  const id = auth.user?.id
  return () => started === generation && auth.isAuthenticated && auth.user?.id === id && auth.user?.role === 'admin'
}
function beginRead(key: keyof typeof versions) {
  const version = ++versions[key]
  const context = captureContext()
  return () => version === versions[key] && context()
}
async function checkSession(failure: unknown, isCurrent: () => boolean) {
  if (!(failure instanceof ApiError) || ![401, 403].includes(failure.status ?? 0) || !isCurrent()) return
  const started = generation
  await auth.restoreSession(true).catch(() => {})
  if (started !== generation) return
  if (!auth.isAuthenticated) await router.replace({ name: 'login' })
  else if (auth.user?.role !== 'admin') await router.replace({ name: 'forbidden' })
}
async function loadUsers() {
  const isCurrent = beginRead('users')
  if (!isCurrent()) return
  usersLoading.value = true; usersError.value = ''
  try {
    const { data } = await http.get<Page<UserAccount>>('/admin/users', { params: { q: userQuery.value.trim(), page: userPage.value, page_size: 20 } })
    if (isCurrent()) { users.value = data.items; userTotal.value = data.total }
  } catch (failure) {
    if (!isCurrent()) return
    usersError.value = getErrorMessage(failure); await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) usersLoading.value = false }
}
function searchUsers() { userPage.value = 1; void loadUsers() }
function changeUserPage(value: number) { userPage.value = value; void loadUsers() }
function openStateChange(id: number) {
  const user = users.value.find(item => item.id === id)
  if (!user || user.role !== 'user' || writing.value || usersLoading.value) return
  changingUser.value = user; stateError.value = ''
}
async function changeUserState() {
  if (!changingUser.value || changingUser.value.role !== 'user' || writing.value) return
  const user = changingUser.value
  const desiredState = !user.is_active
  const isCurrent = captureContext()
  if (!isCurrent()) return
  writing.value = true; stateError.value = ''; notice.value = ''
  try {
    const { data } = await http.patch<UserAccount>(`/admin/users/${user.id}/state`, { is_active: desiredState }, { headers: { 'X-CSRF-Token': auth.csrfToken } })
    if (!isCurrent()) return
    if (data.id !== user.id || data.is_active !== desiredState) { stateError.value = '账号状态未按请求更新，请刷新后核对。'; return }
    users.value = users.value.map(item => item.id === data.id ? data : item)
    notice.value = `已${data.is_active ? '启用' : '停用'}账号“${data.username}”。${data.is_active ? '' : '已有预约未取消，请查看预约并按需前往审批处置。'}`
    changingUser.value = null
    if (reservationUser.value?.id === data.id) reservationUser.value = data
    if (!data.is_active) void openReservations(data)
  } catch (failure) {
    if (!isCurrent()) return
    stateError.value = getErrorMessage(failure); await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) writing.value = false }
}
async function openReservations(user: UserAccount) {
  reservationUser.value = user; reservations.value = []
  await loadReservations()
}
function showReservations(id: number) {
  const user = users.value.find(item => item.id === id)
  if (user) void openReservations(user)
}
async function loadReservations() {
  if (!reservationUser.value) return
  const userId = reservationUser.value.id
  const context = beginRead('reservations')
  const isCurrent = () => context() && reservationUser.value?.id === userId
  if (!isCurrent()) return
  reservationsLoading.value = true; reservationsError.value = ''
  try {
    const { data } = await http.get<UserReservation[]>(`/admin/users/${userId}/reservations`)
    if (isCurrent()) reservations.value = data
  } catch (failure) {
    if (!isCurrent()) return
    reservationsError.value = getErrorMessage(failure); await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) reservationsLoading.value = false }
}
async function loadRooms() {
  const isCurrent = captureContext()
  roomsError.value = ''
  if (!isCurrent()) return
  try {
    const { data } = await http.get<RoomInfo[]>('/admin/rooms')
    if (isCurrent()) rooms.value = data
  } catch (failure) {
    if (!isCurrent()) return
    roomsError.value = getErrorMessage(failure); await checkSession(failure, isCurrent)
  }
}
async function loadStatistics() {
  if (!statisticsFilter.start_date || !statisticsFilter.end_date || statisticsFilter.start_date > statisticsFilter.end_date) {
    statisticsError.value = '请选择完整的提交日期范围，开始日期不能晚于结束日期。'; return
  }
  const isCurrent = beginRead('statistics')
  if (!isCurrent()) return
  statisticsLoading.value = true; statisticsError.value = ''
  const roomName = rooms.value.find(room => room.id === statisticsFilter.room_id)?.name ?? '全部会议室'
  try {
    const { data } = await http.get<Statistics>('/admin/statistics', { params: { ...statisticsFilter } })
    if (isCurrent()) { statistics.value = data; statisticsRoom.value = roomName }
  } catch (failure) {
    if (!isCurrent()) return
    statisticsError.value = getErrorMessage(failure); await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) statisticsLoading.value = false }
}
async function loadOperations() {
  const id = operationFilter.object_id.trim()
  if (id && (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id)))) { operationsError.value = '对象编号须为正整数。'; return }
  const isCurrent = beginRead('operations')
  if (!isCurrent()) return
  operationsLoading.value = true; operationsError.value = ''
  try {
    const { data } = await http.get<Page<Operation>>('/admin/operations', { params: {
      page: operationPage.value, page_size: 20, object_type: operationFilter.object_type || undefined, object_id: id ? Number(id) : undefined,
    } })
    if (isCurrent()) { operations.value = data.items; operationTotal.value = data.total }
  } catch (failure) {
    if (!isCurrent()) return
    operationsError.value = getErrorMessage(failure); await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) operationsLoading.value = false }
}
function searchOperations() { operationPage.value = 1; void loadOperations() }
function changeOperationPage(value: number) { operationPage.value = value; void loadOperations() }
watch(tab, value => {
  if (value === 'statistics') void loadStatistics()
  if (value === 'operations') void loadOperations()
})
watch([() => auth.user?.id, () => auth.user?.role, () => auth.status, () => auth.sessionChangeCount], () => {
  generation += 1
  users.value = []; rooms.value = []; reservations.value = []; statistics.value = null; operations.value = []
  changingUser.value = null; reservationUser.value = null; selectedOperation.value = null
  usersLoading.value = false; writing.value = false; reservationsLoading.value = false; statisticsLoading.value = false; operationsLoading.value = false
  usersError.value = ''; stateError.value = ''; notice.value = ''; reservationsError.value = ''; roomsError.value = ''; statisticsError.value = ''; operationsError.value = ''
  userPage.value = 1; userTotal.value = 0; userQuery.value = ''; operationPage.value = 1; operationTotal.value = 0
  Object.assign(operationFilter, { object_type: '', object_id: '' })
  Object.assign(statisticsFilter, { start_date: `${today().slice(0, 7)}-01`, end_date: today(), room_id: undefined })
  if (auth.isAuthenticated && auth.user?.role === 'admin') {
    void loadUsers(); void loadRooms()
    if (tab.value === 'statistics') void loadStatistics()
    if (tab.value === 'operations') void loadOperations()
  } else void router.replace({ name: auth.isAuthenticated ? 'forbidden' : 'login' })
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <section aria-labelledby="admin-tools-title">
    <div class="account-heading"><div><h1 id="admin-tools-title">管理工具</h1><p class="account-subtitle">管理账号、查看预约数量与操作记录。时间均为北京时间。</p></div></div>
    <ElAlert v-if="notice" class="tool-alert" :title="notice" type="success" show-icon @close="notice = ''" />
    <ElTabs v-model="tab">
      <ElTabPane label="账号管理" name="users">
        <ElForm class="tool-filters" inline :disabled="usersLoading || writing" @submit.prevent="searchUsers"><ElFormItem label="账号 / 姓名"><ElInput v-model="userQuery" placeholder="搜索登录名或姓名" maxlength="100" clearable /></ElFormItem><ElButton native-type="submit" type="primary" :loading="usersLoading">查询 / 刷新</ElButton></ElForm>
        <ElAlert v-if="usersError" class="tool-alert" :title="usersError" type="error" :closable="false" show-icon />
        <ElTable :data="users" row-key="id" :aria-busy="usersLoading" aria-label="账号列表" empty-text="暂无符合条件的账号">
          <ElTableColumn prop="id" label="编号" width="70" /><ElTableColumn label="账号 / 姓名" min-width="190"><template #default="{ row }"><strong>{{ row.username }}</strong><span class="secondary">{{ row.full_name }}</span></template></ElTableColumn>
          <ElTableColumn label="身份" width="85"><template #default="{ row }">{{ row.role === 'admin' ? '管理员' : row.identity_type === 'teacher' ? '教师' : '学生' }}</template></ElTableColumn>
          <ElTableColumn label="账号状态" width="100"><template #default="{ row }"><ElTag :type="row.is_active ? 'success' : 'info'" effect="plain">{{ row.is_active ? '启用' : '停用' }}</ElTag></template></ElTableColumn>
          <ElTableColumn prop="active_reservation_count" label="未结束有效预约" width="135" />
          <ElTableColumn label="注册时间" min-width="175"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></ElTableColumn>
          <ElTableColumn label="操作" width="180"><template #default="{ row }"><ElButton link type="primary" :disabled="writing" @click="showReservations(row.id)">查看预约</ElButton><ElButton link :type="row.is_active ? 'danger' : 'primary'" :disabled="row.role !== 'user' || writing || usersLoading" @click="openStateChange(row.id)">{{ row.is_active ? '停用' : '启用' }}</ElButton></template></ElTableColumn>
        </ElTable>
        <ElPagination class="tool-pagination" :current-page="userPage" :page-size="20" :total="userTotal" layout="total, prev, pager, next" :disabled="usersLoading || writing" @current-change="changeUserPage" />
      </ElTabPane>
      <ElTabPane label="预约统计" name="statistics">
        <p class="tool-note">按提交日期筛选，按当前状态统计。日期边界采用北京时间，统计结果为预约数量，不代表会议室使用率。</p>
        <ElForm class="tool-filters" inline :disabled="statisticsLoading" @submit.prevent="loadStatistics">
          <ElFormItem label="提交起日"><ElDatePicker v-model="statisticsFilter.start_date" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" class="date-input" /></ElFormItem>
          <ElFormItem label="提交止日"><ElDatePicker v-model="statisticsFilter.end_date" type="date" value-format="YYYY-MM-DD" placeholder="结束日期" class="date-input" /></ElFormItem>
          <ElFormItem label="会议室"><ElSelect v-model="statisticsFilter.room_id" clearable placeholder="全部会议室" class="room-input"><ElOption v-for="room in rooms" :key="room.id" :label="room.name" :value="room.id" /></ElSelect></ElFormItem>
          <ElButton native-type="submit" type="primary" :loading="statisticsLoading">查询统计</ElButton>
        </ElForm>
        <ElAlert v-if="roomsError" class="tool-alert" :title="`会议室选项读取失败：${roomsError}`" type="error" :closable="false" show-icon /><ElButton v-if="roomsError" class="tool-alert" @click="loadRooms">重试会议室选项</ElButton>
        <ElAlert v-if="statisticsError" class="tool-alert" :title="statisticsError" type="error" :closable="false" show-icon />
        <div v-if="statistics" :aria-busy="statisticsLoading"><p class="tool-note">查询范围：{{ statistics.start_date }} 至 {{ statistics.end_date }}（含首尾日期） · {{ statisticsRoom }} · 查询时间：{{ formatTime(statistics.query_time) }}</p><div class="statistics-grid"><div><span>预约总数</span><strong>{{ statistics.total }}</strong></div><div v-for="(label, status) in statuses" :key="status"><span>{{ label }}</span><strong>{{ statistics.counts[status] ?? 0 }}</strong></div></div></div>
      </ElTabPane>
      <ElTabPane label="操作记录" name="operations">
        <p class="tool-note">只读查看账号、会议室及预约的操作记录。</p>
        <ElForm class="tool-filters" inline :disabled="operationsLoading" @submit.prevent="searchOperations"><ElFormItem label="对象类型"><ElSelect v-model="operationFilter.object_type" clearable placeholder="全部对象" class="type-input"><ElOption v-for="(label, value) in objectTypes" :key="value" :label="label" :value="value" /></ElSelect></ElFormItem><ElFormItem label="对象编号"><ElInput v-model="operationFilter.object_id" placeholder="正整数，可选" clearable /></ElFormItem><ElButton native-type="submit" type="primary" :loading="operationsLoading">查询 / 刷新</ElButton></ElForm>
        <ElAlert v-if="operationsError" class="tool-alert" :title="operationsError" type="error" :closable="false" show-icon />
        <ElTable :data="operations" row-key="id" :aria-busy="operationsLoading" aria-label="操作记录列表" empty-text="暂无符合条件的操作记录">
          <ElTableColumn prop="id" label="记录编号" width="90" /><ElTableColumn label="时间" min-width="175"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></ElTableColumn>
          <ElTableColumn label="执行人" min-width="110"><template #default="{ row }">{{ row.actor_kind === 'system' ? '系统' : `账号 #${row.actor_user_id ?? '—'}` }}</template></ElTableColumn>
          <ElTableColumn label="操作对象" min-width="130"><template #default="{ row }">{{ objectTypes[row.object_type] ?? row.object_type }} #{{ row.object_id }}</template></ElTableColumn>
          <ElTableColumn label="动作" min-width="140"><template #default="{ row }">{{ actionLabels[row.action] ?? row.action }}</template></ElTableColumn><ElTableColumn label="结果" min-width="100"><template #default="{ row }">{{ resultLabels[row.result] ?? row.result }}</template></ElTableColumn>
          <ElTableColumn label="操作" width="80"><template #default="{ row }"><ElButton link type="primary" @click="selectedOperation = operations.find(item => item.id === row.id) ?? null">详情</ElButton></template></ElTableColumn>
        </ElTable>
        <ElPagination class="tool-pagination" :current-page="operationPage" :page-size="20" :total="operationTotal" layout="total, prev, pager, next" :disabled="operationsLoading" @current-change="changeOperationPage" />
      </ElTabPane>
    </ElTabs>

    <ElDialog :model-value="!!changingUser" :title="changingUser?.is_active ? '确认停用账号' : '确认启用账号'" width="560px" :close-on-click-modal="false" :close-on-press-escape="!writing" :show-close="!writing" @update:model-value="value => { if (!value && !writing) changingUser = null }">
      <template v-if="changingUser"><ElAlert v-if="stateError" class="tool-alert" :title="stateError" type="error" :closable="false" show-icon /><p>确认{{ changingUser.is_active ? '停用' : '启用' }} #{{ changingUser.id }} {{ changingUser.full_name }}（{{ changingUser.username }}）？</p><p>本次查询时有 <strong>{{ changingUser.active_reservation_count }}</strong> 条未结束的有效预约。</p><ElAlert v-if="changingUser.is_active" title="停用账号不会取消已有预约。请查看该用户预约，并前往审批处置仍有效的预约。" type="warning" :closable="false" show-icon /></template>
      <template #footer><ElButton :disabled="writing" @click="changingUser = null">返回</ElButton><ElButton :type="changingUser?.is_active ? 'danger' : 'primary'" :loading="writing" :disabled="writing" @click="changeUserState">确认{{ changingUser?.is_active ? '停用' : '启用' }}</ElButton></template>
    </ElDialog>

    <ElDialog :model-value="!!reservationUser" :title="reservationUser ? `${reservationUser.full_name}（${reservationUser.username}）的预约` : '用户预约'" width="960px" @update:model-value="value => { if (!value) reservationUser = null }">
      <p class="tool-note">显示该用户所有状态的预约；按编号前往审批处理。停用账号不会自动取消预约。</p><div class="reservation-toolbar"><RouterLink :to="{ name: 'management', query: { user_id: reservationUser?.id } }">前往审批</RouterLink><ElButton :loading="reservationsLoading" @click="loadReservations">刷新预约</ElButton></div>
      <ElAlert v-if="reservationsError" class="tool-alert" :title="reservationsError" type="error" :closable="false" show-icon />
      <ElTable :data="reservations" row-key="id" :aria-busy="reservationsLoading" aria-label="用户全部预约" empty-text="该用户暂无预约"><ElTableColumn prop="id" label="预约编号" width="90" /><ElTableColumn prop="title" label="主题" min-width="150" /><ElTableColumn prop="room_snapshot.name" label="会议室" min-width="130" /><ElTableColumn label="使用时段" min-width="190"><template #default="{ row }">{{ formatTime(row.starts_at) }}<span class="secondary">至 {{ formatTime(row.ends_at) }}</span></template></ElTableColumn><ElTableColumn label="状态" width="90"><template #default="{ row }">{{ statusLabel(row.status) }}</template></ElTableColumn><ElTableColumn label="状态原因" min-width="150"><template #default="{ row }">{{ row.status_reason || '无' }}</template></ElTableColumn></ElTable>
      <template #footer><ElButton @click="reservationUser = null">关闭</ElButton></template>
    </ElDialog>

    <ElDialog :model-value="!!selectedOperation" title="操作记录详情" width="620px" @update:model-value="value => { if (!value) selectedOperation = null }">
      <dl v-if="selectedOperation" class="operation-details"><div><dt>记录编号</dt><dd>#{{ selectedOperation.id }}</dd></div><div><dt>时间</dt><dd>{{ formatTime(selectedOperation.created_at) }}</dd></div><div><dt>执行人</dt><dd>{{ selectedOperation.actor_kind === 'system' ? '系统' : `账号 #${selectedOperation.actor_user_id}` }}</dd></div><div><dt>操作对象</dt><dd>{{ objectTypes[selectedOperation.object_type] ?? selectedOperation.object_type }} #{{ selectedOperation.object_id }}</dd></div><div><dt>动作</dt><dd>{{ actionLabels[selectedOperation.action] ?? selectedOperation.action }}</dd></div><div><dt>来源</dt><dd>{{ sourceLabels[selectedOperation.source] ?? selectedOperation.source }}</dd></div><div><dt>操作前状态</dt><dd>{{ statusLabel(selectedOperation.before_status) }}</dd></div><div><dt>操作后状态</dt><dd>{{ statusLabel(selectedOperation.after_status) }}</dd></div><div><dt>结果</dt><dd>{{ resultLabels[selectedOperation.result] ?? selectedOperation.result }}</dd></div><div><dt>原因</dt><dd>{{ selectedOperation.reason || '无' }}</dd></div></dl>
      <template #footer><ElButton @click="selectedOperation = null">关闭</ElButton></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.tool-filters { margin: 14px 0 8px; }
.tool-filters .el-form-item { margin-right: 18px; }
.tool-filters :deep(.date-input) { width: 165px; }
.room-input { width: 175px; }
.type-input { width: 150px; }
.tool-alert { margin-bottom: 18px; }
.tool-note { color: var(--muted); font-size: 14px; line-height: 1.8; }
.tool-pagination { margin-top: 22px; justify-content: flex-end; }
.secondary { display: block; margin-top: 5px; color: var(--muted); font-size: 13px; }
.statistics-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 20px; }
.statistics-grid > div { padding: 22px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
.statistics-grid span { display: block; color: var(--muted); font-size: 14px; }
.statistics-grid strong { display: block; margin-top: 12px; font-size: 28px; color: var(--teal); }
.reservation-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.operation-details { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.operation-details dt { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
.operation-details dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
