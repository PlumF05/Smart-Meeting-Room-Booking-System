import { createRouter, createWebHistory } from 'vue-router'
import { pinia } from '../stores/index.ts'
import { useAuthStore } from '../stores/auth.ts'
import { homeRouteFor, roleAllowed } from './permissions.ts'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('../views/AccountView.vue'), meta: { requiresAuth: true } },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { guestOnly: true } },
    { path: '/register', name: 'register', component: () => import('../views/RegisterView.vue'), meta: { guestOnly: true } },
    { path: '/account', name: 'account', component: () => import('../views/AccountView.vue'), meta: { requiresAuth: true } },
    { path: '/rooms', name: 'rooms', component: () => import('../views/RoomsView.vue'), meta: { requiresAuth: true } },
    { path: '/workspace', name: 'workspace', component: () => import('../views/WorkspaceView.vue'), props: { role: 'user' }, meta: { requiresAuth: true, roles: ['user'] } },
    { path: '/admin', name: 'admin', component: () => import('../views/WorkspaceView.vue'), props: { role: 'admin' }, meta: { requiresAuth: true, roles: ['admin'] } },
    { path: '/forbidden', name: 'forbidden', component: () => import('../views/ForbiddenView.vue'), meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: { name: 'home' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore(pinia)
  try {
    await auth.restoreSession(true)
  } catch {
    // 保留已知身份和重试提示，同时仍执行角色检查，不能因连接故障放开页面。
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) return { name: 'login' }
  if (auth.isAuthenticated && auth.user) {
    if (to.meta.guestOnly || to.name === 'home') return { name: homeRouteFor(auth.user.role) }
    if (!roleAllowed(auth.user.role, to.meta.roles)) return { name: 'forbidden' }
  }
  return true
})

export default router
