<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElAlert, ElButton, ElDialog, ElPagination, ElSwitch, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { ApiError, getErrorMessage, http } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'

interface Message { id: number; event_id: number; title: string; content: string; created_at: string; read_at: string | null }
interface MessagePage { items: Message[]; total: number; unread_count: number; page: number; page_size: number }
const auth = useAuthStore()
const router = useRouter()
const messages = ref<Message[]>([])
const selected = ref<Message | null>(null)
const page = ref(1)
const total = ref(0)
const unreadCount = ref(0)
const unreadOnly = ref(false)
const loading = ref(false)
const writing = ref(false)
const error = ref('')
const readError = ref('')
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
let generation = 0
let listVersion = 0

function captureContext() {
  const started = generation
  const id = auth.user?.id
  return () => started === generation && auth.isAuthenticated && auth.user?.id === id
}
async function checkSession(failure: unknown, isCurrent: () => boolean) {
  if (!(failure instanceof ApiError) || ![401, 403].includes(failure.status ?? 0) || !isCurrent()) return
  const started = generation
  await auth.restoreSession(true).catch(() => {})
  if (started === generation && !auth.isAuthenticated) await router.replace({ name: 'login' })
}
async function loadMessages() {
  const version = ++listVersion
  const context = captureContext()
  const isCurrent = () => version === listVersion && context()
  if (!isCurrent()) return
  loading.value = true
  error.value = ''
  try {
    const { data } = await http.get<MessagePage>('/messages', { params: { page: page.value, page_size: 20, unread_only: unreadOnly.value } })
    if (!isCurrent()) return
    messages.value = data.items
    total.value = data.total
    unreadCount.value = data.unread_count
  } catch (failure) {
    if (!isCurrent()) return
    error.value = getErrorMessage(failure)
    await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) loading.value = false }
}
function changePage(value: number) { page.value = value; void loadMessages() }
function changeFilter() { page.value = 1; void loadMessages() }
function openMessage(id: number) { selected.value = messages.value.find(item => item.id === id) ?? null; readError.value = '' }
async function markRead() {
  if (!selected.value || selected.value.read_at || writing.value || loading.value) return
  const message = selected.value
  const isCurrent = captureContext()
  if (!isCurrent()) return
  writing.value = true
  readError.value = ''
  try {
    const { data } = await http.post<{ id: number; read_at: string }>(`/messages/${message.id}/read`, {}, { headers: { 'X-CSRF-Token': auth.csrfToken } })
    if (!isCurrent()) return
    if (data.id !== message.id || !data.read_at) { readError.value = '未确认已读状态，请刷新后重试。'; return }
    if (selected.value?.id === message.id) selected.value = { ...message, read_at: data.read_at }
    messages.value = messages.value.map(item => item.id === message.id ? { ...item, read_at: data.read_at } : item)
    unreadCount.value = Math.max(0, unreadCount.value - 1)
    if (unreadOnly.value && messages.value.length === 1 && page.value > 1) page.value -= 1
    void loadMessages()
  } catch (failure) {
    if (!isCurrent()) return
    readError.value = getErrorMessage(failure)
    await checkSession(failure, isCurrent)
  } finally { if (isCurrent()) writing.value = false }
}
watch([() => auth.user?.id, () => auth.user?.role, () => auth.status, () => auth.sessionChangeCount], () => {
  generation += 1
  messages.value = []; selected.value = null; total.value = 0; unreadCount.value = 0
  loading.value = false; writing.value = false; error.value = ''; readError.value = ''; page.value = 1; unreadOnly.value = false
  if (auth.isAuthenticated) void loadMessages()
  else void router.replace({ name: 'login' })
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { generation += 1 })
</script>

<template>
  <section aria-labelledby="messages-title">
    <div class="account-heading"><div><h1 id="messages-title">站内消息</h1><p class="account-subtitle">查看预约及账号相关通知。时间均为北京时间。</p></div><ElTag effect="plain">未读 {{ unreadCount }} 条</ElTag></div>
    <div class="inbox-toolbar"><ElSwitch v-model="unreadOnly" active-text="仅看未读" :disabled="loading || writing" @change="changeFilter" /><ElButton :loading="loading" :disabled="writing" @click="loadMessages">刷新消息</ElButton></div>
    <ElAlert v-if="error" class="inbox-error" :title="error" type="error" :closable="false" show-icon />
    <ElTable :data="messages" row-key="id" :aria-busy="loading" aria-label="站内消息列表" empty-text="暂无消息">
      <ElTableColumn label="状态" width="90"><template #default="{ row }"><ElTag :type="row.read_at ? 'info' : 'warning'" effect="plain">{{ row.read_at ? '已读' : '未读' }}</ElTag></template></ElTableColumn>
      <ElTableColumn prop="title" label="标题" min-width="350" />
      <ElTableColumn label="发送时间" width="200"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></ElTableColumn>
      <ElTableColumn label="操作" width="90"><template #default="{ row }"><ElButton link type="primary" :disabled="writing" @click="openMessage(row.id)">查看</ElButton></template></ElTableColumn>
    </ElTable>
    <ElPagination class="inbox-pagination" :current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" :disabled="loading || writing" @current-change="changePage" />
    <ElDialog :model-value="!!selected" title="消息详情" width="650px" :show-close="!writing" :close-on-click-modal="!writing" :close-on-press-escape="!writing" @update:model-value="value => { if (!value && !writing) selected = null }">
      <template v-if="selected"><ElAlert v-if="readError" :title="readError" type="error" :closable="false" show-icon /><h3>{{ selected.title }}</h3><p class="message-time">{{ formatTime(selected.created_at) }} · {{ selected.read_at ? '已读' : '未读' }}</p><p class="message-content">{{ selected.content }}</p></template>
      <template #footer><ElButton :disabled="writing" @click="selected = null">关闭</ElButton><ElButton v-if="selected && !selected.read_at" type="primary" :loading="writing" :disabled="writing || loading" @click="markRead">标记已读</ElButton></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.inbox-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.inbox-error { margin-bottom: 20px; }
.inbox-pagination { margin-top: 22px; justify-content: flex-end; }
.message-time { color: var(--muted); font-size: 0.8125rem; }
.message-content { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.9; }
</style>
