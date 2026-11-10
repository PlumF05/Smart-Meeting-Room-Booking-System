import type { AssistantConfirmation, ConversationView } from '../api/meetingAssistant.ts'
import type { ChatMessage } from '../stores/assistantChat.ts'
import type { RoomInfo } from '../types/rooms.ts'

export interface SpokenReply {
  text: string
  confirmationId?: string
  completed: boolean
}

const RENEW_SUMMARY = '请说重新生成摘要，核对新的完整摘要后再确认提交。'
const STATUS_LABELS: Record<string, string> = {
  pending: '待管理员审批',
  approved: '已批准',
  rejected: '已驳回',
  cancelled: '已取消',
  expired: '已过期',
  revoked: '已撤销',
  finished: '已结束',
}

function clean(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function equipmentText(equipment: string[]): string {
  return equipment.map(clean).filter(Boolean).join('、')
}

function chineseNumber(value: number): string {
  const digits = '零一二三四五六七八九'
  if (value < 10) return digits[value]!
  if (value < 20) return `十${value % 10 ? digits[value % 10] : ''}`
  if (value < 100) return `${digits[Math.floor(value / 10)]}十${value % 10 ? digits[value % 10] : ''}`
  return String(value)
}

function describeRoom(room: RoomInfo): string {
  const equipment = equipmentText(room.equipment)
  return `${clean(room.name)}，地点${clean(room.location) || '未提供'}，容量${room.capacity}人，${equipment ? `设备有${equipment}` : '未列出设备'}${room.is_active ? '' : '，目前已停用，不能选择'}`
}

// 摘要中的日期、时刻已经是北京时间的日历值，不能按浏览器本地时区再次转换。
function spokenDate(value: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(value)
  if (!match) return null
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3])
  const date = new Date(Date.UTC(year, month - 1, day))
  if (year < 1000 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null
  return `${year}年${month}月${day}日`
}

function spokenTime(value: string): string | null {
  const match = /^(\d{2}):(\d{2})$/u.exec(value)
  if (!match) return null
  const hour = Number(match[1]), minute = Number(match[2])
  if (hour > 23 || minute > 59) return null
  return `${hour}点${minute ? `${minute}分` : '整'}`
}

function invalidConfirmation(confirmation: AssistantConfirmation, now: number): string | null {
  if (confirmation.state === 'superseded') return `当前确认摘要已被更新，不能再提交。${RENEW_SUMMARY}`
  if (confirmation.state === 'consumed') return '当前确认摘要已使用，请先核对提交结果，查看实际预约记录。'
  const expires = Date.parse(confirmation.expires_at)
  if (confirmation.state !== 'pending' || !Number.isFinite(expires) || expires <= now
    || !/(?:Z|[+-]\d{2}:\d{2})$/iu.test(confirmation.expires_at)) {
    return `当前确认摘要已过期或失效，不能再提交。${RENEW_SUMMARY}`
  }
  return null
}

function summaryReply(view: ConversationView, confirmation: AssistantConfirmation, now: number): SpokenReply {
  const invalid = invalidConfirmation(confirmation, now)
  if (invalid) return { text: invalid, completed: false }
  const room = view.selected_room
  const summary = confirmation.summary
  const date = spokenDate(summary.date)
  const start = spokenTime(summary.start_time)
  const end = spokenTime(summary.end_time)
  if (view.phase !== 'summary' || confirmation.conversation_id !== view.conversation_id
    || confirmation.revision !== view.revision || confirmation.action !== 'submit'
    || !clean(confirmation.confirmation_id) || !room || room.id !== summary.room_id || !room.is_active
    || !clean(room.name) || !clean(room.location) || !date || !start || !end
    || summary.start_time >= summary.end_time || !Number.isSafeInteger(summary.attendees) || summary.attendees <= 0) {
    return { text: `当前摘要信息不完整或已更新。${RENEW_SUMMARY}`, completed: false }
  }
  const equipment = equipmentText(summary.required_equipment)
  const parts = [
    '请核对本次预约申请。',
    `会议室${clean(room.name)}，地点${clean(room.location)}，容量${room.capacity}人。`,
    `日期${date}，北京时间${start}至${end}。`,
    `参会人数${summary.attendees}人。`,
    equipment ? `所需设备${equipment}。` : '无额外设备要求。',
  ]
  if (clean(summary.title)) parts.push(`主题${clean(summary.title)}。`)
  if (clean(summary.purpose)) parts.push(`用途${clean(summary.purpose)}。`)
  parts.push('确认后提交申请，仍需管理员审批。', '信息正确请说确认提交，需要修改请直接说明。')
  return { text: parts.join(''), confirmationId: confirmation.confirmation_id, completed: false }
}

function actualReservationReply(reservation: Record<string, unknown> | null): SpokenReply | null {
  if (!reservation || Array.isArray(reservation)) return null
  const id = reservation.id
  const validId = typeof id === 'number' ? Number.isSafeInteger(id) && id > 0 : typeof id === 'string' && /^[1-9]\d*$/u.test(id)
  if (!validId) return null
  const statusKey = clean(reservation.status)
  const status = Object.hasOwn(STATUS_LABELS, statusKey) ? STATUS_LABELS[statusKey] : undefined
  return {
    text: `已核对实际预约记录，预约编号${id}。${status ? `当前状态为${status}。` : '当前状态待核对，请在我的预约中查看实际状态。'}`,
    completed: true,
  }
}

/** 仅描述传入的当前助手消息；是否为当前消息由调用方核对，不读取历史卡片或凭证中的旧收据。 */
export function buildSpokenReply(message: ChatMessage, now = Date.now()): SpokenReply {
  if (message.role !== 'assistant') return { text: '', completed: false }
  const view = message.view
  if (message.result) {
    const actual = actualReservationReply(message.result.reservation)
    if (actual) return actual
    const confirmation = message.result.confirmation
    const invalid = invalidConfirmation(confirmation, now)
    if (invalid) return { text: `尚未取得实际预约记录。${invalid}`, completed: false }
    if (view && view.confirmation?.confirmation_id === confirmation.confirmation_id) {
      const reply = summaryReply(view, confirmation, now)
      return { ...reply, text: `当前方案尚未提交。${reply.text}` }
    }
    return { text: `当前方案尚未提交，您可以继续补充条件。${RENEW_SUMMARY}`, completed: false }
  }
  if (!view) return { text: clean(message.text), completed: false }
  if (view.phase === 'summary') {
    return view.confirmation ? summaryReply(view, view.confirmation, now)
      : { text: `当前确认摘要尚未准备完成。${RENEW_SUMMARY}`, completed: false }
  }
  if (view.phase === 'candidates') {
    if (!view.candidates.length) {
      return { text: '当前没有可供选择的会议室，您可以调整日期、时间、人数或设备条件后继续查询。', completed: false }
    }
    const count = Math.max(view.candidates.length, Number.isSafeInteger(view.total) ? view.total : 0)
    const rooms = view.candidates.map((room, index) => `第${chineseNumber(index + 1)}间，${describeRoom(room)}。`)
    return {
      text: `当前找到${count}间匹配会议室，以下是${view.candidates.length}间候选。${rooms.join('')}您可以说选第一间，也可以说查看全部，或继续补充条件。`,
      completed: false,
    }
  }
  return { text: clean(view.message) || clean(message.text), completed: false }
}
