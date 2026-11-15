"""Descriptor-based file access under server-selected directories only."""
import os
import stat
from contextlib import contextmanager
from pathlib import PurePosixPath

from .registry import ToolError

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def relative_parts(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 2048
            or any(c in value for c in ('\\', ':', '%', '~')) or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ToolError('path_denied', '仅接受当前账号工作目录内的相对文件路径。', 1)
    parts = value.split('/')
    if any(not part or part.startswith('.') or len(part.encode('utf-8')) > 255 for part in parts):
        raise ToolError('path_denied', '不允许绝对路径、隐藏路径、目录跳转或编码路径。', 1)
    return parts


@contextmanager
def root_directory(path):
    value = os.fspath(path)
    if not os.path.isabs(value) or '..' in PurePosixPath(value).parts:
        raise ToolError('invalid_context', '文件运行目录无效。', 1)
    descriptor = os.open('/', DIRECTORY)
    try:
        for part in PurePosixPath(value).parts[1:]:
            next_fd = os.open(part, DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        yield descriptor
    except OSError:
        raise ToolError('path_denied', '文件目录不可用或包含不允许的链接。', 1) from None
    finally:
        os.close(descriptor)


@contextmanager
def child_directory(parent, parts, *, create=False):
    descriptor = os.dup(parent)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_fd = os.open(part, DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        yield descriptor
    except OSError:
        raise ToolError('path_denied', '文件目录不存在或包含不允许的链接。', 1) from None
    finally:
        os.close(descriptor)


def regular_file(descriptor):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ToolError('path_denied', '仅允许普通文件，不允许符号链接、硬链接或特殊文件。', 1)
    return info


@contextmanager
def source_file(directory, relative):
    parts = relative_parts(relative)
    try:
        with root_directory(directory) as root, child_directory(root, parts[:-1]) as parent:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            try:
                regular_file(descriptor)
                yield descriptor
            finally:
                os.close(descriptor)
    except OSError:
        raise ToolError('path_denied', '文件不存在或不在当前账号允许访问的目录内。', 1) from None


def fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
