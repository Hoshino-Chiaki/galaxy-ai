"""Shared DSH admission, with cancellable FIFO waiting and visible queue status."""
from contextlib import asynccontextmanager
from .auth import AuthFailure

CONCURRENCY = 3
MAX_INFLIGHT = 8


def refresh(ai):
    for position, (_, run) in enumerate(ai.dsh_waiters, 1):
        if run is not None:
            run.queue_position = position
            run.tool_name = f'排队等待空闲沙盒（第 {position} 位）'
            run.transition('queued')


@asynccontextmanager
async def slot(ai, run):
    if ai.dsh_turns >= MAX_INFLIGHT:
        raise AuthFailure('dsh_queue_full', '当前等待的会话较多，请稍后重试。', 429)
    ai.dsh_turns += 1
    entry = (object(), run)
    acquired = False
    try:
        if ai.dsh_slots.locked():
            ai.dsh_waiters.append(entry)
            refresh(ai)
        await ai.dsh_slots.acquire()
        acquired = True
        if entry in ai.dsh_waiters:
            ai.dsh_waiters.remove(entry)
            refresh(ai)
        if run is not None:
            run.queue_position = None
            run.tool_name = None
            run.transition('preparing')
        yield
    finally:
        if entry in ai.dsh_waiters:
            ai.dsh_waiters.remove(entry)
            refresh(ai)
        if run is not None:run.queue_position = None
        ai.dsh_turns -= 1
        if acquired:ai.dsh_slots.release()
