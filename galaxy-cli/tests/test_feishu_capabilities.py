import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator
from galaxy_cli.feishu_capabilities import DOMAINS, catalog, normalize, describe, cli_arguments, all_scopes, APPLICATION_ONLY, scopes_satisfied, is_write
from galaxy_cli.feishu_client import invoke
from galaxy_cli.registry import ToolError, load_registry


def test_catalog_matches_pinned_binary_and_every_schema_is_valid():
    source=Path(__file__).resolve().parents[2]/'backend/feishu/cli'
    assert catalog()['upstream_commit']==(source/'upstream.commit').read_text().strip()
    paths={'/'.join(e['path']) for info in DOMAINS.values() for e in info['commands'].values()}
    assert paths==set(json.loads((source/'allowed-commands.json').read_text()))
    assert len(paths)==669 and len(DOMAINS)==20
    for info in DOMAINS.values():
        for entry in info['commands'].values():Draft202012Validator.check_schema(entry['input_schema'])


@pytest.mark.parametrize('domain',list(DOMAINS))
def test_each_domain_is_registered_and_can_describe_each_action_without_credentials(domain):
    registry=load_registry()
    result=registry.invoke('feishu.'+domain,{'action':'help'})
    assert len(result['data'])==len(DOMAINS[domain]['commands'])
    for row in result['data']:
        selected=describe(domain,normalize(domain,{'action':'help','command':row['action']}))
        assert selected['data']['input_schema']['type']=='object'


def test_business_input_cannot_inject_global_flags_or_file_expansion():
    for body in [ {'action':'api'}, {'action':'+create','options':{'as':'bot'}},
                  {'action':'+create','options':{'content':'@/etc/passwd','doc-format':'markdown'}},
                  {'action':'+script','options':{}} ]:
        with pytest.raises(ToolError):normalize('docs',body)
    with pytest.raises(ToolError):normalize('sheets',{'action':'spreadsheets.get','params':{}})
    assert cli_arguments('drive',{'action':'files.list','params':{'page_size':10}})==['drive','files','list','--params','{"page_size":10}']


def test_write_and_preview_permissions_are_derived_from_official_operation():
    assert is_write('docs',{'action':'+create'})
    assert not is_write('docs',{'action':'+fetch'})
    assert not is_write('attendance',{'action':'user_tasks.query'})
    assert not is_write('docs',{'action':'+create','dry_run':True})
    entry=DOMAINS['sheets']['commands']['spreadsheets.get']
    assert not scopes_satisfied(entry,['offline_access'])
    assert scopes_satisfied(entry,[entry['scopes'][-1]])


def test_automatic_authorization_covers_every_supported_user_operation_once():
    scopes=all_scopes()
    assert 'offline_access' in scopes and len(scopes)>50
    assert len(scopes)==len(set(scopes))
    for domain,info in DOMAINS.items():
        for action,entry in info['commands'].items():
            if (domain,action) not in APPLICATION_ONLY:
                assert scopes_satisfied(entry,scopes),(domain,entry['path'])
    assert 'attendance:task:readonly' not in scopes
    assert 'im:message.send_as_user' in scopes


def test_business_execution_keeps_private_context_in_stdin_and_preserves_results(tmp_path):
    frame={'categories':{'feishu':{'app_id':'cli_fixture','access_token':'fixture-secret'}},'workspace_directory':str(tmp_path)}
    binary=tmp_path/'binary';binary.touch()
    def run(args,**kwargs):
        assert args[1:3]==['docs','+create'] and '--as' in args
        assert 'fixture-secret' not in repr(args)+repr(kwargs['env'])
        assert json.loads(kwargs['input'])['workspace_directory']==str(tmp_path)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'data':{'document_id':'abc','content':'fixture-secret','saved_path':str(tmp_path/'report.md')}}).encode(),stderr=b'')
    with patch('galaxy_cli.feishu_client.read_context',return_value=frame),patch('galaxy_cli.feishu_client.BINARY',binary),patch('subprocess.run',side_effect=run):
        result=invoke('docs',{'action':'+create','options':{'content':'hello','doc-format':'markdown'}})
    assert result['data']=={'document_id':'abc','content':'[已隐藏凭证]','saved_path':'report.md'}
