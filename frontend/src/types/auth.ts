export type IdentityType = 'teacher' | 'student'
export type UserRole = 'user' | 'admin'

export interface CurrentUser {
  id: number
  username: string
  full_name: string
  identity_type: IdentityType | null
  role: UserRole
  is_active: boolean
}

export interface SessionInfo {
  user: CurrentUser
  csrf_token: string
  expires_at: string
}

export interface LoginInput {
  username: string
  password: string
}

export interface RegisterInput extends LoginInput {
  full_name: string
  identity_type: IdentityType
  confirm_password: string
}

export interface RegisteredUser extends CurrentUser {
  identity_type: IdentityType
  role: 'user'
}
