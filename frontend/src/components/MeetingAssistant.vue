<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput } from 'element-plus'
import { meetingAssistantApi, type ConversationView } from '../api/meetingAssistant.ts'
import { ApiError, getErrorMessage } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'

const auth = useAuthStore()
const canUse = computed(() => auth.isAuthenticated && auth.user?.role === 'user' && !auth.pending)
const message = ref('')
const view = ref<ConversationView | null>(null)
const pending = ref(false)
const error = ref('')
const messageLength = computed(() => Array.from(message.value).length)
const canPrepare = computed(() => {
  const current = view.value
  return current?.phase === 'summary' && !!current.selected_room && !!current.draft.date
    && !!current.draft.start_time && !!current.draft.end_time && !!current.draft.attendees
    && !!current.draft.title?.trim() && !!current.draft.purpose?.trim()
})
let generation = 0
let alive = true
let controller: AbortController | null = null
let owner: { id: number; session: number; csrf: string } | null = null

function releaseConversation() {
  const current = view.value
  if (!current || !owner || auth.user?.id !== owner.id || auth.sessionChangeCount !== owner.session) return
  // 关闭失败不阻断其它页面；服务端仍按会话和有效期限制旧方案。
  void meetingAssistantApi.close(current.conversation_id, owner.csrf).catch(() => {})
}

function reset() {
  generation += 1
  controller?.abort()
  controller = null
  releaseConversation()
  view.value = null
  owner = null
  message.value = ''
  error.value = ''
  pending.value = false
}

defineExpose({ reset })

type RequestAction = (current: ConversationView | null, token: string, signal: AbortSignal) => Promise<ConversationView>

async function request(action: RequestAction) {
  if (!canUse.value || pending.value) return
  if (!auth.csrfToken || !auth.user) {
    error.value = '登录信息尚未就绪，请重新连接后再试。'
    return
  }
  const version = ++generation
  const current = view.value
  const expectedId = current?.conversation_id
  const requestOwner = { id: auth.user.id, session: auth.sessionChangeCount, csrf: auth.csrfToken }
  const isCurrent = () => alive && version === generation && canUse.value
    && auth.user?.id === requestOwner.id && auth.sessionChangeCount === requestOwner.session
  const requestController = new AbortController()
  controller = requestController
  pending.value = true
  error.value = ''
  // 新操作开始后旧摘要不再作为当前可用方案展示，失败仍保留原条件和输入。
  if (view.value) view.value = { ...view.value, confirmation: null }
  try {
    const result = await action(current, requestOwner.csrf, requestController.signal)
    if (!isCurrent()) return
    if (!result.conversation_id || !Number.isInteger(result.revision)
      || (expectedId && result.conversation_id !== expectedId)) {
      throw new ApiError(null, '助手返回的对话与当前操作不一致，请重新开始。')
    }
    view.value = result
    owner = requestOwner
  } catch (failure) {
    if (!isCurrent()) return
    error.value = getErrorMessage(failure)
    if (failure instanceof ApiError && failure.status === 401) {
      await auth.restoreSession(true).catch(() => {})
    }
  } finally {
    if (isCurrent()) {
      pending.value = false
      controller = null
    }
  }
}

async function send() {
  if (!canUse.value || pending.value) return
  const text = message.value.trim()
  if (!text || Array.from(text).length > 500) {
    error.value = '请用1至500个字符描述或补充预约需求。'
    return
  }
  await request((current, token, signal) => current
    ? meetingAssistantApi.message(current.conversation_id, current.revision, text, token, signal)
    : meetingAssistantApi.start(text, token, signal))
}

async function selectRoom(roomId: number) {
  if (!view.value?.candidates.some(room => room.id === roomId && room.is_active)) return
  await request((current, token, signal) => meetingAssistantApi.select(current!.conversation_id, current!.revision, roomId, token, signal))
}

async function showAll() {
  if (!view.value || view.value.total <= view.value.candidates.length) return
  await request((current, token, signal) => meetingAssistantApi.showAll(current!.conversation_id, current!.revision, token, signal))
}

async function prepare() {
  if (!canPrepare.value) return
  await request((current, token, signal) => meetingAssistantApi.prepare(current!.conversation_id, current!.revision, token, signal))
}

