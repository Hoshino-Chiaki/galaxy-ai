from xingxu.permission_policy import resolve, normalize, catalog


def test_department_then_personal_and_inheritance():
    base=['ai:chat','youzan:order:read','feishu:read']
    assert 'youzan:order:read' not in resolve(base,{'youzan:order:read':False})
    assert 'youzan:order:read' in resolve(base,{'youzan:order:read':False},{'youzan:order:read':True})
    assert 'feishu:read' not in resolve(base,None,{'feishu:read':False})
    assert 'feishu:read' in resolve(base)


def test_channel_login_is_derived_from_any_business_capability():
    assert 'youzan:configure' in normalize(['youzan:order:read'])
    assert 'youzan:configure' not in normalize(['youzan:configure'])
    assert 'feishu:configure' in normalize(['feishu:create'])
    assert 'feishu:configure' not in normalize(['feishu:configure'])


def test_admin_has_all_permissions_even_when_department_denies():
    p=resolve(['ai:chat'],{'ai:chat':False},{'users:manage':False},admin=True)
    assert 'users:manage' in p and 'permissions:manage' in p and 'ai:chat' in p
    assert 'permissions:manage' not in resolve(['ai:chat'],admin=False)


def test_catalog_only_existing_capabilities_and_crud_grouping():
    entries={p['code']:p for c in catalog() for p in c['permissions']}
    assert entries['youzan:order:read']['action']=='查询'
    assert entries['youzan:material:category:create']['action']=='新增'
    assert entries['youzan:product:update']['action']=='修改'
    assert 'youzan:configure' not in entries
    assert all(p['action'] in ('新增','删除','修改','查询','其他') for p in entries.values())


def test_feishu_write_split_and_independent_revocation():
    base=['feishu:write','feishu:create','feishu:delete','feishu:update']
    p=resolve(base,None,{'feishu:delete':False})
    assert 'feishu:create' in p and 'feishu:delete' not in p and 'feishu:write' in p
    p=resolve(base,None,{k:False for k in ('feishu:create','feishu:delete','feishu:update')})
    assert 'feishu:write' not in p and 'feishu:configure' not in p
