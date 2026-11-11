<script setup lang="ts">
import { ref } from "vue";

const storageKey = "smrbs-high-contrast";
const enabled = ref(false);
try {
  enabled.value = localStorage.getItem(storageKey) === "on";
} catch {
  // Local storage may be unavailable.
}

function applyPreference() {
  document.documentElement.toggleAttribute("data-high-contrast", enabled.value);
}
applyPreference();

function toggle() {
  enabled.value = !enabled.value;
  applyPreference();
  try {
    localStorage.setItem(storageKey, enabled.value ? "on" : "off");
  } catch {
    // The current page can still use the preference.
  }
}
</script>

<template>
  <button type="button" :aria-pressed="enabled" aria-label="色觉辅助" @click="toggle">
    色觉辅助：{{ enabled ? "开" : "关" }}
  </button>
</template>

<style scoped>
button {
  min-height: 36px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--navy);
  background: #fff;
  cursor: pointer;
  font-size: .875rem;
  line-height: 1.5;
}
button[aria-pressed="true"] {
  color: #fff;
  background: var(--teal);
  border-color: var(--teal);
}
</style>
