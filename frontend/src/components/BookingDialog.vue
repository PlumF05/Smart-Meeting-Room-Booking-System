<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElDialog, ElForm, ElFormItem, ElInput, ElInputNumber, ElSelect, ElOption, ElButton, ElAlert } from 'element-plus'
import type { RoomInfo } from '../types/rooms.ts'
import { useAuthStore } from '../stores/auth.ts'
import { http, ApiError, getErrorMessage } from '../api/http.ts'

const props = defineProps<{ room: RoomInfo; draft: { title: string; purpose: string; attendees: number; date: string; start_time: string; end_time: string; required_equipment: string[] } }>()
const emit = defineEmits<{ close: []; submitted: [] }>()
const auth = useAuthStore()
const form = props.draft
const busy = ref(false)
const message = ref('')
const success = ref(false)
const occupancy = ref('')
let generation = 0
watch(() => [auth.user?.id, auth.status, auth.sessionChangeCount], () => { generation++; emit('close') })
watch(() => [form.date, form.start_time, form.end_time], () => { generation++; occupancy.value = '' })

async function checkOccupancy() {
  if (busy.value) return
  const version = generation
  busy.value = true
  message.value = ''
  try {
    const { data } = await http.get<{ available: boolean; is_active: boolean; occupancy: { status: string; starts_at: string; ends_at: string }[] }>(`/rooms/${props.room.id}/occupancy`, {
      params: { date: form.date, start_time: form.start_time, end_time: form.end_time },
    })
    if (version !== generation) return
    occupancy.value = !data.is_active ? '会议室已停用' : data.available ? '所选时段无房间占位，提交时仍会重新检查。' : data.occupancy.map(item => `${item.status === 'pending' ? '未审批占位' : '已预约'}：${new Date(item.starts_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} 至 ${new Date(item.ends_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`).join('；')
  } catch (error) {
    if (version !== generation) return
    if (version === generation) message.value = getErrorMessage(error)
    if (error instanceof ApiError && error.status === 401) { emit('close'); await auth.restoreSession(true).catch(() => {}) }
  } finally { busy.value = false }
}

async function submit() {
  if (busy.value || success.value) return
  busy.value = true
  message.value = ''
  const version = generation
  try {
    const { data } = await http.post<{ id: number }>('/reservations', { ...form, room_id: props.room.id }, { headers: { 'X-CSRF-Token': auth.csrfToken } })
    if (version === generation) { success.value = true; message.value = `申请 ${data.id} 已提交，待管理员审批。`; emit('submitted') }
  } catch (error) {
    if (version !== generation) return
    if (version === generation) message.value = getErrorMessage(error) + (error instanceof ApiError && (error.status === null || error.status === 503) ? ' 结果可能尚未明确，请先核对实际预约记录，勿直接重复提交。' : '')
    if (error instanceof ApiError && error.status === 401) { emit('close'); await auth.restoreSession(true).catch(() => {}) }
  } finally { busy.value = false }
}
</script>

<template>
  <ElDialog :model-value="true" :title="`预约 · ${room.name}`" width="680px" :close-on-click-modal="false" :before-close="() => { if (!busy) emit('close') }">
    <p>{{ room.location }} · 容量 {{ room.capacity }} 人；所有时间按北京时间填写。</p>
    <ElForm label-position="top" :disabled="busy || success" @submit.prevent="submit">
      <ElFormItem label="会议主题"><ElInput v-model="form.title" maxlength="200" /></ElFormItem>
      <ElFormItem label="用途"><ElInput v-model="form.purpose" type="textarea" maxlength="1000" /></ElFormItem>
      <ElFormItem label="预计人数"><ElInputNumber v-model="form.attendees" :min="1" :max="room.capacity" :precision="0" /></ElFormItem>
      <ElFormItem label="使用日期"><ElInput v-model="form.date" type="date" /></ElFormItem>
      <ElFormItem label="开始时间"><ElInput v-model="form.start_time" type="time" step="1800" /></ElFormItem>
      <ElFormItem label="结束时间"><ElInput v-model="form.end_time" type="time" step="1800" /></ElFormItem>
      <ElFormItem label="所需设备"><ElSelect v-model="form.required_equipment" multiple><ElOption v-for="item in room.equipment" :key="item" :value="item" :label="item" /></ElSelect></ElFormItem>
      <ElButton :loading="busy" @click="checkOccupancy">查询时段占位</ElButton>
      <ElButton type="primary" native-type="submit" :loading="busy">提交申请</ElButton>
    </ElForm>
    <p v-if="occupancy" role="status">{{ occupancy }}</p>
    <ElAlert v-if="message" style="margin-top:16px" :title="message" :type="success ? 'success' : 'error'" :closable="false" show-icon />
    <template #footer><ElButton :disabled="busy" @click="emit('close')">关闭</ElButton></template>
  </ElDialog>
</template>
