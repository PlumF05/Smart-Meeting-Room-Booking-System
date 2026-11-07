<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElAlert, ElButton } from 'element-plus'
import { workspaceApi } from '../api/workspace.ts'
import { ApiError, getErrorMessage } from '../api/http.ts'
import { useAuthStore } from '../stores/auth.ts'
import type { UserRole } from '../types/auth.ts'
import type { WorkspaceInfo } from '../types/workspace.ts'

const props = defineProps<{ role: UserRole }>()
const auth = useAuthStore()
const router = useRouter()
const context = ref<WorkspaceInfo | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const title = computed(() => props.role === 'admin' ? '管理员工作台' : '用户工作台')
let requestVersion = 0

async function loadWorkspace() {
  const version = ++requestVersion
  const expectedRole = props.role
  const expectedUserId = auth.user?.id
  context.value = null
  errorMessage.value = ''
  loading.value = false
  if (!auth.isAuthenticated || expectedUserId === undefined) return
  if (auth.user?.role !== expectedRole) {
    await router.replace({ name: 'forbidden' })
    return
  }

  const isCurrent = () => version === requestVersion
    && props.role === expectedRole && auth.user?.id === expectedUserId
    && auth.user?.role === expectedRole
  loading.value = true
  try {
    const result = await workspaceApi.overview(expectedRole)
    if (!isCurrent()) return
    if (result.workspace !== expectedRole || result.user.role !== expectedRole
      || result.user.id !== expectedUserId) {
      // Cookie可能已在另一标签页换号，先同步身份，不能展示未经确认的新账号资料。
      await auth.restoreSession(true)
      if (isCurrent()) errorMessage.value = '账号信息已更新，请重试。'
      return
    }
    context.value = result
  } catch (error) {
    if (!isCurrent()) return
    errorMessage.value = getErrorMessage(error)
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      try {
        await auth.restoreSession(true)
      } catch {
        return
      }
      if (version !== requestVersion) return
      if (!auth.isAuthenticated) await router.replace({ name: 'login' })
      else if (error.status === 403) await router.replace({ name: 'forbidden' })
      else errorMessage.value = '登录信息已更新，请重试。'
    }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

// 同一工作台内换号或路由组件复用时，立即丢弃上一账号的上下文。
watch([() => props.role, () => auth.user?.id, () => auth.user?.role], () => {
  void loadWorkspace()
}, { immediate: true })
onBeforeUnmount(() => { requestVersion += 1 })
</script>

<template>
  <section class="workspace-page" aria-labelledby="workspace-title">
    <div class="account-heading">
      <div>
        <p class="welcome-label">学院会议室预约</p>
        <h1 id="workspace-title">{{ title }}</h1>
        <p class="account-subtitle">{{ role === 'admin' ? '在这里办理学院会议室管理事务。' : '在这里安排你的学院会议。' }}</p>
      </div>
      <RouterLink class="profile-link" :to="{ name: 'account' }">查看账号信息</RouterLink>
    </div>

    <section class="workspace-context" aria-labelledby="workspace-context-title" :aria-busy="loading">
      <p v-if="loading" class="workspace-status" role="status">正在确认工作台信息…</p>
      <template v-else-if="errorMessage">
        <ElAlert :title="errorMessage" type="error" :closable="false" show-icon role="alert" />
        <ElButton class="retry-button" :loading="loading" @click="loadWorkspace">重试</ElButton>
      </template>
      <template v-else-if="context">
        <div class="workspace-context-heading">
          <h2 id="workspace-context-title">{{ context.user.full_name }}，欢迎回来</h2>
          <span>{{ context.user.role === 'admin' ? '管理员' : '普通用户' }}</span>
        </div>
        <dl class="workspace-identity">
          <div><dt>当前账号</dt><dd>{{ context.user.username }}</dd></div>
          <div><dt>身份</dt><dd>{{ context.user.role === 'admin' ? '管理员' : context.user.identity_type === 'teacher' ? '教师' : '学生' }}</dd></div>
        </dl>
        <p class="workspace-availability">{{ role === 'admin' ? '审批与资源维护功能尚未开放。' : '预约与查询功能尚未开放。' }}</p>
      </template>
    </section>
  </section>
</template>

<style scoped>
.profile-link { flex-shrink: 0; font-size: 14px; font-weight: 600; text-decoration: none; }
.profile-link:hover { text-decoration: underline; }
.workspace-context { padding: 30px 32px; background: #fff; border: 1px solid var(--border); border-radius: 10px; }
.workspace-status { margin: 8px 0; color: var(--muted); font-size: 15px; }
.workspace-context-heading { display: flex; justify-content: space-between; align-items: center; gap: 24px; }
.workspace-context-heading h2 { margin: 0; font-size: 21px; line-height: 1.5; overflow-wrap: anywhere; }
.workspace-context-heading span { color: var(--teal); font-size: 14px; white-space: nowrap; }
.workspace-identity { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin: 30px 0; }
.workspace-identity dt { margin-bottom: 10px; color: var(--muted); font-size: 13px; }
.workspace-identity dd { margin: 0; font-size: 16px; line-height: 1.6; overflow-wrap: anywhere; }
.workspace-availability { margin: 0; padding-top: 22px; border-top: 1px solid var(--border); color: var(--muted); font-size: 14px; line-height: 1.8; }
.retry-button { margin-top: 20px; }
</style>
