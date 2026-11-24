"""Exercise supervisor cleanup with real child processes, without a live model."""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('dsh_worker_under_test',
    Path(__file__).resolve().parents[2] / 'deploy/dsh/worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class Writer:
    def __init__(self):
        self.values = []

    def write(self, raw):
        self.values.append(json.loads(raw))

    async def drain(self):
        pass


class WorkerCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.body = {'owner': 'a' * 64, 'conversation': str(uuid.uuid4()),
                     'run_id': str(uuid.uuid4()), 'token': 'b' * 64, 'input': {}}
        self.reader = asyncio.StreamReader()
        self.writer = Writer()
        self.processes = []
        self.patches = [patch.object(worker, 'JOBS', self.root / 'jobs'),
                        patch.object(worker, 'BROKERS', self.root / 'brokers'),
                        patch.object(worker, 'scope_root', return_value=self.root / 'storage')]
        for item in self.patches:
            item.start()
        worker.JOBS.mkdir()
        broker = worker.BROKERS / self.body['run_id']
        broker.mkdir(parents=True)
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(str(broker / 'broker.sock'))

    async def asyncTearDown(self):
        for process in self.processes:
            if process.returncode is None:
                process.kill()
            await process.communicate()
        self.socket.close()
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def spawn(self, script, *, limit=1024 * 1024):
        original = asyncio.create_subprocess_exec

        async def start(*_args, **kwargs):
            kwargs['limit'] = limit
            process = await original(sys.executable, '-u', '-c', script, **kwargs)
            self.processes.append(process)
            return process
        return patch.object(worker.asyncio, 'create_subprocess_exec', side_effect=start)

    async def wait_until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.005)

    async def test_disconnect_reaps_child_removes_stage_and_allows_next_job(self):
        with self.spawn('import sys,time;sys.stdin.read();time.sleep(60)'):
            task = asyncio.create_task(worker.run_job(self.body, self.reader, self.writer))
            await self.wait_until(lambda: bool(self.processes))
            self.reader.feed_eof()
            await asyncio.wait_for(task, 3)
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertFalse(worker.active)
        self.assertFalse((worker.JOBS / self.body['run_id']).exists())
        self.assertEqual([v['type'] for v in self.writer.values], ['ready'])
        self.reader = asyncio.StreamReader()
        with self.spawn('import sys;sys.stdin.read();print(\'{"type":"result","content":"next job"}\')'):
            await asyncio.wait_for(worker.run_job(self.body, self.reader, self.writer), 3)
        self.assertEqual(self.writer.values[-1]['content'], 'next job')
        self.assertFalse(worker.active)

    async def test_output_backpressure_does_not_hang_reaping(self):
        # Force a paused pipe transport and a collector error. wait() without
        # draining used to retain active forever even though SIGKILL was sent.
        with self.spawn('import sys,time;sys.stdin.read();sys.stdout.write("x"*1048576);sys.stdout.flush();time.sleep(60)', limit=4096):
            with self.assertRaises(ValueError):
                await asyncio.wait_for(worker.run_job(self.body, self.reader, self.writer), 3)
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertFalse(worker.active)
        self.assertFalse((worker.JOBS / self.body['run_id']).exists())

    async def test_scope_failure_does_not_leave_worker_busy(self):
        with patch.object(worker, 'scope_root', side_effect=ValueError('scope unavailable')):
            with self.assertRaises(ValueError):
                await worker.run_job(self.body, self.reader, self.writer)
        self.assertFalse(worker.active)
