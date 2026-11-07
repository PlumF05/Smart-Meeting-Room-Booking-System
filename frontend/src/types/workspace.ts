import type { CurrentUser, UserRole } from './auth.ts'

export interface WorkspaceInfo {
  workspace: UserRole
  user: CurrentUser
}
