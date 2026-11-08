<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElAlert, ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElTabs, ElTabPane } from 'element-plus'
import MeetingAssistant from './MeetingAssistant.vue'
import { assistantApi, type RulesAnswer } from '../api/assistant.ts'
import { ApiError, getErrorMessage } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'

const auth = useAuthStore()
const canUse = computed(() => auth.isAuthenticated && auth.user?.role === 'user' && !auth.pending)
const visible = ref(false)
const activeTab = ref('rules')
const meetingAssistant = ref<{ reset: () => void } | null>(null)
const question = ref('')
const askedQuestion = ref('')
const reply = ref<RulesAnswer | null>(null)
const pending = ref(false)
const error = ref('')
const questionLength = computed(() => Array.from(question.value).length)
let generation = 0
let alive = true
let controller: AbortController | null = null

function closePanel() {
  generation += 1
  meetingAssistant.value?.reset()
  controller?.abort()
  controller = null
  visible.value = false
  pending.value = false
  activeTab.value = 'rules'
  question.value = ''
  askedQuestion.value = ''
  reply.value = null
  error.value = ''
}

function openPanel() {
  if (canUse.value) visible.value = true
}

async function ask() {
  if (!canUse.value || !visible.value || pending.value) return
  const text = question.value.trim()
  if (!text || Array.from(text).length > 500) {
    error.value = '请输入1至500个字符的规则问题。'
    return
  }
  if (!auth.csrfToken) {
    error.value = '登录信息尚未就绪，请重新连接后再提问。'
    return
  }
  const version = ++generation
  const owner = auth.user?.id
  const isCurrent = () => alive && version === generation && visible.value && canUse.value && auth.user?.id === owner
  const requestController = new AbortController()
  controller = requestController
  pending.value = true
  error.value = ''
  reply.value = null
  askedQuestion.value = text
  try {
    const data = await assistantApi.askRules(text, auth.csrfToken, requestController.signal)
    if (isCurrent()) reply.value = data
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

// 账号或登录会话变化时立即清空内容，不能等旧请求结束再处理。
watch([() => auth.user?.id, () => auth.user?.role, () => auth.status,
  () => auth.sessionChangeCount, () => auth.pending], closePanel, { flush: 'sync' })
onBeforeUnmount(() => { alive = false; closePanel() })
</script>

<template>
  <ElButton v-if="canUse" class="rules-assistant-trigger" @click="openPanel">智能助手</ElButton>
  <ElDialog :model-value="visible && canUse" title="智能助手" width="780px" :close-on-click-modal="false"
    @update:model-value="value => { if (!value) closePanel() }">
    <div class="rules-assistant-content">
      <ElTabs v-model="activeTab">
        <ElTabPane label="规则问答" name="rules">
      <p class="rules-assistant-note">此标签回答当前已发布的会议室预约规则，不查询实时空闲，也不提交或取消预约。查找当前空闲会议室请切换到“查找会议室”。</p>
      <ElForm label-position="top" @submit.prevent="ask">
        <ElFormItem label="你的规则问题" for="rules-assistant-question">
          <ElInput id="rules-assistant-question" v-model="question" type="textarea" :rows="3" :disabled="pending"
            placeholder="例如：预约开始后还能取消吗？" aria-describedby="rules-assistant-help" />
        </ElFormItem>
        <p id="rules-assistant-help" class="rules-assistant-help">{{ questionLength }} / 500 个字符。问题和回答仅保留在当前面板内，关闭后清空。</p>
        <ElButton type="primary" native-type="submit" :loading="pending" :disabled="pending">提问</ElButton>
      </ElForm>
      <p v-if="pending" class="rules-assistant-note" role="status">正在检索规则并生成回答，请稍候…</p>
      <ElAlert v-if="error" class="rules-assistant-error" :title="error" type="error" :closable="false" show-icon />
      <section v-if="reply" class="rules-assistant-reply" aria-label="规则回答">
        <p class="rules-assistant-help">已提问：{{ askedQuestion }}</p>
        <ElAlert v-if="!reply.grounded" title="当前规则中没有足够依据，以下内容不能作为已确认规则。"
          type="warning" :closable="false" show-icon />
        <p class="rules-assistant-answer">{{ reply.answer }}</p>
        <p class="rules-assistant-help">知识版本：{{ reply.knowledge_version }}</p>
        <template v-if="reply.sources.length">
          <h3>规则来源</h3>
          <ol class="rules-assistant-sources">
            <li v-for="source in reply.sources" :key="source.id">
              <p class="rules-assistant-source-title">{{ source.section }} · {{ source.title }} · {{ source.version }}</p>
              <p class="rules-assistant-excerpt">{{ source.excerpt }}</p>
            </li>
          </ol>
        </template>
      </section>
        </ElTabPane>
        <ElTabPane label="查找会议室" name="meeting">
          <MeetingAssistant v-if="visible && canUse" ref="meetingAssistant" />
        </ElTabPane>
      </ElTabs>
    </div>
    <template #footer><ElButton @click="closePanel">关闭</ElButton></template>
  </ElDialog>
</template>

<style scoped>
.rules-assistant-trigger { flex-shrink: 0; }
.rules-assistant-content { max-height: 62vh; overflow-y: auto; padding: 2px 8px 2px 2px; }
.rules-assistant-note { margin: 0 0 20px; color: var(--muted); font-size: .875rem; line-height: 1.8; }
.rules-assistant-help { margin: 8px 0 16px; color: var(--muted); font-size: .8125rem; line-height: 1.7; overflow-wrap: anywhere; }
.rules-assistant-error { margin-top: 20px; }
.rules-assistant-reply { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--border); }
.rules-assistant-reply h3 { margin: 24px 0 12px; color: var(--navy); font-size: 1rem; }
.rules-assistant-answer { margin: 16px 0; color: var(--navy); font-size: .9375rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.9; }
.rules-assistant-sources { padding-left: 24px; margin: 0; }
.rules-assistant-sources li { padding: 0 0 12px 3px; }
.rules-assistant-source-title { margin: 0; color: var(--navy); font-size: .875rem; font-weight: 600; line-height: 1.7; }
.rules-assistant-excerpt { margin: 6px 0 0; color: var(--muted); font-size: .8125rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
</style>
