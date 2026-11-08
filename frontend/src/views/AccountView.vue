<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../stores/auth'
import { RouterLink } from 'vue-router'
import { homeRouteFor } from '../router/permissions.ts'

const auth = useAuthStore()
const user = computed(() => auth.user)
const identityLabel = computed(() => {
  if (user.value?.identity_type === 'teacher') return '教师'
  if (user.value?.identity_type === 'student') return '学生'
  return '未填写'
})
const expiresLabel = computed(() => {
  if (!auth.expiresAt) return ''
  const expires = new Date(auth.expiresAt)
  if (Number.isNaN(expires.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(expires)
})
</script>

<template>
  <section v-if="user" class="account-page" aria-labelledby="account-title">
    <div class="account-heading">
      <div>
        <p class="welcome-label">欢迎回来</p>
        <h1 id="account-title">{{ user.full_name }}</h1>
        <p class="account-subtitle">你已登录学院会议室预约系统。</p>
      </div>
      <span class="signed-in-label"><span aria-hidden="true"></span>已登录</span>
    </div>

    <section class="account-profile" aria-labelledby="profile-title">
      <div class="profile-heading"><h2 id="profile-title">账号信息</h2><span>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</span></div>
      <dl class="profile-details">
        <div><dt>登录名</dt><dd>{{ user.username }}</dd></div>
        <div><dt>姓名</dt><dd>{{ user.full_name }}</dd></div>
        <div><dt>师生身份</dt><dd>{{ identityLabel }}</dd></div>
        <div><dt>账号角色</dt><dd>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</dd></div>
      </dl>
      <p v-if="expiresLabel" class="profile-session">本次登录有效至 {{ expiresLabel }}（北京时间）</p>
    </section>

    <section class="next-space" aria-labelledby="workspace-link-title">
      <svg viewBox="0 0 104 80" fill="none" aria-hidden="true">
        <path d="M7 69V11H97V69H7Z" stroke="currentColor" stroke-width="1.5" />
        <rect x="32" y="25" width="40" height="30" rx="8" stroke="currentColor" stroke-width="1.5" />
        <path d="M43 18H61M43 62H61M25 32V48M79 32V48M4 74H100" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
      <div>
        <h2 id="workspace-link-title">进入工作台</h2>
        <p>查看{{ user.role === 'admin' ? '管理员' : '用户' }}工作台及当前可用内容。</p>
        <RouterLink class="workspace-link" :to="{ name: homeRouteFor(user.role) }">进入{{ user.role === 'admin' ? '管理员' : '用户' }}工作台</RouterLink>
      </div>
    </section>
  </section>
</template>

<style scoped>
.workspace-link { display: inline-block; margin-top: 14px; font-size: 0.875rem; font-weight: 600; text-decoration: none; }
.workspace-link:hover { text-decoration: underline; }
</style>
