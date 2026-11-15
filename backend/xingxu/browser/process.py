"""Private stdin/JSON-lines transport for fixed, server-selected browser adapters."""
import asyncio
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path


class OutputLimitExceeded(Exception):
    pass


@dataclass(frozen=True)
class VirtualDisplay:
    runtime: Path

    def prefix(self):
        wrapper = self.runtime / 'bin/xvfb-run'
        if not wrapper.is_file() or not (self.runtime / 'bin/Xvfb').is_file():
            wrapper = Path('/usr/bin/xvfb-run')
            if not wrapper.is_file() or not Path('/usr/bin/Xvfb').is_file():
                raise FileNotFoundError('Virtual display runtime unavailable')
        return (str(wrapper), '-a', '-s', '-screen 0 1280x900x24 -nolisten tcp')

    def environment(self):
        environment = {**os.environ, 'PATH': str(self.runtime / 'bin') + os.pathsep + os.environ.get('PATH', '/usr/bin'),
                       'XDG_SESSION_TYPE': 'x11', 'GDK_BACKEND': 'x11'}
        if (self.runtime / 'lib').is_dir():
            environment['LD_LIBRARY_PATH'] = str(self.runtime / 'lib')
        environment.pop('WAYLAND_DISPLAY', None)
        environment.pop('DISPLAY', None)
        return environment


async def run_json_process(argv, payload, *, cwd, environment, on_event, timeout=180,
                           output_limit=256 * 1024, line_limit=65536):
    # Internal API only. Public tool input cannot select argv, cwd or environment.
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, cwd=cwd, env=environment,
        start_new_session=True, limit=line_limit)
    try:
        async with asyncio.timeout(timeout):
            process.stdin.write(json.dumps(payload).encode())
            await process.stdin.drain()
            process.stdin.close()
            total = 0
            while line := await process.stdout.readline():
                total += len(line)
                if total > output_limit:
                    raise OutputLimitExceeded()
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(event, dict):
                    await on_event(event)
            return await process.wait()
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), 3)
            except (ProcessLookupError, TimeoutError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
