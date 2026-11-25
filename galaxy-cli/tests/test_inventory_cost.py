import io
import json
import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from galaxy_cli.gjp.cost_schema import normalize
from galaxy_cli.gjp.inventory_cost import run, price_row, SqlClient, file_items
from galaxy_cli.registry import ToolError, load_registry


def record(code, **values):
    return {'requested_barcode': code, 'TypeID': 'id-' + code, 'BarCode': code, 'FullName': '商品-' + code,
            'qty': Decimal('36'), 'amount': Decimal('313.77'), 'min_stored_price': Decimal('0'),
            'max_stored_price': Decimal('8.72'), **values}


@pytest.fixture
def frame(tmp_path):
    folders = {}
    for key in ('workspace_directory', 'cache_directory', 'job_directory'):
        root = tmp_path / key; root.mkdir(); folders[key] = str(root)
    return {**folders, 'job_id': uuid.uuid4().hex, 'cache_identity': {'user_id': 'alice', 'workspace_id': 'w'},
            'categories': {'gjp': {'mode': 'inventory-cost-database', 'database': 'book', 'workspace_id': 'w',
                                   'source_revision': 'source-v1'}}}


class Client:
    calls = []
    def __init__(self, _):pass
    def __enter__(self):return self
    def __exit__(self, *_):pass
    def query(self, barcodes, scope):
        self.calls.append((barcodes, scope))
        return [record(code) for code in barcodes]


def test_decimal_rounding_zero_and_exceptional_inventory():
    assert price_row(record('a'))['avg_cost'] == '8.72'
    assert price_row(record('a', qty=Decimal('2'), amount=Decimal('2.01')))['avg_cost'] == '1.01'
    zero = price_row(record('a', qty=0, amount=0, max_stored_price=0))
    assert zero['avg_cost'] == '0.00' and zero['status'] == 'zero_stock'
    for changes in ({'qty': -1}, {'qty': 0, 'amount': 1}, {'qty': None}, {'qty': 0, 'amount': 0}, {'amount': -1}):
        assert price_row(record('a', **changes))['avg_cost'] is None
    assert price_row(record('a', amount=0))['avg_cost'] == '0.00'


def test_bulk_deduplication_and_only_unfinished_batches_are_resumed(frame):
    calls = []
    class FailsOnce(Client):
        def query(self, codes, scope):
            calls.append(codes)
            if len(calls) == 2:raise ToolError('test_connection_failed', 'fixture failure')
            return [record(c) for c in codes]
    arguments = {'barcodes': [str(i).zfill(8) for i in range(1001)] + ['00000000']}
    first = run(arguments, frame, FailsOnce)['data']
    assert first['summary']['total'] == 1001 and first['summary']['processed'] == 500
    assert first['summary']['complete'] is False and 'error' in first
    after = run({'resume_id': first['resume_id']}, frame, FailsOnce)['data']
    assert after['summary']['complete'] and after['summary']['processed'] == 1001
    assert calls[2][0] == '00000500' and calls[-1] == ['00001000']
    before = len(calls)
    assert run({'resume_id': first['resume_id']}, frame, FailsOnce)['data']['summary']['complete']
    assert len(calls) == before


def test_cross_account_source_and_book_changes_do_not_resume(frame, tmp_path):
    first = run({'barcodes': ['001']}, frame, Client)['data']
    for key in ('database', 'source_revision'):
        other = {**frame, 'categories': {'gjp': {**frame['categories']['gjp'], key: 'changed'}}}
        with pytest.raises(ToolError) as error:run({'resume_id': first['resume_id']}, other, Client)
        assert error.value.code == 'gjp_cost_scope_changed'
    other = {**frame, 'cache_identity': {'user_id': 'bob', 'workspace_id': 'w'}}
    with pytest.raises(ToolError):run({'resume_id': first['resume_id']}, other, Client)
    separate = tmp_path / 'bob'; separate.mkdir()
    with pytest.raises(ToolError):run({'resume_id': first['resume_id']}, {**other, 'job_directory': str(separate)}, Client)


