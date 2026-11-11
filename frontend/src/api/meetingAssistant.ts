import { http } from './http.ts'
import type { RoomInfo } from '../types/rooms.ts'

export interface MeetingDraft {
  date: string | null
  start_time: string | null
  end_time: string | null
  attendees: number | null
  equipment: string[]
  title: string | null
  purpose: string | null
}

export interface AssistantConfirmation {
  confirmation_id: string
  conversation_id: string
  revision: number
  action: 'submit'
  summary: {
    room_id: number
    date: string
    start_time: string
    end_time: string
    attendees: number
    required_equipment: string[]
    title: string
    purpose: string
  }
  created_at: string
  expires_at: string
  state: 'pending' | 'consumed' | 'expired' | 'superseded'
  result: Record<string, unknown> | null
}

export interface ConversationView {
  conversation_id: string
  revision: number
  phase: 'clarify' | 'candidates' | 'no_match' | 'summary'
  message: string
  draft: MeetingDraft
  candidates: RoomInfo[]
  total: number
  checked_at: string | null
  selected_room: RoomInfo | null
  confirmation: AssistantConfirmation | null
}

export const meetingAssistantApi = {
  async close(id: string, csrfToken: string): Promise<void> {
    await http.delete(`/assistant/conversations/${encodeURIComponent(id)}`, {
      headers: { 'X-CSRF-Token': csrfToken }, timeout: 65_000,
    })
  },
}
