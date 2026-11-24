"""Bounded snapshots under a server-selected account cache, never model paths."""
import fcntl
import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import ExitStack

from .file_scope import root_directory, child_directory, relative_parts, regular_file
from .registry import ToolError

QUOTA = 512 * 1024 * 1024
TTL = 7 * 24 * 60 * 60
ENTRY = re.compile(r'[0-9a-f]{32}\.(jsonl|json|bin|meta\.json|tmp)')


def prune(directory, now):
    """Walk only real directories; expire generated snapshot pairs by mtime."""
    total = 0
    for name in os.listdir(directory):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with child_directory(directory, [name]) as child:
                total += prune(child, now)
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise ToolError('path_denied', '缓存中不允许硬链接。', 1)
            if ENTRY.fullmatch(name) and now - info.st_mtime > TTL:
                os.unlink(name, dir_fd=directory)
            else:
                total += info.st_size
        else:
            raise ToolError('path_denied', '缓存中不允许链接或特殊文件。', 1)
    return total


class CachedDataset:
    """One streaming JSONL snapshot with an atomic manifest and per-account cap.

    Lock covers cleanup and writes, so simultaneous exports cannot exceed quota
    or prune a live snapshot. No completed dataset is evicted just to make room.
    """
    def __init__(self, context, category, query, *, quota=QUOTA, format='jsonl', cancel=None):
        if (not isinstance(context, dict) or not isinstance(context.get('cache_directory'), str)
                or not isinstance(context.get('cache_identity'), dict)
                or not all(isinstance(context['cache_identity'].get(k), str) and context['cache_identity'][k]
                           for k in ('user_id', 'workspace_id'))):
            raise ToolError('invalid_context', '批量数据需使用服务端提供的本人缓存目录。', 1)
        self.context, self.parts, self.query = context, relative_parts(category), query
        if format not in ('jsonl', 'json', 'binary'):
            raise ValueError('Unsupported cache format')
        self.format, self.cancel = format, cancel
        self.quota = quota
        self.stack = ExitStack()

    def check_cancelled(self):
        if self.cancel is not None and self.cancel.is_set():
            raise ToolError('cache_cancelled', '缓存写入已取消。', 1)

    def __enter__(self):
        try:
            root = self.stack.enter_context(root_directory(self.context['cache_directory']))
            lock = os.open('.write.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
            self.stack.callback(os.close, lock)
            regular_file(lock)
            while True:
                self.check_cancelled()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
            now = time.time()
            self.used = prune(root, now)
            self.directory = self.stack.enter_context(child_directory(root, self.parts, create=True))
            self.identifier = uuid.uuid4().hex
            name = self.identifier + ('.bin' if self.format == 'binary' else '.' + self.format)
            relative = '/'.join([*self.parts, name])
            self.metadata = {'cache_id': self.identifier, 'path': relative, 'filename': name,
                'sandbox_path': '/account-cache/' + relative,
                'metadata_path': '/account-cache/' + '/'.join([*self.parts, self.identifier + '.meta.json']),
                'format': self.format, 'created_at': now, 'expires_at': now + TTL,
                'size': 0, 'total': None, 'count': 0, 'pages': 0, 'complete': False}
            self.digest = hashlib.sha256()
            # Reserve manifest overhead as well as data in the account cap.
            if self.used + 16384 > self.quota:
                raise ToolError('cache_quota', '本人缓存空间不足，请等待过期清理或减少拉取范围。', 1)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.directory)
            self.stream = self.stack.enter_context(os.fdopen(fd, 'wb'))
            self.save()
            return self
        except BaseException:
            self.stack.close()
            raise

    def append(self, encoded):
        self.check_cancelled()
        if self.used + self.metadata['size'] + len(encoded) + 16384 > self.quota:
            raise ToolError('cache_quota', '本人缓存已达容量限制，已保存部分不能视为完整数据。', 1)
        self.stream.write(encoded)
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.digest.update(encoded)
        self.metadata['size'] += len(encoded)

    def save(self):
        self.check_cancelled()
        self.metadata['sha256'] = self.digest.hexdigest()
        value = {**self.metadata, 'schema_version': 1, **self.context['cache_identity'], 'query': self.query}
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        if len(encoded) > 16384:
            raise ToolError('cache_metadata_limit', '缓存查询条件超出元数据限制。', 1)
        temporary = self.identifier + '.tmp'
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.identifier + '.meta.json', src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)
