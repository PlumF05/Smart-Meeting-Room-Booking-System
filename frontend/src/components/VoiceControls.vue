<script setup lang="ts">
import { ElButton, ElCheckbox } from 'element-plus'
import type { VoicePhase } from '../voice/useAssistantVoice.ts'
defineProps<{ supported: boolean; phase: VoicePhase; active: boolean; status: string; preview: string;
  error: string; disabled: boolean; pending: boolean; hasReply: boolean; readSupported: boolean;
  reading: boolean; canRead: boolean; autoRead: boolean }>()
defineEmits<{ start: []; pause: []; stop: []; replay: []; read: []; stopReading: []; 'update:autoRead': [value: boolean] }>()
</script>

<template>
  <section class="voice-controls" aria-labelledby="voice-title">
    <div class="voice-topline">
      <div class="voice-status"><strong id="voice-title">语音预约</strong><span class="voice-dot" :class="{ listening: phase === 'listening' }" aria-hidden="true"></span><span role="status">{{ status }}</span></div>
      <div class="voice-actions">
        <ElButton v-if="!active || reading" type="primary" :disabled="disabled || !supported || pending" @click="$emit('start')">{{ phase === 'paused' ? '继续语音' : '开始语音预约' }}</ElButton>
        <ElButton v-else @click="$emit('pause')">暂停语音</ElButton>
        <ElButton :disabled="phase === 'idle'" @click="$emit('stop')">停止语音</ElButton>
        <ElButton :disabled="disabled || !supported || pending || !hasReply" @click="$emit('replay')">重听回复</ElButton>
      </div>
    </div>
    <div class="readout-actions" role="group" aria-label="助手回复朗读">
      <ElCheckbox :model-value="autoRead" :disabled="disabled || !readSupported"
        @update:model-value="$emit('update:autoRead', $event === true)">自动朗读新回复</ElCheckbox>
      <ElButton :disabled="disabled || !canRead" @click="$emit('read')">{{ reading ? '重新朗读最新回复' : '朗读最新回复' }}</ElButton>
      <ElButton :disabled="!reading" @click="$emit('stopReading')">停止朗读</ElButton>
      <span class="readout-hint">文字聊天也可朗读，无需麦克风；开关仅本次页面有效。</span>
    </div>
    <p v-if="!readSupported" class="voice-error">当前浏览器不支持语音朗读，请使用文字回复。</p>
    <p v-if="!supported && readSupported" class="voice-error">当前浏览器不支持完整语音预约，仍可使用上方的独立朗读功能或文字预约。</p>
    <p v-if="supported" class="voice-help">连续语音预约：首次使用请允许麦克风。等“麦克风已开始采集”再说话，停顿约两秒后自动发送；回答读完后继续听取。可说“暂停语音”“停止语音”“重听回复”。在线识别由浏览器语音服务处理，本系统不保存录音。</p>
    <p v-if="preview" class="voice-preview"><span>识别内容：</span>{{ preview }}</p>
    <p v-if="error" class="voice-error" role="alert">{{ error }} 可继续语音或改用文字。</p>
  </section>
</template>

<style scoped>
.voice-controls { flex-shrink: 0; padding: 14px 26px; border-top: 1px solid var(--border); background: #f1f7f6; }
.voice-topline, .voice-status, .voice-actions { display: flex; align-items: center; gap: 12px; }
.voice-topline { justify-content: space-between; }
.voice-status { font-size: .8125rem; line-height: 1.7; }
.voice-status strong { font-size: .875rem; white-space: nowrap; }
.voice-dot { width: 7px; height: 7px; flex-shrink: 0; border-radius: 50%; background: #899b9c; }
.voice-dot.listening { background: var(--teal); outline: 4px solid #d4e7e2; }
.voice-actions { flex-shrink: 0; gap: 8px; }
.voice-actions .el-button + .el-button { margin-left: 0; }
.readout-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; }
.readout-actions .el-button + .el-button { margin-left: 0; }
.readout-hint { color: var(--muted); font-size: .75rem; line-height: 1.75; }
.voice-help, .voice-preview, .voice-error { margin: 8px 0 0; font-size: .75rem; line-height: 1.75; overflow-wrap: anywhere; }
.voice-help, .voice-preview span { color: var(--muted); }
.voice-error { color: #a2342e; }
.voice-preview { max-height: 70px; overflow-y: auto; }
</style>
