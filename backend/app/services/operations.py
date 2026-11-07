"""把事件及消息加入调用方事务；不提交、不回滚，也不代替业务权限检查。"""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.operation_enums import OperationResult
from app.models.notification import Notification
from app.models.operation_log import OperationLog
from app.schemas.operations import MessageDraft, OperationEntry


def append_operation(
    session: Session, entry: OperationEntry, messages: Sequence[MessageDraft] = (),
) -> OperationLog:
    if not session.in_transaction():
        raise RuntimeError("事件写入需要调用方提供已有事务")
    recipients = [message.recipient_user_id for message in messages]
    if len(recipients) != len(set(recipients)):
        raise ValueError("同一事件不能重复设置收件人")
    if entry.result == OperationResult.FAILURE and messages:
        raise ValueError("失败的业务操作不能附带业务通知")

    event = OperationLog(**entry.model_dump(mode="json"))
    session.add(event)
    # flush会刷新整个Session（可能含预约），获得event.id，但仍未提交。
    session.flush()
    session.add_all([
        Notification(event_id=event.id, **message.model_dump())
        for message in messages
    ])
    session.flush()
    # 唯一键或外键失败交给调用方处理并回滚，不吞异常伪装成功。
    return event
