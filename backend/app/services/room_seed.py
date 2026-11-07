"""固定编号的演示会议室预置，只补缺项，不覆盖已维护的资料。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DatabaseUnavailableError
from app.models import MeetingRoom


@dataclass(frozen=True)
class RoomSeed:
    id: int
    name: str
    location: str
    capacity: int
    equipment: tuple[str, ...]
    is_active: bool = True


DEFAULT_ROOMS = (
    RoomSeed(1, "研讨室101", "教学楼A座1层101", 8, ("白板",)),
    RoomSeed(2, "研讨室102", "教学楼A座1层102", 12, ("投影仪", "白板")),
    RoomSeed(3, "会议室201", "教学楼A座2层201", 20, ("投影仪", "白板", "视频会议")),
    RoomSeed(4, "会议室202", "教学楼A座2层202", 30, ("投影仪", "音响", "白板")),
    RoomSeed(5, "报告厅301", "教学楼A座3层301", 60, ("投影仪", "音响", "视频会议")),
    RoomSeed(6, "会议室302", "教学楼A座3层302", 40, ("投影仪", "白板"), is_active=False),
)


@dataclass(frozen=True)
class RoomSeedResult:
    created: int
    skipped: int


class RoomSeedConflictError(Exception):
    pass


def seed_rooms(session: Session) -> RoomSeedResult:
    # 重复命令并发插入相同编号时，唯一约束决定谁先成功，另一方重新读取一次。
    for attempt in range(2):
        try:
            with session.begin():
                existing_ids = set(session.scalars(
                    select(MeetingRoom.id).where(MeetingRoom.id.in_([room.id for room in DEFAULT_ROOMS]))
                ))
                missing = [room for room in DEFAULT_ROOMS if room.id not in existing_ids]
                if missing:
                    conflicting_id = session.scalar(
                        select(MeetingRoom.id)
                        .where(MeetingRoom.name.in_([room.name for room in missing])).limit(1)
                    )
                    if conflicting_id is not None:
                        raise RoomSeedConflictError()
                    session.add_all([
                        MeetingRoom(
                            id=room.id, name=room.name, location=room.location,
                            capacity=room.capacity, equipment=list(room.equipment),
                            is_active=room.is_active,
                        )
                        for room in missing
                    ])
                    session.flush()
                result = RoomSeedResult(created=len(missing), skipped=len(existing_ids))
            return result
        except IntegrityError as error:
            if getattr(error.orig, "args", ()) and error.orig.args[0] == 1062 and attempt == 0:
                continue
            raise DatabaseUnavailableError() from None
        except SQLAlchemyError:
            raise DatabaseUnavailableError() from None
    raise DatabaseUnavailableError()
