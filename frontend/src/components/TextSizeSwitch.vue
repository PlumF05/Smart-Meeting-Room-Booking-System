<script setup lang="ts">
import { createTextSizePreference } from '../accessibility/textSize.ts'

const { size, setSize } = createTextSizePreference(
  document.documentElement,
  () => window.localStorage,
)
</script>

<template>
  <div class="text-size-switch" role="group" aria-label="字体大小">
    <span class="text-size-label">字号</span>
    <button type="button" :aria-pressed="size === 'standard'" @click="setSize('standard')">标准</button>
    <button type="button" :aria-pressed="size === 'large'" @click="setSize('large')">大号</button>
    <span class="text-size-status" role="status">{{ size === 'large' ? '当前为大号字体' : '当前为标准字体' }}</span>
  </div>
</template>

<style scoped>
.text-size-switch { display: inline-flex; flex-shrink: 0; align-items: center; gap: 4px; margin-left: auto; color: var(--navy); font-size: .875rem; }
.text-size-label { margin-right: 4px; }
button { min-height: 36px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 5px; color: var(--navy); background: #fff; cursor: pointer; line-height: 1.5; }
button:hover { border-color: var(--teal); }
button[aria-pressed="true"] { color: #fff; background: var(--teal); border-color: var(--teal); }
.text-size-status { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
