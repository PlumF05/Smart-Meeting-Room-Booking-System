import { http } from './http.ts'
import type { UserRole } from '../types/auth.ts'
import type { WorkspaceInfo } from '../types/workspace.ts'

export const workspaceApi = {
  async overview(role: UserRole): Promise<WorkspaceInfo> {
    return (await http.get<WorkspaceInfo>(`/workspaces/${role}`)).data
  },
}
