"""Fixed-template native exports. Uses existing sessions; never logs in or solves CAPTCHAs."""
import asyncio
import csv
import datetime as dt
import hashlib
import io
import json
import re
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urljoin,urlencode

import httpx

from galaxy_cli.file_scope import source_file,root_directory,child_directory
from galaxy_cli.request_cache import CachedDataset
from ..account_storage import Accounts,atomic_json
from ..auth import AuthFailure
from .csrf import extract_csrf
from .http import read_youzan_page
from .service import owner_key

TEMPLATE='AI财务订单表1'
HEADERS=['订单号','商品名称','订单商品状态','订单状态','付款方式','订单类型']
FIELDS=['OrderID','GoodsTitle','SimpleOrderState','MainOrderState','BuyWay','OrderType']
CAR='https://carmen.youzan.com'
ACCOUNT='https://account.youzan.com'


class OrderExports:
    def __init__(self,youzan,files,verifications,*,transport=None,sleep=asyncio.sleep):
        self.youzan,self.files,self.verifications=youzan,files,verifications
        self.transport,self.sleep=transport,sleep
        self.locks={}

    def folder(self,user):
        return Accounts(self.youzan.directory).path(user,'tasks/youzan-order-export')

    def save(self,user,job):atomic_json(self.folder(user)/(job['job_id']+'.json'),job)

    def load(self,user,identifier):
        with source_file(self.folder(user),identifier+'.json') as fd:
            import os
            with os.fdopen(os.dup(fd),'r') as stream:job=json.load(stream)
        if job.get('user_id')!=user['id'] or job.get('workspace_id')!=user['workspace_id']:
            raise AuthFailure('export_not_found','导出任务不属于当前账号。',404)
        return job

    @staticmethod
    def stamp(credentials):
        return hashlib.sha256(json.dumps(credentials,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def validate(self,user,job,check):
        check()
        current=self.youzan.tool_credentials(user,job['shop_id'])
        if self.stamp(current)!=job['session_stamp']:
            raise AuthFailure('connection_changed','有赞会话已切换或刷新，请用新的会话续接导出。',409)

    def client(self,credentials):
        cookies=httpx.Cookies()
        for key,value in {**credentials.get('cookies',{}),'sid':credentials['sid'],'kdt_id':credentials['kdt_id']}.items():
            cookies.set(key,value,domain='.youzan.com',path='/')
        return httpx.AsyncClient(cookies=cookies,transport=self.transport,trust_env=False,follow_redirects=False,
            timeout=httpx.Timeout(30,connect=10),headers={'User-Agent':'Mozilla/5.0','Origin':'https://store.youzan.com',
                'Referer':'https://store.youzan.com/v2/order/exportList','Accept':'application/json'})

    def common(self,credentials):
        return {'source':'WEB_BACK_END','retail_source':'WEB-RETAIL-AJAX','idempotent_no':uuid.uuid4().hex,
                **{k:v for k,v in credentials.get('cookies',{}).items() if k in ('carmen_csrf_token','bifrost_csrf_token')}}

    async def request(self,client,method,url,*,params=None,data=None):
        try:
            response=await client.request(method,url,params=params,data=data)
            if response.status_code!=200:raise ValueError()
            result=response.json()
            if not isinstance(result,dict):raise ValueError()
            if 'response' in result:return result['response']
            if result.get('code') not in (0,200) or result.get('success') is False:
                message=str(result.get('msg',result.get('message','')))
                if re.search(r'滑块|人机|安全验证|行为验证|ticket|图形|验证票据',message,re.I):
                    raise AuthFailure('youzan_security_required','有赞要求先人工完成安全验证，短信尚未确认发送。',409)
                raise AuthFailure('youzan_export_rejected','有赞未接受导出或验证请求，请检查会话和导出权限。',409)
            return result.get('data')
        except (httpx.HTTPError,ValueError):
            raise AuthFailure('youzan_export_unavailable','有赞导出接口暂不可用；已创建任务可用 job_id 续接。',502) from None

    async def template(self,client,credentials):
        common={**self.common(credentials),'access_token':credentials['cookies'].get('access_token','')}
        found=[];page=1
        while True:
            result=await self.request(client,'GET','https://gateway.youzan.com/api/youzan.retail.export.list.customized/1.0.0',
                params={**common,'page_no':page,'page_size':20,'contain_system_template':'true'})
            items=result.get('items') if isinstance(result,dict) else None
            if not isinstance(items,list):raise AuthFailure('export_template_invalid','模板列表结构异常。',502)
            found.extend(row for row in items if row.get('customer_template_name')==TEMPLATE)
            total=result.get('paginator',{}).get('total_count')
            if not items or isinstance(total,int) and page*20>=total:break
            page+=1
        if len(found)!=1:raise AuthFailure('export_template_missing','当前店铺未找到唯一的“AI财务订单表1”模板，请先创建或核对名称。',409)
        identifier=str(found[0]['customer_template_id'])
        result=await self.request(client,'GET','https://gateway.youzan.com/api/youzan.retail.export.query.customized/1.0.0',
            params={**common,'customer_template_id':identifier})
        fields=result.get('required_order_fields',[]) if isinstance(result,dict) else []
        if (result.get('dimension')!='goods' or result.get('format')!='csv' or result.get('disabled')
                or [r.get('name') for r in fields]!=FIELDS or [r.get('title') for r in fields]!=HEADERS):
            raise AuthFailure('export_template_changed','“AI财务订单表1”必须为商品维度CSV及已确认的6列；请核对模板。',409)
        return identifier

    async def export_csrf(self,client,credentials):
        saved=credentials.get('cookies',{})
        if saved.get('bifrost_csrf_token') or saved.get('carmen_csrf_token'):
            return saved.get('bifrost_csrf_token') or saved['carmen_csrf_token']
        # Standard shop entry prepares only this adapter's private cookie jar.
        # It never changes saved credentials or invokes the account login flow.
        url='https://www.youzan.com/v4/shop/enter-shop?'+urlencode({'kdtId':credentials['kdt_id'],
            'redirect':'https://store.youzan.com/v2/order/exportList'})
        allowed={('www.youzan.com','/v4/shop/enter-shop'),('store.youzan.com','/v2/order/exportList')}
        try:
            for _ in range(8):
                parts=urlsplit(url)
                if (parts.scheme!='https' or parts.port not in (None,443) or parts.username or parts.password
                        or (parts.hostname,parts.path) not in allowed):break
                status,headers,text=await read_youzan_page(client,url,headers={'Accept':'text/html'})
                if status in (301,302,303,307,308) and headers.get('location'):
                    url=urljoin(url,headers['location']);continue
                if status==200 and parts.hostname=='store.youzan.com':return extract_csrf(text)
                break
        except httpx.HTTPError:pass
        raise AuthFailure('verification_session_required','无法用现有会话准备订单导出验证，请检查该店铺会话及导出权限。',409)

    async def run(self,user,context,body,credentials,check,*,interactive):
        async with self.locks.setdefault((user['id'],body.get('job_id','create')),asyncio.Lock()):
            if body['action']=='resume':
                job=self.load(user,body['job_id'])
                if job['shop_id']!=credentials['kdt_id']:raise AuthFailure('shop_scope_denied','请使用原导出任务对应的店铺续接。',409)
                job['session_stamp']=self.stamp(credentials)
                if job.get('receipt'):
                    with source_file(self.files.cache(user),job['receipt']['path']):pass
                    return job['receipt']
                pending=self.verifications.entries.get(job.get('challenge_id'))
                if pending is not None and pending.state in ('sent','received','checking') and self.verifications.monotonic()<pending.deadline:
                    if pending.owner!=(context.workspace_id,context.user_id,context.conversation_id):
                        raise AuthFailure('verification_busy','原导出任务在另一对话等待验证，请先完成或取消。',409)
                    self.verifications.get(context,pending.id)
                    return {**self.verifications.required(pending),'job_id':job['job_id'],'template':TEMPLATE}
            else:
                job={'job_id':uuid.uuid4().hex,'user_id':user['id'],'workspace_id':user['workspace_id'],
                     'shop_id':credentials['kdt_id'],'session_stamp':self.stamp(credentials),
                     'start_time':body['start_time'],'end_time':body['end_time'],'status':'creating','created_at':int(time.time())}
            async with self.client(credentials) as client:
                self.validate(user,job,check)
                if 'upstream_id' not in job:
                    template=await self.template(client,credentials)
                    tz=dt.timezone(dt.timedelta(hours=8))
                    times={key:int(dt.datetime.strptime(job[key],'%Y-%m-%d %H:%M:%S').replace(tzinfo=tz).timestamp()*1000) for key in ('start_time','end_time')}
                    result=await self.request(client,'POST',CAR+'/gw/web/youzan.retail.trademanager.order.list/1.0.0/export',
                        data={**self.common(credentials),**times,'time_type':'book_time','search_label':'order_nos','order_sources':'[]',
                              'sort':0,'table_id_name':'全部','customer_template_id':template})
                    if not str(result).isdigit():raise AuthFailure('export_create_failed','有赞未返回有效的导出任务ID。',502)
                    job.update(upstream_id=str(result),template_id=template,status='generating');self.save(user,job)
                download=None
                while download is None:
                    self.validate(user,job,check)
                    result=await self.request(client,'GET',CAR+'/gw/web/youzan.trades.export/3.0.0/get',
                        params={**self.common(credentials),'page':1,'size':20,'kdt_id':job['shop_id'],'biz_type':'order'})
                    page=1
                    while True:
                        items=result.get('list',[]) if isinstance(result,dict) else []
                        rows=[r for r in items if str(r.get('id'))==job['upstream_id'] and str(r.get('kdtId'))==job['shop_id']]
                        if rows or not result.get('hasNext'):break
                        page+=1
                        result=await self.request(client,'GET',CAR+'/gw/web/youzan.trades.export/3.0.0/get',
                            params={**self.common(credentials),'page':page,'size':20,'kdt_id':job['shop_id'],'biz_type':'order'})
                    if not rows:raise AuthFailure('export_not_found','导出列表未找到原任务，请稍后按 job_id 续接，勿重复创建。',409)
                    row=rows[0]
                    if row.get('exportState')==10 and row.get('url'):
                        download={'url':row['url'],'token':row.get('token'),'filename':row.get('fileName','')};break
                    if row.get('exportState')!=1:raise AuthFailure('export_failed','有赞导出任务未成功，请检查平台任务状态。',409)
                    await self.sleep(3)
                job['status']='ready';self.save(user,job)
                # Captured account SMS requests use the export page's Bifrost/
                # Carmen CSRF, not the CRM page's csrf and not a login refresh.
                account_csrf=await self.export_csrf(client,credentials)
                state=await self.request(client,'GET',ACCOUNT+'/api/captcha/sms/cache.json',
                    params={'operationType':'ORDER_GOODS_EXPORT','csrf_token':account_csrf})
                self.validate(user,job,check)
                if state.get('method')=='NO_CERT':return await self.download(user,job,download,check)
                if state.get('method')!='SMS_CERT':raise AuthFailure('verification_unsupported','有赞要求其他验证方式，请人工处理。',409)
                if not interactive:return {'status':'verification_client_required','job_id':job['job_id'],
                    'error':{'code':'verification_client_required','message':'导出已生成，需要短信验证；请在已更新的桌面客户端用 job_id 续接。'}}
                with self.youzan.repo.transaction(write=False) as records:
                    account=records['connections'].get(owner_key(user),{}).get('account','')
                if not re.fullmatch(r'\d{11}',account):raise AuthFailure('verification_mobile_required','当前有赞绑定账号未提供可用的验证手机号。',409)
                # This is the official SMS endpoint; a required human challenge is
                # surfaced, never solved or bypassed by this export adapter.
                try:
                    sent=await self.request(client,'GET',ACCOUNT+'/api/captcha/sms/send.json',params={'mobile':account,'countryCode':'+86',
                        'behaviorBizType':185,'platform':'pc','subFrom':'trade_export_double_check','biz':'trade_export_double_check','csrf_token':account_csrf})
                    if not isinstance(sent,dict) or sent.get('success') is not True:raise ValueError()
                except (AuthFailure,ValueError) as error:
                    return {'status':'verification_send_failed','job_id':job['job_id'],'error':{
                        'code':error.code if isinstance(error,AuthFailure) else 'youzan_sms_send_failed',
                        'message':error.message if isinstance(error,AuthFailure) else '有赞未确认短信发送成功；原导出任务已保留，未进入验证码倒计时。'}}
                self.validate(user,job,check)
                cookies=httpx.Cookies(client.cookies)
                async def submit(code,validate):
                    async with self.youzan.operation_guard(user) if hasattr(self.youzan,'operation_guard') else nullcontext():
                        self.validate(user,job,validate)
                        async with self.client(credentials) as verification_client:
                            verification_client.cookies=cookies
                            try:
                                verified=await self.request(verification_client,'POST',ACCOUNT+'/api/captcha/sms/check.json',
                                    data={'biz':'trade_export_double_check','mobile':account,'platform':'pc','smsCaptcha':code,'csrf_token':account_csrf})
                                if not isinstance(verified,dict) or verified.get('success') is not True:raise ValueError()
                            except AuthFailure as error:
                                if error.code!='youzan_export_rejected':raise
                                raise AuthFailure('verification_invalid','有赞未通过本次验证码。',409) from None
                            except ValueError:raise AuthFailure('verification_invalid','有赞未通过本次验证码。',409) from None
                            recheck=await self.request(verification_client,'GET',ACCOUNT+'/api/captcha/sms/cache.json',
                                params={'operationType':'ORDER_GOODS_EXPORT','csrf_token':account_csrf})
                            if recheck.get('method')!='NO_CERT':raise AuthFailure('verification_invalid','有赞尚未确认下载验证通过。',409)
                    return await self.download(user,job,download,validate)
                challenge=self.verifications.issue(context,channel='youzan',purpose='有赞订单导出 · '+TEMPLATE,
                    destination=account[:3]+'****'+account[-4:],submitter=submit,validate=lambda:self.validate(user,job,check))
                job.update(status='verification_required',challenge_id=challenge['challenge_id']);self.save(user,job)
                return {**challenge,'job_id':job['job_id'],'template':TEMPLATE,'message':'短信验证码已发送，请立即调用 interaction.verification，等待客户端输入。'}

    async def download(self,user,job,download,check):
        self.validate(user,job,check)
        url=download['url'];parts=urlsplit(url)
        if (parts.scheme!='https' or parts.hostname!='yz-trade-private.yzcdn.cn' or parts.port not in (None,443)
                or parts.username or parts.password or not parts.path.startswith('/private_file/trade_export/')):
            raise AuthFailure('export_download_denied','有赞返回的下载地址不在允许范围。',502)
        # The platform URL/token is private: never return it to the model or logs.
        params=None if 'token' in parse_qs(parts.query) else ({'token':download['token']} if download.get('token') else None)
        data=bytearray()
        try:
            async with httpx.AsyncClient(transport=self.transport,trust_env=False,follow_redirects=False,timeout=120) as client:
                async with client.stream('GET',url,params=params) as response:
                    if response.status_code!=200:raise ValueError()
                    async for chunk in response.aiter_bytes():
                        self.validate(user,job,check)
                        data.extend(chunk)
                        if len(data)>512*1024*1024:raise ValueError()
            text=bytes(data).decode('utf-8-sig');reader=csv.reader(io.StringIO(text));headers=next(reader)
            if headers!=HEADERS:raise ValueError()
            count=sum(1 for _ in reader)
        except (httpx.HTTPError,UnicodeError,ValueError,StopIteration):
            raise AuthFailure('export_download_failed','订单CSV下载或字段校验失败，原导出任务已保留。',502) from None
        self.validate(user,job,check)
        frame={'cache_directory':str(self.files.cache(user)),'cache_identity':{'user_id':user['id'],'workspace_id':user['workspace_id']}}
        def store():
            with CachedDataset(frame,'youzan/order-export',{'job_id':job['job_id'],'shop_id':job['shop_id'],
                'start_time':job['start_time'],'end_time':job['end_time'],'template':TEMPLATE},format='csv') as dataset:
                dataset.append(bytes(data));dataset.metadata.update(count=count,total=count,complete=True,pages=1,headers=HEADERS)
                dataset.save();return dict(dataset.metadata)
        receipt=await asyncio.to_thread(store)
        self.validate(user,job,check)
        result={**receipt,'status':'downloaded','job_id':job['job_id'],'template':TEMPLATE,'rows':count,
                'start_time':job['start_time'],'end_time':job['end_time']}
        job.update(status='downloaded',receipt=result);self.save(user,job)
        return result
