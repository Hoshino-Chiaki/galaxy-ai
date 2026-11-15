import copy

from test_ai import PROVIDER, setup


def test_category_prompts_are_separate_admin_only_and_empty_disables_injection(setup):
    app, client, admin, employee, requests = setup
    client.post('/api/v1/admin/ai/providers', headers=admin, json=PROVIDER)
    client.put('/api/v1/admin/ai/prompt', headers=admin, json={'prompt': '通用身份规则'})
    path = '/api/v1/admin/ai/tool-prompts'
    catalog = client.get(path, headers=admin).json()['categories']
    assert {'youzan', 'excel', 'file'} <= {row['category'] for row in catalog}
    assert all(row['prompt'] == '' for row in catalog)
    assert client.get(path, headers=employee).status_code == 403
    assert client.put(path + '/youzan', headers=employee, json={'prompt': 'unauthorized'}).status_code == 403
    assert client.put(path + '/unknown', headers=admin, json={'prompt': 'unknown'}).status_code == 404
    assert client.put(path + '/youzan', headers=admin, json={'prompt': '有赞查询优先覆盖全部授权店铺。'}).status_code == 200
    assert client.get('/api/v1/admin/ai', headers=admin).json()['global_prompt'] == '通用身份规则'
    assert client.post('/api/v1/chat/send', headers=employee, json={'message': '测试'}).status_code == 200
    messages = requests[-1][2]['messages']
    assert messages[0]['content'] == '通用身份规则'
    assert messages[1] == {'role': 'system', 'content': '【工具分类：youzan】\n有赞查询优先覆盖全部授权店铺。'}
    assert client.put(path + '/youzan', headers=admin, json={'prompt': '  \n'}).status_code == 200
    assert client.post('/api/v1/chat/send', headers=employee, json={'message': '不注入空说明'}).status_code == 200
    assert not any('工具分类：' in message['content'] for message in requests[-1][2]['messages'])


def test_workspace_and_category_permission_isolation(setup):
    app, _, _, _, _ = setup
    prompts = app.state.ai.tool_prompts
    user = {'workspace_id': 'workspace-a', 'permissions': ['tools:galaxy_cli', 'galaxy:help', 'youzan:customer:read']}
    prompts.save(user, 'youzan', 'workspace-a-private-guidance')
    prompts.save(user, 'excel', 'excel-only-guidance')
    permissions = {'youzan.customer-search': 'youzan:customer:read', 'excel.create': 'excel:export'}
    assert [message['content'] for message in prompts.messages(user, permissions)] == ['【工具分类：youzan】\nworkspace-a-private-guidance']
    other = {**user, 'workspace_id': 'workspace-b'}
    assert prompts.messages(other, permissions) == []
    assert all(row['prompt'] == '' for row in prompts.settings(other)['categories'])
    assert prompts.messages({**user, 'permissions': []}, permissions) == []
