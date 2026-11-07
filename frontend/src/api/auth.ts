import { http } from './http.ts'
import type { LoginInput, RegisteredUser, RegisterInput, SessionInfo } from '../types/auth.ts'

export const authApi = {
  async register(data: RegisterInput): Promise<RegisteredUser> {
    return (await http.post<RegisteredUser>('/auth/register', data)).data
  },
  async login(data: LoginInput): Promise<SessionInfo> {
    return (await http.post<SessionInfo>('/auth/login', data)).data
  },
  async me(): Promise<SessionInfo> {
    return (await http.get<SessionInfo>('/auth/me')).data
  },
  async logout(csrfToken: string): Promise<void> {
    await http.post('/auth/logout', undefined, { headers: { 'X-CSRF-Token': csrfToken } })
  },
}
