"""Persistent owner-only uploads, capacity reservations and hosted-file views."""
import asyncio
import copy
import hashlib
import io
import mimetypes
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from galaxy_cli.file_scope import root_directory, child_directory, source_file, regular_file
from galaxy_cli.registry import ToolError
from starlette.requests import ClientDisconnect
from .account_storage import locked, read_json, atomic_json
from .auth import AuthFailure

MIB = 1024 * 1024


class FileStorage:
    def __init__(self, files, config):
        self.files, self.config = files, config
        self.uploads = None

    @staticmethod
    def permission(user, write=False):
        required = 'task:upload' if write else 'file:read'
        if required not in user.get('permissions', []):
            raise AuthFailure('forbidden', '当前账号没有文件上传权限。' if write else '当前账号没有文件读取权限。', 403)

    def settings(self):
        with self.config.transaction(write=False) as state:
            mb = state['settings'].get('file_storage', {}).get('quota_mb', 512)
        return {'quota_mb': mb, 'quota_bytes': mb * MIB}

    def save_settings(self, user, quota_mb):
        if 'ai:configure' not in user.get('permissions', []):
            raise AuthFailure('forbidden', '只有管理员可调整文件容量。', 403)
        with self.config.transaction() as state:
            state['settings']['file_storage'] = {'quota_mb': quota_mb}
        return self.settings()

    def library(self, user):
        path = self.files.workspace(user)
        with root_directory(path) as root, child_directory(root, ['library'], create=True):
            pass
        return path / 'library'

    def metadata_root(self, user):
        path = self.files.account_root(user)
        with root_directory(path) as root, child_directory(root, ['files'], create=True):
            pass
        return path / 'files'

    @contextmanager
    def state(self, user):
        root = self.metadata_root(user)
        with locked(root / '.storage.lock'):
            path = root / 'storage.json'
            value = read_json(path, {'files': {}})
            before = copy.deepcopy(value)
            now = time.time()
            # Expired reservations cannot be committed by a stale request.
            for key, item in list(value['files'].items()):
                if item['status'] != 'ready' and item['expires_at'] < now:
                    if item['status'] != 'external':
                        try:
                            if str(uuid.UUID(key)) != key:raise ValueError()
                            with root_directory(root) as parent, child_directory(parent,['incoming']) as incoming:
                                os.unlink(key,dir_fd=incoming)
                        except (ValueError,FileNotFoundError,ToolError):pass
                    del value['files'][key]
            try:
                yield value['files']
            finally:
                if value != before:
                    atomic_json(path, value)

    def legacy_bytes(self, user):
        root = self.files.workspace(user)
        total = 0
        uploads = root / 'uploads'
        if uploads.exists() and not uploads.is_symlink():
            for folder in uploads.iterdir():
                if folder.is_symlink() or not folder.is_dir():
                    continue
                try:
                    with source_file(root, 'uploads/' + folder.name + '/payload') as fd:
                        total += os.fstat(fd).st_size
                except (ToolError, OSError):
                    pass
        return total

    def usage(self, user, records):
        used = self.legacy_bytes(user) + sum(r['size'] for r in records.values() if r['status'] == 'ready')
        reserved = sum(r['size'] for r in records.values() if r['status'] != 'ready')
        quota = self.settings()['quota_bytes']
        return {'used_bytes': used, 'reserved_bytes': reserved, 'quota_bytes': quota,
                'available_bytes': max(0, quota - used - reserved)}

    @staticmethod
    def filename(value):
        if (not isinstance(value, str) or not value.strip() or value in ('.', '..')
                or len(value.encode()) > 240 or any(c in value for c in '/\\')
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise AuthFailure('invalid_filename', '文件名无效，请去掉路径或控制字符。', 422)
        return value

    @staticmethod
    def identifier(value):
        try:
            parsed = str(uuid.UUID(str(value)))
            if parsed != str(value):raise ValueError()
            return parsed
        except ValueError:
            raise AuthFailure('not_found', '文件不存在。', 404) from None

    @staticmethod
    def view(item):
        return {key: item[key] for key in ('id', 'filename', 'size', 'media_type', 'created_at', 'path', 'status', 'temporary') if key in item}

    def legacy_items(self, user):
        if self.uploads is None:return []
        with self.uploads.repo.transaction(write=False) as state:
            rows=[copy.deepcopy(r) for r in state['requests'].values() if self.uploads._owner(r,user)
                  and r['status']=='ready' and r['expires_at']>time.time()]
        result=[]
        for row in rows:
            try:
                with source_file(self.files.workspace(user),row['path']) as fd:
                    if os.fstat(fd).st_size!=row['size']:continue
            except (ToolError,OSError):continue
            result.append({**row,'id':row['upload_request_id'],'temporary':True,
                'media_type':mimetypes.guess_type(row['filename'])[0] or 'application/octet-stream'})
        return result

    def list(self, user):
        self.permission(user)
        self.library(user)
        with self.state(user) as records:
            result = self.usage(user, records)
            result['files'] = sorted([self.view(r) for r in records.values() if r['status'] == 'ready'], key=lambda r:r['created_at'], reverse=True)
        result['files'] = sorted(result['files']+[self.view(r) for r in self.legacy_items(user)],key=lambda r:r['created_at'],reverse=True)
        return result

    def reserve(self, user, filename, size, *, external=False):
        self.permission(user, True)
        filename = self.filename(filename)
        if type(size) is not int or size < 0:
            raise AuthFailure('file_size', '文件大小无效。', 422)
        with self.state(user) as records:
            capacity = self.usage(user, records)
            if size > capacity['available_bytes']:
                raise AuthFailure('storage_quota', f"空间不足：已用 {capacity['used_bytes']/MIB:.2f} MB，上传中 {capacity['reserved_bytes']/MIB:.2f} MB，总容量 {capacity['quota_bytes']/MIB:g} MB。请删除文件或联系管理员调整容量。", 413)
            identifier = str(uuid.uuid4())
            leaf = re.sub(r'[%~:]', '_', filename)
            if leaf.startswith('.'):leaf = 'upload' + leaf
            records[identifier] = {'id': identifier, 'filename': filename, 'size': size, 'created_at': int(time.time()),
                'leaf': leaf,
                'expires_at': time.time() + 3600, 'status': 'external' if external else 'pending',
                'media_type': mimetypes.guess_type(filename)[0] or 'application/octet-stream'}
            return self.view(records[identifier])

    def release(self, user, identifier):
        with self.state(user) as records:
            if records.get(identifier, {}).get('status') != 'ready':
                records.pop(identifier, None)

    async def receive(self, user, identifier, size, chunks, reauthorize):
        self.permission(user, True)
        identifier = self.identifier(identifier)
        with self.state(user) as records:
            item = records.get(identifier)
            if not item or item['status'] != 'pending':
                raise AuthFailure('upload_unavailable', '上传入口已使用或已过期。', 409)
            if item['size'] != size:
                records.pop(identifier)
                raise AuthFailure('file_size', '上传大小与预留大小不一致。', 413)
            item['status'] = 'receiving'
            item = copy.deepcopy(item)
        completed = False
        try:
            incoming = self.metadata_root(user)
            library = self.library(user)
            with root_directory(incoming) as root, child_directory(root, ['incoming'], create=True) as parent:
                fd = os.open(identifier, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
                try:
                    count = 0
                    async for chunk in chunks:
                        count += len(chunk)
                        if count > size:
                            raise AuthFailure('file_size', '上传内容超出声明的文件大小。', 413)
                        if chunk:
                            def write(data):
                                view = memoryview(data)
                                while view:view = view[os.write(fd, view):]
                            task = asyncio.create_task(asyncio.to_thread(write, chunk))
                            try:await asyncio.shield(task)
                            except asyncio.CancelledError:
                                await task
                                raise
                    if count != size:
                        raise AuthFailure('upload_incomplete', '文件上传不完整，请重试。', 422)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                fresh = reauthorize()
                self.permission(fresh, True)
                if (fresh['id'], fresh['workspace_id']) != (user['id'], user['workspace_id']):
                    raise AuthFailure('not_found', '文件不存在。', 404)
                with self.state(user) as records:
                    current = records.get(identifier)
                    if not current or current['status'] != 'receiving':
                        raise AuthFailure('upload_unavailable', '上传已取消或过期。', 409)
                    if self.usage(user, records)['used_bytes'] + self.usage(user, records)['reserved_bytes'] > self.settings()['quota_bytes']:
                        raise AuthFailure('storage_quota', '管理员已调整容量，当前空间不足。', 413)
                    with root_directory(library) as lib, child_directory(lib, [identifier], create=True) as destination:
                        os.rename(identifier, item['leaf'], src_dir_fd=parent, dst_dir_fd=destination)
                        os.fsync(destination)
                    current.update(status='ready', path='library/' + identifier + '/' + item['leaf'])
                    current.pop('expires_at', None)
                    completed = True
                    return self.view(current)
        except ClientDisconnect:
            raise AuthFailure('upload_interrupted', '上传中断，预留空间已释放。', 400) from None
        except (OSError, ToolError):
            raise AuthFailure('upload_failed', '文件保存失败，请重试。', 502) from None
        finally:
            if not completed:
                self.release(user, identifier)
                path = self.metadata_root(user) / 'incoming' / identifier
                try:
                    with root_directory(path.parent) as parent:os.unlink(identifier, dir_fd=parent)
                except (FileNotFoundError, ToolError):pass

    def resolve(self, user, identifier):
        self.permission(user)
        identifier = self.identifier(identifier)
        with self.state(user) as records:
            item = copy.deepcopy(records.get(identifier))
        if not item or item['status'] != 'ready':
            item=next((r for r in self.legacy_items(user) if r['id']==identifier),None)
            if not item:raise AuthFailure('not_found', '文件不存在或不属于当前账号。', 404)
        path = self.files.workspace(user) / item['path']
        try:
            with source_file(self.files.workspace(user), item['path']) as fd:
                if os.fstat(fd).st_size != item['size']:raise ToolError('changed', '文件已变化。')
        except (ToolError, OSError):
            raise AuthFailure('not_found', '文件不存在。', 404) from None
        return path, self.view(item)

    def delete(self, user, identifier):
        self.permission(user, True)
        identifier = self.identifier(identifier)
        legacy=next((r for r in self.legacy_items(user) if r['id']==identifier),None)
        if legacy:
            with self.uploads.repo.transaction() as state:
                current=state['requests'].get(identifier)
                if self.uploads._owner(current,user):current['status']='deleted'
            self.uploads._erase(user,identifier)
            return {'ok':True}
        with self.state(user) as records:
            item = records.get(identifier)
            if not item:
                raise AuthFailure('not_found', '文件不存在。', 404)
            if item['status'] == 'ready':
                path = self.files.workspace(user) / item['path']
                try:
                    with root_directory(path.parent) as parent:os.unlink(path.name, dir_fd=parent)
                except FileNotFoundError:pass
                try:
                    with root_directory(self.library(user)) as parent:os.rmdir(identifier,dir_fd=parent)
                except OSError:pass
            records.pop(identifier)
        return {'ok': True}

    def thumbnail(self, user, identifier):
        path, _ = self.resolve(user, identifier)
        from PIL import Image, UnidentifiedImageError
        try:
            with source_file(path.parent, path.name) as fd, os.fdopen(os.dup(fd), 'rb') as stream:
                with Image.open(stream) as image:
                    if image.width * image.height > 25000000:raise ValueError()
                    image.thumbnail((420, 280))
                    output = io.BytesIO()
                    image.convert('RGB').save(output, format='JPEG', quality=82)
                    return output.getvalue()
        except (OSError, ValueError, ToolError, UnidentifiedImageError, Image.DecompressionBombError):
            raise AuthFailure('no_preview', '此文件不支持图片预览，仍可下载原文件。', 415) from None

    def published(self, user):
        self.permission(user)
        entries = []
        with self.files.repo.transaction(write=False) as state:
            private_names = {item.get('stored_name',key+'.xlsx') for key,item in state['files'].items()}
            names = {item.get('stored_name', key+'.xlsx'):item.get('filename', key+'.xlsx')
                for key,item in state['files'].items() if item['user_id']==user['id'] and item['workspace_id']==user['workspace_id']}
        roots = [('personal', self.files.published_root(user)), ('shared', self.files.root)]
        for scope, root in roots:
            if not root.exists():continue
            for path in root.iterdir():
                if not self.files._valid_name(path.name):continue
                if scope=='personal' and root==self.files.root and path.name not in names:continue
                if scope=='shared' and (path.name in private_names or re.match(r'^[0-9a-f-]{36}(?:_|\.xlsx$)', path.name)):continue
                try:
                    with source_file(root,path.name) as fd:info=os.fstat(fd)
                except (OSError,ToolError):continue
                entries.append({'scope':scope,'name':path.name,'filename':names.get(path.name,path.name) if scope=='personal' else path.name,
                    'size':info.st_size,'created_at':int(info.st_mtime),'media_type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream'})
        return {'files':sorted(entries,key=lambda r:r['created_at'],reverse=True)}

    def resolve_published(self, user, scope, name):
        self.permission(user)
        if scope not in ('personal','shared') or not self.files._valid_name(name):
            raise AuthFailure('not_found','文件不存在。',404)
        entry=next((r for r in self.published(user)['files'] if r['scope']==scope and r['name']==name),None)
        if not entry:raise AuthFailure('not_found','文件不存在或不属于当前账号。',404)
        root=self.files.published_root(user) if scope=='personal' else self.files.root
        return root/name, entry
