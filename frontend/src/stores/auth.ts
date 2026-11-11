import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { authApi } from "../api/auth.ts";
import { ApiError, getErrorMessage } from "../api/http.ts";
import type {
  CurrentUser,
  LoginInput,
  RegisterInput,
  SessionInfo,
} from "../types/auth.ts";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<CurrentUser | null>(null);
  const status = ref<"unknown" | "authenticated" | "anonymous">("unknown");
  const csrfToken = ref("");
  const expiresAt = ref("");
  const restoring = ref(false);
  const pending = ref(false);
  const restoreError = ref("");
  const notice = ref("");
  const sessionChangeCount = ref(0);
  const isAuthenticated = computed(
    () => status.value === "authenticated" && user.value !== null,
  );
  let registrationUsername = "";
  let generation = 0;
  let restoreRequest: Promise<void> | null = null;

  function invalidateRestoration() {
    generation += 1;
    restoreRequest = null;
    restoring.value = false;
  }

  function applySession(info: SessionInfo) {
    user.value = info.user;
    csrfToken.value = info.csrf_token;
    expiresAt.value = info.expires_at;
    status.value = "authenticated";
    restoreError.value = "";
  }

  function clearSession() {
    user.value = null;
    csrfToken.value = "";
    expiresAt.value = "";
    status.value = "anonymous";
    restoreError.value = "";
  }

  function restoreSession(force = false): Promise<void> {
    if (restoreRequest) return restoreRequest;
    if (!force && status.value !== "unknown") return Promise.resolve();
    const startedAt = generation;
    restoring.value = true;
    const request = Promise.resolve()
      .then(async () => {
        try {
          const info = await authApi.me();
          if (startedAt === generation) applySession(info);
        } catch (error) {
          // 登录/退出之后才到达的旧查询不能覆盖新的账号状态。
          if (startedAt !== generation) return;
          if (error instanceof ApiError && error.status === 401) {
            if (status.value === "authenticated")
              notice.value = "登录已失效，请重新登录。";
            clearSession();
          } else {
            restoreError.value = getErrorMessage(error);
            throw error;
          }
        }
      })
      .finally(() => {
        if (restoreRequest === request) {
          restoreRequest = null;
          restoring.value = false;
        }
      });
    restoreRequest = request;
    return request;
  }

  function beginMutation() {
    if (pending.value) throw new ApiError(null, "操作正在处理中，请稍候。");
    pending.value = true;
    invalidateRestoration();
  }

  async function login(data: LoginInput): Promise<void> {
    beginMutation();
    try {
      const info = await authApi.login(data);
      invalidateRestoration();
      applySession(info);
      notice.value = "";
      sessionChangeCount.value += 1;
    } finally {
      pending.value = false;
    }
  }

  async function register(data: RegisterInput): Promise<void> {
    if (pending.value) throw new ApiError(null, "操作正在处理中，请稍候。");
    pending.value = true;
    try {
      const registered = await authApi.register(data);
      registrationUsername = registered.username;
      notice.value = "注册成功，请使用新账号登录。";
    } finally {
      pending.value = false;
    }
  }

  async function logout(): Promise<void> {
    beginMutation();
    try {
      if (!csrfToken.value) await restoreSession(true);
      if (!csrfToken.value) {
        clearSession();
      } else {
        try {
          await authApi.logout(csrfToken.value);
        } catch (error) {
          if (error instanceof ApiError && error.status === 403) {
            await restoreSession(true);
            throw new ApiError(
              403,
              "登录信息已更新，请确认当前账号后重试退出。",
            );
          }
          if (!(error instanceof ApiError && error.status === 401)) throw error;
        }
        invalidateRestoration();
        clearSession();
      }
      notice.value = "";
      sessionChangeCount.value += 1;
    } finally {
      pending.value = false;
    }
  }

  function clearNotice() {
    notice.value = "";
  }
  function sessionChangedExternally() {
    invalidateRestoration();
  }
  function takeRegistrationUsername() {
    const result = registrationUsername;
    registrationUsername = "";
    return result;
  }

  // 凭据由HttpOnly Cookie管理；资料与CSRF只留内存，不使用持久化插件。
  return {
    user,
    status,
    csrfToken,
    expiresAt,
    restoring,
    pending,
    restoreError,
    notice,
    sessionChangeCount,
    isAuthenticated,
    restoreSession,
    login,
    register,
    logout,
    clearNotice,
    takeRegistrationUsername,
    sessionChangedExternally,
  };
});
