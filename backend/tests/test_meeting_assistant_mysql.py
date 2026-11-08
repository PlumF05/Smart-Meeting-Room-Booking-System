"""8.3/8.4 联调：真实图和隔离MySQL，模型使用固定提取结果。"""
import json
import os

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.assistant import conversation as flow
from app.assistant.model import AIConnectionError
from app.models import AssistantConfirmation, LoginSession, MeetingRoom
from app.schemas.meeting_assistant import Demand, ExtractedDemand
from app.services import assistant_confirmations, assistant_search, room_occupancy
from app.services.assistant_confirmations import inspect_confirmation
from test_reservation_submission_mysql import (
    NOW, bind_user, client, counts, encoded_password, submit, world,
)

pytestmark = [pytest.mark.mysql, pytest.mark.skipif(
    os.environ.get('SMRBS_RUN_MYSQL_SCHEMA_TEST') != '1', reason='仅运行于独立MySQL实例')]
PREFIX = '/api/assistant/conversations'
FULL = dict(date='2026-11-08', start_time='10:00', end_time='11:00', attendees=8,
            equipment=['白板'], title='课程讨论', purpose='讨论课程设计')


@pytest.fixture(autouse=True)
def fixed_flow(monkeypatch):
    monkeypatch.setattr(flow, '_runtime', None)
    for module in (assistant_confirmations, assistant_search, room_occupancy):
        monkeypatch.setattr(module, 'get_beijing_now', lambda: NOW)

    async def extract(message, draft, settings):
        if message == 'fail':
            raise AIConnectionError('timeout')
        return ExtractedDemand(updates=Demand.model_validate(json.loads(message)))
    monkeypatch.setattr(flow, 'extract_demand', extract)


def start(client, headers, patch=None):
    response = client.post(PREFIX + '/start', headers=headers,
                           json={'message': json.dumps(FULL if patch is None else patch)})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def event(client, headers, view, action, **fields):
    return client.post(f"{PREFIX}/{view['conversation_id']}/{action}", headers=headers,
                       json={'revision': view['revision'], **fields})


def summary(client, headers):
    view = start(client, headers)
    response = event(client, headers, view, 'select', room_id=view['candidates'][0]['id'])
    assert response.status_code == 200, response.text
    assert response.json()['phase'] == 'summary'
    return response.json()


def test_multi_turn_search_select_and_prepare_never_create_reservation(client, engine, world):
    headers = bind_user(client, world)
    view = start(client, headers, {'attendees': 8})
    assert view['phase'] == 'clarify' and '日期' in view['message']
    view = event(client, headers, view, 'message', message=json.dumps({
        'date': FULL['date'], 'start_time': '10:00', 'end_time': '11:00',
    })).json()
    assert view['phase'] == 'candidates' and view['total'] == 10
    assert len(view['candidates']) == 3
    view = event(client, headers, view, 'candidates', show_all=True).json()
    assert len(view['candidates']) == 10
    view = event(client, headers, view, 'select', room_id=view['candidates'][5]['id']).json()
    assert view['phase'] == 'clarify' and '会议主题' in view['message'] and '用途' in view['message']
    chosen = view['selected_room']['id']
    view = event(client, headers, view, 'message', message=json.dumps({
        'title': '讨论', 'purpose': '分配工作',
    })).json()
    assert view['phase'] == 'summary' and view['selected_room']['id'] == chosen
    prepared = event(client, headers, view, 'prepare')
    assert prepared.status_code == 200, prepared.text
    credential = prepared.json()['confirmation']
    assert credential['summary']['room_id'] == chosen and credential['state'] == 'pending'
    assert credential['action'] == 'submit'
    assert counts(engine) == (0, 0, 0)


def test_prepare_retry_same_expiry_and_changed_draft_invalidates_old(client, engine, world):
    headers = bind_user(client, world)
    view = summary(client, headers)
    first = event(client, headers, view, 'prepare').json()['confirmation']
    second = event(client, headers, view, 'prepare').json()['confirmation']
    assert first == second
    user = world['users']['student']
    response = event(client, headers, view, 'message', message=json.dumps({'title': '新主题'}))
    assert response.status_code == 200, response.text
    with Session(engine) as db:
        old = inspect_confirmation(db, user_id=user['id'], raw_token=user['token'], csrf_token=user['csrf'],
            conversation_id=view['conversation_id'], confirmation_id=first['confirmation_id'])
    assert old['state'] == 'superseded'
    newer = event(client, headers, response.json(), 'prepare').json()['confirmation']
    assert newer['confirmation_id'] != first['confirmation_id']
    assert newer['summary']['title'] == '新主题' and counts(engine) == (0, 0, 0)
    # 旧revision被拒绝，不能顺便作废刚生成的新凭证。
    assert event(client, headers, view, 'candidates', show_all=True).status_code == 409
    current = event(client, headers, response.json(), 'prepare').json()['confirmation']
    assert current == newer and current['state'] == 'pending'


