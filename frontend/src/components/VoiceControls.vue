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
        <ElButton v-if="!active || reading" type="primary" :disabled="disabled || !supported || pending" @click="$emit('start')">{{ phase === 'paused' ? '继续语音' : '开始语音' }}</ElButton>
        <ElButton v-else @click="$emit('pause')">暂停语音</ElButton>
        <ElButton :disabled="phase === 'idle'" @click="$emit('stop')">停止语音</ElButton>
        <ElButton :disabled="disabled || !supported || pending || !hasReply" @click="$emit('replay')">重听回复</ElButton>
      </div>
    </div>
    <div class="readout-actions" role="group" aria-label="助手回复朗读">
      <ElCheckbox :model-value="autoRead" :disabled="disabled || !readSupported"
        @update:model-value="$emit('update:autoRead', $event === true)">自动朗读新回复</ElCheckbox>
      <ElButton :disabled="disabled || !canRead" @click="$emit('read')">{{ reading ? '重新朗读最' : '朗读' }}</ElButton>
      <ElButton :disabled="!reading" @click="$emit('stopReading')">停止朗读</ElButton>
    </div>
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
.voice-dot.listening { background: var(--teal); outline: 4px solid var(--el-color-primary-light-8); }
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
