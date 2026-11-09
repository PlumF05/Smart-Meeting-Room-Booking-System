"""统一聊天接口的真实图、SSE协议与隔离MySQL业务检查；不调用真实模型。"""

from datetime import timedelta
import json
import os

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.assistant import conversation as flow
from app.assistant.model import AIConnectionError
from app.models import AssistantConfirmation, LoginSession, Reservation
from app.schemas.meeting_assistant import Demand, ExtractedDemand
from app.services import assistant_confirmations, assistant_search, my_reservations, room_occupancy
from test_reservation_submission_mysql import (
    NOW, bind_user, client, counts, encoded_password, submit, world,
)


pytestmark = [pytest.mark.mysql, pytest.mark.skipif(
    os.environ.get('SMRBS_RUN_MYSQL_SCHEMA_TEST') != '1', reason='仅运行于独立MySQL实例')]
ENDPOINT = '/api/assistant/chat'
FULL = dict(date='2026-11-08', start_time='10:00', end_time='11:00', attendees=8,
            equipment=['白板'], title='课程讨论', purpose='讨论课程设计')


@pytest.fixture(autouse=True)
def fixed_flow(monkeypatch):
    monkeypatch.setattr(flow, '_runtime', None)
    current = [NOW]
    for module in (assistant_confirmations, assistant_search, room_occupancy, my_reservations):
        monkeypatch.setattr(module, 'get_beijing_now', lambda: current[0])

    async def extract(message, draft, settings):
        if message == 'fail':
            raise AIConnectionError('timeout')
        if message == 'unsupported':
            return ExtractedDemand(updates=Demand(), intent='unsupported')
        parsed = json.loads(message)
        return ExtractedDemand.model_validate(parsed) if 'updates' in parsed else ExtractedDemand(
            updates=Demand.model_validate(parsed))
    monkeypatch.setattr(flow, 'extract_demand', extract)
    return current


def parse_sse(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-store'
    parsed = []
    for block in response.text.replace('\r\n', '\n').split('\n\n'):
        if not block.strip():
            continue
        lines = block.splitlines()
        kind = next(line[7:] for line in lines if line.startswith('event: '))
        payload = json.loads('\n'.join(line[6:] for line in lines if line.startswith('data: ')))
        parsed.append((kind, payload))
    assert parsed and parsed[-1] == ('done', {})
    assert sum(kind == 'done' for kind, _ in parsed) == 1
    return parsed


def send(client, headers, *, action='message', view=None, **fields):
    body = dict(action=action, **fields)
    if view:
        body.update(conversation_id=view['conversation_id'], revision=view['revision'])
    return parse_sse(client.post(ENDPOINT, headers=headers, json=body))


def item(events, kind):
    found = [payload for name, payload in events if name == kind]
    assert len(found) == 1, [(name, payload.get('message')) for name, payload in events]
    return found[0]


def new_view(client, headers, fields=None):
    return item(send(client, headers, message=json.dumps(FULL if fields is None else fields)), 'view')


def summary(client, headers):
    view = new_view(client, headers)
    return item(send(client, headers, action='select', view=view,
                     room_id=view['candidates'][0]['id']), 'view')


def confirm(client, headers, view, *, words=None):
    fields = dict(confirmation_id=view['confirmation']['confirmation_id'])
    if words:
        fields.update(message=words)
    return send(client, headers, action='message' if words else 'confirm', view=view, **fields)


def test_multiturn_candidates_and_auto_summary_are_read_only(client, engine, world):
    headers = bind_user(client, world)
    view = new_view(client, headers, {'attendees': 8})
    assert view['phase'] == 'clarify' and '日期' in view['message']
    events = send(client, headers, view=view, message=json.dumps({
        key: value for key, value in FULL.items() if key != 'attendees'}))
    view = item(events, 'view')
    assert view['phase'] == 'candidates' and view['total'] == 10
    assert len(view['candidates']) == 3
    assert [kind for kind, _ in events].count('status') >= 2
    view = item(send(client, headers, view=view, action='select', room_id=view['candidates'][0]['id']), 'view')
    assert view['phase'] == 'summary' and view['confirmation']['state'] == 'pending'
    assert view['confirmation']['summary']['attendees'] == 8
    assert counts(engine) == (0, 0, 0)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(AssistantConfirmation)) == 1


