export interface RoomInfo {
  id: number
  name: string
  location: string
  capacity: number
  equipment: string[]
  // 资源启停状态；不能据此判断某个时段是否可预约。
  is_active: boolean
}

export interface RoomFilters {
  attendees?: number
  equipment?: string[]
  is_active?: boolean
}

export interface RoomWindowQuery {
  date: string
  start_time: string
  end_time: string
}

export interface RoomOccupancy {
  room_id: number
  available: boolean
  is_active: boolean
  occupancy: { status: 'pending' | 'approved'; starts_at: string; ends_at: string }[]
  server_now: string
  reserves_room: false
}

export interface ValidatedRoomWindow extends RoomWindowQuery {
  starts_at: string
  ends_at: string
}

export interface RoomWindowCheck {
  timezone: 'Asia/Shanghai'
  server_now: string
  min_date: string
  max_date: string
  opens_at: string
  closes_at: string
  slot_minutes: number
  min_duration_minutes: number
  max_duration_minutes: number
  time_rules_passed: boolean
  // 当前仅校验时间规则，不能将成功响应当作时段空闲或已占位。
  availability_checked: false
  window: ValidatedRoomWindow | null
}
