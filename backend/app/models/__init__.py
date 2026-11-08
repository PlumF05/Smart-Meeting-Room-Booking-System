"""集中导入模型，确保Alembic能发现全部已实现的表。"""

from app.models.login_session import LoginSession
from app.models.meeting_room import MeetingRoom
from app.models.notification import Notification
from app.models.operation_log import OperationLog
from app.models.reservation import Reservation
from app.models.user import User
from app.models.assistant_confirmation import AssistantConfirmation

__all__ = ["User", "MeetingRoom", "LoginSession", "Reservation", "OperationLog", "Notification", "AssistantConfirmation"]
