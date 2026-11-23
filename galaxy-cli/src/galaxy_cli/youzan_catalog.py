"""Fixed merchant endpoints and private per-account material metadata."""
import copy
import fcntl
import hashlib
import io
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from contextlib import contextmanager

from PIL import Image
from .context import read_context, category_credentials
from .file_scope import root_directory, child_directory, source_file, regular_file, fingerprint
from .registry import ToolError
from .youzan_http import YouzanClient, NoRedirect

NAMES = ('material-category', 'material-list', 'material-upload', 'product-get', 'product-options', 'product-update', 'product-search', 'product-group', 'order-search')
ACTIONS = {'order-search': ('options', 'query'), 'material-category': ('list', 'create', 'rename', 'move'),
    'product-options': ('groups', 'sku-values', 'detail-templates', 'delivery', 'shops'), 'product-update': ('preview', 'apply'), 'product-search': ('options','query'),
    'product-group': ('list', 'get', 'stores', 'create')}
OUTPUT = {'type': 'object'}
ID = {'type': 'string', 'pattern': r'^[1-9][0-9]{0,19}$'}
IDS = {'type': 'array', 'items': ID, 'uniqueItems': True}
BOOL = {'type': 'boolean'}
TEXT = {'type': 'string', 'maxLength': 200}
MATERIAL = '/v4/materials/attachment/'
PRODUCT = '/v4/goods/publish/api/'


def object_schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def invalid(message):
    raise ToolError('invalid_input', message)


def require(context, *permissions):
    if not set(permissions).issubset(context.get('permissions', [])):
        raise ToolError('permission_denied', '当前账号没有执行此操作的权限。', 1)


def permissions(name, arguments):
    if name == 'order-search': return {'youzan:order:read'}
    if name == 'material-category':
        return {'youzan:material:read', *({'youzan:material:category:create'} if arguments['action'] == 'create' else
            {'youzan:material:category:update'} if arguments['action'] in ('rename', 'move') else set())}
    if name == 'material-list': return {'youzan:material:read'}
    if name == 'material-upload': return {'youzan:material:read', 'youzan:material:upload'}
    needed = {'youzan:product:read'}
    if name == 'product-group' and arguments['action'] == 'create':
        needed.add('youzan:product:update')
        if arguments.get('content_html'): needed.add('youzan:material:read')
    if name == 'product-update':
        changes = arguments['changes']
        if changes.keys() & {'image_ids', 'append_image_ids'}: needed.add('youzan:material:read')
        if arguments['action'] == 'apply':
            needed.add('youzan:product:update')
            if changes.keys() & {'price', 'origin_price'} or any('price' in row for row in changes.get('sku_updates', [])):
                needed.add('youzan:product:price:update')
            if any('stock_num' in row for row in changes.get('sku_updates', [])): needed.add('youzan:product:stock:update')
            if changes.keys() & {'is_display', 'sale_scope'}: needed.add('youzan:product:display:update')
    return needed


