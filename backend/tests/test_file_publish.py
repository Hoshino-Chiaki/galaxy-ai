import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from test_excel import setup, context, headers, call
from xingxu.auth import ROLES
from xingxu.app import scoped_download
from galaxy_cli.registry import ToolError


def publish(path, **extra):
    return {'id': 'publish-fixture', 'type': 'function', 'function': {'name': 'galaxy_cli',
        'arguments': json.dumps({'args': ['file', 'publish', '--input', json.dumps({'path': path, **extra})]})}}


def draft(app, session, name='demo.txt', data=b'scoped file fixture'):
    workspace = app.state.ai.dispatcher.files.workspace(session['user'])
    (workspace / name).write_bytes(data)
    return workspace / name


def committed(app, session, ctx, event):
    with app.state.ai.dispatcher.files.chats.transaction() as state:
        state['conversations'][ctx.conversation_id] = {'user_id': session['user']['id'],
            'workspace_id': session['user']['workspace_id'], 'messages': [{'tool_events': [event]}]}


@pytest.mark.parametrize('path', ['/etc/passwd', '../../backend/private/auth.json', '.work/x', 'a/../secret', '%2fetc/passwd', 'C:\\Windows\\a', 'https://example.org/x'])
def test_invalid_paths_never_spawn(setup, path):
    app, _, session, _ = setup
    app.state.ai.dispatcher._run = AsyncMock()
    event = asyncio.run(app.state.ai.dispatcher.execute(context(session), publish(path)))
    assert event['status'] == 'denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_identity_and_context_arguments_cannot_be_injected(setup):
    app, _, session, _ = setup
    app.state.ai.dispatcher._run = AsyncMock()
    event = asyncio.run(app.state.ai.dispatcher.execute(context(session), publish('demo.txt', workspace_directory='/etc', user_id='another')))
    assert event['status'] == 'denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


@pytest.mark.parametrize('kind', ['leaf', 'ancestor', 'hard', 'fifo', 'other-user'])
def test_preflight_denies_links_special_files_and_cross_user_sources(setup, kind):
    app, admin, session, directory = setup
    private = directory / 'outside'; private.mkdir(); secret = private / 'secret.txt'; secret.write_text('sensitive')
    workspace = app.state.ai.dispatcher.files.workspace(session['user'])
    relative = 'probe'
    if kind == 'leaf': (workspace / relative).symlink_to(secret)
    if kind == 'ancestor':
        (workspace / relative).symlink_to(private, target_is_directory=True); relative += '/secret.txt'
    if kind == 'hard': os.link(secret, workspace / relative)
    if kind == 'fifo': os.mkfifo(workspace / relative)
    if kind == 'other-user':
        draft(app, admin, 'secret.txt'); relative = 'secret.txt'
    app.state.ai.dispatcher._run = AsyncMock()
    event = asyncio.run(app.state.ai.dispatcher.execute(context(session), publish(relative)))
    assert event['result']['error']['code'] == 'path_denied'
    app.state.ai.dispatcher._run.assert_not_awaited()


def test_generic_publication_commit_download_owner_idempotence_and_source_persistence(setup):
    app, admin, session, _ = setup
    dispatcher=app.state.ai.dispatcher; original=draft(app, session, '报告.txt'); ctx=context(session)
    async def twice():
        first=await dispatcher.execute(ctx, publish('报告.txt'))
        second=await dispatcher.execute(ctx, publish('报告.txt'))
        return first, second
    first, second=asyncio.run(twice())
    assert first['status'] == second['status'] == 'success'
    assert first['result'] == second['result']
    assert original.read_bytes() == b'scoped file fixture'
    info=first['result']; assert info['media_type'] == 'text/plain'
    hosted=[p for p in dispatcher.files.root.iterdir() if not p.name.startswith('.')]
    assert len(hosted)==1
    with TestClient(app) as client:
        assert client.get(info['download_path'], headers=headers(session)).status_code == 404
        committed(app, session, ctx, first)
        response=client.get(info['download_path'], headers=headers(session))
        assert response.status_code == 200 and response.content == original.read_bytes()
        assert response.headers['content-type'].startswith('text/plain')
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert client.get(info['download_path'], headers=headers(admin)).status_code == 404
        assert client.get(info['download_path']).status_code == 401
        assert client.get('/files/.work/' + original.parent.name + '/报告.txt', headers=headers(session)).status_code == 404
        # Legacy Excel conversation events remain accepted after migration.
        with dispatcher.files.chats.transaction() as state:
            state['conversations'][ctx.conversation_id]['messages'][0]['tool_events'][0]['command']='galaxy-cli excel create'
        assert client.get(info['download_path'], headers=headers(session)).status_code==200


