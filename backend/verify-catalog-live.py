"""Sequential live tool checks; credentials never enter argv, logs, or artifacts."""
import hashlib,json,ssl,subprocess,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'desktop-client/artifacts/catalog-live-check.json'
phase='login';records=[];client=None;token=None

def run(args=None,seed=False,allow_error=False):
    frame={'token':token,'seed_input':seed} if seed else {'token':token,'args':args}
    process=subprocess.run(['ssh','root@124.222.51.137','runuser -u galaxy-ai -- env GALAXY_FILES_DIR=/opt/galaxy-ai/hosted-files PYTHONPATH=/opt/galaxy-ai/backend /opt/galaxy-ai/backend/.venv/bin/python /tmp/galaxy-catalog-dispatch-runner.py'],
        input=json.dumps(frame).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=200)
    if process.returncode:raise RuntimeError('remote verification failed')
    result=json.loads(process.stdout)
    if seed:return result
    event=result
    records.append({'command':event['command'],'status':event['status'],'error_code':event['result'].get('error',{}).get('code'),
        'upstream_code':event['result'].get('code'), 'message':event['result'].get('msg',event['result'].get('error',{}).get('message')) if event['status']!='success' else None})
    print(json.dumps(records[-1],ensure_ascii=False),flush=True)
    REPORT.write_text(json.dumps({'complete':False,'phase':phase,'steps':records},ensure_ascii=False,indent=2)+'\n')
    if event['status']!='success' and not allow_error:raise RuntimeError('tool check failed')
    return event['result']

def command(name,body=None,action=None):
    return ['youzan',name,*([action] if action else []),*(['--input',json.dumps(body,ensure_ascii=False)] if body is not None else [])]

try:
    lines=(ROOT/'1.txt').read_text().lstrip('\ufeff').splitlines()
    client=httpx.Client(base_url='https://124.222.51.137',verify=ssl.create_default_context(cafile=str(ROOT/'desktop-client/electron/certs/server.crt')),trust_env=False,timeout=30)
    login=client.post('/api/v1/auth/login',json={'username':lines[0],'password':lines[1],'remember':False});assert login.status_code==200
    token=login.json()['access_token'];client.headers['Authorization']='Bearer '+token
    phase='catalog'
    config=client.get('/api/v1/admin/ai').json();prompt_hash=hashlib.sha256(config['global_prompt'].encode()).hexdigest()
    catalog=client.get('/api/v1/admin/ai/tools').json()['data'];assert len(catalog)==11
    for name in ('product-get','product-options','product-update'):
        description=next(t['description'] for t in catalog if t['tool']=='youzan.'+name)
        assert '分销' in description and '供货价' in description
    phase='material_category'
    categories=run(command('material-category',action='list'))['data']
    root=next(str(row['categoryId']) for row in categories if row['categoryType']==5 and not row.get('parentCategoryId'))
    created=run(command('material-category',{'name':'GalaxyT'+time.strftime('%H%M%S'),'parent_id':root},'create'))
    parent=str(created['data'])
    child=str(run(command('material-category',{'name':'测试图片','parent_id':parent},'create'))['data'])
    run(command('material-category',{'category_id':child,'name':'测试图片-已改名'},'rename'))
    run(command('material-category',{'category_ids':[child],'parent_id':root},'move'))
    run(command('material-category',{'category_ids':[child],'parent_id':parent},'move'))
    cycle=run(command('material-category',{'category_ids':[parent],'parent_id':child},'move'),allow_error=True)
    assert 'error' in cycle
    phase='local_image_upload'
    run(seed=True)
    local=run(command('material-upload',{'category_id':child,'path':'input/catalog-test-image.jpg'}))
    local_data=local.get('data',local)
    url=local_data.get('attachment_full_url',local_data.get('attachmentFullUrl',local_data.get('attachment_url')))
    assert isinstance(url,str)
    phase='network_image_upload'
    network=run(command('material-upload',{'category_id':child,'url':url}))
    phase='material_list'
    images=run(command('material-list',{'category_id':child}))
    assert images['data']['items']
    phase='product_get'
    original=run(command('product-get',{'product_id':'4903044508'}))['data']
    phase='product_options'
    for action,extra in [('groups',{}),('sku-values',{'spec_id':str(original['itemStockParam']['skuName1']['id'])}),('detail-templates',{}),('delivery',{}),('shops',{})]:
        run(command('product-options',{'product_id':str(original['id']),**extra},action))
    phase='product_preview'
    preview=run(command('product-update',{'product_id':str(original['id']),'changes':{'title':original['title']}},'preview'))
    assert not preview['changes'] and preview.get('warnings')
    first_sku=original['itemStockParam']['stock'][0]
    denied=run(command('product-update',{'product_id':str(original['id']),'changes':{'sku_updates':[{'sku_id':str(first_sku['id']),'stock_num':1}]}},'preview'),allow_error=True)
    assert denied.get('error',{}).get('code')=='distribution_read_only'
    assert hashlib.sha256(client.get('/api/v1/admin/ai').json()['global_prompt'].encode()).hexdigest()==prompt_hash
    result={'complete':True,'steps':records,'category_id':child,'parent_category_id':parent,
        'local_upload':True,'network_upload':True,'distribution_rules_in_help':True,'global_prompt_unchanged':True,
        'product_write_test':'pending selected test product lookup','verified_tools':5,'preview_verified':True}
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'phase':'complete','steps':len(records),'local_upload':True,'network_upload':True,'preview':True},ensure_ascii=False))
except Exception:
    print(json.dumps({'complete':False,'phase':phase,'recorded_steps':len(records)}));raise SystemExit(1)
finally:
    if client:
        if token:
            try:client.post('/api/v1/auth/logout')
            except Exception:pass
        client.close()
