import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch, AsyncMock
from xingxu.dsh_runtime import public_address, owner_key, DshTurn
from xingxu.tool_context import ExecutionContext


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_worker_cleanup_before_releasing_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            started, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            async def worker(reader, writer):
                try:
                    await reader.readline()
                    started.set()
                    await reader.read()
                    stopping.set()
                    await release.wait()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_unix_server(worker, str(root / 'worker.sock'))
            ai = SimpleNamespace(dispatcher=SimpleNamespace(directory=root, files=SimpleNamespace(root=root)),
                                 context_policy=SimpleNamespace(input_tokens=4096))
            turn = DshTurn(ai, ExecutionContext('u', 'w', (), 'c', 's'),
                {'base_url': 'https://api.deepseek.com', 'model': 'test'},
                run=None, progress={}, thinking=None, reasoning_effort=None)
            async def prepare(messages):return messages
            try:
                with patch('xingxu.dsh_runtime.SOCKET', str(root / 'worker.sock')), \
                        patch.object(turn, 'principal', return_value={'permissions': []}):
                    task = asyncio.create_task(turn.execute([{'role': 'user', 'content': 'cancel'}], prepare=prepare))
                    await asyncio.wait_for(started.wait(), 2)
                    task.cancel()
                    await asyncio.wait_for(stopping.wait(), 2)
                    await asyncio.sleep(.02)
                    self.assertFalse(task.done(), 'must not finish before the worker releases its resources')
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                self.assertFalse(turn.connections)
                self.assertFalse(list((root / '.dsh-brokers').iterdir()))
            finally:
                release.set()
                server.close()
                await server.wait_closed()

    async def test_stopping_turn_rejects_late_broker_calls(self):
        ai = SimpleNamespace(dispatcher=SimpleNamespace(_principal=Mock()))
        run = SimpleNamespace(stop_requested=True)
        turn = DshTurn(ai, ExecutionContext('u', 'w', (), 'c', 's'), {},
            run=run, progress={}, thinking=None, reasoning_effort=None)
        from xingxu.auth import AuthFailure
        with self.assertRaises(AuthFailure):
            turn.principal()
        run.stop_requested = False
        turn.closing = True
        with self.assertRaises(AuthFailure):
            turn.principal()
        ai.dispatcher._principal.assert_not_called()

    async def test_private_mapped_and_mixed_dns_are_rejected(self):
        loop = asyncio.get_running_loop()
        for address in ('127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '::ffff:8.8.8.8', '124.222.51.137'):
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
            with self.subTest(address=address), patch.object(loop, 'getaddrinfo', AsyncMock(return_value=records)):
                with self.assertRaises(ValueError):
                    await public_address('test.example', 443)
        with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 443))])):
            with self.assertRaises(ValueError):
                await public_address('test.example', 443)

    async def test_public_resolution_is_pinned_and_scope_includes_workspace(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])):
            self.assertEqual(await public_address('test.example', 443), ('8.8.8.8', socket.AF_INET))
        self.assertNotEqual(owner_key(ExecutionContext('u', 'w1', (), 'c', 's')),
                            owner_key(ExecutionContext('u', 'w2', (), 'c', 's')))

    async def test_native_gateway_cannot_read_provider_account_files_or_run_server_tools(self):
        ai = SimpleNamespace(cipher=Mock())
        turn = DshTurn(ai, ExecutionContext('u','w',(),'c','s'), {'base_url':'https://api.deepseek.com'},
            run=None, progress={}, thinking=None, reasoning_effort=None)
        with self.assertRaises(ValueError):
            await turn.native_model({'messages':[{'role':'user','content':[{'type':'document',
                'source':{'type':'file','file_id':'another-provider-account-file'}}]}]}, None)
        with self.assertRaises(ValueError):
            await turn.native_model({'messages':[{'role':'user','content':'hello'}], 'tools':[{
                'type':'deepseek_web_search','name':'web_search','input_schema':{}}]}, None)
        ai.cipher.decrypt.assert_not_called()
