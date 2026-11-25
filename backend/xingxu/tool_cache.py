"""One return boundary for all authenticated business tools, including future ones."""
import asyncio
import hashlib
import json
import os
import re
import threading

from galaxy_cli.file_scope import source_file, regular_file, root_directory, child_directory, relative_parts
from galaxy_cli.registry import ToolError
from galaxy_cli.request_cache import CachedDataset

from .account_storage import enabled
from .auth import AuthFailure
from .tool_context import DispatchDenied, strict_json

# These are protocol/control responses consumed directly by the UI or agent.
# Business tools default to file receipts; new platforms do not need an allowlist.
INLINE = {'help', 'time.now', 'interaction.ask', 'task.request-upload', 'excel.create', 'excel.inspect', 'excel.transform', 'file.publish'}
PRIVATE = re.compile(r'password|secret|cookie|authorization|access_token|refresh_token|credential|csrf|^sid$|^headers$|^session', re.I)


def safe_input(value):
    if isinstance(value, dict):
        return {k: '[已隐藏凭证]' if PRIVATE.search(k) else safe_input(v) for k, v in value.items()}
    if isinstance(value, list):return [safe_input(v) for v in value]
    return value


def cacheable(command, arguments):
    if command in INLINE:
        return False
    if command.startswith('feishu.') and '--input' in arguments:
        body = strict_json(arguments[arguments.index('--input') + 1])
        if body.get('action') == 'help' or 'action' not in body and command.split('.', 1)[1] not in (
                'search', 'document-get', 'folder-list', 'file-download', 'authorized-users'):
            return False
    return True


def cache_failure(command, canonical, result, failed, code):
    # A successful remote mutation cannot be retried as though cache I/O failure
    # meant the business action never happened. Keep its small acknowledgement.
    from .tools import required_permissions, readonly_youzan
    mutation = (command.startswith(('erp.', 'clawbot.')) and 'options' != body_action(canonical)
                or command.startswith('schedule.') and command != 'schedule.list'
                or command.startswith('youzan.') and not readonly_youzan(canonical)
                or command.startswith('feishu.') and 'feishu:write' in required_permissions(command, canonical))
    if mutation and not failed:
        ack = {k: v for k, v in result.items() if k in ('id', 'action', 'created', 'deleted', 'sent', 'success', 'ok', 'code') and isinstance(v, (str, int, bool))}
        return {**ack, 'operation_completed': True, 'cached': False,
                'cache_error': {'code': code, 'message': '业务操作已完成，但结果缓存未保存；请回读核对，勿重复提交。'}}
    raise DispatchDenied(code, '查询结果未能保存到本人缓存；请检查缓存空间或稍后重试。') from None


def discard(root, receipt):
    """Delete only files allocated by this cache invocation, with no link traversal."""
    for relative in (receipt['path'], receipt['metadata_path'].removeprefix('/account-cache/')):
        parts = relative_parts(relative)
        with root_directory(root) as descriptor, child_directory(descriptor, parts[:-1]) as parent:
            try:os.unlink(parts[-1], dir_fd=parent)
            except FileNotFoundError:pass


def write_result(frame, command, canonical, context, scope, result, failed, cancel, files, user):
    body = strict_json(canonical[canonical.index('--input') + 1]) if '--input' in canonical else {}
    # Inputs can contain an ERP password or user-provided authorization material.
    # Bind the request by hash, not plaintext arguments, in the reusable manifest.
    query = {'command': command, 'action': body.get('action'), 'conversation_id': context.conversation_id,
             'arguments_sha256': hashlib.sha256(json.dumps([canonical, scope], ensure_ascii=False).encode()).hexdigest()}
    inputs = safe_input({'input': body, 'shops': scope})
    if len(json.dumps(inputs, ensure_ascii=False).encode()) <= 8192:
        query['request'] = inputs
    else:
        query['input_omitted'] = True
    category, name = command.split('.', 1)
    binary = command == 'feishu.file-download' and not failed
    receipt = None
    with CachedDataset(frame, category + '/' + name, query, format='binary' if binary else 'json', cancel=cancel) as dataset:
        try:
            if binary:
                with source_file(files.workspace(user), result['path']) as source:
                    if regular_file(source).st_size != result['size']:
                        raise ToolError('file_changed', '下载文件已变化，未缓存。', 1)
                    while chunk := os.read(source, 512 * 1024):
                        dataset.append(chunk)
                dataset.metadata['filename'] = result['filename']
            else:
                dataset.append(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode() + b'\n')
            dataset.metadata.update(complete=True, count=1, pages=1, coverage='downloaded_file' if binary else 'tool_response',
                                    business_success=not failed, command=command)
            if command == 'gjp.inventory-cost' and isinstance(result.get('data', {}).get('summary'), dict):
                summary = result['data']['summary']
                dataset.metadata.update(complete=summary['complete'], count=summary['processed'],
                                        total=summary['total'], pages=summary['batches'], coverage='requested_barcodes')
            dataset.save()
            receipt = dict(dataset.metadata)
            return receipt
        finally:
            if receipt is None:
                discard(frame['cache_directory'], dataset.metadata)


