import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from test_excel import setup, context
from xingxu.auth import ROLES
from xingxu.tools import help_arguments, required_permissions


def call(args):
    return {'id':'catalog-test','type':'function','function':{'name':'galaxy_cli','arguments':json.dumps({'args':args})}}


def args(name,body,action=None):
    return ['youzan',name,*([action] if action else []),'--input',json.dumps(body)]


@pytest.mark.parametrize('name,action,body',[('product-search','query',{'keyword':'fixture'}),('product-search','options',{'section':'basic'}),('material-category','list',{}),('material-category','create',{'name':'Test'}),
    ('material-list',None,{'category_id':'10'}),('material-upload',None,{'category_id':'10','path':'a.png'}),
    ('product-get',None,{'product_id':'100'}),('product-options','groups',{'product_id':'100'}),
    ('product-update','preview',{'product_id':'100','changes':{'title':'new'}})])
def test_catalog_command_canonicalization_and_aliases(name,action,body):
    canonical=help_arguments(json.dumps({'args':args(name,body,action)}))
    alias=['youzan.'+name,*([action] if action else []),'--input',json.dumps(body)]
    assert canonical==help_arguments(json.dumps({'args':alias}))
    assert canonical[-2:]==['--format','json']


@pytest.mark.parametrize('name,action,body',[('material-category','create',{'name':'Test'}),
    ('material-category','rename',{'category_id':'10','name':'New'}),('material-upload',None,{'category_id':'10','url':'https://example.org/a.png'}),
    ('product-update','apply',{'product_id':'100','base_update_time':1,'changes':{'title':'new'}})])
def test_employee_write_permissions_deny_before_child(setup,name,action,body):
    app,_,employee,_=setup;dispatcher=app.state.ai.dispatcher;dispatcher._run=AsyncMock()
    event=asyncio.run(dispatcher.execute(context(employee),call(args(name,body,action))))
    assert event['result']['error']['code']=='permission_denied';dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize('changes,permission',[({'origin_price':'10.00'},'youzan:product:price:update'),
    ({'sku_updates':[{'sku_id':'1','stock_num':1}]},'youzan:product:stock:update'),
    ({'is_display':False},'youzan:product:display:update')])
def test_admin_field_grants_are_rechecked(setup,monkeypatch,changes,permission):
    app,admin,_,_=setup
    monkeypatch.setitem(ROLES,'admin',[p for p in ROLES['admin'] if p!=permission])
    dispatcher=app.state.ai.dispatcher;dispatcher._run=AsyncMock()
    event=asyncio.run(dispatcher.execute(context(admin),call(args('product-update',{'product_id':'100','base_update_time':1,'changes':changes},'apply'))))
    assert event['result']['error']['code']=='permission_denied';dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize('body',[{'product_id':'100','changes':{'supplier_price':'1.00'}},
    {'product_id':'100','changes':{'title':'new'},'workspace_directory':'/etc'},
    {'product_id':'100','changes':{'sku_updates':[{'sku_id':'1','price':'1e5'}]}},
    {'product_id':'100','changes':{'sku_updates':[{'sku_id':'1','price':'1.00'},{'sku_id':'1','price':'2.00'}]}}])
def test_unapproved_product_fields_and_types_never_execute(setup,body):
    app,admin,_,_=setup;dispatcher=app.state.ai.dispatcher;dispatcher._run=AsyncMock()
    event=asyncio.run(dispatcher.execute(context(admin),call(args('product-update',body,'preview'))))
    assert event['status']=='denied';dispatcher._run.assert_not_awaited()


def test_large_html_uses_private_stdin_and_server_selected_scope(setup,tmp_path):
    app,admin,_,_=setup;dispatcher=app.state.ai.dispatcher
    observed=tmp_path/'observed.json';executable=tmp_path/'fixture-cli'
    executable.write_text('#!/usr/bin/python3\nimport sys,json\nfrom pathlib import Path\nf=json.load(sys.stdin)\nPath('+repr(str(observed))+').write_text(json.dumps({"args":sys.argv[1:],"frame":f}))\nprint(json.dumps({"product_id":"100","base_update_time":1,"changes":[]}))\n')
    executable.chmod(0o700);dispatcher.executable=executable
    dispatcher.category_provider=lambda user:{'sid':'fixture-private-sid','csrf':'fixture-private-csrf','kdt_id':'900','cookies':{}}
    dispatcher.shop_resolver=AsyncMock(return_value=[{'kdt_id':'900','shop_name':'Fixture shop'}])
    event=asyncio.run(dispatcher.execute(context(admin),call(args('product-update',{'product_id':'100','changes':{'detail_html':'<p>'+('a'*20000)+'</p>'}},'preview'))))
    assert event['status']=='success'
    captured=json.loads(observed.read_text())
    assert captured['args']==['youzan','product-update','preview','--input-stdin']
    assert captured['frame']['categories']['youzan']['sid']=='fixture-private-sid'
    assert captured['frame']['permissions']==admin['user']['permissions']
    assert captured['frame']['workspace_directory']==str(dispatcher.files.workspace(admin['user']))
    assert len(captured['frame']['input']['changes']['detail_html'])>20000
    assert 'fixture-private' not in json.dumps(event)
    assert 'detail_html' not in (tmp_path/'tool_audit.json').read_text() if (tmp_path/'tool_audit.json').exists() else True


def test_same_shop_write_lock_is_shared_across_accounts_but_material_cache_is_not(setup):
    app,admin,employee,_=setup;files=app.state.ai.dispatcher.files
    a=files.catalog_context(admin['user'],'900');b=files.catalog_context(employee['user'],'900')
    assert a['catalog_lock_directory']==b['catalog_lock_directory']
    assert a['workspace_directory']!=b['workspace_directory']
    assert a['catalog_lock_directory']!=files.catalog_context(admin['user'],'901')['catalog_lock_directory']


def test_cli_action_and_unknown_identity_injections_rejected(setup):
    app,admin,_,_=setup;dispatcher=app.state.ai.dispatcher;dispatcher._run=AsyncMock()
    for raw in (['youzan','product-get','--input-stdin'],['youzan','material-category','delete','--input','{}'],
        args('material-upload',{'category_id':'1','path':'/etc/passwd'}),args('product-options',{'product_id':'1','kdt_id':'another'},'groups')):
        event=asyncio.run(dispatcher.execute(context(admin),call(raw)))
        assert event['status']=='denied'
    dispatcher._run.assert_not_awaited()
