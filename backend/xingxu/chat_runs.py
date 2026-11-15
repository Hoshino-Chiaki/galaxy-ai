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
                run.task.cancel()
            await run.finished.wait()
        return {'requested': True}

    def finish(self, run):
        if self.active.get(run.key) is run:
            self.active.pop(run.key)
        run.finished.set()
