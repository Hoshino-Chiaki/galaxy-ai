"""Owner-scoped cancellation of in-flight chat turns, including a stop-before-send race."""
import asyncio
import time
from dataclasses import dataclass, field
from .auth import AuthFailure


@dataclass
class ChatRun:
    key: tuple
    task: asyncio.Task
    stop_requested: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float = field(default_factory=time.monotonic)
    phase_started_at: float = field(default_factory=time.monotonic)
    phase: str = 'preparing'
    phase_times: dict = field(default_factory=dict)
    model_calls: int = 0
    summary_calls: int = 0
    tools_completed: int = 0
    tool_name: str | None = None

    def transition(self, phase):
        if phase == self.phase:
            return
        now = time.monotonic()
        self.phase_times[self.phase] = self.phase_times.get(self.phase, 0) + now - self.phase_started_at
        self.phase, self.phase_started_at = phase, now

    def snapshot(self):
        now = time.monotonic()
        model_seconds = self.phase_times.get('model', 0)
        if self.phase == 'model':
            model_seconds += now - self.phase_started_at
        return {'status': 'running', 'phase': self.phase, 'elapsed_ms': max(0, round((now - self.started_at) * 1000)),
            'phase_elapsed_ms': max(0, round((now - self.phase_started_at) * 1000)),
            'model_elapsed_ms': max(0, round(model_seconds * 1000)), 'model_calls': self.model_calls,
            'summary_calls': self.summary_calls, 'tools_completed': self.tools_completed, 'tool_name': self.tool_name}


class ChatRuns:
    def __init__(self):
        self.active = {}
        self.pending_stops = {}

    @staticmethod
    def key(user, identifier):
        return (user['workspace_id'], user['id'], str(identifier))

    def _prune(self):
        now = time.monotonic()
        self.pending_stops = {key: expiry for key, expiry in self.pending_stops.items() if expiry > now}

    def begin(self, user, identifier):
        self._prune()
        key = self.key(user, identifier)
        if key in self.active:
            raise AuthFailure('request_busy', '此消息正在处理，请勿重复发送。', 409)
        run = ChatRun(key, asyncio.current_task(), key in self.pending_stops)
        self.pending_stops.pop(key, None)
        self.active[key] = run
        return run

    def status(self, user, identifier):
        run = self.active.get(self.key(user, identifier))
        if run is None:
            raise AuthFailure('not_found', '请求不存在或已结束。', 404)
        return run.snapshot()

    async def stop(self, user, identifier):
        self._prune()
        key = self.key(user, identifier)
        run = self.active.get(key)
        if run is None:
            # A separate cancel HTTP request can reach the server before send does.
            self.pending_stops[key] = time.monotonic() + 60
        else:
            if not run.stop_requested:
                run.stop_requested = True
                run.transition('stopping')
                run.task.cancel()
            await run.finished.wait()
        return {'requested': True}

    def finish(self, run):
        if self.active.get(run.key) is run:
            self.active.pop(run.key)
        run.finished.set()
