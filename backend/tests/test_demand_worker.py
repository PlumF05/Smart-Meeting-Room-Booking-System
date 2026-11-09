import json
import pytest
from app.assistant.demand_worker import validate_extraction


def test_model_selection_in_updates_is_normalized_without_changing_demand():
    parsed=validate_extraction(json.dumps({'updates':{'room_id':12,'attendees':6}}))
    assert parsed.room_id==12 and parsed.updates.attendees==6
    assert 'room_id' not in parsed.updates.model_dump()


@pytest.mark.parametrize('value', [
    {'room_id':3,'updates':{'room_id':4}},
    {'updates':{'room_id':'12'}},
    {'updates':{'user_id':99}},
    {'updates':{'source':'assistant'}},
])
def test_conflicting_types_or_server_controlled_fields_remain_rejected(value):
    with pytest.raises(ValueError):validate_extraction(json.dumps(value))
