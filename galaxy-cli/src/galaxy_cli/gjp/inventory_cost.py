"""Fixed read-only SQL, decimal pricing, private checkpoints and file ingestion."""
import csv
import fcntl
import io
import json
import os
import re
import time
import unicodedata
import uuid
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import PurePosixPath

from .cost_schema import normalize
from .categories import fields as category_fields, query as query_categories
from ..file_scope import source_file, regular_file, fingerprint, root_directory
from ..registry import ToolError

BATCH_SIZE = 500
TTL = 7 * 86400


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def text_code(value, number_format=None):
    if value is None:
        return ''
    if isinstance(value, bool):
        raise ToolError('invalid_barcode', '条码不能是布尔值。')
    if isinstance(value, (int, float, Decimal)):
        numeric = number(value)
        if numeric is None or numeric != numeric.to_integral_value():
            raise ToolError('invalid_barcode', '条码数值无效，请将条码列保存为文本。')
        code = format(numeric, 'f').split('.')[0]
        if number_format and re.fullmatch(r'0{2,128}', number_format):
            code = code.zfill(len(number_format))
    elif isinstance(value, str):
        code = value.strip()
    else:
        raise ToolError('invalid_barcode', '条码列必须为文本或完整整数。')
    if len(code) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in code):
        raise ToolError('invalid_barcode', '条码包含不允许的内容。')
    return code


def clean_name(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def items_from_records(records, arguments):
    result, seen = [], set()
    column = arguments.get('barcode_column', '规格编码')
    for record in records:
        if isinstance(record, dict):
            code = text_code(record.get(column))
            name = str(record.get(arguments.get('name_column'), '') or '') if arguments.get('name_column') else ''
        else:
            code, name = text_code(record), ''
        if not code:
            continue
        key = (code, clean_name(name))
        if key not in seen:
            seen.add(key); result.append({'barcode': code, **({'product_name': name} if name else {})})
    return result


def file_items(arguments, frame):
    path = arguments['source_path']
    directory = frame['workspace_directory']
    if path.startswith('/account-files/'):
        path = 'library/' + path[len('/account-files/'):]
    elif path.startswith('/account-cache/'):
        path = path[len('/account-cache/'):]; directory = frame['cache_directory']
    elif path.startswith('/workspace/'):
        path = path[len('/workspace/'):]
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in ('.xlsx', '.csv', '.json', '.jsonl'):
        raise ToolError('unsupported_file', '请先将条码整理成XLSX、CSV、JSON或JSONL记录。')
    with source_file(directory, path) as fd:
        before = regular_file(fd)
        limit = frame.get('source_limit_bytes', 512 * 1024 * 1024)
        if before.st_size > limit:
            raise ToolError('source_size', '源文件超过当前服务器文件容量设置。')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            if suffix == '.xlsx':
                with zipfile.ZipFile(stream) as archive:
                    if sum(item.file_size for item in archive.infolist()) > limit:
                        raise ToolError('source_size', '工作簿解压后的内容超过服务器文件容量设置。')
                stream.seek(0)
                from openpyxl import load_workbook
                book = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
                try:
                    if arguments.get('sheet') and arguments['sheet'] not in book.sheetnames:
                        raise ToolError('source_column', '工作表不存在。')
                    sheet = book[arguments['sheet']] if arguments.get('sheet') else book.worksheets[0]
                    rows = sheet.iter_rows(min_row=arguments.get('header_row', 1))
                    header = [str(cell.value or '').strip() for cell in next(rows, [])]
                    column = arguments.get('barcode_column', '规格编码')
                    if header.count(column) != 1 or arguments.get('name_column') and header.count(arguments['name_column']) != 1:
                        raise ToolError('source_column', '条码列或商品名列不存在或重复，请确认列名和标题行。')
                    index = header.index(column)
                    name_index = header.index(arguments['name_column']) if arguments.get('name_column') else None
                    def records():
                        for row in rows:
                            if index >= len(row):continue
                            cell = row[index]
                            yield {column: text_code(cell.value, cell.number_format),
                                   **({arguments['name_column']: row[name_index].value if name_index < len(row) else ''} if name_index is not None else {})}
                    result = items_from_records(records(), arguments)
                finally:
                    book.close()
            elif suffix == '.csv':
                wrapper = io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')
                for _ in range(arguments.get('header_row', 1) - 1):next(wrapper, None)
                reader = csv.DictReader(wrapper)
                if not reader.fieldnames or reader.fieldnames.count(arguments.get('barcode_column', '规格编码')) != 1:
                    raise ToolError('source_column', 'CSV中未找到唯一的条码列，请确认列名及UTF-8编码。')
                if arguments.get('name_column') and reader.fieldnames.count(arguments['name_column']) != 1:
                    raise ToolError('source_column', 'CSV中未找到唯一的商品名列。')
                result = items_from_records(reader, arguments)
            elif suffix == '.json':
                records = json.load(stream)
                if not isinstance(records, list):
                    raise ToolError('source_format', 'JSON必须是条码或对象记录数组；嵌套接口结果请先用Python整理。')
                result = items_from_records(records, arguments)
            else:
                result = items_from_records((json.loads(line) for line in stream if line.strip()), arguments)
        if fingerprint(before) != fingerprint(regular_file(fd)):
            raise ToolError('source_changed', '读取时源文件已变化，请重新查询。')
    return result


def scope_sql(scope):
    where, params = 's.Deleted=0', []
    if scope.get('organization_id'):
        where += ' AND LEFT(s.BranchID,LEN(%s))=%s'
        params += [scope['organization_id']] * 2
    if scope.get('warehouse_id'):
        where += ' AND s.TypeID=%s'; params.append(scope['warehouse_id'])
    return where, params


class SqlClient:
    def __init__(self, credentials):
        self.credentials, self.connection = credentials, None

    def __enter__(self):
        try:
            import pymssql
        except ImportError:
            raise ToolError('gjp_cost_driver_missing', '服务器尚未安装共享数据库读取依赖。') from None
        self.driver = pymssql
        try:
            c = self.credentials
            self.connection = pymssql.connect(server=c['server'], port=str(c['port']), user=c['user'],
                password=c['password'], database=c['database'], charset='UTF-8', tds_version='7.4',
                encryption='require', read_only=True, login_timeout=15, timeout=30, as_dict=True,
                appname='Galaxy inventory cost SELECT only', autocommit=False)
        except pymssql.Error:
            raise ToolError('gjp_cost_connection_failed', '库存均价数据库连接失败，请管理员检查数据源及网络；不会改用其他账套。') from None
        return self

    def __exit__(self, *_):
        if self.connection is not None:
            self.connection.close()

    def query(self, barcodes, scope):
        where, params = scope_sql(scope)
        values = ','.join('(CAST(%s AS nvarchar(128)))' for _ in barcodes)
        sql = f'''WITH requested AS (SELECT barcode FROM (VALUES {values}) inp(barcode)),
        target AS (SELECT r.barcode AS requested_barcode,p.TypeID,p.BarCode,p.FullName FROM requested r JOIN dbo.Ptype p ON p.BarCode=r.barcode),
        inventory AS (SELECT g.PtypeId,COUNT_BIG(*) AS inventory_rows,
            SUM(CAST(g.Qty AS decimal(28,8))) AS qty,SUM(CAST(g.Total AS decimal(28,8))) AS amount,
            MIN(g.Price) AS min_stored_price,MAX(g.Price) AS max_stored_price
            FROM dbo.GoodsStocks g JOIN (SELECT DISTINCT TypeID FROM target) t ON t.TypeID=g.PtypeId
            JOIN dbo.Stock s ON s.TypeID=g.KtypeId WHERE {where} GROUP BY g.PtypeId)
        SELECT t.*,i.inventory_rows,i.qty,i.amount,i.min_stored_price,i.max_stored_price
        FROM target t LEFT JOIN inventory i ON i.PtypeId=t.TypeID ORDER BY t.requested_barcode,t.TypeID'''
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, tuple(barcodes) + tuple(params))
                rows = cursor.fetchall()
        except self.driver.Error:
            raise ToolError('gjp_cost_query_failed', '库存均价数据库查询失败或超时；已完成批次已保存，可使用resume_id续跑。') from None
        categories = query_categories(self, [str(row['TypeID']) for row in rows])
        return [{**row, **categories[str(row['TypeID'])]} for row in rows]


