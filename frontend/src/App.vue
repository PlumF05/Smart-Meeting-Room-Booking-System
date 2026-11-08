<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { ElAlert, ElButton } from 'element-plus'
import BrandMark from './components/BrandMark.vue'
import { getErrorMessage } from './api/http'
import { useAuthStore } from './stores/auth'
import { homeRouteFor } from './router/permissions.ts'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const signingOut = ref(false)
const actionError = ref('')
const publicPage = computed(() => route.meta.guestOnly === true)
const homeRoute = computed(() => auth.user ? homeRouteFor(auth.user.role) : 'login')

async function signOut() {
  if (signingOut.value) return
  signingOut.value = true
  actionError.value = ''
  try {
    await auth.logout()
    await router.replace({ name: 'login' })
  } catch (error) {
    actionError.value = getErrorMessage(error)
  } finally {
    signingOut.value = false
  }
}

async function retrySession() {
  actionError.value = ''
  try {
    await auth.restoreSession(true)
    await router.replace({ name: auth.isAuthenticated ? homeRoute.value : 'login' })
  } catch {
    // 会话恢复错误由store保留，页面继续显示可重试提示。
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <div class="header-inner">
        <RouterLink class="brand" :to="{ name: homeRoute }" aria-label="学院会议室预约首页">
          <span class="brand-mark"><BrandMark /></span>
          <span class="brand-name">学院会议室预约<span class="brand-abbreviation">SMRBS</span></span>
        </RouterLink>
        <div v-if="auth.isAuthenticated" class="header-account">
          <nav class="workspace-nav" aria-label="工作区导航">
            <RouterLink :to="{ name: homeRoute }">工作台</RouterLink>
            <RouterLink :to="{ name: 'rooms' }">会议室查询</RouterLink>
            <RouterLink v-if="auth.user?.role === 'user'" :to="{ name: 'reservations' }">我的预约</RouterLink>
            <RouterLink v-if="auth.user?.role === 'admin'" :to="{ name: 'management' }">审批与资源维护</RouterLink>
            <RouterLink v-if="auth.user?.role === 'admin'" :to="{ name: 'admin-tools' }">管理工具</RouterLink>
            <RouterLink :to="{ name: 'messages' }">站内消息</RouterLink>
            <RouterLink :to="{ name: 'account' }">账号信息</RouterLink>
          </nav>
          <span class="header-user" :title="auth.user?.full_name">{{ auth.user?.full_name }}</span>
          <ElButton :loading="signingOut" :disabled="signingOut || auth.restoring" @click="signOut">退出登录</ElButton>
        </div>
        <nav v-else class="account-nav" aria-label="账号导航">
          <RouterLink :to="{ name: 'login' }">登录</RouterLink>
          <RouterLink :to="{ name: 'register' }">注册</RouterLink>
        </nav>
      </div>
    </header>

    <div class="status-region" aria-live="polite">
      <div v-if="auth.restoreError" class="session-banner" role="alert">
        <p>{{ auth.restoreError }}</p>
        <ElButton size="small" :loading="auth.restoring" :disabled="auth.restoring" @click="retrySession">重新连接</ElButton>
      </div>
      <ElAlert v-if="actionError" class="action-alert" :title="actionError" type="error" show-icon @close="actionError = ''" />
    </div>

    <main :class="publicPage ? 'auth-layout' : 'account-layout'">
      <aside v-if="publicPage" class="auth-story" aria-label="学院会议室预约">
        <div class="story-copy">
          <p class="story-intro">学院里的讨论，从这里开始</p>
          <h1>让讨论<br />有处可去。</h1>
          <p class="story-description">教师与学生均可注册普通账号，<br class="desktop-break" />按照相同规则使用学院会议室。</p>
        </div>
        <svg class="room-plan" viewBox="0 0 420 220" fill="none" aria-hidden="true">
          <path d="M21 25H399V195H21V25Z" stroke="currentColor" stroke-width="1.5" />
          <path d="M36 40H384V180H36V40ZM148 40V180M272 40V180" stroke="currentColor" stroke-width="1.3" />
          <path d="M148 40H272V180H148V40Z" fill="#245369" stroke="#b1d8d8" stroke-width="1.7" />
          <g stroke="#c0dddd" stroke-width="1.5">
            <rect x="62" y="76" width="60" height="66" rx="12" />
            <rect x="178" y="70" width="65" height="78" rx="12" />
            <rect x="298" y="76" width="60" height="66" rx="12" />
            <path d="M77 67H107M77 151H107M54 91V127M130 91V127M194 61H227M194 157H227M169 88V131M252 88V131M313 67H343M313 151H343M290 91V127M366 91V127" stroke-linecap="round" />
          </g>
          <path d="M48 166H136M160 166H260M284 166H372" stroke="currentColor" stroke-dasharray="3 5" />
          <path d="M21 208H399M8 25V195" stroke="currentColor" stroke-width="1" opacity=".6" />
          <path d="M21 204V212M399 204V212M4 25H12M4 195H12" stroke="currentColor" opacity=".6" />
        </svg>
        <div class="story-note"><span class="story-note-line" aria-hidden="true"></span><p>一个账号，开启你的会议安排。</p></div>
      </aside>

      <div :class="publicPage ? 'auth-content' : 'account-content'">
        <div v-if="auth.restoring && !route.name" class="initial-loading" role="status">正在确认登录状态…</div>
        <RouterView v-else />
      </div>
    </main>

    <footer class="site-footer">学院智能会议室预约系统<span aria-hidden="true"> / </span>Smart Meeting Room Booking System</footer>
  </div>
</template>

<style scoped>
.workspace-nav { display: flex; align-items: center; gap: 16px; }
.header-inner { width: min(1400px, calc(100% - 64px)); }
.header-account { gap: 14px; }
.header-user { max-width: 100px; }
.workspace-nav a { padding: 9px 0; border-bottom: 2px solid transparent; color: var(--muted); font-size: 14px; text-decoration: none; white-space: nowrap; }
.workspace-nav a.router-link-active { color: var(--teal); border-bottom-color: var(--teal); font-weight: 600; }
</style>
