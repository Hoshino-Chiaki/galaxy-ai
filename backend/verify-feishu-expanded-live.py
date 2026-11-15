"""Server-only read verification. Credentials arrive in stdin, never in logs."""
import asyncio
import json
import sys
import uuid

from xingxu.app import create_app
from xingxu.tools import ExecutionContext
from galaxy_cli.feishu_capabilities import DOMAINS, scopes_satisfied


async def main():
    values=json.load(sys.stdin);app=create_app();auth=app.state.auth
    session=auth.login(values['username'],values['password'],False,'feishu-release-verification')
    try:
        user=session['user'];summary=app.state.feishu.summary(user)
        context=ExecutionContext(user['id'],user['workspace_id'],tuple(user['permissions']),str(uuid.uuid4()),session['access_token'])
        report={'role':user['role'],'connected':summary['connected'],
                'business_domains':summary['business_domains'],'business_operations':summary['business_operations'],
                'authorization_mode':summary.get('authorization_mode'),'checks':[]}
        credentials=await app.state.feishu.credentials(user)
        current_identity=await app.state.feishu.oauth.identity(credentials['access_token'])
        report['live_identity_verified']=all(current_identity.get(k)==summary['identity'].get(k) for k in ('open_id','tenant_key'))
        report['real_document_content_read']=False
        calls=[('docs',{'action':'help','command':'+create'}),
               ('search',{'query':'','page_size':1}),
               ('drive',{'action':'+search','options':{'query':'','page-size':'1'}}),
               ('drive',{'action':'files.list','params':{'page_size':1}})]
        for name,body in calls:
            call={'id':str(uuid.uuid4()),'type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':['feishu',name,'--input',json.dumps(body)]})}}
            event=await app.state.ai.dispatcher.execute(context,call)
            result=event['result'];data=result.get('data',{})
            entry=DOMAINS['drive']['commands']['+search'] if name=='search' else DOMAINS[name]['commands'].get(body.get('action'))
            expected_scope_denial=bool(entry and not scopes_satisfied(entry,summary['scopes']))
            report['checks'].append({'tool':name,'action':body.get('action'), 'status':event['status'],
                'expected_scope_denial':expected_scope_denial,
                'error_code':result.get('error',{}).get('code'),
                'data_type':type(data).__name__,'data_keys':list(data) if isinstance(data,dict) else [],
                'array_counts':{k:len(v) for k,v in data.items() if isinstance(v,list)} if isinstance(data,dict) else {}})
        print(json.dumps(report,ensure_ascii=False))
        if not report['live_identity_verified'] or not all(item['status']=='success' or item['expected_scope_denial'] and item['error_code']=='feishu_scope_required' for item in report['checks']):raise SystemExit(1)
    finally:
        auth.logout(session['access_token']);await app.state.youzan.close()


if __name__=='__main__':asyncio.run(main())