@pytest.mark.parametrize('when', ['before', 'queued', 'after'])
def test_permission_changes_are_enforced_before_and_after_publication(setup, monkeypatch, when):
    app, _, session, _ = setup; dispatcher=app.state.ai.dispatcher
    draft(app, session)
    def revoke(): monkeypatch.setitem(ROLES, 'employee', [p for p in ROLES['employee'] if p != 'file:publish'])
    original=dispatcher._run
    if when=='before':
        revoke(); dispatcher._run=AsyncMock()
    if when=='after':
        async def run(*args, **kwargs):
            result=await original(*args, **kwargs); revoke(); return result
        dispatcher._run=run
    async def execute():
        if when!='queued': return await dispatcher.execute(context(session), publish('demo.txt'))
        dispatcher.slots=asyncio.Semaphore(0)
        task=asyncio.create_task(dispatcher.execute(context(session), publish('demo.txt')))
        await asyncio.sleep(0.01); revoke(); dispatcher.slots.release(); return await task
    event=asyncio.run(execute())
    assert event['result']['error']['code']=='permission_denied'
    assert not [p for p in dispatcher.files.root.iterdir() if not p.name.startswith('.')]
    if when=='before': dispatcher._run.assert_not_awaited()


def test_forged_receipt_cannot_adopt_another_users_file(setup):
    app, admin, session, _=setup; dispatcher=app.state.ai.dispatcher
    draft(app, admin); draft(app, session)
    valid=asyncio.run(dispatcher.execute(context(admin), publish('demo.txt')))
    assert valid['status']=='success'
    dispatcher._run=AsyncMock(return_value=valid['result'])
    attempted=asyncio.run(dispatcher.execute(context(session), publish('demo.txt')))
    assert attempted['result']['error']['code']=='invalid_publication'
    with dispatcher.files.repo.transaction(write=False) as state:
        assert len(state['files'])==1


def test_download_reopens_with_nofollow_and_streams_pinned_descriptor(tmp_path):
    inside=tmp_path/'inside.txt'; outside=tmp_path/'outside.txt'
    inside.write_bytes(b'allowed'); outside.write_bytes(b'private')
    response=scoped_download(inside, 'inside.txt', 'text/plain')
    inside.unlink(); inside.symlink_to(outside)
    async def read():
        data=b''.join([chunk async for chunk in response.body_iterator])
        await response.background(); return data
    assert asyncio.run(read())==b'allowed'
    from xingxu.auth import AuthFailure
    with pytest.raises(AuthFailure): scoped_download(inside, 'inside.txt', 'text/plain')


def test_session_revocation_after_copy_removes_unregistered_publication(setup, monkeypatch):
    app, _, session, _=setup; dispatcher=app.state.ai.dispatcher
    draft(app, session); original=dispatcher._run
    async def revoke(*args, **kwargs):
        result=await original(*args, **kwargs)
        app.state.auth.logout(session['access_token'])
        return result
    monkeypatch.setattr(dispatcher, '_run', revoke)
    event=asyncio.run(dispatcher.execute(context(session), publish('demo.txt')))
    assert event['result']['error']['code']=='session_expired'
    assert not [p for p in dispatcher.files.root.iterdir() if not p.name.startswith('.')]


def test_changed_source_after_copy_is_not_registered(setup, monkeypatch):
    app, _, session, _=setup; dispatcher=app.state.ai.dispatcher
    source=draft(app, session); original=dispatcher._run
    async def change(*args, **kwargs):
        result=await original(*args, **kwargs); source.write_bytes(b'changed'); return result
    monkeypatch.setattr(dispatcher, '_run', change)
    event=asyncio.run(dispatcher.execute(context(session), publish('demo.txt')))
    assert event['result']['error']['code']=='invalid_publication'
    assert not [p for p in dispatcher.files.root.iterdir() if not p.name.startswith('.')]


def test_concurrent_same_named_sources_keep_owners_and_payloads_separate(setup):
    app, admin, employee, _=setup; dispatcher=app.state.ai.dispatcher
    draft(app, admin, data=b'admin only'); draft(app, employee, data=b'employee only')
    async def both():
        return await asyncio.gather(*(dispatcher.execute(context(session), publish('demo.txt')) for session in (admin, employee)))
    events=asyncio.run(both())
    assert all(e['status']=='success' for e in events)
    assert len({e['result']['file_id'] for e in events})==2
    with dispatcher.files.repo.transaction(write=False) as state:
        for session, event, expected in zip((admin, employee), events, (b'admin only', b'employee only')):
            record=state['files'][event['result']['file_id']]
            assert record['user_id']==session['user']['id']
            assert (dispatcher.files.root/record['stored_name']).read_bytes()==expected
