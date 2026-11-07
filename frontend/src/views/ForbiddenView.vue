<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '../stores/auth.ts'
import { homeRouteFor } from '../router/permissions.ts'

const auth = useAuthStore()
const destination = computed(() => auth.user ? homeRouteFor(auth.user.role) : 'login')
</script>

<template>
  <section class="forbidden-page" aria-labelledby="forbidden-title">
    <div class="access-mark" aria-hidden="true">
      <svg viewBox="0 0 40 40" fill="none"><path d="M11 18V12a9 9 0 0 1 18 0v6M9 18h22v18H9z" stroke="currentColor" stroke-width="1.8" /><path d="M20 24v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>
    </div>
    <h1 id="forbidden-title">无权访问此页面</h1>
    <p>当前账号没有此页面的访问权限，请返回自己的工作台。</p>
    <div class="access-links">
      <RouterLink class="back-to-workspace" :to="{ name: destination }">返回我的工作台</RouterLink>
      <RouterLink :to="{ name: 'account' }">查看账号信息</RouterLink>
    </div>
  </section>
</template>

<style scoped>
.forbidden-page { width: 680px; max-width: 100%; margin: 28px auto; padding: 44px 46px; border: 1px solid var(--border); border-radius: 10px; background: #fff; }
.access-mark { display: grid; place-items: center; width: 58px; height: 58px; margin-bottom: 26px; border-radius: 9px; color: var(--navy); background: #e8f0f2; }
.access-mark svg { width: 34px; height: 34px; }
.forbidden-page h1 { margin: 0; font-size: 28px; line-height: 1.5; }
.forbidden-page p { margin: 16px 0 28px; color: var(--muted); font-size: 15px; line-height: 1.8; }
.access-links { display: flex; align-items: center; gap: 26px; }
.access-links a { font-size: 14px; text-decoration: none; }
.access-links a:hover { text-decoration: underline; }
.back-to-workspace { display: inline-flex; align-items: center; min-height: 40px; padding: 8px 17px; border-radius: 6px; color: #fff; background: var(--teal); }
.back-to-workspace:hover { color: #fff; background: #005650; }
</style>