def price_row(row):
    qty, amount = number(row.get('qty')), number(row.get('amount'))
    status, cost = 'matched', None
    if qty is None or amount is None:
        status = 'no_inventory_record'
    elif qty < 0:
        status = 'negative_stock_review'
    elif amount < 0:
        status = 'negative_stock_amount_review'
    elif qty == 0:
        if amount == 0 and number(row.get('min_stored_price')) == 0 and number(row.get('max_stored_price')) == 0:
            status, cost = 'zero_stock', Decimal('0.00')
        else:
            status = 'zero_stock_cost_review'
    else:
        with localcontext() as context:
            context.prec = 50
            cost = (amount / qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {'product_id': str(row['TypeID']), 'product_name': row['FullName'],
            'quantity': format(qty, 'f') if qty is not None else None,
            'stock_amount': format(amount, 'f') if amount is not None else None,
            'avg_cost': format(cost, '.2f') if cost is not None else None, 'status': status,
            **{key: row.get(key, default) for key, default in category_fields('unavailable').items()}}


def match_item(item, candidates):
    if not candidates:
        return {'barcode': item['barcode'], 'avg_cost': None, 'status': 'not_found', **category_fields('not_found')}
    choices = candidates
    if len(choices) > 1 and item.get('product_name'):
        choices = [row for row in choices if clean_name(row['FullName']) == clean_name(item['product_name'])]
    if len(choices) != 1:
        return {'barcode': item['barcode'], 'avg_cost': None, 'status': 'ambiguous',
                **category_fields('ambiguous'),
                'candidates': [price_row(row) for row in candidates]}
    return {'barcode': item['barcode'], **price_row(choices[0])}


def save_state(root, identifier, state):
    temporary = identifier + '.tmp'
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root)
        try:
            regular_file(fd); os.ftruncate(fd, 0)
        except BaseException:
            os.close(fd); raise
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, identifier + '.json', src_dir_fd=root, dst_dir_fd=root); os.fsync(root)
    finally:
        try:os.unlink(temporary, dir_fd=root)
        except FileNotFoundError:pass


