import pytest
from galaxy_cli.registry import load_registry,ToolError
from galaxy_cli.tools.schedule import create,update_time


def test_schedule_category_has_only_registered_owner_scoped_tools():
    registry=load_registry();tools=registry.describe(category='schedule')
    assert {t['name'] for t in tools}=={'schedule.create','schedule.list','schedule.update-time','schedule.delete'}
    for name in ('schedule.list','schedule.create','schedule.update-time','schedule.delete'):
        assert registry.get(name).category=='schedule'
    with pytest.raises(ToolError) as e:registry.invoke('schedule.list',{})
    assert e.value.code=='server_context_required'


def test_schedule_input_cannot_choose_identity_or_credentials():
    for extra in ({'user_id':'other'},{'session_token':'secret'},{'path':'/etc/passwd'}):
        with pytest.raises(ToolError):create.normalize({'name':'test','instruction':'test','time_description':'test','rule':{'type':'cron','expression':'0 9 * * *'},'request_key':'test',**extra})
