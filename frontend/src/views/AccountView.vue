<script setup lang="ts">
import { computed } from "vue";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const user = computed(() => auth.user);
const identityLabel = computed(() => {
  if (user.value?.identity_type === "teacher") return "教师";
  if (user.value?.identity_type === "student") return "学生";
  return "未填写";
});
</script>

<template>
  <section v-if="user" class="account-page" aria-labelledby="account-title">
    <div class="account-heading">
      <div>
        <p class="welcome-label">欢迎回来</p>
        <h1 id="account-title">{{ user.full_name }}</h1>
      </div>
      <span class="signed-in-label"
        ><span aria-hidden="true"></span>已登录</span
      >
    </div>

    <section class="account-profile" aria-labelledby="profile-title">
      <div class="profile-heading">
        <h2 id="profile-title">账号信息</h2>
        <span>{{ user.role === "admin" ? "管理员" : "普通用户" }}</span>
      </div>
      <dl class="profile-details">
        <div>
          <dt>登录名</dt>
          <dd>{{ user.username }}</dd>
        </div>
        <div>
          <dt>姓名</dt>
          <dd>{{ user.full_name }}</dd>
        </div>
        <div>
          <dt>身份</dt>
          <dd>{{ identityLabel }}</dd>
        </div>
        <div>
          <dt>账号角色</dt>
          <dd>{{ user.role === "admin" ? "管理员" : "普通用户" }}</dd>
        </div>
      </dl>
    </section>
  </section>
</template>
