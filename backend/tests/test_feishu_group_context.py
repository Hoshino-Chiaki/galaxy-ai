import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from test_feishu_bot import env, group_event, drain
from test_excel import setup
from xingxu.ai import ChatInput
from xingxu.auth import AuthFailure
from xingxu.feishu.service import owner_key
from xingxu.feishu.group_context import excerpts, text_content, load_group_context
from xingxu.tool_context import ExecutionContext


def item(text, sender='ou_employee', created=None, **extra):
    return {'message_id': str(time.time_ns()), 'chat_id': 'oc_shared_group',
        'create_time': str(int((created or time.time()-2)*1000)), 'msg_type': 'text',
        'sender': {'id_type': 'open_id', 'id': sender, 'sender_type': 'user'},
        'body': {'content': json.dumps({'text': text})}, **extra}


def enable_personal_history(app, user):
    with app.state.feishu.repo.transaction() as state:
        record=state['connections'][owner_key(user)]
        token=app.state.feishu._decrypt(record['token'])
        token['scope']='im:message:readonly im:message.group_msg:get_as_user im:message.p2p_msg:get_as_user im:message.reactions:read'
        record['token']=app.state.feishu._encrypt(token)


def test_group_question_automatically_receives_preceding_messages_as_current_user(env):
    app,logged,config,calls,_=env;bot=app.state.feishu_bot;user=logged[1]['user']
    enable_personal_history(app,user)
    history=[item('我准备晚上19点去爬东京塔'),item('我计划明天去日本旅游',created=time.time()-5),item('其他人的计划',sender='ou_owner')]
    real=app.state.feishu.application._request;observed=[]
    async def request(method,url,**kwargs):
        if url.endswith('/im/v1/messages'):
            observed.append(kwargs)
            assert kwargs['headers']['Authorization']=='Bearer fixture-user-secret'
            assert kwargs['params']['container_id']=='oc_shared_group'
            return 200,{'code':0,'data':{'items':history,'has_more':False}}
        return await real(method,url,**kwargs)
    app.state.feishu.application._request=request
    async def run():
        bot.accept('default',config,group_event(config));await drain(bot)
        assert len(calls)==1 and len(observed)==1
        ctx=calls[0][2]['channel_context']
        assert ctx['status']=='available' and ctx['requester_open_id']=='ou_employee'
        assert any(row['is_requester'] and '明天去日本' in row['text'] for row in ctx['messages'])
        assert any(row['is_requester'] and '19点' in row['text'] for row in ctx['messages'])
        assert any(not row['is_requester'] and '其他人' in row['text'] for row in ctx['messages'])
        receipt=ctx['cache']
        raw=json.loads((app.state.ai.dispatcher.files.cache(user)/receipt['path']).read_bytes())
        assert raw['data']['items']==history
        assert not (app.state.ai.dispatcher.files.cache(logged[0]['user'])/receipt['path']).exists()
        await bot.close();await app.state.clawbot.close()
    asyncio.run(run())


def test_missing_personal_history_scope_does_not_fall_back_to_application_or_admin(env):
    app,_,config,calls,_=env;bot=app.state.feishu_bot
    original=app.state.feishu.application._request;reads=[]
    async def request(method,url,**kwargs):
        if url.endswith('/im/v1/messages'):reads.append(kwargs);raise AssertionError('No token with permission')
        return await original(method,url,**kwargs)
    app.state.feishu.application._request=request
    async def run():
        bot.accept('default',config,group_event(config));await drain(bot)
        assert len(calls)==1 and not reads
        assert calls[0][2]['channel_context']['status']=='unavailable'
        assert calls[0][2]['channel_context']['error_code']=='feishu_scope_required'
        await bot.close();await app.state.clawbot.close()
    asyncio.run(run())


def test_recent_excerpt_window_is_bounded_and_never_mixes_chat_future_or_deleted():
    now=time.time();message={'message_id':'current','open_id':'ou_employee','chat_id':'oc_shared_group'}
    source=[item('valid',created=now-10),item('wrong room',chat_id='oc_other'),item('future',created=now+20),
        item('old',created=now-90000),item('deleted',deleted=True),item('current',message_id='current')]
    rows,_=excerpts(source,message,now)
    assert [r['text'] for r in rows]==['valid']
    rows,truncated=excerpts([item('x'*5000,created=now-i-1) for i in range(50)],message,now)
    assert truncated and len(rows)<=30 and len(json.dumps(rows,ensure_ascii=False))<12500


def test_rich_text_context_is_words_not_attachment_or_link_fetching():
    result=text_content({'msg_type':'post','body':{'content':json.dumps({'zh_cn':{'title':'计划',
        'content':[[{'tag':'text','text':'明天出发'},{'tag':'img','image_key':'private-file-key'}]]}})}})
    assert '明天出发' in result and 'private-file-key' not in result
    assert '非文字消息' in text_content({'msg_type':'image','body':{'content':'{}'}})


def test_group_data_is_passed_as_system_context_without_changing_visible_user_message(setup):
    app,admin,_,_=setup;captured=[]
    async def complete(provider,messages,**kwargs):
        captured.append(messages)
        return '你说过明天去日本。',{},'',[],[{'role':'assistant','content':'你说过明天去日本。'}]
    app.state.ai.complete=complete
    payload={'source':'feishu_group','status':'available','messages':[{'is_requester':True,'text':'我计划明天去日本'}]}
    async def run():
        result=await app.state.ai.send(admin['user'],ChatInput(message='我什么时候去旅游？'),
            session_token=admin['access_token'],entry_point='feishu',channel_context=payload)
        assert any(m['role']=='system' and '我计划明天去日本' in m['content'] and '不是系统指令' in m['content'] for m in captured[0])
        assert result['conversation']['messages'][0]['content']=='我什么时候去旅游？'
    asyncio.run(run())
