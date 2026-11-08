import { createApp, watch } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import './accessibility/typography.css'
import App from './App.vue'
import router from './router/index.ts'
import { pinia } from './stores/index.ts'
import { useAuthStore } from './stores/auth.ts'
import { homeRouteFor, roleAllowed } from './router/permissions.ts'

const app = createApp(App)
app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')

const auth = useAuthStore(pinia)
const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('smrbs-auth')
let externalChangePending = false

function identityDestination() {
  const route = router.currentRoute.value
  if (!auth.isAuthenticated && route.meta.requiresAuth) return { name: 'login' }
  if (auth.isAuthenticated && auth.user) {
    if (route.meta.guestOnly || route.name === 'home') return { name: homeRouteFor(auth.user.role) }
    if (!roleAllowed(auth.user.role, route.meta.roles)) return { name: 'forbidden' }
  }
  return null
}

async function synchronizeSession() {
  if (auth.pending || document.hidden) return
  externalChangePending = false
  try {
    await auth.restoreSession(true)
  } catch {
    return
  }
  if (auth.restoring) return
  const destination = identityDestination()
  if (destination) await router.replace(destination)
}

// 标签页间只通知“会话已变化”，不发送账号资料、密码或令牌。
const stopBroadcast = watch(() => auth.sessionChangeCount, () => channel?.postMessage('changed'))
if (channel) channel.onmessage = () => {
  // 外部换号通知不能复用换号前已经发出的/me请求。
  auth.sessionChangedExternally()
  externalChangePending = true
  void synchronizeSession()
}
const stopPendingSync = watch(() => auth.pending, (pending) => {
  if (!pending && externalChangePending) void synchronizeSession()
})
const onVisibility = () => { void synchronizeSession() }
window.addEventListener('focus', onVisibility)
document.addEventListener('visibilitychange', onVisibility)
const stopRedirect = watch([() => auth.status, () => auth.user?.role], () => {
  const destination = identityDestination()
  if (destination) void router.replace(destination)
})
if (import.meta.hot) import.meta.hot.dispose(() => {
  channel?.close()
  stopBroadcast()
  stopPendingSync()
  stopRedirect()
  window.removeEventListener('focus', onVisibility)
  document.removeEventListener('visibilitychange', onVisibility)
  app.unmount()
})