def test_duplicate_barcodes_require_unique_exact_product_name(frame):
    class Multi(Client):
        def query(self, codes, scope):return [record('001', TypeID='a', FullName='甲'), record('001', TypeID='b', FullName='乙')]
    rows = run({'barcodes': ['001', {'barcode': '001', 'product_name': '乙'},
                              {'barcode': '001', 'product_name': '不匹配'}]}, frame, Multi)['data']['rows']
    assert rows[0]['status'] == rows[2]['status'] == 'ambiguous'
    assert rows[0]['avg_cost'] is None and len(rows[0]['candidates']) == 2
    assert rows[1]['product_id'] == 'b'


def test_file_inputs_preserve_leading_zeros_and_are_bound_to_own_roots(frame, tmp_path):
    workspace = Path(frame['workspace_directory']); (workspace / 'library').mkdir()
    (workspace / 'library' / 'codes.csv').write_text('规格编码,名称\n00123,甲\n00123,甲\n,空\n', encoding='utf-8-sig')
    args = {'source_path': '/account-files/codes.csv', 'name_column': '名称'}
    assert file_items(args, frame) == [{'barcode': '00123', 'product_name': '甲'}]
    from openpyxl import Workbook
    book = Workbook();sheet = book.active;sheet.append(['规格编码']);sheet.append([123]);sheet['A2'].number_format = '000000'
    book.save(workspace / 'codes.xlsx')
    assert file_items({'source_path': 'codes.xlsx'}, frame)[0]['barcode'] == '000123'
    (workspace / 'codes.json').write_text('["001","002"]')
    assert len(file_items({'source_path': 'codes.json'}, frame)) == 2
    (workspace / 'codes.jsonl').write_text('{"规格编码":"001"}\n{"规格编码":"002"}\n')
    assert len(file_items({'source_path': 'codes.jsonl'}, frame)) == 2
    secret = tmp_path / 'outside.csv';secret.write_text('规格编码\nsecret\n')
    (workspace / 'link.csv').symlink_to(secret)
    with pytest.raises(ToolError):file_items({'source_path': 'link.csv'}, frame)
    os.link(secret, workspace / 'hard.csv')
    with pytest.raises(ToolError):file_items({'source_path': 'hard.csv'}, frame)
    for path in ('../outside.csv', '/etc/passwd', '/account-files/../outside.csv'):
        with pytest.raises(ToolError):normalize({'source_path': path})


def test_sql_uses_bound_parameters_and_unique_product_ids_before_aggregation():
    captured = []
    class Cursor:
        def __enter__(self):return self
        def __exit__(self, *_):pass
        def execute(self, sql, params):captured.append((sql, params))
        def fetchall(self):return []
    client = SqlClient({});client.connection = type('Connection', (), {'cursor': lambda self: Cursor()})()
    client.driver = type('Driver', (), {'Error': RuntimeError})
    attack = "x'); DROP TABLE Ptype;--"
    client.query([attack, '001'], {'organization_id': '00010', 'warehouse_id': '00020'})
    sql, params = captured[0]
    assert attack not in sql and params == (attack, '001', '00010', '00010', '00020')
    assert 'SELECT DISTINCT TypeID FROM target' in sql
    assert not any(word in sql.upper() for word in ('UPDATE ', 'DELETE ', 'INSERT ', 'NOLOCK'))


def test_contract_rejects_overrides_and_exposes_registered_tool():
    for body in ({'barcodes': ['001'], 'sql': 'select 1'}, {'barcodes': ['001'], 'database': 'other'},
                 {'barcodes': ['001'], 'source_path': 'x.csv'}, {'resume_id': 'a' * 32, 'warehouse_id': '01'},
                 {'barcodes': ['001'], 'organization_id': "1' OR 1=1"}):
        with pytest.raises(ToolError):normalize(body)
    assert '保留零库存' in load_registry().get('gjp.inventory-cost').description
