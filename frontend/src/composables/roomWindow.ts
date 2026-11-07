import { ApiError } from '../api/http.ts'
import type { RoomWindowCheck, RoomWindowQuery } from '../types/rooms.ts'

export interface RoomWindowDraft {
  date?: string
  start_time?: string
  end_time?: string
}

function calendarDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leapYear = year % 400 === 0 || (year % 4 === 0 && year % 100 !== 0)
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return day <= days[month - 1]
}

function clockTime(value: string): boolean {
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)
}

export function buildRoomWindowQuery(draft: RoomWindowDraft): RoomWindowQuery | null {
  const date = draft.date?.trim() ?? ''
  const start_time = draft.start_time?.trim() ?? ''
  const end_time = draft.end_time?.trim() ?? ''
  if (!date && !start_time && !end_time) return null
  if (!date || !start_time || !end_time) throw new ApiError(422, '使用日期、开始时间和结束时间需要一起填写。')
  if (!calendarDate(date)) throw new ApiError(422, '使用日期请输入有效的YYYY-MM-DD日期。')
  if (!clockTime(start_time) || !clockTime(end_time)) throw new ApiError(422, '起止时间请使用HH:mm格式。')
  // 日期范围、开放时段、时长和是否已到开始时间统一交由服务器判断。
  return { date, start_time, end_time }
}

export function roomTimeOptions(rules: RoomWindowCheck | null, boundary: 'start' | 'end'): string[] {
  if (!rules || !clockTime(rules.opens_at) || !clockTime(rules.closes_at)) return []
  const minutes = (value: string) => Number(value.slice(0, 2)) * 60 + Number(value.slice(3))
  const from = minutes(rules.opens_at)
  const until = minutes(rules.closes_at)
  const step = rules.slot_minutes
  if (!Number.isInteger(step) || step <= 0 || step > until - from) return []
  const values: string[] = []
  const start = boundary === 'start' ? from : from + step
  const end = boundary === 'start' ? until - step : until
  for (let value = start; value <= end; value += step) {
    values.push(`${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`)
  }
  return values
}
