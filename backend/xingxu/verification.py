"""Channel-neutral, human-entered verification; secrets never enter tool results."""
import asyncio
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field

from .auth import AuthFailure


@dataclass
class Challenge:
    owner: tuple
    channel: str
    purpose: str
    destination: str
    submitter: object = field(repr=False)
    expires: float
    deadline: float
    validator: object = field(default=lambda: None, repr=False)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: str = 'sent'
    code: str | None = field(default=None, repr=False)
    reference: str | None = field(default=None, repr=False)
    future: object = field(default=None, repr=False)
    run_key: tuple | None = None


class Verifications:
    def __init__(self, *, clock=time.time, monotonic=time.monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.entries = {}

    def issue(self, context, *, channel, purpose, destination, submitter, validate=lambda: None):
        self.prune()
        entry = Challenge((context.workspace_id, context.user_id, context.conversation_id),
            channel, purpose, destination, submitter, self.clock()+120, self.monotonic()+120, validator=validate)
        self.entries[entry.id] = entry
        return self.required(entry)

    def get(self, context, identifier):
        entry = self.entries.get(identifier)
        if entry is None or entry.owner != (context.workspace_id, context.user_id, context.conversation_id):
            raise AuthFailure('verification_not_found', '验证已结束或不属于当前账号与对话。', 404)
        if self.monotonic() >= entry.deadline and entry.state=='sent':
            self.clear(entry, 'expired')
            raise AuthFailure('verification_expired', '验证码等待已超过两分钟，请重新发起验证。', 409)
        try:entry.validator()
        except BaseException:
            self.clear(entry,'failed');raise
        return entry

    def required(self, entry):
        return {'status': 'verification_required', 'challenge_id': entry.id, 'channel': entry.channel,
                'purpose': entry.purpose, 'destination': entry.destination,
                'expires_at_ms': int(entry.expires*1000), 'next_tool': 'interaction.verification'}

    def clear(self, entry, state):
        entry.state, entry.code, entry.reference, entry.submitter = state, None, None, None
        entry.validator = lambda: None
        if entry.future is not None and not entry.future.done():
            entry.future.set_result({'status': state, 'challenge_id': entry.id})

    def prune(self):
        for key, entry in list(self.entries.items()):
            if self.monotonic() >= entry.deadline and entry.state not in ('received','checking'):
                self.clear(entry, 'expired'); self.entries.pop(key, None)

    async def wait(self, run, entry):
        if entry.state == 'received':
            return {'status': 'received', 'challenge_id': entry.id, 'submission_id': entry.reference,
                    'next_tool': 'interaction.verification-submit'}
        if entry.state != 'sent' or run.interaction is not None:
            raise AuthFailure('verification_busy', '当前验证已提交、已结束或另有询问等待处理。', 409)
        if run.key[:2] != entry.owner[:2]:
            raise AuthFailure('verification_not_found', '验证不属于当前账号。', 404)
        entry.run_key = run.key
        entry.future = asyncio.get_running_loop().create_future()
        run.interaction = {'kind': 'verification', 'id': entry.id, 'channel': entry.channel,
            'purpose': entry.purpose, 'destination': entry.destination,
            'expires_at_ms': int(entry.expires*1000), 'server_now_ms': int(self.clock()*1000)}
        run.transition('waiting')
        try:
            return await asyncio.wait_for(asyncio.shield(entry.future), max(0, entry.deadline-self.monotonic()))
        except asyncio.TimeoutError:
            self.clear(entry, 'expired')
            return {'status': 'expired', 'challenge_id': entry.id, 'message': '两分钟内未完成输入，本次等待已结束；已创建的导出任务保留。'}
        except asyncio.CancelledError:
            self.clear(entry, 'cancelled'); raise
        finally:
            entry.future = None; run.interaction = None
            if not run.stop_requested: run.transition('tool')

    def answer(self, run, user, identifier, *, code=None, cancel=False):
        entry = self.entries.get(identifier)
        if (entry is None or entry.owner[:2] != (user['workspace_id'],user['id'])
                or entry.run_key != run.key or not run.interaction or run.interaction.get('id') != identifier):
            raise AuthFailure('verification_not_found', '验证已结束或不属于当前账号。', 404)
        if self.monotonic() >= entry.deadline:
            self.clear(entry, 'expired')
            raise AuthFailure('verification_expired', '验证码输入已超时。', 409)
        if cancel:
            self.clear(entry, 'cancelled'); return {'accepted': True}
        if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9]{4,16}',code):
            raise AuthFailure('invalid_input','请填写有效验证码。',422)
        if entry.state != 'sent' or entry.future is None or entry.future.done():
            raise AuthFailure('verification_submitted', '验证码已提交，请等待验证结果。', 409)
        entry.code, entry.reference, entry.state = code, secrets.token_urlsafe(24), 'received'
        entry.future.set_result({'status':'received','challenge_id':entry.id,'submission_id':entry.reference,
                                'next_tool':'interaction.verification-submit'})
        return {'accepted': True}

    async def submit(self, context, identifier, reference, validate):
        entry = self.get(context,identifier)
        if entry.state != 'received' or not entry.reference or not secrets.compare_digest(entry.reference,reference):
            raise AuthFailure('verification_submitted','提交引用无效或已使用。',409)
        validate()
        code, callback = entry.code, entry.submitter
        entry.code, entry.reference, entry.state = None, None, 'checking'
        try:
            result = await callback(code, validate)
            validate()
        except AuthFailure as error:
            if error.code == 'verification_invalid' and self.monotonic() < entry.deadline:
                entry.state = 'sent'
                return {**self.required(entry), 'message': '验证码未通过，请在剩余时间内重新输入。'}
            self.clear(entry,'failed'); raise
        except BaseException:
            self.clear(entry,'failed'); raise
        finally:
            code = None
        self.clear(entry,'verified')
        return result

    def cancel_run(self, run):
        for entry in self.entries.values():
            if entry.run_key == run.key and entry.state not in ('verified','failed','expired','cancelled'):
                self.clear(entry,'cancelled')
