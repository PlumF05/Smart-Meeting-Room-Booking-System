"""预约状态与入口的固定词汇；不在此执行审批或自动状态转换。"""

from enum import StrEnum


class ReservationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REVOKED = "revoked"
    FINISHED = "finished"


class ReservationSource(StrEnum):
    PAGE = "page"
    ASSISTANT = "assistant"


RESERVATION_STATUS_LABELS = {
    ReservationStatus.PENDING: "未审批",
    ReservationStatus.APPROVED: "已批准",
    ReservationStatus.REJECTED: "已驳回",
    ReservationStatus.CANCELLED: "已取消",
    ReservationStatus.EXPIRED: "已过期",
    ReservationStatus.REVOKED: "已撤销",
    ReservationStatus.FINISHED: "已结束",
}

TERMINAL_RESERVATION_STATUSES = frozenset({
    ReservationStatus.REJECTED, ReservationStatus.CANCELLED, ReservationStatus.EXPIRED,
    ReservationStatus.REVOKED, ReservationStatus.FINISHED,
})
