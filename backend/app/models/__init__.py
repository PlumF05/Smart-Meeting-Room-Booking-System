"""集中导入模型，确保Alembic能发现全部已实现的表。"""

from app.models.meeting_room import MeetingRoom
from app.models.user import User

__all__ = ["User", "MeetingRoom"]
