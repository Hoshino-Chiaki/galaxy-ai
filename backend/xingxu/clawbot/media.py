"""Send an account-owned file via the official iLink encrypted CDN upload protocol."""
import base64
import hashlib
import os
import secrets
from pathlib import PurePosixPath
from urllib.parse import urlencode, urlsplit
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from galaxy_cli.file_scope import source_file
from galaxy_cli.registry import ToolError
from ..auth import AuthFailure


def snapshot(files, user, relative):
    expected_size, expected_hash = files.inspect_source(user, relative)
    try:
        with source_file(files.workspace(user), relative) as descriptor:
            chunks, size = [], 0
            while chunk := os.read(descriptor, 512 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ValueError()
                chunks.append(chunk)
            data = b''.join(chunks)
        if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError()
        return data, PurePosixPath(relative).name
    except (ToolError, OSError, ValueError):
        raise AuthFailure('path_denied', '文件无法读取或内容已变化，请重新生成后发送。', 403) from None


async def upload(api, record, data, filename, authorize):
    key, filekey = secrets.token_bytes(16), secrets.token_hex(16)
    padder = PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    authorize()
    value = await api.request('getuploadurl', base=record['base'], token=record['token'], body={
        'filekey': filekey, 'media_type': 3, 'to_user_id': record['peer'], 'rawsize': len(data),
        'rawfilemd5': hashlib.md5(data).hexdigest(), 'filesize': len(encrypted),
        'no_need_thumb': True, 'aeskey': key.hex(), 'base_info': {'channel_version': '2.4.10', 'bot_agent': 'GalaxyAI/0.8.17'}})
    target = value.get('upload_full_url') or ('https://novac2c.cdn.weixin.qq.com/c2c/upload?' + urlencode(
        {'encrypted_query_param': value['upload_param'], 'filekey': filekey}) if value.get('upload_param') else '')
    parsed = urlsplit(target)
    if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.cdn.weixin.qq.com')
            or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
        raise AuthFailure('clawbot_upload_error', '微信未返回有效的附件上传地址。', 502)
    authorize()
    try:
        # No bot Authorization header is forwarded to the CDN. No automatic POST retry.
        response = await api.client.post(target, content=encrypted, headers={'Content-Type': 'application/octet-stream'})
        response.raise_for_status()
        download = response.headers.get('x-encrypted-param')
        if not download:
            raise ValueError()
    except (httpx.HTTPError, ValueError):
        raise AuthFailure('clawbot_upload_error', '微信附件上传未完成，尚未提交文件消息。', 502) from None
    return {'type': 4, 'file_item': {'file_name': filename, 'len': str(len(data)), 'media': {
        'encrypt_query_param': download, 'aes_key': base64.b64encode(key.hex().encode()).decode(), 'encrypt_type': 1}}}