def result(state, error=None):
    rows = [{**category_fields('legacy_checkpoint'), **row} for row in state['rows']]
    counts = Counter(row['status'] for row in rows)
    complete = len(rows) == len(state['items'])
    priced = sum(row.get('avg_cost') is not None for row in rows)
    summary = {'total': len(state['items']), 'processed': len(rows), 'priced': priced,
               'not_found': counts['not_found'], 'ambiguous': counts['ambiguous'], 'zero_stock': counts['zero_stock'],
               'needs_review': len(rows) - priced, 'complete': complete, 'all_priced': complete and priced == len(rows),
               'batches': state['batches'], 'database': state['database'], 'scope': state['scope'],
               'started_at': state['created_at'], 'as_of_from': state.get('as_of_from'), 'as_of_to': state.get('as_of_to'),
               'price_precision': 2,
               'categorized': sum(row['category_status'] == 'matched' for row in rows),
               'category_needs_review': sum(row['category_status'] != 'matched' for row in rows)}
    return {'data': {'resume_id': state['id'], 'summary': summary, 'rows': rows, **({'error': error} if error else {})}}


def run(arguments, frame, client_factory=SqlClient):
    arguments = normalize(arguments)
    credentials = frame.get('categories', {}).get('gjp', {})
    identity = frame.get('cache_identity', {})
    identifier = arguments.get('resume_id') or frame.get('job_id')
    if (credentials.get('mode') != 'inventory-cost-database' or not re.fullmatch(r'[0-9a-f]{32}', str(identifier))
            or not all(isinstance(identity.get(k), str) and identity[k] for k in ('user_id', 'workspace_id'))
            or credentials.get('workspace_id') != identity['workspace_id']):
        raise ToolError('server_context_required', '库存均价需通过本人登录后的Galaxy CLI调用。')
    binding = {**identity, 'database': credentials['database'], 'source_revision': credentials['source_revision']}
    with root_directory(frame['job_directory']) as root:
        lock = os.open(identifier + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root)
        try:
            regular_file(lock)
            try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:raise ToolError('gjp_cost_busy', '该库存均价任务仍在执行，请等待完成或先停止原任务。') from None
            if 'resume_id' in arguments:
                try:
                    with source_file(frame['job_directory'], identifier + '.json') as fd, os.fdopen(os.dup(fd), 'r') as stream:
                        state = json.load(stream)
                except FileNotFoundError:
                    raise ToolError('gjp_cost_job_not_found', '本人任务进度不存在，请使用已有输入重新查询。') from None
                except ToolError:
                    raise ToolError('gjp_cost_job_not_found', '本人任务进度不存在或不可读取。') from None
                if state.get('binding') != binding or state.get('id') != identifier:
                    raise ToolError('gjp_cost_scope_changed', '账号、账套或数据源已变化，不能混用原任务结果，请重新查询。')
                if time.time() - state['updated_at'] > TTL:
                    raise ToolError('gjp_cost_job_expired', '任务进度已超过7天，请重新查询当前库存。')
            else:
                if 'source_path' in arguments:
                    items = file_items(arguments, frame)
                else:
                    items = items_from_records([{'barcode': row} if isinstance(row, str) else row for row in arguments['barcodes']],
                                               {'barcode_column': 'barcode', 'name_column': 'product_name'})
                if not items:raise ToolError('empty_barcodes', '输入中没有有效条码，未查询数据库。')
                now = time.time()
                state = {'id': identifier, 'binding': binding, 'database': credentials['database'],
                         'scope': {key: arguments[key] for key in ('organization_id', 'warehouse_id') if key in arguments},
                         'items': items, 'rows': [], 'batches': 0, 'created_at': now, 'updated_at': now}
                save_state(root, identifier, state)
            if len(state['rows']) == len(state['items']):return result(state)
            try:
                with client_factory(credentials) as client:
                    while len(state['rows']) < len(state['items']):
                        batch = state['items'][len(state['rows']):len(state['rows']) + BATCH_SIZE]
                        before = time.time()
                        fetched = client.query(list(dict.fromkeys(item['barcode'] for item in batch)), state['scope'])
                        by_code = defaultdict(list)
                        for row in fetched:by_code[row['requested_barcode']].append(row)
                        state['rows'].extend(match_item(item, by_code[item['barcode']]) for item in batch)
                        state['batches'] += 1; state['updated_at'] = time.time()
                        state.setdefault('as_of_from', before); state['as_of_to'] = state['updated_at']
                        save_state(root, identifier, state)
            except ToolError as error:
                return result(state, {'code': error.code, 'message': error.message})
            return result(state)
        except (UnicodeError, ValueError, KeyError, OSError, zipfile.BadZipFile):
            raise ToolError('gjp_cost_input_or_progress_invalid', '输入文件或本人任务进度无法读取，请核对文件格式及列名。') from None
        finally:
            os.close(lock)
