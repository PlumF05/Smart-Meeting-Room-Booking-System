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

function options(csrfToken: string, signal?: AbortSignal) {
  return { headers: { 'X-CSRF-Token': csrfToken }, timeout: 65_000, signal }
}

function conversationPath(id: string) {
  return `/assistant/conversations/${encodeURIComponent(id)}`
}

export const meetingAssistantApi = {
  async start(message: string, csrfToken: string, signal?: AbortSignal): Promise<ConversationView> {
    const { data } = await http.post<ConversationView>('/assistant/conversations/start', { message }, options(csrfToken, signal))
    return data
  },
  async message(id: string, revision: number, message: string, csrfToken: string, signal?: AbortSignal): Promise<ConversationView> {
    const { data } = await http.post<ConversationView>(`${conversationPath(id)}/message`, { revision, message }, options(csrfToken, signal))
    return data
  },
  async select(id: string, revision: number, roomId: number, csrfToken: string, signal?: AbortSignal): Promise<ConversationView> {
    const { data } = await http.post<ConversationView>(`${conversationPath(id)}/select`, { revision, room_id: roomId }, options(csrfToken, signal))
    return data
  },
  async showAll(id: string, revision: number, csrfToken: string, signal?: AbortSignal): Promise<ConversationView> {
    const { data } = await http.post<ConversationView>(`${conversationPath(id)}/candidates`, { revision, show_all: true }, options(csrfToken, signal))
    return data
  },
  async prepare(id: string, revision: number, csrfToken: string, signal?: AbortSignal): Promise<ConversationView> {
    const { data } = await http.post<ConversationView>(`${conversationPath(id)}/prepare`, { revision }, options(csrfToken, signal))
    return data
  },
  async close(id: string, csrfToken: string): Promise<void> {
    await http.delete(conversationPath(id), options(csrfToken))
  },
}