function displayTime(value: string | null) {
  if (!value) return '尚未查询'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间不可用' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

const confirmationLabels = { pending: '未执行', consumed: '已处理', expired: '已过期', superseded: '已被新方案替代' }
watch([() => auth.user?.id, () => auth.user?.role, () => auth.status,
  () => auth.sessionChangeCount, () => auth.pending], reset, { flush: 'sync' })
onBeforeUnmount(() => { alive = false; reset() })
</script>

<template>
  <section class="meeting-assistant" aria-label="查找会议室">
    <p class="meeting-note">本阶段只查找与核对方案，尚未提交，也不占位。</p>
    <ElForm label-position="top" @submit.prevent="send">
      <ElFormItem label="描述或补充预约需求" for="meeting-assistant-message">
        <ElInput id="meeting-assistant-message" v-model="message" type="textarea" :rows="3" :disabled="pending"
          placeholder="例如：明天下午两点到三点，8个人，需要白板。" aria-describedby="meeting-assistant-help" />
      </ElFormItem>
      <p id="meeting-assistant-help" class="meeting-help">{{ messageLength }} / 500 个字符。补充或修改条件会更新当前方案；关闭面板后清空对话。</p>
      <div class="meeting-actions">
        <ElButton type="primary" native-type="submit" :loading="pending" :disabled="pending">{{ view ? '发送补充 / 修改' : '查找会议室' }}</ElButton>
        <ElButton v-if="view || pending || error" @click="reset">重新开始</ElButton>
      </div>
    </ElForm>
    <p v-if="pending" class="meeting-note" role="status">正在处理需求并核对当前资料，请稍候…</p>
    <ElAlert v-if="error" class="meeting-error" :title="error" type="error" :closable="false" show-icon />
    <template v-if="view">
      <p class="meeting-message" role="status">{{ view.message }}</p>
      <section class="meeting-draft" aria-labelledby="meeting-draft-title">
        <h3 id="meeting-draft-title">当前条件</h3>
        <dl>
          <div><dt>日期与时段</dt><dd>{{ view.draft.date || '待补充日期' }} · {{ view.draft.start_time || '待补充开始时间' }} — {{ view.draft.end_time || '待补充结束时间' }}（北京时间）</dd></div>
          <div><dt>人数与设备</dt><dd>{{ view.draft.attendees ?? '待补充人数' }} 人 · {{ view.draft.equipment.join('、') || '未指定设备需求' }}</dd></div>
          <div><dt>会议主题</dt><dd>{{ view.draft.title || '待补充' }}</dd></div>
          <div><dt>用途</dt><dd>{{ view.draft.purpose || '待补充' }}</dd></div>
        </dl>
      </section>
      <section v-if="view.candidates.length" class="meeting-candidates" aria-labelledby="meeting-candidates-title">
        <div class="meeting-section-heading"><h3 id="meeting-candidates-title">匹配会议室</h3><span>共 {{ view.total }} 间，当前显示 {{ view.candidates.length }} 间</span></div>
        <p class="meeting-help">按容量、名称排序。查询不会保留时段，后续办理仍须重新校验。</p>
        <ol>
          <li v-for="room in view.candidates" :key="room.id">
            <div><h4>{{ room.name }}</h4><p>{{ room.location }} · 容量 {{ room.capacity }} 人</p><p>设备：{{ room.equipment.join('、') || '无' }}</p></div>
            <ElButton :disabled="pending || !room.is_active" :aria-label="`选择${room.name}`" @click="selectRoom(room.id)">选择此会议室</ElButton>
          </li>
        </ol>
        <ElButton v-if="view.total > view.candidates.length" :disabled="pending" @click="showAll">查看全部匹配会议室</ElButton>
      </section>
      <p v-if="view.checked_at" class="meeting-help">本次查询时间：{{ displayTime(view.checked_at) }}（北京时间）</p>
      <section v-if="view.selected_room" class="meeting-summary" aria-labelledby="meeting-summary-title">
        <h3 id="meeting-summary-title">已选择的会议室</h3>
        <p>{{ view.selected_room.name }} · {{ view.selected_room.location }} · 容量 {{ view.selected_room.capacity }} 人</p>
        <p>当前设备：{{ view.selected_room.equipment.join('、') || '无' }}</p>
        <ElButton v-if="canPrepare" :disabled="pending" @click="prepare">{{ view.confirmation ? '核对方案状态' : '生成方案摘要' }}</ElButton>
        <template v-if="view.confirmation">
          <h4>待核对方案</h4>
          <dl>
            <div><dt>动作</dt><dd>提交预约申请（本阶段尚未执行）</dd></div>
            <div><dt>日期与时段</dt><dd>{{ view.confirmation.summary.date }} · {{ view.confirmation.summary.start_time }} — {{ view.confirmation.summary.end_time }}（北京时间）</dd></div>
            <div><dt>人数与所需设备</dt><dd>{{ view.confirmation.summary.attendees }} 人 · {{ view.confirmation.summary.required_equipment.join('、') || '无' }}</dd></div>
            <div><dt>主题</dt><dd>{{ view.confirmation.summary.title }}</dd></div>
            <div><dt>用途</dt><dd>{{ view.confirmation.summary.purpose }}</dd></div>
            <div><dt>状态</dt><dd>{{ confirmationLabels[view.confirmation.state] }}</dd></div>
            <div><dt>有效至</dt><dd>{{ displayTime(view.confirmation.expires_at) }}（北京时间）</dd></div>
          </dl>
          <p class="meeting-help">五分钟从完整方案生成时起算，反复核对不会延长。过期后请先发送补充或重新开始，再生成新摘要；修改条件后也需重新生成。此处没有提交执行按钮。</p>
        </template>
      </section>
    </template>
  </section>
</template>

<style scoped>
.meeting-note { margin: 0 0 20px; color: var(--muted); font-size: .875rem; line-height: 1.8; }
.meeting-help { margin: 8px 0 16px; color: var(--muted); font-size: .8125rem; line-height: 1.7; overflow-wrap: anywhere; }
.meeting-actions, .meeting-section-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.meeting-section-heading { justify-content: space-between; }
.meeting-section-heading span { color: var(--muted); font-size: .8125rem; }
.meeting-error { margin-top: 20px; }
.meeting-message { margin: 24px 0; color: var(--navy); font-size: .9375rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.meeting-draft, .meeting-candidates, .meeting-summary { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--border); }
h3 { margin: 0 0 14px; font-size: 1rem; color: var(--navy); }
h4 { margin: 0 0 8px; font-size: .9375rem; color: var(--navy); }
dl { margin: 0; font-size: .875rem; line-height: 1.8; }
dl > div { margin-bottom: 12px; }
dt { color: var(--muted); }
dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.meeting-candidates ol { margin: 0 0 16px; padding: 0; list-style: none; }
.meeting-candidates li { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 0; border-bottom: 1px solid var(--border); }
.meeting-candidates li p, .meeting-summary > p { margin: 7px 0; color: var(--muted); font-size: .875rem; line-height: 1.8; overflow-wrap: anywhere; }
.meeting-candidates li .el-button { flex-shrink: 0; }
.meeting-summary h4 { margin-top: 20px; }
</style>