@pytest.mark.parametrize('change', ['disabled', 'capacity', 'metadata', 'occupied'])
def test_prepare_rechecks_current_resource_without_replacing_it(client, engine, world, change):
    headers = bind_user(client, world)
    view = summary(client, headers)
    selected = view['selected_room']['id']
    if change == 'occupied':
        index = next(i for i, room in enumerate(world['rooms']) if room['id'] == selected)
        submit(engine, world, label='teacher', room_index=index)
    else:
        with Session(engine) as db, db.begin():
            room = db.get(MeetingRoom, selected)
            if change == 'disabled': room.is_active = False
            elif change == 'capacity': room.capacity = 1
            else: room.location = '资源信息发生变化'
    response = event(client, headers, view, 'prepare')
    assert response.status_code == 409 and '变化' in response.json()['detail']
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(AssistantConfirmation)) == 0


def test_search_filters_and_ranks_real_rooms_and_adjacent_window(client, engine, world):
    with Session(engine) as db, db.begin():
        rooms = [db.get(MeetingRoom, row['id']) for row in world['rooms']]
        rooms[0].capacity = 2
        rooms[1].is_active = False
        rooms[2].equipment = ['投影仪']
        rooms[3].capacity = 9
        rooms[4].capacity = 8
    submit(engine, world, label='teacher', room_index=5)
    headers = bind_user(client, world)
    view = start(client, headers)
    assert view['total'] == 6
    assert [r['id'] for r in view['candidates'][:2]] == [world['rooms'][4]['id'], world['rooms'][3]['id']]
    # 结束时间相邻不占用11点之后。
    later = event(client, headers, view, 'message', message=json.dumps({'start_time': '11:00', 'end_time': '12:00'}))
    assert later.status_code == 200 and later.json()['total'] == 7


def test_personal_conflict_explains_without_exposing_other_user_data(client, engine, world):
    submit(engine, world)
    view = start(client, bind_user(client, world))
    assert view['phase'] == 'no_match' and '个人时间冲突' in view['message']
    assert view['total'] == 0 and '课程小组讨论' not in json.dumps(view, ensure_ascii=False)
    assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize('scenario,code', [('csrf', 403), ('origin', 403), ('admin', 403), ('anonymous', 401)])
def test_auth_before_any_model_call(client, world, monkeypatch, scenario, code):
    async def forbidden(*args): raise AssertionError('拒绝请求不应调用模型')
    monkeypatch.setattr(flow, 'extract_demand', forbidden)
    headers = {} if scenario == 'anonymous' else bind_user(client, world, 'admin' if scenario == 'admin' else 'student')
    if scenario == 'csrf': headers = {}
    if scenario == 'origin': headers['Origin'] = 'https://attacker.example'
    response = client.post(PREFIX + '/start', json={'message': 'test'}, headers=headers)
    assert response.status_code == code


def test_owner_revision_selection_and_close_are_checked(client, engine, world):
    headers = bind_user(client, world)
    view = start(client, headers)
    assert event(client, headers, view, 'select', room_id=999999).status_code == 409
    assert event(client, headers, {**view, 'revision': 999}, 'candidates', show_all=True).status_code == 409
    other = bind_user(client, world, 'teacher')
    assert event(client, other, view, 'prepare').status_code == 404
    headers = bind_user(client, world)
    selected = event(client, headers, view, 'select', room_id=view['candidates'][0]['id']).json()
    credential = event(client, headers, selected, 'prepare').json()['confirmation']
    response = client.delete(f"{PREFIX}/{view['conversation_id']}", headers=headers)
    assert response.status_code == 204
    assert event(client, headers, selected, 'prepare').status_code == 410
    with Session(engine) as db:
        record = db.scalar(select(AssistantConfirmation).where(AssistantConfirmation.token == credential['confirmation_id']))
        assert record.state == 'superseded'


def test_failed_message_preserves_old_draft_and_allows_retry(client, world):
    headers = bind_user(client, world)
    view = start(client, headers, {'attendees': 8})
    response = event(client, headers, view, 'message', message='fail')
    assert response.status_code == 504
    retry = event(client, headers, view, 'message', message=json.dumps(FULL))
    assert retry.status_code == 200, retry.text
    assert retry.json()['phase'] == 'candidates'
    assert retry.json()['revision'] == view['revision'] + 1


def test_session_revoked_during_extraction_cannot_receive_result(client, world, engine, monkeypatch):
    headers = bind_user(client, world)

    async def revoke(message, draft, settings):
        with Session(engine) as db, db.begin():
            db.execute(delete(LoginSession).where(LoginSession.user_id == world['users']['student']['id']))
        return ExtractedDemand(updates=Demand(attendees=8))
    monkeypatch.setattr(flow, 'extract_demand', revoke)
    response = client.post(PREFIX + '/start', headers=headers, json={'message': '8人'})
    assert response.status_code == 401
    assert 'conversation_id' not in response.text and counts(engine) == (0, 0, 0)


def test_time_elapsing_before_prepare_is_business_conflict(client, world, monkeypatch):
    headers = bind_user(client, world)
    view = summary(client, headers)
    from datetime import timedelta
    monkeypatch.setattr(assistant_search, 'get_beijing_now', lambda: NOW + timedelta(days=2))
    response = event(client, headers, view, 'prepare')
    assert response.status_code == 409 and '日期' in response.json()['detail']


def test_no_public_execute_or_cancel_route(client, world):
    from app.main import app
    paths = {route.path for route in app.routes if hasattr(route, 'path')}
    assert not any(path.startswith(PREFIX) and path.endswith(('/execute', '/submit', '/cancel')) for path in paths)
    response = client.post(PREFIX + '/unknown/execute', headers=bind_user(client, world), json={})
    assert response.status_code == 404