class State:
    def __init__(self, context, shop):
        self.workspace = context.get('workspace_directory')
        self.shop = str(shop)
        if not isinstance(self.workspace, str) or not self.shop.isdecimal():
            raise ToolError('invalid_context', '素材工作目录不可用。', 1)

    @contextmanager
    def transaction(self):
        with root_directory(self.workspace) as root, child_directory(root, ['.youzan'], create=True) as directory:
            lock = os.open(self.shop + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            regular_file(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                name = self.shop + '.json'
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                    try:
                        regular_file(fd)
                        with os.fdopen(os.dup(fd), 'rb') as stream: state = json.load(stream)
                    finally: os.close(fd)
                except FileNotFoundError: state = {'assets': {}}
                if not isinstance(state, dict) or not isinstance(state.get('assets'), dict):
                    raise ToolError('invalid_storage', '素材索引不可用。', 1)
                yield state
                temporary = '.' + uuid.uuid4().hex + '.part'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(state, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                finally:
                    try: os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError: pass
            finally: os.close(lock)

    def remember(self, rows):
        with self.transaction() as state:
            for row in rows:
                identifier = row.get('attachmentId', row.get('attachment_id', row.get('id')))
                url = row.get('attachmentFullUrl', row.get('attachment_full_url', row.get('url', row.get('attachmentUrl', row.get('attachment_url')))))
                if str(identifier).isdecimal() and isinstance(url, str):
                    parts = urllib.parse.urlsplit(url)
                    if parts.scheme == 'https' and (parts.hostname or '').endswith('.yzcdn.cn') and not parts.username:
                        state['assets'][str(identifier)] = {'id': int(identifier), 'url': url,
                            'width': row.get('width', 0), 'height': row.get('height', 0)}

    def asset(self, identifier):
        with self.transaction() as state: value = copy.deepcopy(state['assets'].get(identifier))
        if not value: raise ToolError('material_not_found', '素材未在当前店铺的查询或上传结果中找到，请先查询对应分类或上传。', 1)
        return value


@contextmanager
def write_lock(context, key):
    directory = context.get('catalog_lock_directory')
    if not isinstance(directory, str): raise ToolError('invalid_context', '商品写入锁目录不可用。', 1)
    with root_directory(directory) as root:
        fd = os.open(hashlib.sha256(key.encode()).hexdigest() + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
        try:
            regular_file(fd); fcntl.flock(fd, fcntl.LOCK_EX); yield
        finally: os.close(fd)


def success(result):
    return isinstance(result, dict) and result.get('code') == 0 and 'error' not in result


def must(result):
    if not success(result):
        # Upstream text is untrusted; no request details or credential values are echoed here.
        raise ToolError('upstream_rejected', '有赞未通过当前请求，请检查店铺权限、商品类型和分类连接状态。', 1)
    return result.get('data')


class CatalogClient(YouzanClient):
    def __init__(self, credentials, context):
        super().__init__({**credentials, 'origin': 'https://store.youzan.com'})
        self.context = context
        self.state = State(context, credentials['kdt_id'])

    def request(self, path, *, params=None, body=None, method='GET', form=False, host='store.youzan.com'):
        # Call sites select fixed routes. No model input is ever used as path/host/method.
        if host not in ('store.youzan.com', 'materials.youzan.com') or not path.startswith(('/v4/materials/', '/v4/goods/', '/shop/v2/showcase/skuProperty/', '/v4/shop/api/search/','/v2/goods/api/store/getAllStores')):
            raise ToolError('endpoint_denied', '不允许访问该接口。', 1)
        query = dict(params or {})
        headers = {'Accept': 'application/json', 'Origin': 'https://store.youzan.com',
            'Referer': 'https://store.youzan.com/v4/materials/attachment' if '/materials/' in path else 'https://store.youzan.com/v4/goods/shop-publish',
            'User-Agent': 'Mozilla/5.0', 'csrf-token': self.credentials['csrf']}
        if path.startswith(('/v4/goods/tag/', '/v4/goods/api/tag/')):
            headers['Referer'] = 'https://store.youzan.com/v4/goods/shop-tag/index'
        raw = None
        if method == 'GET': query['csrf_token'] = self.credentials['csrf']
        else:
            payload = {**(body or {}), 'csrf_token': self.credentials['csrf']}
            raw = urllib.parse.urlencode(payload).encode() if form else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        url = 'https://' + host + path + ('?' + urllib.parse.urlencode(query) if query else '')
        request = urllib.request.Request(url, data=raw, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024: raise ToolError('upstream_limit', '有赞响应过大，请缩小查询范围。', 1)
            value = json.loads(raw)
            if not isinstance(value, dict): raise ValueError()
            return self.redact(value)
        except urllib.error.HTTPError as error:
            raise ToolError('credential_expired' if error.code in (301,302,303,307,308,401) else 'upstream_error',
                '有赞商家接口未通过，请检查当前店铺并刷新对应商家会话。', 1) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ToolError('upstream_error', '有赞请求未完成；写操作请先回读核对，不要盲目重试。', 1) from None

    def categories(self):
        return self.request(MATERIAL + 'multi-category/tile-query.json', params={'mediaType': 1})

    def category(self, identifier, listing=None):
        rows = must(listing if listing is not None else self.categories())
        row = next((r for r in rows if str(r.get('categoryId')) == identifier), None) if isinstance(rows, list) else None
        if row is None: raise ToolError('category_not_found', '分类不属于当前店铺，请先查询当前店铺素材分类。', 1)
        return row

    def product(self, identifier):
        result = self.request(PRODUCT + 'item/get', params={'id': identifier, 'withoutSubItemDisplay': 'true'})
        if success(result):
            value = result.get('data')
            if (not isinstance(value, dict) or str(value.get('id')) != identifier or str(value.get('kdtId')) != self.credentials['kdt_id']):
                raise ToolError('product_not_found', '商品不属于当前连接的店铺。', 1)
            self.state.remember(value.get('picture', []))
        return result


def client_context(name, arguments):
    context = read_context()
    require(context, *permissions(name, arguments))
    credentials=context.get('categories',{}).get('youzan')
    if not isinstance(credentials,dict) or not all(isinstance(credentials.get(k),str) and credentials[k] for k in ('sid','csrf','kdt_id')):
        raise ToolError('connection_required','当前用户有赞分类凭证不完整，请手动刷新。',1)
    return CatalogClient(credentials, context), context


def public_image_url(value):
    try:
        url = urllib.parse.urlsplit(value)
        host = url.hostname
        if (url.scheme not in ('http','https') or not host or url.username or url.password or url.fragment
                or url.port not in (None,80,443) or '\\' in value or any(ord(c)<33 or ord(c)==127 for c in value)):
            raise ValueError()
        host = host.encode('idna').decode('ascii')
        addresses = socket.getaddrinfo(host, url.port or (443 if url.scheme=='https' else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses): raise ValueError()
        return value
    except (ValueError, UnicodeError, OSError):
        raise ToolError('invalid_image_url', '图片地址必须是可访问的公网 HTTP/HTTPS 地址，不能指向本机、私网或带登录凭证。') from None


def image_bytes(directory, relative):
    with source_file(directory, relative) as fd:
        before = regular_file(fd)
        if not 0 < before.st_size <= 20*1024*1024: raise ToolError('invalid_image', '图片须为 1 字节至 20 MB。')
        chunks=[];total=0
        while chunk:=os.read(fd,min(512*1024,before.st_size+1-total)):
            chunks.append(chunk);total+=len(chunk)
            if total>before.st_size:break
        raw=b''.join(chunks)
        if len(raw) != before.st_size or fingerprint(before) != fingerprint(regular_file(fd)):
            raise ToolError('file_changed', '图片在读取期间发生变化。', 1)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                kind, dimensions = image.format, image.size
                if kind not in ('JPEG','PNG','GIF','WEBP'): raise ValueError()
                image.verify()
            with Image.open(io.BytesIO(raw)) as image: image.load()
        return raw, kind, dimensions
    except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ToolError('invalid_image', '文件不是有效的 JPEG/PNG/GIF/WebP 图片，或图片像素超过安全处理范围。') from None
