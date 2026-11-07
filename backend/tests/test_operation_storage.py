"""内部事件输入及事务边界检查；仅使用虚构内容和模拟Session。"""

from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.operation_enums import OperationAction, OperationResult
from app.models import Notification, OperationLog
from app.schemas.operations import MessageDraft, OperationEntry
from app.services.operations import append_operation


def entry(**changes):
    values = dict(
        event_key="reservation:17:submit", actor_kind="user", actor_user_id=1,
        object_type="reservation", object_id=17, action="submit", source="page",
        after_status="pending",
    )
    values.update(changes)
    return OperationEntry(**values)


def message(recipient=2, **changes):
    values = dict(recipient_user_id=recipient, title="预约结果", content="请查看预约记录。")
    values.update(changes)
    return MessageDraft(**values)


def session_in_transaction():
    database = MagicMock(spec=Session)
    database.in_transaction.return_value = True

    def give_event_an_id():
        # 模拟flush产生主键，使消息必须引用本次事件而不是另一个编号。
        event = database.add.call_args.args[0]
        event.id = 91

    database.flush.side_effect = give_event_an_id
    return database


def assert_no_transaction_control(database):
    database.begin.assert_not_called()
    database.commit.assert_not_called()
    database.rollback.assert_not_called()


def assert_no_writes(database):
    database.add.assert_not_called()
    database.add_all.assert_not_called()
    database.flush.assert_not_called()
    assert_no_transaction_control(database)


def test_user_and_system_events_keep_only_the_explicit_business_fields():
    user = entry(before_status="  draft ", after_status=" pending ", reason="  需要审批  ")
    data = user.model_dump(mode="json")
    assert data["actor_kind"] == "user" and data["source"] == "page"
    assert data["action"] == "submit" and data["result"] == "success"
    assert data["before_status"] == "draft" and data["after_status"] == "pending"
    assert data["reason"] == "需要审批"
    system = entry(
        event_key="reservation:17:expire", actor_kind="system", actor_user_id=None,
        action="expire", source="system", before_status="pending", after_status="expired",
    )
    assert system.actor_user_id is None
    assert system.model_dump(mode="json")["source"] == "system"


@pytest.mark.parametrize("changes", [
    {"actor_user_id": None},
    {"source": "system"},
    {"actor_kind": "system", "source": "system", "actor_user_id": 1},
    {"actor_kind": "system", "source": "page", "actor_user_id": None},
])
def test_actor_and_source_must_describe_the_same_kind_of_operation(changes):
    with pytest.raises(ValidationError):
        entry(**changes)


@pytest.mark.parametrize("changes", [
    {"object_id": 0}, {"object_id": "17"}, {"actor_user_id": True},
    {"actor_user_id": 9223372036854775808},
    {"action": "SUBMIT"}, {"source": "unknown"}, {"result": "pending"},
    {"before_status": "  "}, {"after_status": "x" * 33}, {"reason": "x" * 1001},
])
def test_invalid_identifiers_enums_and_status_summary_values_are_rejected(changes):
    with pytest.raises(ValidationError):
        entry(**changes)


@pytest.mark.parametrize("field", ["password", "api_key", "request_body", "conversation"])
def test_extra_sensitive_event_fields_are_rejected_without_echoing_values(field):
    secret = "synthetic-sensitive-marker-never-real"
    with pytest.raises(ValidationError) as error:
        entry(**{field: secret})
    assert secret not in str(error.value)
    assert "input_value=" not in str(error.value)


def test_message_text_is_normalized_and_extra_secret_fields_are_rejected():
    result = message(title="  处理结果  ", content="  申请已处理，请查看记录。  ")
    assert result.title == "处理结果" and result.content == "申请已处理，请查看记录。"
    secret = "synthetic-message-secret-marker"
    with pytest.raises(ValidationError) as error:
        message(password=secret)
    assert secret not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"recipient_user_id": 0}, {"recipient_user_id": "2"},
    {"title": "  "}, {"title": "字" * 101}, {"content": "字" * 1001},
])
def test_message_requires_a_real_identifier_and_bounded_nonempty_text(changes):
    with pytest.raises(ValidationError):
        message(**changes)


def test_storage_models_have_no_arbitrary_request_or_credential_columns():
    forbidden = {"password", "password_hash", "api_key", "request_body", "payload", "conversation", "transcript"}
    for model in (OperationLog, Notification):
        assert forbidden.isdisjoint(model.__table__.columns.keys())
    assert "read_at" in Notification.__table__.columns
    assert "is_read" not in Notification.__table__.columns


def test_missing_transaction_is_rejected_without_writes_or_implicit_begin():
    database = MagicMock(spec=Session)
    database.in_transaction.return_value = False
    with pytest.raises(RuntimeError):
        append_operation(database, entry(), [message()])
    assert_no_writes(database)


def test_duplicate_recipients_are_rejected_before_flushing_any_pending_changes():
    database = session_in_transaction()
    with pytest.raises(ValueError):
        append_operation(database, entry(), [message(), message(content="另一份内容")])
    assert_no_writes(database)


def test_failed_operation_cannot_attach_business_notifications():
    database = session_in_transaction()
    with pytest.raises(ValueError):
        append_operation(database, entry(result="failure"), [message()])
    assert_no_writes(database)


def test_successful_rejection_is_a_successful_action_that_can_notify_its_recipient():
    database = session_in_transaction()
    rejected = entry(
        action="reject", before_status="pending", after_status="rejected", reason="时间安排不合适",
    )
    result = append_operation(database, rejected, [message()])
    assert isinstance(result, OperationLog)
    assert result.action == OperationAction.REJECT and result.result == OperationResult.SUCCESS
    assert result.id == 91
    notifications = database.add_all.call_args.args[0]
    assert len(notifications) == 1
    assert isinstance(notifications[0], Notification)
    assert notifications[0].event_id == 91 and notifications[0].recipient_user_id == 2
    assert notifications[0].read_at is None
    assert_no_transaction_control(database)


def test_failed_event_without_notifications_can_be_added_but_not_independently_committed():
    database = session_in_transaction()
    result = append_operation(database, entry(result="failure", reason="校验未通过"))
    assert result.result == OperationResult.FAILURE
    assert not database.add_all.call_args.args[0]
    assert_no_transaction_control(database)


def test_database_failure_is_returned_to_the_caller_without_commit_rollback_or_logging(caplog):
    database = session_in_transaction()
    failure = SQLAlchemyError("synthetic-internal-database-failure")
    database.flush.side_effect = failure
    with pytest.raises(SQLAlchemyError) as error:
        append_operation(database, entry(), [message()])
    assert error.value is failure
    database.add_all.assert_not_called()
    assert_no_transaction_control(database)
    assert "synthetic-internal-database-failure" not in caplog.text
