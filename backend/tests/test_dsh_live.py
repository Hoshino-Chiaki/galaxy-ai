"""Real SDK and Linux jail, synthetic accounts/model, no production chats/business writes."""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from xingxu.ai import AiService, ChatInput, ProviderInput
from xingxu.auth import AuthFailure
from xingxu.dsh_runtime import DshTurn, owner_key, worker_request
from xingxu.tool_context import ExecutionContext


@unittest.skipUnless(Path('/run/galaxy-dsh/runner.sock').exists(), 'Requires installed isolated DSH worker')
class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='galaxy-dsh-test-')
        self.patch = patch.dict(os.environ, {'GALAXY_FILES_DIR': self.folder.name + '/files',
            'GALAXY_DSH_BROKER_DIR': '/opt/galaxy-ai/hosted-files/.dsh-brokers',
            'GALAXY_DSH_STORAGE': '/opt/galaxy-ai/hosted-files/.dsh'})
        self.patch.start()
        self.user = {'id': 'dsh-test-' + uuid.uuid4().hex, 'workspace_id': 'dsh-test-workspace',
            'permissions': ['ai:chat', 'tools:galaxy_cli', 'galaxy:help', 'workspace:read', 'file:publish', 'task:upload', 'task:har:read']}
        self.alive = True
        def authorize(_token):
            if not self.alive:
                raise AuthFailure('session_expired', 'expired')
            return {'user': self.user}
        self.ai = AiService(Path(self.folder.name) / 'private', authorize=authorize)
        self.context = ExecutionContext(self.user['id'], self.user['workspace_id'], tuple(self.user['permissions']), str(uuid.uuid4()), 'synthetic')
        self.ai.dispatcher.executable = Path(__file__).resolve().parents[2] / 'galaxy-cli/.venv/bin/galaxy-cli'
        self.provider = {'model': 'deepseek-v4-flash', 'max_tokens': 4096}
        self.progress = {}

    async def asyncTearDown(self):
        for _ in range(30):
            status = await worker_request({'command': 'status'})
            if not status.get('active'):
                break
            await asyncio.sleep(.1)
        root = Path('/opt/galaxy-ai/hosted-files/.dsh') / owner_key(self.context)
        if root.exists():
            assert root.parent == Path('/opt/galaxy-ai/hosted-files/.dsh') and not root.is_symlink()
            shutil.rmtree(root)
        self.patch.stop()
        self.folder.cleanup()

    def turn(self):
        return DshTurn(self.ai, self.context, self.provider, run=None, progress=self.progress, thinking=None, reasoning_effort=None)

    async def run_command(self, command, *, checkpoint=None):
        calls = []
        async def model(_provider, wire, **kwargs):
            calls.append(wire)
            if len(calls) == 1:
                names = [t['function']['name'] for t in kwargs.get('tools', [])]
                self.assertIn('bash', names)
                response = {'role': 'assistant', 'content': None, 'reasoning_content': '测试', 'tool_calls': [{
                    'id': 'cmd-' + uuid.uuid4().hex, 'type': 'function', 'function': {'name': 'bash', 'arguments': json.dumps({'command': command})}}]}
            else:
                response = {'role': 'assistant', 'content': '验证完成', 'reasoning_content': ''}
            return response, {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}, ''
        async def prepare(wire):
            return [{'role': 'user', 'content': '迁移记忆：测试暗号 legacy-note-42'},
                    {'role': 'assistant', 'content': '已记录该历史暗号。'}, *wire]
        with patch.object(self.ai, '_model_message', model):
            result = await self.turn().execute([{'role': 'user', 'content': '执行验证'}], prepare=prepare, checkpoint=checkpoint)
        return result, calls

    async def test_native_python_cli_upload_and_resumed_context(self):
        result, calls = await self.run_command("python -c \"from pathlib import Path;Path('proof.txt').write_text('own-value');print(6*7)\"; galaxy-cli time now --input '{}' --format json; galaxy-cli task request-upload --input '{\"purpose\":\"用于本次合成测试解析接口\",\"accepted_extensions\":[\"har\"]}' --format json; galaxy-cli file publish --input '{\"path\":\"proof.txt\"}' --format json")
        self.assertEqual(result[0], '验证完成')
        self.assertEqual(len(result[3]), 3)
        self.assertTrue(all(e['status'] == 'success' for e in result[3]), result[3])
        checkpoint = result[-1]
        resumed, wire = await self.run_command("python -c \"print(open('proof.txt').read())\"", checkpoint=checkpoint)
        self.assertIn('own-value', json.dumps(wire[-1], ensure_ascii=False))
        self.assertIn('执行验证', json.dumps(wire[0], ensure_ascii=False))
        self.assertIn('legacy-note-42', json.dumps(wire[0], ensure_ascii=False))
        # Knowing another account's checkpoint id cannot select that account.
        original = self.context
        self.user['id'] = 'different-' + uuid.uuid4().hex
        self.context = ExecutionContext(self.user['id'], self.user['workspace_id'], tuple(self.user['permissions']), original.conversation_id, 'synthetic')
        try:
            with self.assertRaises(AuthFailure):
                await self.run_command('true', checkpoint=checkpoint)
        finally:
            self.user['id'] = original.user_id
            self.context = original

    async def test_host_accounts_admin_files_and_private_network_not_visible(self):
        marker = Path(self.folder.name) / 'admin-canary.txt'
        marker.write_text('ADMIN-CANARY-DO-NOT-EXPOSE')
        script = "import os,urllib.request; paths=" + repr([str(marker), '/opt/galaxy-ai/backend/private/ai-secret.key', '/proc/1/root'+str(marker), '/run/galaxy-dsh/runner.sock', '/opt/galaxy-ai/hosted-files/.work']) + ";print({p:os.path.exists(p) for p in paths});print('key',os.environ.get('OPENAI_API_KEY'));\ntry: urllib.request.urlopen('https://127.0.0.1:18443',timeout=2)\nexcept Exception: print('local-blocked')\ntry: urllib.request.urlopen('https://169.254.169.254',timeout=2)\nexcept Exception: print('metadata-blocked')"
        import shlex
        result, wire = await self.run_command('python -c ' + shlex.quote(script))
        text = json.dumps(wire[-1], ensure_ascii=False)
        self.assertNotIn('ADMIN-CANARY-DO-NOT-EXPOSE', text)
        self.assertIn('metadata-blocked', text)
        self.assertIn('local-blocked', text)
        self.assertIn('False', text)
        self.assertNotIn('True', text)

    async def test_cli_authorization_is_rechecked(self):
        turn = self.turn()
        self.alive = False
        with self.assertRaises(AuthFailure):
            turn.principal()
        self.alive = True
        event = await turn.cli({'args': ['youzan', 'customer-search', '--input', '{}']})
        self.assertEqual(event['error']['code'], 'permission_denied')

    async def test_cancel_reaps_job_without_committing(self):
        async def model(_provider, _wire, **_kwargs):
            await asyncio.sleep(100)
        async def prepare(wire):return wire
        turn = self.turn()
        with patch.object(self.ai, '_model_message', model):
            task = asyncio.create_task(turn.execute([{'role': 'user', 'content': 'cancel'}], prepare=prepare))
            for _ in range(100):
                if turn.model_calls or task.done():
                    break
                await asyncio.sleep(.1)
            if task.done():
                await task
            self.assertGreater(turn.model_calls, 0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        # Cancellation must await supervisor cleanup, rather than relying on
        # eventual cleanup after the frontend has already reported completion.
        self.assertEqual((await worker_request({'command': 'status'}))['active'], 0)
        self.assertFalse((Path('/opt/galaxy-ai/hosted-files/.dsh') / owner_key(self.context) / self.context.conversation_id / turn.identifier).exists())

    async def test_cancel_running_shell_releases_worker_immediately(self):
        count = 0
        async def model(_provider, _wire, **_kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'long-shell', 'type': 'function', 'function': {'name': 'bash',
                    'arguments': json.dumps({'command': 'python -c "from pathlib import Path;import time;Path(\'started.txt\').write_text(\'running\');time.sleep(60)"'})}}]}, {}, ''
            raise AssertionError('model must not be called again after cancellation')
        turn = self.turn()
        async def prepare(wire):return wire
        with patch.object(self.ai, '_model_message', model):
            task = asyncio.create_task(turn.execute([{'role': 'user', 'content': 'long shell'}], prepare=prepare))
            async with asyncio.timeout(20):
                while True:
                    if task.done():await task
                    result = await worker_request({'command': 'read', 'run_id': turn.identifier, 'path': 'started.txt'})
                    if 'data' in result:break
                    await asyncio.sleep(.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertEqual((await worker_request({'command': 'status'}))['active'], 0)
        result, _wire = await self.run_command('python -c "print(6*7)"')
        self.assertEqual(result[0], '验证完成')

    async def test_stop_during_cli_reaps_process_and_releases_channel_lock(self):
        self.ai.runtime_name = 'dsh'
        self.ai.snapshot = lambda: ({**self.provider, 'name': 'Synthetic DSH'}, '测试打断。')
        marker = Path(self.folder.name) / 'cli-started'
        executable = Path(self.folder.name) / 'sleeping-cli'
        executable.write_text('#!/usr/bin/python3\nimport pathlib,time\npathlib.Path(' + repr(str(marker)) + ').touch()\ntime.sleep(60)\n')
        executable.chmod(0o700)
        self.ai.dispatcher.executable = executable
        lock = asyncio.Lock()
        original = self.ai.dispatcher._run
        interrupted = asyncio.Event()
        async def locked(*args, **kwargs):
            async with lock:
                try:return await original(*args, **kwargs)
                finally:interrupted.set()
        async def model(*_args, **_kwargs):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'sleeping-cli', 'type': 'function', 'function': {'name': 'bash',
                'arguments': json.dumps({'command': 'galaxy-cli help --format json'})}}]}, {}, ''
        request_id = uuid.uuid4()
        with patch.object(self.ai, '_model_message', model), patch.object(self.ai.dispatcher, '_run', locked):
            task = asyncio.create_task(self.ai.send(self.user, ChatInput(message='stop CLI', request_id=request_id), session_token='synthetic'))
            async with asyncio.timeout(20):
                while not marker.exists():
                    if task.done():await task
                    await asyncio.sleep(.05)
            await asyncio.wait_for(self.ai.cancel(self.user, request_id), 5)
            result = await task
        self.assertTrue(result['interrupted'])
        self.assertTrue(interrupted.is_set())
        self.assertFalse(lock.locked())
        self.assertFalse(self.ai.running)
        self.assertFalse(self.ai.runs.active)
        self.assertEqual(result['conversation']['messages'][-1]['tool_events'][-1]['status'], 'cancelled')
        self.assertEqual((await worker_request({'command': 'status'}))['active'], 0)

    async def test_chat_commit_upload_materialization_and_har_continuation(self):
        from test_uploads import har_bytes
        self.ai.runtime_name = 'dsh'
        self.ai.snapshot = lambda: ({**self.provider, 'name': 'Synthetic DSH'}, '使用中文回答。')
        count, upload_id = 0, None
        seen = []
        async def model(_provider, wire, **kwargs):
            nonlocal count
            seen.append(wire)
            self.assertIs(kwargs.get('thinking'), False)
            if count in (0, 2):
                if count == 0:
                    command = "galaxy-cli task request-upload --input '{\"purpose\":\"上传合成HAR以验证Python与接口解析衔接\",\"accepted_extensions\":[\"har\"]}' --format json"
                else:
                    command = f"galaxy-files get {upload_id}; galaxy-cli task har --input '{{\"file_id\":\"{upload_id}\"}}' --format json"
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': uuid.uuid4().hex, 'type': 'function',
                    'function': {'name': 'bash', 'arguments': json.dumps({'command': command})}}]}
            else:
                message = {'role': 'assistant', 'content': '请上传资料。' if count == 1 else '资料验证完成。'}
            count += 1
            return message, {}, ''
        with patch.object(self.ai, '_model_message', model), patch.object(self.ai.agent, 'run', side_effect=AssertionError('LangGraph must not run')):
            first = await self.ai.send(self.user, ChatInput(message='创建测试上传入口', thinking=False), session_token='synthetic')
            chat = first['conversation']
            identifier = chat['id']
            self.context = ExecutionContext(self.user['id'], self.user['workspace_id'], tuple(self.user['permissions']), identifier, 'synthetic')
            upload_id = chat['messages'][-1]['tool_events'][0]['result']['upload_request_id']
            self.assertNotIn('dsh_state', chat)
            private = self.ai.conversation(self.user, identifier, include_context=True)
            self.assertEqual(private['dsh_state']['through_message_id'], private['messages'][-1]['id'])
            self.assertNotIn('context_state', private)
            data = har_bytes()
            async def chunks():yield data
            receipt = await self.ai.dispatcher.uploads.receive(self.user, upload_id, 'fixture.har', len(data), chunks(), lambda:self.user)
            second = await self.ai.send(self.user, ChatInput(conversation_id=identifier, message='文件已上传，请继续', upload_id=upload_id, thinking=False), session_token='synthetic')
        event = second['conversation']['messages'][-1]['tool_events'][0]
        self.assertEqual(event['status'], 'success', event)
        self.assertEqual(event['result']['source']['sha256'], receipt['sha256'])
        self.assertIn('fixture.har', json.dumps(seen[-1], ensure_ascii=False))
        self.assertIn(receipt['purpose'], json.dumps(seen[2], ensure_ascii=False))
        other = ExecutionContext(self.user['id'], self.user['workspace_id'], tuple(self.user['permissions']), str(uuid.uuid4()), 'synthetic')
        turn = DshTurn(self.ai, other, self.provider, run=None, progress={}, thinking=None, reasoning_effort=None)
        with self.assertRaises(AuthFailure):
            await turn.materialize({'file_id': upload_id})
        result = self.ai.delete_conversation(self.user, identifier)
        self.assertTrue(result['ok'])
        self.assertFalse((Path('/opt/galaxy-ai/hosted-files/.dsh') / owner_key(self.context) / identifier).exists())

    async def test_disk_bound_and_symlink_export_cannot_read_host(self):
        import shlex
        canary = Path('/opt/galaxy-ai/hosted-files') / ('.dsh-isolation-test-' + uuid.uuid4().hex)
        canary.write_text('HOST-CANARY-SECRET')
        canary.chmod(0o600)
        try:
            script = "from pathlib import Path\nimport os\nfiles=[]\ntry:\n for n in range(20):\n  p=Path('quota-'+str(n));files.append(p)\n  with p.open('wb') as f:\n   for _ in range(20): f.write(b'x'*1048576)\nexcept OSError as e: print('disk-limit',e.errno)\nfinally:\n for p in files:p.unlink(missing_ok=True)\n"
            command = 'python -c ' + shlex.quote(script) + '; ln -s ' + shlex.quote(str(canary)) + " leak.txt; galaxy-cli file publish --input '{\"path\":\"leak.txt\"}' --format json"
            result, wire = await self.run_command(command)
            self.assertIn('disk-limit 28', json.dumps(wire[-1], ensure_ascii=False))
            self.assertNotIn('HOST-CANARY-SECRET', json.dumps(wire, ensure_ascii=False))
            self.assertEqual(result[3][0]['status'], 'denied')
        finally:
            canary.unlink(missing_ok=True)

    async def test_native_protocol_keeps_provider_key_outside_jail(self):
        import httpx
        from xingxu.dsh_protocol import messages_request, messages_response
        self.ai.save_provider(ProviderInput(name='Native synthetic', base_url='https://api.deepseek.com',
            model='deepseek-flash', api_key='native-provider-test-only', max_tokens=4096))
        provider, _ = self.ai.snapshot()
        calls = []
        def upstream(request):
            self.assertEqual(str(request.url), 'https://api.deepseek.com/anthropic/v1/messages')
            self.assertEqual(request.headers['x-api-key'], 'native-provider-test-only')
            body = json.loads(request.content)
            self.assertEqual(body['thinking']['type'], 'disabled')
            self.assertNotIn('output_config', body)
            self.assertNotIn('dsh_session_log', body)
            wire, _ = messages_request(body)
            calls.append(wire)
            if len(calls) == 1:
                response = {'content': None, 'tool_calls': [{'id': 'native-command', 'type': 'function', 'function': {
                    'name': 'bash', 'arguments': json.dumps({'command': "python -c \"import os;print(os.environ.get('DEEPSEEK_API_KEY'));print(20+22)\""})}}]}
            else:
                self.assertNotIn('native-provider-test-only', json.dumps(wire))
                response = {'content': '原生协议验证完成'}
            kind, data = messages_response(response, {'prompt_tokens': 123, 'completion_tokens': 15}, provider['model'])
            return httpx.Response(200, content=data, headers={'Content-Type': kind})
        self.ai.transport = httpx.MockTransport(upstream)
        turn = DshTurn(self.ai, self.context, provider, run=None, progress={}, thinking=False, reasoning_effort=None)
        async def prepare(wire):return wire
        with patch.object(self.ai, '_model_message', side_effect=AssertionError('Native route must not call Chat Completions')):
            result = await turn.execute([{'role': 'user', 'content': '原生测试'}], prepare=prepare)
        self.assertEqual(result[0], '原生协议验证完成')
        self.assertEqual(result[1]['total_tokens'], 276)
        self.assertIn('None\n42', calls[-1][-1]['content'])
