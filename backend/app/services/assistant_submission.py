"""与页面正常流程复用同一个预约事务核心。"""

from sqlalchemy.orm import Session

from app.core.reservation_enums import ReservationSource
from app.schemas.reservations import ReservationCreate
from app.services.assistant_confirmations import (
    ConfirmationError,
    consume_confirmation,
    inspect_confirmation,
)
from app.services.my_reservations import read_mine
from app.services.reservations import create_reservation_in_transaction


def _current_result(db: Session, user_id: int, confirmation: dict) -> dict:
    reservation = None
    if confirmation["state"] == "consumed":
        receipt = confirmation["result"]
        reservation_id = (
            receipt.get("reservation_id") if isinstance(receipt, dict) else None
        )
        if type(reservation_id) is not int or reservation_id <= 0:
            raise ConfirmationError("invalid_result")
        # 读取数据库
        reservation = read_mine(db, user_id, reservation_id)
    return {"confirmation": confirmation, "reservation": reservation}


def submit_confirmed(
    db: Session,
    *,
    user_id: int,
    raw_token: str | None,
    csrf_token: str | None,
    conversation_id: str,
    confirmation_id: str,
    revision: int,
) -> dict:
    def submit_in_transaction(transaction: Session, payload: dict) -> dict:
        created = create_reservation_in_transaction(
            transaction,
            ReservationCreate.model_validate(payload),
            user_id=user_id,
            raw_token=raw_token,
            csrf_token=csrf_token,
            source=ReservationSource.ASSISTANT,
        )
        return {"reservation_id": created.id}

    confirmation = consume_confirmation(
        db,
        user_id=user_id,
        raw_token=raw_token,
        csrf_token=csrf_token,
        conversation_id=conversation_id,
        confirmation_id=confirmation_id,
        revision=revision,
        action=submit_in_transaction,
    )
    return _current_result(db, user_id, confirmation)


def inspect_submission(
    db: Session,
    *,
    user_id: int,
    raw_token: str | None,
    csrf_token: str | None,
    conversation_id: str,
    confirmation_id: str,
    revision: int,
) -> dict:
    confirmation = inspect_confirmation(
        db,
        user_id=user_id,
        raw_token=raw_token,
        csrf_token=csrf_token,
        conversation_id=conversation_id,
        confirmation_id=confirmation_id,
        revision=revision,
    )
    return _current_result(db, user_id, confirmation)
