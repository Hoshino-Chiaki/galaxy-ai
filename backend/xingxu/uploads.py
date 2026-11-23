"""Purpose-bound, owner-scoped upload invitations and bounded streaming storage."""
import asyncio
import copy
import hashlib
import os
import time
import uuid
from starlette.requests import ClientDisconnect

from galaxy_cli.file_scope import root_directory, child_directory, source_file
from .account_storage import scoped
from galaxy_cli.registry import ToolError
from galaxy_cli.tools.task.request_upload import normalize
from .auth import AuthFailure
from .store import JsonRepository

MAX_UPLOAD = 256 * 1024 * 1024
USER_QUOTA = 512 * 1024 * 1024
INVITATION_SECONDS = 30 * 60
RETENTION_SECONDS = 24 * 60 * 60


class UploadService:
    def __init__(self, directory, files):
        self.repo = JsonRepository(directory, 'uploads', ('requests',))
        self.files = files
        self.slots = asyncio.Semaphore(2)
        self.storage = None

    @staticmethod
    def _permission(user):
        if not {'ai:chat', 'tools:galaxy_cli', 'task:upload'} <= set(user.get('permissions', [])):
            raise AuthFailure('forbidden', '当前账号没有文件上传权限。', 403)

    @staticmethod
    def _owner(item, user):
        return item and item['user_id'] == user['id'] and item['workspace_id'] == user['workspace_id']

    def _get(self, user, identifier, *, committed=True):
        self._permission(user)
        with self.repo.transaction(write=False) as state:
            item = copy.deepcopy(state['requests'].get(str(identifier)))
        if not self._owner(item, user):
            raise AuthFailure('not_found', '上传入口不存在。', 404)
        if committed:
            with scoped(self.files.chats, user, write=False, identifier=item['conversation_id']) as state:
                chat = state['conversations'].get(item['conversation_id'])
                if (not self._owner(chat, user) or not any(
                    e.get('command') == 'galaxy-cli task request-upload' and e.get('status') == 'success'
                    and e.get('result', {}).get('upload_request_id') == item['upload_request_id']
                    for m in chat.get('messages', []) for e in m.get('tool_events', []))):
                    raise AuthFailure('not_found', '上传入口不属于已保存的对话。', 404)
        return item

    @staticmethod
    def _view(item):
        keys = ('upload_request_id', 'purpose', 'accepted_extensions', 'max_size_bytes', 'expires_at',
                'retention_hours', 'status', 'conversation_id', 'filename', 'size', 'sha256', 'file_id', 'path')
        result = {k: item[k] for k in keys if k in item}
        if item['expires_at'] <= int(time.time()) and item['status'] in ('pending', 'receiving', 'ready'):
            result['status'] = 'expired'
        return result

    def create(self, user, conversation_id, arguments):
        self._permission(user)
        arguments = normalize(arguments)
        self.cleanup()
        now = int(time.time())
        with self.repo.transaction() as state:
            pending = [r for r in state['requests'].values() if self._owner(r, user)
                       and r['status'] == 'pending' and r['expires_at'] > now]
            for item in pending:
                if item['conversation_id'] == str(conversation_id) and item['purpose'] == arguments['purpose']:
                    return {k: item[k] for k in ('upload_request_id', 'purpose', 'accepted_extensions', 'max_size_bytes', 'expires_at', 'retention_hours', 'status')}
            if len(pending) >= 3:
                raise AuthFailure('upload_limit', '已有 3 个待上传入口，请先使用或取消。', 409)
            identifier = str(uuid.uuid4())
            item = {'upload_request_id': identifier, 'user_id': user['id'], 'workspace_id': user['workspace_id'],
                    'conversation_id': str(conversation_id), 'purpose': arguments['purpose'],
                    'accepted_extensions': arguments.get('accepted_extensions', []),
                    'max_size_bytes': arguments.get('max_size_mb', 128) * 1024 * 1024,
                    'expires_at': now + INVITATION_SECONDS, 'retention_hours': 24, 'created_at': now, 'status': 'pending'}
            state['requests'][identifier] = item
            return {k: item[k] for k in ('upload_request_id', 'purpose', 'accepted_extensions', 'max_size_bytes', 'expires_at', 'retention_hours', 'status')}

    def metadata(self, user, identifier):
        item = self._get(user, identifier)
        return self._view(item)

    async def receive(self, user, identifier, filename, size, chunks, reauthorize):
        identifier = str(identifier)
        item = self._get(user, identifier)
        if (not isinstance(filename, str) or not filename.strip() or len(filename.encode('utf-8')) > 255
                or any(c in filename for c in '/\\') or any(ord(c) < 32 for c in filename)):
            raise AuthFailure('invalid_filename', '文件名无效。', 422)
        extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if extension and item['accepted_extensions'] and extension not in item['accepted_extensions']:
            raise AuthFailure('file_type', '文件类型不符合当前上传要求。', 415)
        if not 0 < size <= min(MAX_UPLOAD, item['max_size_bytes']):
            raise AuthFailure('file_size', '文件大小超出当前上传要求。', 413)
        if self.slots.locked():
            raise AuthFailure('upload_busy', '当前上传较多，请稍后重试。', 429)
        async with self.slots:
            now = int(time.time())
            nonce = str(uuid.uuid4())
            with self.repo.transaction() as state:
                current = state['requests'].get(identifier)
                if not self._owner(current, user) or current['status'] != 'pending' or current['expires_at'] <= now:
                    raise AuthFailure('upload_unavailable', '上传入口已使用、过期或正在上传。', 409)
                used = sum(r.get('reserved_size', r.get('size', 0)) for r in state['requests'].values()
                           if self._owner(r, user) and r['status'] in ('receiving', 'ready'))
                if self.storage is None and used + size > USER_QUOTA:
                    raise AuthFailure('storage_quota', '当前账号文件占用已达到 512 MB，请先删除不再需要的文件。', 413)
                current.update({'status': 'receiving', 'reserved_size': size, 'nonce': nonce,
                                'expires_at': now + INVITATION_SECONDS})
            completed = False
            reservation = None
            try:
                if self.storage is not None:
                    reservation = self.storage.reserve(user, filename, size, external=True)['id']
                workspace = self.files.workspace(user)
                with root_directory(workspace) as root, child_directory(root, ['uploads', identifier], create=True) as parent:
                    descriptor = os.open('partial', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                    try:
                        total, digest = 0, hashlib.sha256()
                        async for chunk in chunks:
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > size:
                                raise AuthFailure('file_size', '上传数据超出声明的文件大小。', 413)
                            # File writes run outside the event loop; memory stays bounded by a network chunk.
                            def write(data):
                                view = memoryview(data)
                                while view:
                                    view = view[os.write(descriptor, view):]
                            writing = asyncio.create_task(asyncio.to_thread(write, chunk))
                            try:
                                await asyncio.shield(writing)
                            except asyncio.CancelledError:
                                await writing
                                raise
                            digest.update(chunk)
                        if total != size:
                            raise AuthFailure('upload_incomplete', '文件上传不完整，请重试。', 422)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    fresh = reauthorize()
                    self._permission(fresh)
                    if fresh['id'] != user['id'] or fresh['workspace_id'] != user['workspace_id']:
                        raise AuthFailure('not_found', '上传入口不存在。', 404)
                    self._get(fresh, identifier)
                    with self.repo.transaction() as state:
                        current = state['requests'].get(identifier)
                        if not self._owner(current, fresh) or current.get('nonce') != nonce or current['status'] != 'receiving' or current['expires_at'] <= int(time.time()):
                            raise AuthFailure('upload_unavailable', '上传入口已取消或过期。', 409)
                        os.rename('partial', 'payload', src_dir_fd=parent, dst_dir_fd=parent)
                        os.fsync(parent)
                        current.update({'status': 'ready', 'filename': filename, 'size': total, 'sha256': digest.hexdigest(),
                                        'file_id': identifier, 'path': f'uploads/{identifier}/payload',
                                        'expires_at': int(time.time()) + RETENTION_SECONDS})
                        current.pop('reserved_size', None)
                        current.pop('nonce', None)
                        receipt = self._view(current)
                    completed = True
                    return receipt
            except ClientDisconnect:
                raise AuthFailure('upload_interrupted', '上传已中断，可以重新选择文件。', 400) from None
            except (OSError, ToolError):
                raise AuthFailure('upload_failed', '文件保存失败，请重试。', 502) from None
            finally:
                if reservation is not None:
                    self.storage.release(user, reservation)
                if not completed:
                    self._erase(user, identifier)
                    with self.repo.transaction() as state:
                        current = state['requests'].get(identifier)
                        if current and current.get('nonce') == nonce:
                            current.update({'status': 'pending'})
                            current.pop('reserved_size', None)
                            current.pop('nonce', None)

    def resolve(self, user, identifier, conversation_id):
        item = self._get(user, identifier)
        if item['conversation_id'] != str(conversation_id):
            raise AuthFailure('not_found', '文件不属于当前对话。', 404)
        if item['status'] != 'ready' or item['expires_at'] <= int(time.time()):
            raise AuthFailure('file_unavailable', '文件未上传、已删除或已过期，请重新申请上传。', 410)
        try:
            with source_file(self.files.workspace(user), item['path']) as descriptor:
                if os.fstat(descriptor).st_size != item['size']:
                    raise ToolError('file_changed', '文件内容已变化。')
        except (ToolError, OSError):
            raise AuthFailure('file_unavailable', '文件不可用，请重新上传。', 410) from None
        return self._view(item)

    def delete(self, user, identifier):
        item = self._get(user, identifier)
        if item['status'] == 'receiving':
            raise AuthFailure('upload_busy', '请先停止上传后再删除。', 409)
        with self.repo.transaction() as state:
            current = state['requests'].get(str(identifier))
            if self._owner(current, user):
                if current['status'] == 'receiving':
                    raise AuthFailure('upload_busy', '文件正在上传。', 409)
                current['status'] = 'deleted'
        self._erase(user, str(identifier))
        return {'ok': True}

    def _erase(self, user, identifier):
        # Only constant leaf names under a server-allocated UUID directory are removed.
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError('Invalid upload identifier')
        try:
            with root_directory(self.files.workspace(user)) as root, child_directory(root, ['uploads']) as uploads:
                with child_directory(uploads, [identifier]) as parent:
                    for name in ('partial', 'payload'):
                        try:
                            os.unlink(name, dir_fd=parent)
                        except FileNotFoundError:
                            pass
                os.rmdir(identifier, dir_fd=uploads)
        except (OSError, ToolError):
            pass

    def delete_conversation(self, user, conversation_id):
        with self.repo.transaction() as state:
            items = [r for r in state['requests'].values() if self._owner(r, user) and r['conversation_id'] == str(conversation_id)]
            for item in items:
                item['status'] = 'deleted'
        for item in items:
            self._erase(user, item['upload_request_id'])

    def cleanup(self):
        now = int(time.time())
        with self.repo.transaction() as state:
            expired = [copy.deepcopy(r) for r in state['requests'].values()
                       if r['expires_at'] <= now or r['status'] in ('expired', 'deleted')]
            for item in expired:
                if item['status'] != 'deleted':
                    state['requests'][item['upload_request_id']]['status'] = 'expired'
            for key in list(state['requests']):
                if state['requests'][key]['expires_at'] < now - 30 * RETENTION_SECONDS:
                    del state['requests'][key]
        for item in expired:
            self._erase({'id': item['user_id'], 'workspace_id': item['workspace_id']}, item['upload_request_id'])

    async def housekeeping(self):
        while True:
            await asyncio.to_thread(self.cleanup)
            await asyncio.sleep(600)