@pytest.mark.parametrize('words', [None, '确认提交', '确认预约！', '确认提交申请。'])
def test_explicit_confirm_submits_once_and_replay_reads_current_status(client, world, engine, words):
    headers = bind_user(client, world)
    view = summary(client, headers)
    first = item(confirm(client, headers, view, words=words), 'result')
    assert first['confirmation']['state'] == 'consumed'
    assert first['reservation']['source'] == 'assistant'
    assert first['reservation']['status'] == 'pending'
    assert '待管理员审批' in first['message'] and counts(engine) == (1, 1, 2)
    repeated = item(confirm(client, headers, view, words=words), 'result')
    assert repeated['reservation']['id'] == first['reservation']['id']
    assert counts(engine) == (1, 1, 2)
    with Session(engine) as db, db.begin():
        db.get(Reservation, first['reservation']['id']).status = 'approved'
    replay = item(confirm(client, headers, view), 'result')
    assert replay['reservation']['status'] == 'approved'
    assert '待管理员审批' not in replay['message'] and counts(engine) == (1, 1, 2)


def test_all_and_unsupported_messages_do_not_change_plan_or_extend_expiry(client, world, engine, fixed_flow):
    headers = bind_user(client, world)
    view = summary(client, headers)
    credential = view['confirmation']
    fixed_flow[0] += timedelta(minutes=1)
    all_view = item(send(client, headers, action='all', view=view), 'view')
    assert all_view['revision'] == view['revision'] and len(all_view['candidates']) == 10
    assert all_view['confirmation'] == credential
    other = item(send(client, headers, view=all_view, message='unsupported'), 'view')
    assert other['draft'] == view['draft'] and other['selected_room'] == view['selected_room']
    assert other['revision'] == view['revision'] and other['confirmation'] == credential
    assert counts(engine) == (0, 0, 0)


def test_changed_business_fields_supersede_old_credential(client, world, engine):
    headers = bind_user(client, world)
    view = summary(client, headers)
    revised = item(send(client, headers, view=view, message=json.dumps({'title': '新主题'})), 'view')
    assert revised['revision'] > view['revision']
    assert revised['confirmation']['confirmation_id'] != view['confirmation']['confirmation_id']
    with Session(engine) as db:
        old = db.scalar(select(AssistantConfirmation).where(
            AssistantConfirmation.token == view['confirmation']['confirmation_id']))
        assert old.state == 'superseded'
    error = item(confirm(client, headers, view), 'error')
    assert error['status'] == 409 and counts(engine) == (0, 0, 0)


def test_expired_confirmation_is_rejected_and_explicit_renew_creates_new_summary(client, world, engine, fixed_flow):
    headers = bind_user(client, world)
    view = summary(client, headers)
    fixed_flow[0] += timedelta(minutes=5)
    error = item(confirm(client, headers, view), 'error')
    assert error['status'] == 409 and '五分钟' in error['message']
    assert counts(engine) == (0, 0, 0)
    renewed = item(send(client, headers, view=view, message='重新生成摘要'), 'view')
    assert renewed['revision'] > view['revision']
    assert renewed['confirmation']['confirmation_id'] != view['confirmation']['confirmation_id']
    assert renewed['confirmation']['state'] == 'pending'
    result = item(confirm(client, headers, renewed), 'result')
    assert result['reservation']['status'] == 'pending' and counts(engine) == (1, 1, 2)


@pytest.mark.parametrize('scenario,expected', [('anonymous',401), ('admin',403), ('csrf',403), ('origin',403)])
def test_auth_rejection_precedes_model_and_stream(client, world, monkeypatch, scenario, expected):
    async def forbidden(*args):
        raise AssertionError('拒绝请求不可调用模型')
    monkeypatch.setattr(flow, 'extract_demand', forbidden)
    headers = {} if scenario == 'anonymous' else bind_user(client, world, 'admin' if scenario == 'admin' else 'student')
    if scenario == 'csrf': headers = {}
    if scenario == 'origin': headers['Origin'] = 'https://attacker.example'
    response = client.post(ENDPOINT, headers=headers, json={'action':'message','message':'8人'})
    assert response.status_code == expected
    assert not response.headers.get('content-type','').startswith('text/event-stream')


@pytest.mark.parametrize('extra', [{'user_id':99}, {'source':'assistant'}, {'status':'approved'}, {'payload':FULL}])
def test_server_owned_fields_cannot_be_injected(client, world, extra):
    response = client.post(ENDPOINT, headers=bind_user(client, world),
        json={'action':'message','message':json.dumps(FULL), **extra})
    assert response.status_code == 422


