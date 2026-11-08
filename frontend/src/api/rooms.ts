import { http } from './http.ts'
import type { RoomFilters, RoomInfo, RoomOccupancy, RoomWindowCheck, RoomWindowQuery } from '../types/rooms.ts'

export const roomsApi = {
  async list(filters: RoomFilters = {}): Promise<RoomInfo[]> {
    const params = new URLSearchParams()
    if (filters.attendees !== undefined) params.set('attendees', String(filters.attendees))
    for (const equipment of new Set(filters.equipment ?? [])) {
      // 后端要求重复的equipment键，不能使用数组下标或逗号连接替代。
      params.append('equipment', equipment)
    }
    if (filters.is_active !== undefined) params.set('is_active', String(filters.is_active))
    return (await http.get<RoomInfo[]>('/rooms', { params })).data
  },

  async detail(id: number): Promise<RoomInfo> {
    return (await http.get<RoomInfo>(`/rooms/${id}`)).data
  },

  async occupancy(query: RoomWindowQuery): Promise<RoomOccupancy[]> {
    return (await http.get<RoomOccupancy[]>('/rooms/occupancy', { params: new URLSearchParams({ ...query }) })).data
  },

  async queryWindow(query?: RoomWindowQuery): Promise<RoomWindowCheck> {
    const params = new URLSearchParams()
    if (query) {
      params.set('date', query.date)
      params.set('start_time', query.start_time)
      params.set('end_time', query.end_time)
    }
    return (await http.get<RoomWindowCheck>('/rooms/query-window', { params })).data
  },
}
