<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElTable, ElTableColumn, ElDialog, ElAlert, ElInput, ElEmpty } from 'element-plus'
import { http, ApiError, getErrorMessage } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'

interface Booking {
  id: number; title: string; purpose: string; attendees: number; status: string; status_reason: string | null
  starts_at: string; ends_at: string; can_cancel: boolean; required_equipment: string[]
  room_snapshot: { name: string; location: string; capacity: number; equipment: string[] }
  current_room: { name: string; location: string; capacity: number; equipment: string[] }
}
const auth = useAuthStore()
const router = useRouter()
const rows = ref<Booking[]>([])
const selected = ref<Booking | null>(null)
const cancelTarget = ref<Booking | null>(null)
const reason = ref('')
const busy = ref(false)
const error = ref('')
let generation = 0
const labels: Record<string, string> = { pending: '未审批', approved: '已批准', cancelled: '已取消', rejected: '已驳回', revoked: '已撤销', expired: '已过期', finished: '已结束' }
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
async function load() {
  const version = ++generation
  busy.value = true
  error.value = ''
  try {
    const { data } = await http.get<Booking[]>('/reservations')
    if (version === generation) rows.value = data
  } catch (failure) {
    if (version !== generation) return
    error.value = getErrorMessage(failure)
    if (failure instanceof ApiError && failure.status === 401) {
      rows.value = []; selected.value = null; cancelTarget.value = null
      await auth.restoreSession(true).catch(() => {})
    }
  } finally { if (version === generation) busy.value = false }
}
watch(() => [auth.user?.id, auth.status, auth.sessionChangeCount], () => {
  generation++; rows.value = []; selected.value = null; cancelTarget.value = null; reason.value = ''
  if (auth.status === 'authenticated' && auth.user?.role === 'user') void load()
}, { immediate: true })
onUnmounted(() => { generation++ })
async function cancel() {
  if (!cancelTarget.value || busy.value) return
  const version = generation
  busy.value = true; error.value = ''
  try {
    await http.post(`/reservations/${cancelTarget.value.id}/cancel`, { reason: reason.value || null }, { headers: { 'X-CSRF-Token': auth.csrfToken } })
    if (version !== generation) return
    cancelTarget.value = null; selected.value = null; reason.value = ''; await load()
  } catch (failure) {
    if (version !== generation) return
    error.value = getErrorMessage(failure)
    if (failure instanceof ApiError && failure.status === 401) { rows.value = []; cancelTarget.value = null; await auth.restoreSession(true).catch(() => {}) }
  } finally { if (version === generation) busy.value = false }
}
</script>

<template>
  <section style="min-width:960px">
    <h1>我的预约</h1>
    <p>时间均为北京时间。改约需先取消原预约，再重新申请；新申请失败不会恢复原预约。</p>
    <ElButton :loading="busy" @click="load">刷新记录</ElButton>
    <ElButton @click="router.push({ name: 'rooms' })">查询会议室 / 重新申请</ElButton>
    <ElAlert v-if="error" :title="error" type="error" :closable="false" style="margin:16px 0" />
    <ElEmpty v-if="!busy && !rows.length" description="暂无预约记录" />
    <ElTable :data="rows" style="margin-top:20px">
      <ElTableColumn prop="id" label="编号" width="85" />
      <ElTableColumn prop="title" label="主题" />
      <ElTableColumn prop="room_snapshot.name" label="申请时会议室" />
      <ElTableColumn label="开始时间" width="190"><template #default="{ row }">{{ formatTime(row.starts_at) }}</template></ElTableColumn>
      <ElTableColumn label="状态" width="100"><template #default="{ row }">{{ labels[row.status] }}</template></ElTableColumn>
      <ElTableColumn label="操作" width="190"><template #default="{ row }">
        <ElButton link @click="selected = rows.find(item => item.id === row.id) ?? null">详情</ElButton>
        <ElButton v-if="row.can_cancel" link type="danger" :disabled="busy" @click="cancelTarget = rows.find(item => item.id === row.id) ?? null; reason = ''">取消</ElButton>
      </template></ElTableColumn>
    </ElTable>
    <ElDialog :model-value="!!selected" title="预约详情" width="650px" @close="selected = null">
      <template v-if="selected">
        <p>主题：{{ selected.title }}</p><p>用途：{{ selected.purpose }}</p><p>人数：{{ selected.attendees }}</p>
        <p>时段：{{ formatTime(selected.starts_at) }} — {{ formatTime(selected.ends_at) }}</p>
        <p>状态：{{ labels[selected.status] }}；原因：{{ selected.status_reason || '无' }}</p>
        <p>所需设备：{{ selected.required_equipment.join('、') || '无' }}</p>
        <h3>提交时资料</h3><p>{{ selected.room_snapshot.name }} · {{ selected.room_snapshot.location }} · {{ selected.room_snapshot.capacity }}人</p>
        <p>设备：{{ selected.room_snapshot.equipment.join('、') || '无' }}</p>
        <h3>当前资料</h3><p>{{ selected.current_room.name }} · {{ selected.current_room.location }} · {{ selected.current_room.capacity }}人</p>
        <p>设备：{{ selected.current_room.equipment.join('、') || '无' }}</p>
      </template>
    </ElDialog>
    <ElDialog :model-value="!!cancelTarget" title="确认取消预约" width="500px" :close-on-click-modal="false" :before-close="() => { if (!busy) cancelTarget = null }">
      <ElAlert v-if="error" :title="error" type="error" :closable="false" />
      <p>确认取消 {{ cancelTarget?.title }}？取消后立即释放时段，原记录保留。</p>
      <ElInput v-model="reason" type="textarea" maxlength="1000" placeholder="取消原因（可选）" :disabled="busy" />
      <template #footer><ElButton :disabled="busy" @click="cancelTarget = null">返回</ElButton><ElButton type="danger" :loading="busy" @click="cancel">确认取消</ElButton></template>
    </ElDialog>
  </section>
</template>
