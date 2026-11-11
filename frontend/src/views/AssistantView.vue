<script setup lang="ts">
import {
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { RouterLink } from "vue-router";
import { ElAlert, ElButton, ElInput } from "element-plus";
import { useAssistantChatStore } from "../stores/assistantChat.ts";
import VoiceControls from "../components/VoiceControls.vue";
import { useAssistantVoice } from "../voice/useAssistantVoice.ts";

const chat = useAssistantChatStore();
const voice = useAssistantVoice(chat);
const log = ref<HTMLElement | null>(null);
const composer = ref<InstanceType<typeof ElInput> | null>(null);
const followBottom = ref(true);
const newMessages = ref(false);
const now = ref(Date.now());
const samples = ["明天14点到15点，6个人", "预约开始后还能取消吗？"];
let clock: ReturnType<typeof setInterval> | null = null;

function observeScroll() {
  const element = log.value;
  if (!element) return;
  followBottom.value =
    element.scrollHeight - element.scrollTop - element.clientHeight < 100;
  if (followBottom.value) newMessages.value = false;
}

async function bottom() {
  await nextTick();
  const element = log.value;
  if (element) element.scrollTop = element.scrollHeight;
  followBottom.value = true;
  newMessages.value = false;
}

watch(
  () => [chat.messages.length, chat.statusText, chat.error],
  async () => {
    if (followBottom.value) await bottom();
    else newMessages.value = true;
  },
  { flush: "post" },
);

async function send() {
  voice.pause("已切换为文字操作。");
  followBottom.value = true;
  await chat.send();
}
function sample(text: string) {
  voice.pause("已切换为文字操作。");
  chat.input = text;
  composer.value?.focus();
}
function modify() {
  voice.pause("请修改预约条件。");
  chat.input = "";
  composer.value?.focus();
}
function restart() {
  chat.newConversation();
  followBottom.value = true;
  composer.value?.focus();
}
function keydown(event: Event | KeyboardEvent) {
  if (!("key" in event)) return;
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void send();
  }
}

function time(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间不可用"
    : date.toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      });
}

const statusLabels: Record<string, string> = {
  pending: "待管理员审批",
  approved: "已批准",
  rejected: "已驳回",
  cancelled: "已取消",
  expired: "已过期",
  revoked: "已撤销",
  finished: "已结束",
  ended: "已结束",
};
function reservationStatus(value: unknown) {
  return typeof value === "string"
    ? (statusLabels[value] ?? "请查看我的预约")
    : "请查看我的预约";
}
function expired(value: string) {
  return new Date(value).getTime() <= now.value;
}

onMounted(() => {
  chat.expireIdle();
  void bottom();
  clock = setInterval(() => {
    now.value = Date.now();
    chat.expireIdle();
  }, 10_000);
});
onBeforeUnmount(() => {
  if (clock) clearInterval(clock);
});
</script>

