<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput, type FormInstance, type FormRules } from 'element-plus'
import { getErrorMessage } from '../api/http'
import { useAuthStore } from '../stores/auth'
import { homeRouteFor } from '../router/permissions.ts'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const errorMessage = ref('')
let active = true
const form = reactive({ username: auth.takeRegistrationUsername(), password: '' })
const rules: FormRules<typeof form> = {
  username: [
    { required: true, message: '请输入登录名', trigger: 'blur' },
    { pattern: /\S/, message: '登录名不能只有空格', trigger: 'blur' },
    { max: 64, message: '登录名不能超过64个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { max: 1024, message: '密码不能超过1024个字符', trigger: 'blur' },
  ],
}

async function submit() {
  if (submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const valid = await formRef.value?.validate().catch(() => false)
    if (!valid) return
    auth.clearNotice()
    await auth.login({ username: form.username.trim(), password: form.password })
    form.password = ''
    if (active && auth.user) await router.replace({ name: homeRouteFor(auth.user.role) })
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => {
  active = false
  form.password = ''
})
</script>

<template>
  <section class="auth-form-panel" aria-labelledby="login-title">
    <div class="form-heading">
      <h2 id="login-title">登录账号</h2>
      <p>欢迎回来，请输入你的登录信息。</p>
    </div>
    <ElAlert v-if="auth.notice" class="form-alert" :title="auth.notice" type="info" show-icon @close="auth.clearNotice" />
    <ElAlert v-if="errorMessage" class="form-alert" :title="errorMessage" type="error" :closable="false" show-icon role="alert" />
    <ElForm ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="submitting" hide-required-asterisk @submit.prevent="submit">
      <ElFormItem label="登录名" prop="username">
        <ElInput v-model="form.username" placeholder="请输入登录名" autocomplete="username" :maxlength="64" autofocus />
      </ElFormItem>
      <ElFormItem label="密码" prop="password">
        <ElInput v-model="form.password" type="password" placeholder="请输入密码" autocomplete="current-password" show-password :maxlength="1024" />
      </ElFormItem>
      <ElButton class="submit-button" type="primary" native-type="submit" :loading="submitting" :disabled="submitting">登录</ElButton>
    </ElForm>
    <p class="form-switch">还没有账号？<RouterLink :to="{ name: 'register' }" :aria-disabled="submitting" :tabindex="submitting ? -1 : undefined" :class="{ 'link-disabled': submitting }">注册普通账号</RouterLink></p>
  </section>
</template>
