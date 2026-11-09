import os
import pytest
from app.schemas.reservations import ReservationCreate
from test_reservation_submission_mysql import world,client,encoded_password,bind_user,body,counts
from test_assistant_chat_mysql import fixed_flow,new_view,send,item,confirm

pytestmark=[pytest.mark.mysql,pytest.mark.skipif(os.environ.get('SMRBS_RUN_MYSQL_SCHEMA_TEST')!='1',reason='独立MySQL测试')]


@pytest.mark.parametrize('values',[{}, {'title':'','purpose':' '},{'title':None,'purpose':None}])
def test_page_submission_accepts_omitted_blank_and_null_details(client,world,values):
    data=body(world);data.pop('title');data.pop('purpose');data.update(values)
    r=client.post('/api/reservations',json=data,headers=bind_user(client,world))
    assert r.status_code==201,r.text
    assert r.json()['title']==r.json()['purpose']==''


def test_assistant_minimum_fields_select_summary_and_submit(client,world,engine):
    headers=bind_user(client,world)
    view=new_view(client,headers,{'date':'2026-11-08','start_time':'10:00','end_time':'11:00','attendees':6})
    view=item(send(client,headers,action='select',view=view,room_id=view['candidates'][0]['id']),'view')
    assert view['phase']=='summary' and view['confirmation']['summary']['title']==''
    assert counts(engine)==(0,0,0)
    result=item(confirm(client,headers,view),'result')
    assert result['reservation']['title']==result['reservation']['purpose']==''
    assert counts(engine)==(1,1,2)
