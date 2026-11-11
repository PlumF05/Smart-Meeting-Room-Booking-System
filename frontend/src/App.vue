<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { ElAlert, ElButton } from "element-plus";
import TextSizeSwitch from "./components/TextSizeSwitch.vue";
import ContrastSwitch from "./components/ContrastSwitch.vue";
import { getErrorMessage } from "./api/http";
import { useAuthStore } from "./stores/auth";
import { homeRouteFor } from "./router/permissions.ts";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const signingOut = ref(false);
const actionError = ref("");
const publicPage = computed(() => route.meta.guestOnly === true);
const homeRoute = computed(() =>
  auth.user ? homeRouteFor(auth.user.role) : "login",
);

async function signOut() {
  if (signingOut.value) return;
  signingOut.value = true;
  actionError.value = "";
  try {
    await auth.logout();
    await router.replace({ name: "login" });
  } catch (error) {
    actionError.value = getErrorMessage(error);
  } finally {
    signingOut.value = false;
  }
}

async function retrySession() {
  actionError.value = "";
  try {
    await auth.restoreSession(true);
    await router.replace({
      name: auth.isAuthenticated ? homeRoute.value : "login",
    });
  } catch {
    // 会话恢复错误由store保留，页面继续显示可重试提示。
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <div class="header-inner">
        <RouterLink
          class="brand"
          :to="{ name: homeRoute }"
          aria-label="学院会议室预约首页"
        >
          <span class="brand-name">会议室预约系统</span>
        </RouterLink>
        <div v-if="auth.isAuthenticated" class="header-account">
          <nav class="workspace-nav" aria-label="工作区导航">
            <RouterLink :to="{ name: 'rooms' }">会议室查询</RouterLink>
            <RouterLink
              v-if="auth.user?.role === 'user'"
              :to="{ name: 'reservations' }"
              >我的预约</RouterLink
            >
            <RouterLink
              v-if="auth.user?.role === 'user'"
              :to="{ name: 'assistant' }"
              >智能助手</RouterLink
            >
            <RouterLink
              v-if="auth.user?.role === 'admin'"
              :to="{ name: 'management' }"
              >审批与资源维护</RouterLink
            >
            <RouterLink
              v-if="auth.user?.role === 'admin'"
              :to="{ name: 'admin-tools' }"
              >管理工具</RouterLink
            >
            <RouterLink :to="{ name: 'messages' }">站内消息</RouterLink>
            <RouterLink :to="{ name: 'account' }">账号信息</RouterLink>
          </nav>
          <span class="header-user" :title="auth.user?.full_name">{{
            auth.user?.full_name
          }}</span>
          <ElButton
            :loading="signingOut"
            :disabled="signingOut || auth.restoring"
            @click="signOut"
            >退出登录</ElButton
          >
        </div>
        <nav v-else class="account-nav" aria-label="账号导航">
          <RouterLink :to="{ name: 'login' }">登录</RouterLink>
          <RouterLink :to="{ name: 'register' }">注册</RouterLink>
        </nav>
        <div class="display-controls">
          <TextSizeSwitch />
          <ContrastSwitch />
        </div>
      </div>
    </header>

    <div class="status-region" aria-live="polite">
      <div v-if="auth.restoreError" class="session-banner" role="alert">
        <p>{{ auth.restoreError }}</p>
        <ElButton
          size="small"
          :loading="auth.restoring"
          :disabled="auth.restoring"
          @click="retrySession"
          >重新连接</ElButton
        >
      </div>
      <ElAlert
        v-if="actionError"
        class="action-alert"
        :title="actionError"
        type="error"
        show-icon
        @close="actionError = ''"
      />
    </div>

    <main
      :class="
        publicPage
          ? 'auth-layout'
          : route.name === 'assistant'
            ? 'assistant-layout'
            : 'account-layout'
      "
    >
      <div :class="publicPage ? 'auth-content' : 'account-content'">
        <div
          v-if="auth.restoring && !route.name"
          class="initial-loading"
          role="status"
        >
          正在确认登录状态…
        </div>
        <RouterView v-else />
      </div>
    </main>
  </div>
</template>

<style scoped>
.workspace-nav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
}
.assistant-layout {
  width: min(1280px, calc(100% - 64px));
  margin: 28px auto 32px;
}
.header-inner {
  width: min(1400px, calc(100% - 64px));
  flex-wrap: wrap;
  padding-block: 16px;
  row-gap: 12px;
}
.display-controls {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
  white-space: nowrap;
}
.header-account {
  gap: 14px;
  flex-wrap: wrap;
}
.header-user {
  max-width: 100px;
}
.workspace-nav a {
  padding: 9px 0;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  font-size: 0.875rem;
  text-decoration: none;
  white-space: nowrap;
}
.workspace-nav a.router-link-active {
  color: var(--teal);
  border-bottom-color: var(--teal);
  font-weight: 600;
}
</style>