async def cache_result(dispatcher, context, command, canonical, scope, result, failed):
    if not enabled(dispatcher.directory) or not cacheable(command, canonical):
        return result
    user = dispatcher._check_command(context, command, canonical)
    try:
        root = dispatcher.files.cache(user)
    except (AuthFailure, OSError, RuntimeError):
        return cache_failure(command, canonical, result, failed, 'cache_unavailable')
    # The bulk adapter has already streamed and validated its dataset. Reuse it.
    if command == 'youzan.order-search' and isinstance(result.get('data'), dict):
        receipt = result['data']
        if isinstance(receipt.get('path'), str) and receipt.get('sandbox_path') == '/account-cache/' + receipt['path']:
            try:
                with source_file(root, receipt['path']) as descriptor:
                    if regular_file(descriptor).st_size != receipt['size']:raise ValueError()
                return {**result, 'cached': True, 'sandbox_path': receipt['sandbox_path'], 'format': receipt['format'],
                        'metadata_path': receipt['metadata_path']}
            except (ToolError, KeyError, ValueError, OSError):
                raise DispatchDenied('invalid_cache_receipt', '批量数据缓存回执无效，请勿将其当作完整数据。') from None
    frame = {'cache_directory': str(root), 'cache_identity': {'user_id': user['id'], 'workspace_id': user['workspace_id']}}
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(write_result, frame, command, canonical, context, scope, result, failed, cancel, dispatcher.files, user))
    try:
        receipt = await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        try:
            receipt = await task
        except (ToolError, OSError):
            pass
        else:
            discard(root, receipt)
        raise
    except (ToolError, OSError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ToolError) else 'cache_write_failed'
        return cache_failure(command, canonical, result, failed, code)
    try:
        dispatcher._check_command(context, command, canonical)
    except DispatchDenied:
        discard(root, receipt)
        raise
    if command == 'feishu.file-download' and not failed:
        # The complete source is now in cache; don't retain a redundant work copy.
        parts = relative_parts(result['path'])
        with root_directory(dispatcher.files.workspace(user)) as descriptor, child_directory(descriptor, parts[:-1]) as parent:
            try:os.unlink(parts[-1], dir_fd=parent)
            except OSError:pass
    cost_summary = ({'summary': result['data']['summary'], 'resume_id': result['data']['resume_id']}
                    if command == 'gjp.inventory-cost' and isinstance(result.get('data', {}).get('summary'), dict) else {})
    return {**receipt, **cost_summary, 'cached': True, 'success': not failed,
            **({'partial': True} if result.get('partial') is True else {}),
            **({'error': {'code': 'upstream_failed', 'message': '业务工具返回失败，完整错误见缓存；勿将其视为成功结果。'}} if failed else {}),
            'read_hint': ('二进制文件，按filename的扩展名选择解析库；如需发布，先复制到/workspace再file.publish。' if receipt['format'] == 'binary'
                          else '用json.load(open(sandbox_path))读取原始工具JSON。此路径仅代表本次调用结果，不代表已取得所有分页。')}


def body_action(canonical):
    return strict_json(canonical[canonical.index('--input') + 1]).get('action') if '--input' in canonical else None
