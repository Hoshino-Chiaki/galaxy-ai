import contextlib
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from galaxy_cli.cli import main
from galaxy_cli.feishu_client import invoke
from galaxy_cli.feishu_errors import raise_upstream
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.feishu.authorized_users import invoke as directory_invoke, normalize


@pytest.mark.parametrize('value', [
    {'action':'users'}, {'action':'departments'}, {'action':'user'},
    {'action':'scope','department_id':'0'}, {'action':'scope','user_id':'another'},
    {'action':'user','user_id':'valid','page_size':10}, {'action':'users','department_id':'../../secret'},
    {'url':'https://example.invalid'}, {'access_token':'injected'}, {'directory_result':{'code':0}},
    {'page_size':0}, {'page_size':51}, {'page_token':'@local'}, {'page_token':'x\x00y'}])
def test_directory_input_cannot_expand_identity_host_path_or_action(value):
    with pytest.raises(ToolError):normalize(value)


def test_directory_requires_server_prepared_context():
    with patch('galaxy_cli.tools.feishu.authorized_users.read_context',return_value={}):
        with pytest.raises(ToolError) as error:directory_invoke({})
    assert error.value.code=='server_context_required'


def test_directory_preserves_business_result_and_marks_coverage_without_token():
    original={'code':0,'msg':'success','data':{'user_ids':['real_a','real_b'],
        'department_ids':['od-authorized'],'group_ids':['group_a'],'has_more':True,'page_token':'next+/='}}
    frame={'categories':{'feishu':{'directory_access':'application','directory_result':original,'directory_http_status':200}}}
    with patch('galaxy_cli.tools.feishu.authorized_users.read_context',return_value=frame):result=directory_invoke({})
    assert result['data']==original['data']
    assert result['coverage']=='application_authorized_scope'
    assert 'coverage' not in original
    assert 'access_token' not in json.dumps(frame)
    assert load_registry().get('feishu.authorized-users').category=='feishu'


def test_directory_error_keeps_business_code_even_with_nested_log_metadata():
    value={'code':40004,'msg':'no dept authority error','error':{'log_id':'LOG_123'}}
    frame={'categories':{'feishu':{'directory_access':'application','directory_result':value,'directory_http_status':403}}}
    with patch('galaxy_cli.tools.feishu.authorized_users.read_context',return_value=frame):
        with pytest.raises(ToolError) as error:directory_invoke({'action':'users','department_id':'0'})
    assert error.value.code=='feishu_api_40004'
    assert error.value.details=={'upstream_code':40004,'http_status':403,'request_id':'LOG_123'}
    assert 'no dept authority error' in error.value.message


def test_native_error_preserves_invalid_employee_reason_and_redacts_token(tmp_path):
    frame={'workspace_directory':str(tmp_path),'categories':{'feishu':{
        'app_id':'cli_test','access_token':'fixture-private-token','identity':'application',
        'employee_id':'self','attendance_access':'application'}}}
    fake=tmp_path/'cli';fake.touch()
    value={'ok':False,'error':{'type':'api','subtype':'unknown','code':1220001,
        'message':'userIds all invalid; Bearer fixture-private-token','log_id':'LOG_456'}}
    completed=SimpleNamespace(returncode=1,stdout=b'',stderr=json.dumps(value).encode())
    with patch('galaxy_cli.feishu_client.read_context',return_value=frame),patch('galaxy_cli.feishu_client.BINARY',fake),patch('subprocess.run',return_value=completed):
        with pytest.raises(ToolError) as error:invoke('attendance',{'action':'user_tasks.query',
            'data':{'user_ids':['invalid'],'check_date_from':20261111,'check_date_to':20261111}})
    assert error.value.code=='feishu_api_1220001'
    assert error.value.details['upstream_code']==1220001
    assert 'userIds all invalid' in error.value.message
    assert 'fixture-private-token' not in error.value.message


def test_cli_error_json_exposes_selected_diagnostics_only():
    value={'code':99991672,'msg':'missing permission access_token="private value"',
           'error':{'log_id':'LOG_SCOPE'},'request_headers':{'Authorization':'must-not-escape'}}
    def fail(*_):raise_upstream(value,http_status=403)
    registry=SimpleNamespace(categories=lambda:[],invoke=fail)
    output=io.StringIO()
    with patch('galaxy_cli.cli.load_registry',return_value=registry),contextlib.redirect_stdout(output):
        assert main(['fake'])==1
    result=json.loads(output.getvalue())['error']
    assert result['upstream_code']==99991672 and result['http_status']==403
    assert result['request_id']=='LOG_SCOPE'
    assert 'request_headers' not in output.getvalue() and 'must-not-escape' not in output.getvalue()
    assert 'private value' not in output.getvalue()
