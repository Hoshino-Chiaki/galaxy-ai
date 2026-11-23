import asyncio
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

from xingxu.tools import GalaxyDispatcher, help_visible, command_allowed, FEISHU_DOMAINS, feishu_permissions
from xingxu.tool_context import ExecutionContext


def test_visibility_uses_full_execution_grants_and_hides_unknown_tools():
    assert help_visible('youzan.order-search', ['youzan:order:read'])
    assert not help_visible('youzan.material-upload', ['youzan:material:read'])
    assert help_visible('youzan.material-upload', ['youzan:material:read','youzan:material:upload'])
    assert not help_visible('feishu.authorized-users', ['feishu:read'])
    assert help_visible('feishu.authorized-users', ['feishu:read','feishu:attendance:company:read'])
    assert not help_visible('future.unregistered', ['permissions:manage'])
    assert not help_visible('schedule.create', ['schedule:read'])
    assert help_visible('schedule.list', ['schedule:read'])
    # Mixed tools remain discoverable when at least one operation is permitted.
    assert help_visible('youzan.product-update', ['youzan:product:read'])


def test_all_help_variants_filter_current_identity_and_recheck_revocation(tmp_path):
    user={'id':'u','workspace_id':'w','username':'employee','permissions':[
        'tools:galaxy_cli','galaxy:help','youzan:order:read']}
    dispatcher=GalaxyDispatcher(tmp_path,authorize=lambda _: {'user':user})
    context=ExecutionContext('u','w',tuple(user['permissions']),'chat','session')
    catalog=[{'tool':name,'category':name.split('.')[0],'description':'test'} for name in (
        'youzan.order-search','youzan.material-upload','excel.create','erp.department-create','future.unregistered')]
    async def source(canonical,_user):
        filters=json.loads(canonical[-1]) if '--input' in canonical else {}
        return {'version':'test','data':[row for row in catalog
            if ('tool' not in filters or row['tool']==filters['tool'])
            and ('category' not in filters or row['category']==filters['category'])]}
    dispatcher._run=AsyncMock(side_effect=source)
    async def run(args):
        return await dispatcher.execute(context,{'id':'c','type':'function','function':{
            'name':'galaxy_cli','arguments':json.dumps({'args':args})}})
    for args in (['help'],['youzan','help'],['help','--category','youzan'],['help','youzan.order-search']):
        result=asyncio.run(run(args))
        assert result['status']=='success'
        assert [r['tool'] for r in result['result']['data']]==['youzan.order-search']
    result=asyncio.run(run(['help','youzan.material-upload']))
    assert result['status']=='success' and result['result']['data']==[]
    async def revoke(canonical,_user):
        user['permissions'].remove('youzan:order:read')
        return {'data':catalog}
    dispatcher._run=AsyncMock(side_effect=revoke)
    assert asyncio.run(run(['help']))['result']['data']==[]


def test_feishu_domain_help_lists_only_allowed_operations_including_write_only_users(tmp_path):
    grants=['tools:galaxy_cli','galaxy:help','feishu:write','feishu:create']
    user={'id':'u','workspace_id':'w','username':'employee','permissions':grants}
    dispatcher=GalaxyDispatcher(tmp_path,authorize=lambda _: {'user':user})
    dispatcher.feishu_provider=SimpleNamespace()
    context=ExecutionContext('u','w',tuple(grants),'chat','session')
    command='feishu.docs'
    canonical=['feishu','docs','--input','{"action":"help"}']
    assert help_visible(command,grants) and command_allowed(command,canonical,grants)
    result=asyncio.run(dispatcher._feishu_run(context,command,canonical))
    assert result['data']
    for row in result['data']:
        assert feishu_permissions(command,['feishu','docs','--input',json.dumps({'action':row['action']})]).issubset(grants)
    denied=next(k for k in FEISHU_DOMAINS['docs']['commands']
        if not feishu_permissions(command,['feishu','docs','--input',json.dumps({'action':k})]).issubset(grants))
    from xingxu.tool_context import DispatchDenied
    import pytest
    with pytest.raises(DispatchDenied) as error:
        asyncio.run(dispatcher._feishu_run(context,command,['feishu','docs','--input',json.dumps({'action':'help','command':denied})]))
    assert error.value.code=='permission_denied'