def test_invalid_model_patch_is_error_event_without_success_or_business_write(client, engine, world):
    events = send(client, bind_user(client, world), message=json.dumps({'source':'assistant'}))
    error = item(events, 'error')
    assert error['status'] == 503
    assert not any(kind in ('view','result') for kind, _ in events)
    assert counts(engine) == (0, 0, 0)


def test_session_revoked_while_model_runs_cannot_receive_view(client, engine, world, monkeypatch):
    async def revoked(message, draft, settings):
        with Session(engine) as db, db.begin():
            db.execute(delete(LoginSession).where(LoginSession.user_id == world['users']['student']['id']))
        return ExtractedDemand(updates=Demand(attendees=8))
    monkeypatch.setattr(flow, 'extract_demand', revoked)
    events = send(client, bind_user(client, world), message='8人')
    assert item(events, 'error')['status'] == 401
    assert not any(kind in ('view','result') for kind, _ in events)
    assert counts(engine) == (0, 0, 0)


def test_check_after_runtime_restart_only_reads_persistent_receipt(client, world, engine, monkeypatch):
    headers = bind_user(client, world)
    view = summary(client, headers)
    result = item(confirm(client, headers, view), 'result')
    monkeypatch.setattr(flow, '_runtime', None)
    checked = item(send(client, headers, view=view, action='check',
        confirmation_id=view['confirmation']['confirmation_id']), 'result')
    assert checked['reservation']['id'] == result['reservation']['id']
    assert checked['confirmation']['state'] == 'consumed' and counts(engine) == (1, 1, 2)
    bind_user(client, world, 'teacher')
    events = send(client, bind_user(client, world, 'teacher'), view=view, action='check',
        confirmation_id=view['confirmation']['confirmation_id'])
    assert item(events, 'error')['status'] == 404


@pytest.mark.parametrize('action', ['confirm', 'check'])
def test_session_revoked_after_service_result_is_not_sent_to_client(client, world, engine, monkeypatch, action):
    from app.services import assistant_submission
    headers = bind_user(client, world)
    view = summary(client, headers)
    if action == 'check':
        item(confirm(client, headers, view), 'result')
    method = 'submit_confirmed' if action == 'confirm' else 'inspect_submission'
    original = getattr(assistant_submission, method)

    def revoke_after_result(db, **arguments):
        result = original(db, **arguments)
        with Session(engine) as transaction, transaction.begin():
            transaction.execute(delete(LoginSession).where(LoginSession.user_id == world['users']['student']['id']))
        return result
    monkeypatch.setattr(assistant_submission, method, revoke_after_result)
    events = send(client, headers, view=view, action=action,
        confirmation_id=view['confirmation']['confirmation_id'])
    assert item(events, 'error')['status'] == 401
    assert not any(kind == 'result' for kind, _ in events)
    # 返回前会话失效不撤回已经提交的业务，下次登录仍按业务记录核对。
    assert counts(engine) == (1, 1, 2)


def test_model_failure_is_terminal_error_and_retry_keeps_previous_draft(client, world):
    headers = bind_user(client, world)
    view = new_view(client, headers, {'attendees':8})
    failed = send(client, headers, view=view, message='fail')
    assert item(failed, 'error')['status'] == 504
    assert not any(kind in ('view','result') for kind, _ in failed)
    retried = item(send(client, headers, view=view, message=json.dumps(FULL)), 'view')
    assert retried['phase'] == 'candidates' and retried['draft']['attendees'] == 8


def test_room_taken_after_summary_refuses_without_automatic_replacement(client, engine, world):
    headers = bind_user(client, world)
    view = summary(client, headers)
    chosen = view['selected_room']['id']
    index = next(i for i, room in enumerate(world['rooms']) if room['id'] == chosen)
    submit(engine, world, label='teacher', room_index=index)
    events = confirm(client, headers, view)
    assert item(events, 'error')['status'] == 409
    assert not any(kind == 'result' for kind, _ in events)
    assert counts(engine) == (1, 1, 2)


def test_plain_confirmation_without_summary_does_not_execute(client, engine, world):
    events = send(client, bind_user(client, world), message='确认提交')
    assert item(events, 'error')['status'] == 409
    assert counts(engine) == (0, 0, 0)
