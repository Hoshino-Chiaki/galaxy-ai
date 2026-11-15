import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from galaxy_cli.feishu_client import invoke, normalize
from galaxy_cli.registry import ToolError


def test_attendance_contract_defaults_employee_scope_and_preserves_full_result(tmp_path):
    value={'action':'user_tasks.query','data':{'check_date_from':20261111,'check_date_to':20261111}}
    assert normalize('attendance',value)==value
    frame={'workspace_directory':str(tmp_path),'categories':{'feishu':{
        'app_id':'cli_test','access_token':'private-application-token','identity':'application',
        'employee_id':'employee_self','is_tenant_manager':False,'scopes':'attendance:task:readonly'}}}
    binary=tmp_path/'lark-cli';binary.touch()
    def run(args,**kwargs):
        assert args[args.index('--as')+1]=='bot'
        assert json.loads(args[args.index('--params')+1])['employee_type']=='employee_id'
        assert json.loads(args[args.index('--data')+1])['user_ids']==['employee_self']
        assert json.loads(kwargs['input'])['identity']=='application'
        assert 'private-application-token' not in repr(args)+repr(kwargs['env'])
        return SimpleNamespace(returncode=0,stdout=b'{"ok":true,"data":{"user_task_results":[{"records":[{"status":"Normal"}]}],"invalid_user_ids":[],"unauthorized_user_ids":[]}}',stderr=b'')
    with patch('galaxy_cli.feishu_client.read_context',return_value=frame),patch('galaxy_cli.feishu_client.BINARY',binary),patch('subprocess.run',side_effect=run):
        result=invoke('attendance',value)
    assert result['data']['user_task_results'][0]['records']==[{'status':'Normal'}]
    assert result['data']['unauthorized_user_ids']==[]


def test_application_token_cannot_be_used_for_unrelated_personal_tools(tmp_path):
    frame={'workspace_directory':str(tmp_path),'categories':{'feishu':{
        'app_id':'cli_test','access_token':'private-application-token','identity':'application'}}}
    with patch('galaxy_cli.feishu_client.read_context',return_value=frame):
        with pytest.raises(ToolError) as error:invoke('search',{'query':'anything'})
    assert error.value.code=='feishu_identity_denied'


def test_platform_administrator_credential_is_not_a_caller_company_grant():
    from galaxy_cli.tools.feishu.attendance import prepare
    value={'data':{'user_ids':['another_employee'],'check_date_from':20261111,'check_date_to':20261111}}
    credentials={'identity':'application','employee_id':'self','is_tenant_manager':True}
    with pytest.raises(ToolError) as error:prepare(value,credentials)
    assert error.value.code=='feishu_attendance_forbidden'
    credentials['attendance_access']='application'
    assert prepare(value,credentials)['data']['user_ids']==['another_employee']


@pytest.mark.parametrize('ids',[[],['x']*51])
def test_attendance_employee_batch_limits_are_from_interface(ids):
    with pytest.raises(ToolError):normalize('attendance',{
        'action':'user_tasks.query','data':{'user_ids':ids,'check_date_from':20261111,'check_date_to':20261111}})
