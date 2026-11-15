"""Check every pinned business command resolves, without business API calls."""
import concurrent.futures
import json
import os
import subprocess
import tempfile
from pathlib import Path

root=Path(__file__).resolve().parents[3]
catalog=json.loads((root/'galaxy-cli/src/galaxy_cli/feishu_catalog.json').read_text())
binary=root/'backend/feishu/bin/lark-cli'
with tempfile.TemporaryDirectory(prefix='feishu-command-check-') as temporary:
    scopes=sorted({s for d in catalog['domains'].values() for e in d['commands'].values() for s in e['scopes']})
    frame=json.dumps({'app_id':'cli_fixture','access_token':'fixture-private-token','open_id':'ou_fixture','scopes':' '.join(scopes),'workspace_directory':temporary})
    env={'PATH':'/usr/bin:/bin','HOME':temporary,'LARKSUITE_CLI_CONFIG_DIR':temporary+'/config','NO_COLOR':'1'}
    entries=[e for d in catalog['domains'].values() for e in d['commands'].values()]
    def check(entry):
        context=json.loads(frame)
        context['identity']='application' if entry['path']==['attendance','user_tasks','query'] else 'user'
        result=subprocess.run([str(binary),*entry['path'],'--help'],input=json.dumps(context),text=True,capture_output=True,timeout=30,env=env)
        return '/'.join(entry['path']),result.returncode,result.stdout[:100] if result.returncode else ''
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(check,entries))
    failures=[{'command':path,'exit_code':code,'output':output} for path,code,output in results if code]
    report={'checked':len(results),'passed':len(results)-len(failures),'failed':failures,'business_api_calls':0,'real_credentials_used':False}
    print(json.dumps(report,ensure_ascii=False))
    if failures:raise SystemExit(1)
