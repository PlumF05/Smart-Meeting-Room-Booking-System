<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput, ElRadio, ElRadioGroup, type FormInstance, type FormRules } from 'element-plus'
import { getErrorMessage } from '../api/http'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const errorMessage = ref('')
let active = true
const form = reactive({
  username: '', full_name: '', identity_type: 'student' as 'teacher' | 'student',
  password: '', confirm_password: '',
})
const rules: FormRules<typeof form> = {
  username: [
    { required: true, message: '请输入登录名', trigger: 'blur' },
    { pattern: /\S/, message: '登录名不能只有空格', trigger: 'blur' },
    { max: 64, message: '登录名不能超过64个字符', trigger: 'blur' },
  ],
  full_name: [
    { required: true, message: '请输入姓名', trigger: 'blur' },
    { pattern: /\S/, message: '姓名不能只有空格', trigger: 'blur' },
    { max: 100, message: '姓名不能超过100个字符', trigger: 'blur' },
  ],
  identity_type: [{ required: true, message: '请选择教师或学生身份', trigger: 'change' }],
  password: [{
    validator: (_rule, value: string, callback) => {
      const length = Array.from(value).length
      if (!length) callback(new Error('请输入密码'))
      else if (length < 8) callback(new Error('密码至少需要8个字符'))
      else if (length > 1024) callback(new Error('密码不能超过1024个字符'))
      else callback()
    }, trigger: 'blur',
  }],
  confirm_password: [{
    validator: (_rule, value: string, callback) => {
      if (!value) callback(new Error('请再次输入密码'))
      else if (value !== form.password) callback(new Error('两次输入的密码不一致'))
      else callback()
    }, trigger: 'blur',
  }],
}

async function submit() {
  if (submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const valid = await formRef.value?.validate().catch(() => false)
    if (!valid) return
    await auth.register({
      username: form.username.trim(), full_name: form.full_name.trim(),
      identity_type: form.identity_type, password: form.password,
      confirm_password: form.confirm_password,
    })
    form.password = ''
    form.confirm_password = ''
    if (active) await router.replace({ name: 'login' })
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  } finally {
    submitting.value = false
  }
}

function recheckConfirmation() {
  if (form.confirm_password) void formRef.value?.validateField('confirm_password').catch(() => undefined)
}

onBeforeUnmount(() => {
  active = false
  form.password = ''
  form.confirm_password = ''
})
</script>

<template>
  <section class="auth-form-panel register-panel" aria-labelledby="register-title">
    <div class="form-heading">
      <h2 id="register-title">注册普通账号</h2>
      <p>填写以下信息，注册后即可返回登录。</p>
    </div>
    <ElAlert v-if="errorMessage" class="form-alert" :title="errorMessage" type="error" :closable="false" show-icon role="alert" />
    <ElForm ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="submitting" hide-required-asterisk @submit.prevent="submit">
      <ElFormItem label="登录名" prop="username">
        <ElInput v-model="form.username" placeholder="设置一个不重复的登录名" autocomplete="username" :maxlength="64" autofocus />
      </ElFormItem>
      <ElFormItem label="姓名" prop="full_name">
        <ElInput v-model="form.full_name" placeholder="请输入姓名" autocomplete="name" :maxlength="100" />
      </ElFormItem>
      <ElFormItem label="师生身份" prop="identity_type" class="identity-form-item">
        <ElRadioGroup v-model="form.identity_type" aria-label="师生身份">
          <ElRadio value="student" border>学生</ElRadio>
          <ElRadio value="teacher" border>教师</ElRadio>
        </ElRadioGroup>
        <p class="field-help">教师与学生享有相同的普通用户权限。</p>
      </ElFormItem>
      <ElFormItem label="密码" prop="password">
        <ElInput v-model="form.password" type="password" placeholder="至少8个字符" autocomplete="new-password" show-password :maxlength="1024" @input="recheckConfirmation" />
      </ElFormItem>
      <ElFormItem label="确认密码" prop="confirm_password">
        <ElInput v-model="form.confirm_password" type="password" placeholder="请再次输入密码" autocomplete="new-password" show-password :maxlength="1024" />
      </ElFormItem>
      <ElButton class="submit-button" type="primary" native-type="submit" :loading="submitting" :disabled="submitting">注册账号</ElButton>
    </ElForm>
    <p class="form-switch">已有账号？<RouterLink :to="{ name: 'login' }" :aria-disabled="submitting" :tabindex="submitting ? -1 : undefined" :class="{ 'link-disabled': submitting }">返回登录</RouterLink></p>
  </section>
</template>