<template>
  <section class="assistant-page" aria-labelledby="assistant-title">
    <header class="assistant-heading">
      <div>
        <h1 id="assistant-title">智能助手</h1>
      </div>
      <div class="assistant-actions">
        <ElButton :disabled="chat.pending || chat.outcomeUnknown" @click="restart"
          >新对话</ElButton
        >
      </div>
    </header>
    <div class="chat-panel">
      <div
        ref="log"
        class="chat-log"
        aria-label="预约对话记录"
        @scroll="observeScroll"
      >
        <article class="chat-message assistant-message">
          <span class="speaker">会议室预约助手</span>
          <div class="message-body">
            <p>
              你好，可以直接问预约规则，也可以告诉我日期、时段和人数，我会帮你查找会议室。
            </p>
            <div
              v-if="!chat.messages.length"
              class="suggestions"
              aria-label="试试这样说"
            >
              <button
                v-for="text in samples"
                :key="text"
                type="button"
                @click="sample(text)"
              >
                {{ text }}
              </button>
            </div>
          </div>
        </article>
        <article
          v-for="item in chat.messages"
          :key="item.id"
          class="chat-message"
          :class="item.role === 'user' ? 'user-message' : 'assistant-message'"
        >
          <span class="speaker">{{
            item.kind === "rules"
              ? (item.role === "user" ? "你 · 规则问答" : "规则问答")
              : (item.role === "user" ? "你" : "会议室助手")
          }}</span>
          <div class="message-body">
            <p class="message-text">{{ item.text }}</p>
            <section v-if="item.rulesAnswer" class="rule-evidence" aria-label="规则依据">
              <p v-if="!item.rulesAnswer.grounded">当前发布规则中没有足够依据。</p>
              <p>知识版本 {{ item.rulesAnswer.knowledge_version }}</p>
              <details v-if="item.rulesAnswer.sources.length">
                <summary>查看规则来源（{{ item.rulesAnswer.sources.length }}）</summary>
                <ol>
                  <li v-for="source in item.rulesAnswer.sources" :key="source.id">
                    <strong>{{ source.section }} · {{ source.title }} · {{ source.version }}</strong>
                    <p>{{ source.excerpt }}</p>
                  </li>
                </ol>
              </details>
            </section>
            <template v-if="item.view">
              <section
                v-if="item.view.candidates.length && !item.view.confirmation"
                class="candidate-list"
                aria-label="匹配会议室"
              >
                <p class="card-caption">
                  共 {{ item.view.total }} 间，显示
                  {{ item.view.candidates.length }} 间 · 按容量、名称排序
                </p>
                <div
                  v-for="room in item.view.candidates"
                  :key="room.id"
                  class="room-option"
                >
                  <div>
                    <h2>{{ room.name }}</h2>
                    <p>{{ room.location }} · {{ room.capacity }} 人</p>
                    <p>{{ room.equipment.join("、") || "无设备" }}</p>
                  </div>
                  <ElButton
                    :disabled="
                      !chat.isCurrentMessage(item) ||
                      chat.outcomeUnknown ||
                      !room.is_active
                    "
                    :aria-label="`选择${room.name}`"
                    @click="chat.selectRoom(room.id)"
                    >选这间</ElButton
                  >
                </div>
                <ElButton
                  v-if="item.view.total > item.view.candidates.length"
                  link
                  type="primary"
                  :disabled="
                    !chat.isCurrentMessage(item) || chat.outcomeUnknown
                  "
                  @click="chat.showAll"
                  >查看全部匹配会议室</ElButton
                >
                <p class="card-caption">
                  查询和选房不会占位，提交时将再次检查空闲情况。
                </p>
              </section>
              <p
                v-if="item.view.selected_room && !item.view.confirmation"
                class="selected-note"
              >
                已选：{{
                  item.view.selected_room.name
                }}。请继续补充上面提到的信息。
              </p>
              <section
                v-if="item.view.confirmation"
                class="confirmation-card"
                aria-label="预约申请确认摘要"
              >
                <div class="confirmation-heading">
                  <h2>请核对预约申请</h2>
                  <span>{{
                    chat.isCurrentMessage(item) &&
                    item.view.confirmation.state === "pending" &&
                    !expired(item.view.confirmation.expires_at)
                      ? "尚未提交"
                      : "提交前摘要"
                  }}</span>
                </div>
                <dl>
                  <div>
                    <dt>会议室</dt>
                    <dd>
                      {{
                        item.view.selected_room?.name ||
                        `会议室编号 ${item.view.confirmation.summary.room_id}`
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>日期时段</dt>
                    <dd>
                      {{ item.view.confirmation.summary.date }}<br />{{
                        item.view.confirmation.summary.start_time
                      }}—{{
                        item.view.confirmation.summary.end_time
                      }}（北京时间）
                    </dd>
                  </div>
                  <div>
                    <dt>参会人数</dt>
                    <dd>{{ item.view.confirmation.summary.attendees }} 人</dd>
                  </div>
                  <div>
                    <dt>所需设备</dt>
                    <dd>
                      {{
                        item.view.confirmation.summary.required_equipment.join(
                          "、",
                        ) || "未指定"
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>会议主题</dt>
                    <dd>
                      {{ item.view.confirmation.summary.title || "未填写" }}
                    </dd>
                  </div>
                  <div>
                    <dt>用途</dt>
                    <dd>
                      {{ item.view.confirmation.summary.purpose || "未填写" }}
                    </dd>
                  </div>
                </dl>
                <p class="confirmation-expiry">
                  {{
                    expired(item.view.confirmation.expires_at)
                      ? "摘要已过期"
                      : "确认有效至"
                  }}：{{ time(item.view.confirmation.expires_at) }}
                </p>
                <p class="card-caption">
                  确认后提交申请并占位，仍需管理员审批。五分钟有效期不会因重复查看延长。
                </p>
                <div
                  v-if="chat.isCurrentMessage(item)"
                  class="confirmation-actions"
                >
                  <ElButton
                    type="primary"
                    :disabled="!chat.canConfirm(now)"
                    @click="chat.confirm"
                    >确认提交申请</ElButton
                  >
                  <ElButton
                    v-if="
                      expired(item.view.confirmation.expires_at) ||
                      chat.unverifiedPlan ||
                      item.view.confirmation.state !== 'pending'
                    "
                    :disabled="chat.pending || chat.outcomeUnknown"
                    @click="chat.renewSummary"
                    >重新生成摘要</ElButton
                  >
                  <ElButton
                    :disabled="chat.pending || chat.outcomeUnknown"
                    @click="modify"
                    >修改条件</ElButton
                  >
                  <ElButton
                    :disabled="chat.pending || chat.outcomeUnknown"
                    @click="restart"
                    >放弃本次申请</ElButton
                  >
                </div>
                <p v-else class="card-caption">
                  历史摘要，请以当前对话的最新结果为准。
                </p>
                <p
                  v-if="
                    chat.isCurrentMessage(item) &&
                    expired(item.view.confirmation.expires_at)
                  "
                  class="card-caption"
                >
                  请重新生成摘要，或发送补充信息重新核对后再确认。
                </p>
              </section>
            </template>
            <section
              v-if="item.result?.reservation"
              class="result-card"
              aria-label="预约办理结果"
            >
              <h2>申请办理结果</h2>
              <dl>
                <div>
                  <dt>预约编号</dt>
                  <dd>{{ item.result.reservation.id }}</dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>
                    {{ reservationStatus(item.result.reservation.status) }}
                  </dd>
                </div>
                <div v-if="item.result.reservation.starts_at">
                  <dt>开始时间</dt>
                  <dd>{{ time(String(item.result.reservation.starts_at)) }}</dd>
                </div>
              </dl>
              <RouterLink :to="{ name: 'reservations' }"
                >到“我的预约”查看实际记录</RouterLink
              >
            </section>
          </div>
        </article>
        <p v-if="chat.pending" class="processing" role="status">
          <span aria-hidden="true"></span>{{ chat.statusText }}
        </p>
        <ElAlert
          v-if="chat.error"
          :title="chat.error"
          type="error"
          :closable="false"
          show-icon
          class="chat-error"
        />
        <div v-if="chat.outcomeUnknown" class="recovery-actions">
          <ElButton type="primary" :disabled="chat.pending" @click="chat.check"
            >核对提交结果</ElButton
          >
          <RouterLink :to="{ name: 'reservations' }">查看我的预约</RouterLink>
        </div>
      </div>
      <button
        v-if="newMessages"
        class="new-messages"
        type="button"
        @click="bottom"
      >
        有新消息，点击查看
      </button>
      <VoiceControls
        :supported="voice.supported"
        :phase="voice.phase.value"
        :active="voice.active.value"
        :status="voice.status.value"
        :preview="voice.preview.value"
        :error="voice.error.value"
        :read-supported="voice.readSupported"
        :reading="voice.reading.value"
        :can-read="voice.canRead.value"
        v-model:auto-read="voice.autoRead.value"
        :disabled="!chat.canUse"
        :pending="chat.pending"
        :has-reply="chat.messages.some((item) => item.role === 'assistant')"
        @start="voice.start"
        @pause="voice.pause()"
        @stop="voice.stop"
        @replay="voice.replay"
        @read="voice.readLatest"
        @stop-reading="voice.stopReading"
      />
      <form class="chat-composer" @submit.prevent="send">
        <label for="assistant-chat-input">输入规则问题或预约需求</label>
        <div class="composer-row">
          <ElInput
            id="assistant-chat-input"
            ref="composer"
            v-model="chat.input"
            type="textarea"
            :rows="2"
            :disabled="chat.pending || chat.outcomeUnknown || !chat.canUse"
            placeholder="例如：预约需要审批吗？或明天14点到15点，6个人。"
            @keydown="keydown"
          />
          <ElButton
            type="primary"
            native-type="submit"
            :loading="chat.pending"
            :disabled="chat.pending || chat.outcomeUnknown || !chat.canUse"
            >发送</ElButton
          >
        </div>
      </form>
    </div>
  </section>
</template>

<style scoped>
.rule-evidence {
  margin-top: .75rem;
  padding-top: .5rem;
  border-top: 1px solid var(--border);
  font-size: .8125rem;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.rule-evidence summary { cursor: pointer; color: var(--teal); }
.rule-evidence li p { white-space: pre-wrap; }
.assistant-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.assistant-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.assistant-page {
  color: var(--navy);
}
.assistant-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
  margin-bottom: 20px;
}
.assistant-heading h1 {
  margin: 0 0 8px;
  font-size: 1.5rem;
  line-height: 1.5;
}
.assistant-heading p {
  margin: 0;
  color: var(--muted);
  font-size: 0.875rem;
  line-height: 1.7;
}
.chat-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  height: calc(100dvh - 285px);
  min-height: 520px;
  max-height: 1050px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.chat-log {
  flex: 1;
  min-height: 0;
  padding: 28px 34px;
  overflow-y: auto;
  overscroll-behavior: contain;
  background: #f7fafb;
}
.chat-message {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  margin-bottom: 25px;
}
.speaker {
  margin: 0 0 7px 2px;
  font-size: 0.75rem;
  color: var(--muted);
}
.message-body {
  max-width: 78%;
  padding: 14px 19px;
  border: 1px solid #e2eaed;
  border-radius: 3px 12px 12px;
  background: #fff;
  min-width: 0;
}
.message-body > p {
  max-width: 65ch;
  margin: 0;
  font-size: 0.9375rem;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.user-message {
  align-items: flex-end;
}
.user-message .speaker {
  margin-right: 2px;
}
.user-message .message-body {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-8);
  border-radius: 12px 3px 12px 12px;
}
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 16px;
}
.suggestions button {
  cursor: pointer;
  text-align: left;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--teal);
  background: #fff;
  padding: 8px 11px;
  font-size: 0.8125rem;
  line-height: 1.5;
}
.suggestions button:hover {
  background: var(--el-color-primary-light-9);
  border-color: var(--teal);
}
.candidate-list {
  min-width: 440px;
  padding-top: 14px;
}
.room-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  padding: 15px 0;
  border-bottom: 1px solid var(--border);
}
.room-option h2,
.confirmation-card h2,
.result-card h2 {
  font-size: 0.9375rem;
  line-height: 1.6;
  margin: 0 0 5px;
}
.room-option p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 0.8125rem;
  line-height: 1.6;
}
.room-option .el-button {
  flex-shrink: 0;
}
.candidate-list > .el-button {
  margin-top: 12px;
}
.card-caption {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 0.75rem;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.message-body > .selected-note {
  margin-top: 14px;
  color: var(--teal);
  font-size: 0.8125rem;
}
.confirmation-card,
.result-card {
  min-width: 440px;
  margin-top: 17px;
  padding-top: 17px;
  border-top: 2px solid var(--teal);
}
.confirmation-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
}
.confirmation-heading span {
  color: var(--teal);
  font-size: 0.75rem;
}
dl {
  margin: 14px 0;
}
dl > div {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 14px;
  margin: 10px 0;
  font-size: 0.8125rem;
  line-height: 1.8;
}
dt {
  color: var(--muted);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.confirmation-expiry {
  margin: 14px 0 0;
  font-size: 0.75rem;
  line-height: 1.8;
  color: var(--teal);
}
.confirmation-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}
.confirmation-actions .el-button + .el-button {
  margin-left: 0;
}
.result-card a {
  font-size: 0.8125rem;
  line-height: 1.8;
}
.processing {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 15px 0 25px;
  color: var(--muted);
  font-size: 0.8125rem;
  line-height: 1.8;
}
.processing span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--teal);
}
.chat-error {
  max-width: 78%;
  margin-top: 16px;
}
.recovery-actions {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-top: 16px;
  font-size: 0.8125rem;
}
.new-messages {
  position: sticky;
  align-self: center;
  bottom: 0;
  padding: 9px 17px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--el-color-primary-light-9);
  color: var(--teal);
  font-size: 0.8125rem;
  cursor: pointer;
}
.chat-composer {
  flex-shrink: 0;
  padding: 17px 26px 14px;
  border-top: 1px solid var(--border);
  background: #fff;
}
.chat-composer label {
  display: block;
  margin-bottom: 9px;
  color: var(--muted);
  font-size: 0.8125rem;
}
.composer-row {
  display: flex;
  align-items: flex-end;
  gap: 16px;
}
.composer-row .el-button {
  height: 44px;
  min-width: 86px;
}
.chat-composer > p {
  color: var(--muted);
  font-size: 0.6875rem;
  line-height: 1.7;
  margin: 9px 0 0;
}
</style>
