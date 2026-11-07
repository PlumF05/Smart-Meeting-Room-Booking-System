import type { UserRole } from '../types/auth.ts'

export function homeRouteFor(role: UserRole): 'admin' | 'workspace' {
  return role === 'admin' ? 'admin' : 'workspace'
}

export function roleAllowed(role: UserRole, allowedRoles?: readonly UserRole[]): boolean {
  return allowedRoles === undefined || allowedRoles.includes(role)
}

declare module 'vue-router' {
  interface RouteMeta {
    guestOnly?: boolean
    requiresAuth?: boolean
    roles?: readonly UserRole[]
  }
}
