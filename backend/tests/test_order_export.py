import asyncio
import copy
import csv
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from xingxu.auth import AuthFailure
from xingxu.files import FileService
from xingxu.tool_context import ExecutionContext
from xingxu.verification import Verifications
from xingxu.youzan.order_export import OrderExports,HEADERS,FIELDS,TEMPLATE
from xingxu.youzan.service import owner_key
from galaxy_cli.tools.youzan.order_export import normalize
from xingxu.tools import help_arguments,help_visible


@pytest.mark.parametrize("mode", ["sms", "page", "cached", "oldclient", "rejected"])
def test_native_export_auto_sms_private_input_and_download(tmp_path, mode):
    async def run():
        user={'id':'u','workspace_id':'w','username':'employee','permissions':['youzan:order:read','ai:chat','file:read']}
        credentials={'sid':'fixture-session','csrf':'crm-csrf','kdt_id':'94598170','cookies':{'access_token':'fixture-open-token','bifrost_csrf_token':'export-csrf'},'origin':'https://store.youzan.com'}
        if mode=="page":credentials["cookies"].pop("bifrost_csrf_token")
        requests=[];verified=mode=="cached";polls=0
        @contextmanager
        def transaction(**kwargs):yield {'connections':{owner_key(user):{'account':'13800000000'}}}
        yz=SimpleNamespace(directory=tmp_path,repo=SimpleNamespace(transaction=transaction),tool_credentials=lambda u,s:copy.deepcopy(credentials))
        def handler(request):
            nonlocal verified,polls
            requests.append(request)
            path=request.url.path
            if path=='/v4/shop/enter-shop':
                assert request.url.params['kdtId']=='94598170'
                return httpx.Response(302,headers={'location':'https://store.youzan.com/v2/order/exportList'})
            if path=='/v2/order/exportList':return httpx.Response(200,text='<script>window._global={"csrf_token":"export-csrf"}</script>',headers={'set-cookie':'bifrost_csrf_token=export-csrf; Path=/; Secure'})
            if 'list.customized' in path:return httpx.Response(200,json={'code':200,'data':{'items':[{'customer_template_name':TEMPLATE,'customer_template_id':'888'}],'paginator':{'total_count':1}}})
            if 'query.customized' in path:return httpx.Response(200,json={'code':200,'data':{'dimension':'goods','format':'csv','disabled':0,'required_order_fields':[{'name':a,'title':b} for a,b in zip(FIELDS,HEADERS)]}})
            if path.endswith('/export'):
                body=parse_qs(request.content.decode());assert body['customer_template_id']==['888'] and body['time_type']==['book_time']
                return httpx.Response(200,json={'response':'123'})
            if 'trades.export' in path:
                polls+=1
                return httpx.Response(200,json={'response':{'hasNext':False,'list':[{'id':123,'kdtId':94598170,'exportState':1 if polls==1 else 10,'url':'' if polls==1 else 'https://yz-trade-private.yzcdn.cn/private_file/trade_export/result.csv?e=1&token=private-file-secret','fileName':'result.csv'}]}})
            if path.endswith('/sms/cache.json'):
                assert request.url.params['csrf_token']=='export-csrf'
                return httpx.Response(200,json={'code':0,'data':{'method':'NO_CERT' if verified else 'SMS_CERT'}})
            if path.endswith('/sms/send.json'):
                return httpx.Response(200,json={'code':400,'message':'需要人工安全验证'}) if mode=='rejected' else httpx.Response(200,json={'code':0,'data':{'success':True}})
            if path.endswith('/sms/check.json'):
                body=parse_qs(request.content.decode());assert body['smsCaptcha']==['461237'];verified=True
                return httpx.Response(200,json={'code':0,'data':{'success':True}})
            if path.endswith('result.csv'):
                assert verified
                out=io.StringIO();w=csv.writer(out);w.writerow(HEADERS);w.writerow(['E1','商品','交易完成','交易完成','现金支付','普通订单'])
                return httpx.Response(200,content=out.getvalue().encode('utf-8-sig'))
            raise AssertionError(path)
        manager=Verifications();files=FileService(tmp_path);export=OrderExports(yz,files,manager,transport=httpx.MockTransport(handler),sleep=lambda _:asyncio.sleep(0))
        ctx=ExecutionContext('u','w',tuple(user['permissions']),'conversation','fixture',verification_inputs=True)
        body=normalize({'start_time':'2026-11-16 00:00:00','end_time':'2026-11-22 23:59:59'})
        result=await export.run(user,ctx,body,credentials,lambda:None,interactive=mode!="oldclient")
        if mode not in ("sms","page"):
            expected={"cached":"downloaded","oldclient":"verification_client_required","rejected":"verification_send_failed"}[mode]
            assert result["status"]==expected and result["job_id"]
            assert sum(r.url.path.endswith("/sms/send.json") for r in requests)==(1 if mode=="rejected" else 0)
            assert not manager.entries
            if mode=="rejected":assert result["error"]["code"]=="youzan_security_required"
            return
        assert result['status']=='verification_required'
        assert sum(r.url.path.endswith('/sms/send.json') for r in requests)==1  # sent before any input tool
        assert '13800000000' not in json.dumps(result) and 'private-file-secret' not in json.dumps(result)
        resumed=await export.run(user,ctx,normalize({'action':'resume','job_id':result['job_id']}),credentials,lambda:None,interactive=True)
        assert resumed['challenge_id']==result['challenge_id']
        assert sum(r.url.path.endswith('/sms/send.json') for r in requests)==1
        entry=manager.get(ctx,result['challenge_id']);ui=SimpleNamespace(key=('w','u','r'),interaction=None,stop_requested=False,transition=lambda _:None)
        wait=asyncio.create_task(manager.wait(ui,entry));await asyncio.sleep(0)
        manager.answer(ui,user,entry.id,code='461237');answer=await wait
        downloaded=await manager.submit(ctx,entry.id,answer['submission_id'],lambda:None)
        assert downloaded['status']=='downloaded' and downloaded['rows']==1 and downloaded['format']=='csv'
        assert downloaded['headers']==HEADERS and '461237' not in json.dumps(downloaded) and 'private-file-secret' not in json.dumps(downloaded)
        assert (files.cache(user)/downloaded['path']).read_bytes().startswith(b'\xef\xbb\xbf')
        old_count=len(requests)
        again=await export.run(user,ctx,normalize({'action':'resume','job_id':result['job_id']}),credentials,lambda:None,interactive=True)
        assert again['path']==downloaded['path'] and len(requests)==old_count
        with pytest.raises(Exception):export.load({**user,'id':'other','username':'other'},result['job_id'])
    asyncio.run(run())


def test_commands_permissions_and_no_raw_code():
    assert help_visible('youzan.order-export',['youzan:order:read'])
    for command,value in [('youzan.order-export',{'start_time':'2026-11-16 00:00:00','end_time':'2026-11-22 23:59:59'}),('interaction.verification',{'challenge_id':'1147bbee-b2e0-4fe5-b550-865fb1934832'})]:
        assert help_arguments(json.dumps({'args':[command,'--input',json.dumps(value)]}))[0] in ('youzan','interaction')
    from xingxu.tool_context import DispatchDenied
    with pytest.raises(DispatchDenied):help_arguments(json.dumps({'args':['interaction','verification-submit','--input','{"code":"461237"}']}))
