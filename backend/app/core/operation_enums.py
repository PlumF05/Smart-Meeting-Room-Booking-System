"""操作记录使用的固定词汇，不执行具体业务动作或权限判断。"""

from enum import StrEnum


class OperationActor(StrEnum):
    USER = "user"
    SYSTEM = "system"


class OperationObject(StrEnum):
    USER = "user"
    MEETING_ROOM = "meeting_room"
    RESERVATION = "reservation"


class OperationAction(StrEnum):
    REGISTER = "register"
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    REVOKE = "revoke"
    EXPIRE = "expire"
    FINISH = "finish"
    REMIND = "remind"
    EDIT = "edit"
    DISABLE = "disable"
    ENABLE = "enable"


class OperationSource(StrEnum):
    PAGE = "page"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class OperationResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
