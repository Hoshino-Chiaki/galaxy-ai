import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from galaxy_cli.feishu_client import BINARY, INPUTS, cli_arguments, invoke, normalize
from galaxy_cli.registry import ToolError, load_registry


@pytest.mark.parametrize('name,value',[('search',{'query':'@/etc/passwd'}),('search',{'query':'x'*31}),
    ('document-get',{'document':'http://example.feishu.cn/docx/abc'}),('document-get',{'document':'https://example.feishu.cn@evil.invalid/docx/abc'}),
    ('document-get',{'document':'https://127.0.0.1/docx/abc'}),('document-get',{'document':'https://example.feishu.cn/drive/folder/abc'}),
    ('file-download',{'file':'abc','filename':'../secret.txt'}),('file-download',{'file':'abc','filename':'/etc/secret'}),
    ('file-download',{'file':'abc','filename':'x\\secret'}),('document-get',{'document':'abc','scope':'keyword'}),
    ('document-get',{'document':'abc','scope':'section'}),('search',{'access_token':'injected'}),('search',{'user_id':'another'})])
def test_input_cannot_select_local_files_external_hosts_or_identity(name,value):
    with pytest.raises(ToolError):normalize(name,value)


def test_private_employee_token_is_only_in_stdin_and_native_output_is_preserved(tmp_path):
    context={'categories':{'feishu':{'app_id':'cli_fixture','access_token':'fixture-private-token','open_id':'ou_fixture','scopes':'scope'}},'workspace_directory':str(tmp_path)}
    seen=[]
    def run(args,**kwargs):
        seen.append((args,kwargs))
        assert 'fixture-private-token' not in repr(args)+repr(kwargs['env'])
        assert json.loads(kwargs['input'])['access_token']=='fixture-private-token'
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'items':[{'title':'原始文档','reflected':'fixture-private-token'}],'has_more':True,'page_token':'next'}}).encode(),stderr=b'')
    fake=tmp_path/'lark-cli';fake.touch()
    with patch('galaxy_cli.feishu_client.read_context',return_value=context),patch('galaxy_cli.feishu_client.BINARY',fake),patch('subprocess.run',side_effect=run):
        result=invoke('search',{'query':'季度总结'})
    assert result['data']['items'][0]['title']=='原始文档'
    assert result['data']['items'][0]['reflected']=='[已隐藏凭证]'
    assert result['data']['has_more'] is True
    assert seen[0][0][1:3]==['drive','+search']


def test_download_is_saved_under_workdir_and_returns_real_relative_file(tmp_path):
    context={'categories':{'feishu':{'app_id':'cli_fixture','access_token':'fixture-private-token'}},'workspace_directory':str(tmp_path)}
    fake=tmp_path/'lark-cli';fake.touch()
    def run(args,**kwargs):
        destination=Path(args[args.index('--output')+1]);assert destination.is_relative_to(tmp_path)
        destination.write_bytes(b'fixture PDF bytes')
        return SimpleNamespace(returncode=0,stdout=b'{"ok":true,"data":{}}',stderr=b'')
    with patch('galaxy_cli.feishu_client.read_context',return_value=context),patch('galaxy_cli.feishu_client.BINARY',fake),patch('subprocess.run',side_effect=run):
        result=invoke('file-download',{'file':'fixture','filename':'报告.pdf'})
    assert set(result)=={'path','filename','size'} and result['filename']=='报告.pdf'
    assert (tmp_path/result['path']).read_bytes()==b'fixture PDF bytes'
    assert not Path(result['path']).is_absolute()


def test_failure_does_not_expose_cli_stderr_or_leave_download_files(tmp_path):
    context={'categories':{'feishu':{'app_id':'cli_fixture','access_token':'fixture-private-token'}},'workspace_directory':str(tmp_path)}
    fake=tmp_path/'lark-cli';fake.touch()
    def run(args,**kwargs):
        Path(args[args.index('--output')+1]).write_bytes(b'incomplete')
        return SimpleNamespace(returncode=1,stdout=b'',stderr=b'{"error":{"type":"authentication","message":"fixture-private-token"}}')
    with patch('galaxy_cli.feishu_client.read_context',return_value=context),patch('galaxy_cli.feishu_client.BINARY',fake),patch('subprocess.run',side_effect=run):
        with pytest.raises(ToolError) as error:invoke('file-download',{'file':'fixture','filename':'test.pdf'})
    assert 'fixture-private-token' not in error.value.message
    assert not list(tmp_path.glob('feishu-*'))


@pytest.mark.skipif(not BINARY.exists(),reason='Build the pinned Feishu wrapper to run official CLI dry-run checks')
def test_official_cli_wrapper_readonly_commands_and_private_provider(tmp_path):
    frame=json.dumps({'app_id':'cli_fixture','access_token':'fixture-private-token','open_id':'ou_fixture','scopes':'','workspace_directory':str(tmp_path)})
    env={'PATH':'/usr/bin:/bin','HOME':str(tmp_path),'LARKSUITE_CLI_CONFIG_DIR':str(tmp_path/'config')}
    for name,value in [('search',{'query':'业务'}),('document-get',{'document':'fixture'}),('folder-list',{}),('file-download',{'file':'fixture','filename':'f.bin'})]:
        args=cli_arguments(name,value)
        if name=='file-download':args+=['--output',str(tmp_path/'f.bin')]
        completed=subprocess.run([str(BINARY),*args,'--as','user','--dry-run'],input=frame,text=True,capture_output=True,timeout=20,env=env)
        assert completed.returncode==0,(name,completed.stderr)
        result=json.loads(completed.stdout)
        assert result['ok'] is True and result['dry_run'] is True
        assert 'fixture-private-token' not in completed.stdout+completed.stderr
    denied=subprocess.run([str(BINARY),'api','GET','/open-apis/drive/v1/files'],input=frame,text=True,capture_output=True,env=env)
    assert denied.returncode!=0 and 'policy' in denied.stderr


def test_registry_contains_feishu_readonly_category():
    data=load_registry().invoke('help',{'category':'feishu'})['data']
    assert {tool['tool'] for tool in data}=={'feishu.'+name for name in INPUTS}
    assert all('凭证' in tool['description'] or 'token' in tool['description'] for tool in data)
