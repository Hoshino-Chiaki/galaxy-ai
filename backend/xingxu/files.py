"""Private, user- and conversation-scoped generated attachments."""
import os
import hashlib
import json
import time
import uuid
import zipfile
import re
import mimetypes
import errno
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from galaxy_cli.file_scope import root_directory, child_directory, source_file, regular_file, relative_parts, fingerprint
from galaxy_cli.registry import ToolError
from .auth import AuthFailure
from .store import JsonRepository
from .account_storage import enabled as account_files, Accounts, scoped

MAX_FILE_SIZE = 20 * 1024 * 1024
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class FileService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.legacy_root = Path(directory) / 'files'
        self.root = Path(os.environ.get('GALAXY_FILES_DIR') or self.legacy_root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.repo = JsonRepository(directory, 'files', ('files',))
        self.chats = JsonRepository(directory, 'chats', ('conversations',))

    def account_root(self, user):
        return Accounts(self.directory).ensure(user)

    def published_root(self, user):
        return Accounts(self.directory).path(user, 'files/published') if account_files(self.directory) else self.root

    def dsh_root(self, user):
        return Accounts(self.directory).path(user, 'tasks/dsh') if account_files(self.directory) else Path(os.environ.get('GALAXY_DSH_STORAGE') or self.root / '.dsh') / hashlib.sha256(json.dumps([user['workspace_id'], user['id']], separators=(',', ':')).encode()).hexdigest()

    def cache(self, user):
        # Only the authenticated principal chooses the account, never model input.
        if account_files(self.directory):
            return Accounts(self.directory).path(user, 'cache')
        workspace = self.workspace(user)
        with root_directory(workspace) as root, child_directory(root, ['cache'], create=True):
            pass
        return workspace / 'cache'

    def workspace(self, user):
        # Identity never comes from model arguments or a caller-supplied path.
        if account_files(self.directory):
            return Accounts(self.directory).path(user, 'workspace')
        owner = hashlib.sha256(json.dumps([user['workspace_id'], user['id']], separators=(',', ':')).encode()).hexdigest()
        try:
            with root_directory(self.root) as root, child_directory(root, ['.work', owner], create=True):
                pass
        except ToolError:
            raise AuthFailure('invalid_storage', '当前账号文件目录不可用。', 502) from None
        return self.root / '.work' / owner

    def catalog_context(self, user, shop):
        workspace=self.workspace(user)
        scope=hashlib.sha256(json.dumps([user['workspace_id'],str(shop)],separators=(',',':')).encode()).hexdigest()
        try:
            with root_directory(self.root) as root, child_directory(root,['.locks',scope],create=True): pass
        except ToolError:
            raise AuthFailure('invalid_storage','有赞操作锁目录不可用。',502) from None
        return {'workspace_directory':str(workspace),'catalog_lock_directory':str(self.root/'.locks'/scope),
            'cache_directory':str(self.cache(user)),
            'cache_identity':{'user_id':user['id'],'workspace_id':user['workspace_id']},
            'permissions':list(user['permissions'])}

    def inspect_source(self, user, relative):
        try:
            with source_file(self.workspace(user), relative) as descriptor:
                info = regular_file(descriptor)
                if not 0 < info.st_size <= MAX_FILE_SIZE:
                    raise ValueError()
                digest = hashlib.sha256()
                total = 0
                while chunk := os.read(descriptor, 512 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE_SIZE: raise ValueError()
                    digest.update(chunk)
                if fingerprint(info) != fingerprint(regular_file(descriptor)):
                    raise ValueError()
                return info.st_size, digest.hexdigest()
        except (ToolError, OSError, ValueError):
            raise AuthFailure('path_denied', '仅允许当前账号工作目录内、未链接的普通文件，文件须为 1 字节至 20 MB。', 403) from None

    def generated(self, descriptor, user):
        try:
            relative_parts(descriptor['path'])
            if descriptor['filename'] != Path(descriptor['path']).name:
                raise ValueError()
            size, _ = self.inspect_source(user, descriptor['path'])
            if size != descriptor['size']:
                raise ValueError()
            return descriptor
        except (ToolError, KeyError, ValueError, TypeError):
            raise AuthFailure('invalid_export', '生成文件无效。', 502) from None

    def accept(self, descriptor, relative, publication_id, snapshot, user, conversation_id):
        # Independently validate the child receipt and source bytes before registering.
        name = Path(relative).name
        stored = publication_id + '_' + name.encode('utf-8')[:200].decode('utf-8', errors='ignore')
        try:
            if (descriptor['file_id'] != publication_id or descriptor['filename'] != name
                    or descriptor['download_path'] != '/files/' + quote(stored, safe='')
                    or descriptor['media_type'] != (mimetypes.guess_type(name)[0] or 'application/octet-stream')):
                raise ValueError()
            if self.inspect_source(user, relative) != snapshot:
                raise ValueError()
            with source_file(self.published_root(user), stored) as hosted:
                info = regular_file(hosted)
                digest = hashlib.sha256()
                while chunk := os.read(hosted, 512 * 1024): digest.update(chunk)
                if (info.st_size, digest.hexdigest()) != snapshot or descriptor['size'] != info.st_size or fingerprint(info) != fingerprint(regular_file(hosted)):
                    raise ValueError()
            with self.repo.transaction() as state:
                for item in state['files'].values():
                    if (item['user_id'] == user['id'] and item['workspace_id'] == user['workspace_id']
                            and item['conversation_id'] == str(conversation_id) and item.get('source_path') == relative
                            and item.get('sha256') == snapshot[1]):
                        try:
                            with source_file(self.published_root(user), item['stored_name']) as existing:
                                check = hashlib.sha256()
                                while chunk := os.read(existing, 512 * 1024): check.update(chunk)
                                if check.hexdigest() == snapshot[1]:
                                    return {k: item[k] for k in ('file_id', 'filename', 'size', 'media_type', 'download_path')}
                        except (OSError, ToolError): pass
                if publication_id in state['files']:
                    raise ValueError()
                state['files'][publication_id] = {**descriptor, 'stored_name': stored,
                    'user_id': user['id'], 'workspace_id': user['workspace_id'], 'conversation_id': str(conversation_id),
                    'source_path': relative, 'sha256': snapshot[1], 'created_at': int(time.time())}
            return descriptor
        except (ToolError, KeyError, TypeError, ValueError, OSError):
            raise AuthFailure('invalid_publication', '文件托管结果无效，请重新生成后重试。', 502) from None

    def cleanup_publication(self, publication_id, relative, user=None):
        # Delete only this server-allocated invocation's output, never caller paths.
        stored = publication_id + '_' + Path(relative).name.encode('utf-8')[:200].decode('utf-8', errors='ignore')
        with self.repo.transaction(write=False) as state:
            if publication_id in state['files']: return
        try:
            with root_directory(self.published_root(user) if user is not None else self.root) as root:
                for name in (stored, '.' + publication_id + '.part'):
                    try: os.unlink(name, dir_fd=root)
                    except FileNotFoundError: pass
        except (OSError, ToolError): pass

    def adopt(self, descriptor, staging, user, conversation_id):
        try:
            identifier = str(uuid.UUID(descriptor['file_id']))
            if identifier != descriptor['file_id']:
                raise ValueError()
            source = Path(staging) / (identifier + '.xlsx')
            size = source.stat().st_size
            if source.is_symlink() or not source.is_file() or not 0 < size <= MAX_FILE_SIZE or size != descriptor['size']:
                raise ValueError()
            name = descriptor['filename']
            if not isinstance(name, str) or len(name) > 110 or not name.endswith('.xlsx') or any(c in name for c in '/\\\r\n\x00'):
                raise ValueError()
            with zipfile.ZipFile(source) as book:
                if '[Content_Types].xml' not in book.namelist() or 'xl/workbook.xml' not in book.namelist():
                    raise ValueError()
            readable_stem = name[:-5].encode('utf-8')[:200].decode('utf-8', errors='ignore')
            stored_name = identifier + '_' + readable_stem + '.xlsx'
            hosting = self.published_root(user)
            target = hosting / stored_name
            if target.exists():
                raise ValueError()
            source.chmod(0o600)
            try:
                os.replace(source, target)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                # Service isolation or a separate hosting volume can make rename cross-device.
                descriptor_fd, temporary = tempfile.mkstemp(prefix='.' + identifier + '-', dir=hosting)
                try:
                    with os.fdopen(descriptor_fd, 'wb') as output, source.open('rb') as original:
                        shutil.copyfileobj(original, output)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                    source.unlink()
                finally:
                    Path(temporary).unlink(missing_ok=True)
            try:
                with self.repo.transaction() as state:
                    state['files'][identifier] = {**descriptor, 'stored_name': stored_name, 'user_id': user['id'], 'workspace_id': user['workspace_id'],
                        'conversation_id': str(conversation_id), 'created_at': int(time.time())}
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return descriptor
        except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile):
            raise AuthFailure('invalid_export', '生成文件无效或超出 20 MB 限制。', 502) from None

    def resolve(self, user, identifier):
        if 'file:read' not in user['permissions']:
            raise AuthFailure('forbidden', '没有下载权限。', 403)
        with self.repo.transaction(write=False) as state:
            item = state['files'].get(str(identifier))
            if not item or item['user_id'] != user['id'] or item['workspace_id'] != user['workspace_id']:
                raise AuthFailure('not_found', '文件不存在。', 404)
            item = dict(item)
        with scoped(self.chats, user, write=False, identifier=item['conversation_id']) as state:
            chat = state['conversations'].get(item['conversation_id'])
            if not chat or chat['user_id'] != user['id'] or chat['workspace_id'] != user['workspace_id']:
                raise AuthFailure('not_found', '文件所属对话不存在。', 404)
            # Only attachments from a successfully saved turn can be downloaded.
            if not any(e.get('command') in ('galaxy-cli excel create', 'galaxy-cli file publish') and e.get('status') == 'success' and e.get('result', {}).get('file_id') == str(identifier)
                       for m in chat['messages'] for e in m.get('tool_events', [])):
                raise AuthFailure('not_found', '附件不存在。', 404)
        stored_name = item.get('stored_name', str(identifier) + '.xlsx')
        if not self._valid_name(stored_name):
            raise AuthFailure('not_found', '文件不存在。', 404)
        path = self.published_root(user) / stored_name
        if not path.exists() and not item.get('stored_name'):
            path = self.legacy_root / stored_name
        try:
            with source_file(path.parent, path.name): pass
        except ToolError:
            raise AuthFailure('not_found', '文件不存在。', 404) from None
        metadata = {k: item[k] for k in ('file_id', 'filename', 'size', 'sheets', 'media_type') if k in item}
        metadata['download_path'] = '/files/' + quote(stored_name, safe='')
        return path, metadata

    @staticmethod
    def _valid_name(name):
        return (isinstance(name, str) and bool(name) and len(name.encode('utf-8')) <= 255 and not name.startswith('.')
            and not any(c in name for c in '/\\') and not any(ord(c) < 32 or ord(c) == 127 for c in name) and Path(name).name == name)

    def resolve_filename(self, user, filename):
        if not self._valid_name(filename):
            raise AuthFailure('not_found', '文件不存在。', 404)
        with self.repo.transaction(write=False) as state:
            item = next((dict(f) for key, f in state['files'].items()
                if f.get('stored_name', key + '.xlsx') == filename), None)
        if item is not None:
            path, metadata = self.resolve(user, item['file_id'])
            return path, metadata['filename'], metadata.get('media_type', MIME)
        # Generated names are reserved, including files from an unfinished turn.
        if re.match(r'^[0-9a-f-]{36}(?:_|\.xlsx$)', filename):
            raise AuthFailure('not_found', '文件不存在。', 404)
        if 'file:read' not in user['permissions']:
            raise AuthFailure('forbidden', '没有下载权限。', 403)
        # Files placed manually in this dedicated directory are shared with logged-in users.
        path = self.root / filename
        try:
            with source_file(self.root, filename): pass
        except ToolError:
            raise AuthFailure('not_found', '文件不存在。', 404) from None
        return path, filename, mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    def delete_conversation(self, user, identifier):
        with self.repo.transaction() as state:
            keys = [key for key, f in state['files'].items() if f['user_id'] == user['id'] and f['workspace_id'] == user['workspace_id'] and f['conversation_id'] == str(identifier)]
            for key in keys:
                item = state['files'][key]
                stored_name = item.get('stored_name', key + '.xlsx')
                if self._valid_name(stored_name):
                    (self.published_root(user) / stored_name).unlink(missing_ok=True)
                    if not item.get('stored_name'):
                        (self.legacy_root / stored_name).unlink(missing_ok=True)
                del state['files'][key]
