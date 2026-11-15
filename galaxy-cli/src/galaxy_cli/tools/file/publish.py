"""Publish a single file from the authenticated user's private workspace."""
import mimetypes
import os
import uuid
from pathlib import PurePosixPath
from urllib.parse import quote

from jsonschema import Draft202012Validator

from ...context import read_context
from ...file_scope import root_directory, source_file, regular_file, relative_parts, fingerprint
from ...registry import ToolError

INPUT = {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'maxLength': 2048}},
    'required': ['path'], 'additionalProperties': False}
OUTPUT = {'type': 'object', 'properties': {
    'file_id': {'type': 'string'}, 'filename': {'type': 'string'}, 'size': {'type': 'integer', 'minimum': 0},
    'media_type': {'type': 'string'}, 'download_path': {'type': 'string'}},
    'required': ['file_id', 'filename', 'size', 'media_type', 'download_path'], 'additionalProperties': False}
DESCRIPTION = (
    '将当前账号工作目录中的文件托管为聊天下载附件，调用 galaxy-cli file publish --input JSON。'
    '唯一必填参数 path 为相对文件路径，通常直接使用 excel.create 或其他文件生成工具返回的 path；'
    '例如 {"path":"generated/文件目录/经营报表.xlsx"}。路径基准由服务器按当前登录账号和工作区确定，'
    '不能传绝对路径、..、隐藏路径、反斜杠、编码路径、URL、其他用户目录、符号链接、硬链接或特殊文件；不接受用户身份、输出目录、凭证或外部文件路径参数。'
    '支持 Excel、PDF、Word、文本、图片、ZIP 等普通文件，工具只复制文件内容，不执行文件或修改源文件。'
    '成功返回 {file_id,filename,size,media_type,download_path}，size 为字节，download_path 为当前服务的相对下载路径，聊天框据此显示附件。'
    '仅使用生成工具实际返回的 path；源文件路径不是下载链接，不编造路径或附件信息，取得实际 file_id 后才能告知用户从附件下载。'
    '托管完成立即可下载，无需刷新或重启服务；下载仍需登录，并校验文件归属。'
    '同一对话重复托管内容相同的同一路径会返回已有下载信息；失败返回 error.code、error.message。'
)


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input', '文件托管只接受必填的 path 相对路径。')
    relative_parts(arguments['path'])
    return dict(arguments)


def publish(arguments, context):
    arguments = normalize(arguments)
    try:
        identifier = str(uuid.UUID(context['publication_id']))
        directory, hosting = context['workspace_directory'], context['hosting_directory']
        if identifier != context['publication_id'] or not isinstance(directory, str) or not isinstance(hosting, str):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ToolError('server_context_required', '文件托管需通过已登录的 galaxy_cli 调用。', 1) from None
    filename = PurePosixPath(arguments['path']).name
    stored = identifier + '_' + filename.encode('utf-8')[:200].decode('utf-8', errors='ignore')
    temporary = '.' + identifier + '.part'
    with source_file(directory, arguments['path']) as original, root_directory(hosting) as root:
        before = regular_file(original)
        if not 0 < before.st_size <= 20 * 1024 * 1024:
            raise ToolError('file_size', '文件须为 1 字节至 20 MB。', 1)
        copied = 0
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
            try:
                while chunk := os.read(original, 512 * 1024):
                    copied += len(chunk)
                    if copied > 20 * 1024 * 1024:
                        raise ToolError('file_changed', '文件内容发生变化，请重试。', 1)
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(descriptor, view):]
                os.fsync(descriptor)
                after = regular_file(original)
                if fingerprint(before) != fingerprint(after):
                    raise ToolError('file_changed', '文件在托管过程中发生变化，请重新生成或重试。', 1)
            finally:
                os.close(descriptor)
            # Publication IDs are allocated by the server for this invocation.
            os.link(temporary, stored, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            os.unlink(temporary, dir_fd=root)
            os.fsync(root)
        except OSError:
            raise ToolError('publish_failed', '文件托管失败，未生成下载附件。', 1) from None
        finally:
            try: os.unlink(temporary, dir_fd=root)
            except FileNotFoundError: pass
    return {'file_id': identifier, 'filename': filename, 'size': before.st_size,
        'media_type': mimetypes.guess_type(filename)[0] or 'application/octet-stream',
        'download_path': '/files/' + quote(stored, safe='')}


def register(registry):
    @registry.tool(name='file.publish', category='file', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def publish_file(arguments):
        return publish(arguments, read_context())
