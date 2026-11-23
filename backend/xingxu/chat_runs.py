"""Owner-scoped cancellation of in-flight chat turns, including a stop-before-send race."""
import asyncio
import time
import copy
import uuid
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
    interactive_questions: bool = False
    reasoning_content: str = ''
    interaction: dict | None = None
    answer_future: asyncio.Future | None = field(default=None, repr=False)
    answered: dict = field(default_factory=dict, repr=False)

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
        return {'status': 'awaiting_input' if self.interaction else 'running', 'phase': self.phase, 'elapsed_ms': max(0, round((now - self.started_at) * 1000)),
            'phase_elapsed_ms': max(0, round((now - self.phase_started_at) * 1000)),
            'model_elapsed_ms': max(0, round(model_seconds * 1000)), 'model_calls': self.model_calls,
            'summary_calls': self.summary_calls, 'tools_completed': self.tools_completed, 'tool_name': self.tool_name,
            'reasoning_content': self.reasoning_content, 'interaction': copy.deepcopy(self.interaction)}


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

    async def ask(self, run, body):
        from galaxy_cli.tools.interaction.ask import normalize
        if run is None or self.active.get(run.key) is not run or run.stop_requested:
            raise AuthFailure('request_ended', '当前聊天请求已结束。', 409)
        if run.interaction is not None:
            raise AuthFailure('question_busy', '请先回答当前问题。', 409)
        question = {'id': str(uuid.uuid4()), **normalize(body)}
        future = asyncio.get_running_loop().create_future()
        run.interaction, run.answer_future = question, future
        run.transition('waiting')
        try:
            return await future
        finally:
            run.interaction, run.answer_future = None, None
            if not future.done():
                future.cancel()
            if not run.stop_requested:
                run.transition('tool')

    def answer(self, user, identifier, interaction_id, answers):
        run = self.active.get(self.key(user, identifier))
        if run is None or run.stop_requested:
            raise AuthFailure('not_found', '当前询问已结束，请重新发送问题。', 404)
        key = str(interaction_id)
        if key in run.answered:
            if run.answered[key] != answers:
                raise AuthFailure('answer_conflict', '此问题已经提交，不能重复修改答案。', 409)
            return {'accepted': True, 'interaction_id': key}
        question, future = run.interaction, run.answer_future
        if not question or question['id'] != key or future is None or future.done():
            raise AuthFailure('question_expired', '问题已变化，请使用最新的选择框。', 409)
        indexed = {item['question_id']: item for item in answers}
        if len(indexed) != len(answers) or set(indexed) != {q['id'] for q in question['questions']}:
            raise AuthFailure('invalid_input', '请回答全部问题，不能重复或增加问题。', 422)
        result = []
        for item in question['questions']:
            answer = indexed[item['id']]
            if ('option_id' in answer) == ('text' in answer):
                raise AuthFailure('invalid_input', '每题请点选一项或自行填写。', 422)
            row = {'question_id': item['id'], 'question': item['title']}
            if 'option_id' in answer:
                selected = next((o for o in item['options'] if o['id'] == answer['option_id']), None)
                if selected is None:
                    raise AuthFailure('invalid_input', '选项不属于当前问题。', 422)
                row.update(option_id=selected['id'], answer=selected['label'])
            else:
                value = answer['text'].strip()
                if not value or any(ord(c)<32 and c not in '\n\t' for c in value):
                    raise AuthFailure('invalid_input', '请填写有效答案。', 422)
                row['answer'] = value
            result.append(row)
        run.answered[key] = copy.deepcopy(answers)
        future.set_result({'interaction_id': key, 'answers': result})
        return {'accepted': True, 'interaction_id': key}

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
