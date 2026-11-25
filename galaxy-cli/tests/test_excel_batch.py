import copy
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from galaxy_cli.excel.engine import inspect_file, transform_files
from galaxy_cli.excel.schema import normalize
from galaxy_cli.registry import ToolError, load_registry


@pytest.fixture
def files(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    cache = tmp_path / 'cache'; cache.mkdir()
    (workspace / 'library').mkdir()
    (workspace / 'sales.csv').write_text('条码,商品,数量,成交金额,部门,部门\n001,甲,2,19.95,A,B\n002,乙,-3,-12.50,C,D\n003,丙,0,0,,\n004,丁,-,5,,\n,戊,1,1,,\n', encoding='utf-8')
    (cache / 'cost.json').write_text(json.dumps([{'code': '001', 'price': '0.1'}, {'code': '002', 'price': '2.35'},
        {'code': '003', 'price': '0'}, {'code': None, 'price': '999'}]), encoding='utf-8')
    return {'workspace_directory': str(workspace), 'cache_directory': str(cache)}


def plan():
    return {'sources': {'sales': {'path': '/workspace/sales.csv'}, 'cost': {'path': '/account-cache/cost.json'}},
        'steps': [
            {'op': 'lookup', 'table': 'sales', 'from': 'cost', 'into': 'matched', 'left_on': ['条码'], 'right_on': ['code'],
             'columns': {'price': '均价'}, 'match_column': '有匹配'},
            {'op': 'calculate', 'table': 'matched', 'into': 'detail', 'columns': [
                {'name': '成本', 'expression': 'p*q', 'variables': {'p': '均价', 'q': '数量'}, 'status_column': '计算状态'},
                {'name': '毛利', 'expression': 'amount-cost', 'variables': {'amount': '成交金额', 'cost': '成本'}}]}],
        'exports': [{'table': 'detail', 'sheet': '完整明细', 'totals': ['数量', '成交金额', '成本', '毛利'], 'formats': {'成本': 'currency'}},
                    {'table': 'sales', 'sheet': '原始明细'}], 'filename': '测试.xlsx'}


def test_decimal_finance_zero_negative_missing_and_complete_rows(files):
    source_before = (os.path.join(files['workspace_directory'], 'sales.csv'))
    before = open(source_before, 'rb').read()
    receipt = transform_files(normalize(plan(), 'transform'), files)
    assert receipt['sheets'][0]['rows'] == 5
    assert receipt['summary'][0]['details'] == {'matched': 3, 'unmatched': 2, 'duplicate_source_rows': 0}
    assert receipt['sheets'][0]['totals']['成本'] == {'sum': '-6.85', 'numeric': 3, 'blank': 2, 'invalid': 0}
    assert len(json.dumps(receipt)) < 2400
    output = os.path.join(files['workspace_directory'], receipt['path'])
    with open(output, 'rb') as stream:
        book = load_workbook(stream, data_only=False)
        rows = list(book['完整明细'].values); columns = rows[0]
        data = [dict(zip(columns, row)) for row in rows[1:]]
        assert [row['条码'] for row in data] == ['001', '002', '003', '004', None]
        assert [row['成本'] for row in data] == [.2, -7.05, 0, None, None]
        assert data[0]['毛利'] == 19.75 and data[1]['毛利'] == -5.45
        assert data[3]['计算状态'] == 'missing_value'
        assert data[-1]['均价'] is None  # empty keys never match
        assert book['完整明细'].freeze_panes == 'A2'
        book.close()
    assert open(source_before, 'rb').read() == before


def test_inspect_headers_preview_and_decimal_totals(files):
    value = inspect_file({'source': {'path': 'sales.csv'}, 'totals': ['成交金额', '数量'], 'preview_rows': 1}, files)
    assert value['rows'] == 5
    assert value['columns'][-2:] == ['部门', '部门__2']
    assert value['preview'][0]['条码'] == '001'
    assert value['totals']['成交金额']['sum'] == '13.45'
    assert value['totals']['数量']['invalid'] == 1


def test_duplicate_lookup_stops_or_joins_without_multiplying_rows(files):
    path = os.path.join(files['cache_directory'], 'cost.json')
    with open(path, 'w') as out: json.dump([{'code': '001', 'price': 'A'}, {'code': '001', 'price': 'B'}], out)
    value = plan(); value['steps'] = value['steps'][:1]; value['exports'] = [{'table': 'matched', 'sheet': '明细'}]
    with pytest.raises(ToolError, match='重复键') as error:
        transform_files(value, files)
    assert error.value.code == 'excel_duplicate_keys'
    value['steps'][0]['multiple'] = 'join'
    receipt = transform_files(value, files)
    detail = inspect_file({'source': {'path': receipt['path']}, 'preview_rows': 1}, files)
    assert detail['rows'] == 5 and detail['preview'][0]['均价'] == 'A、B'


def test_filter_select_concat_and_safe_formula_text(files):
    value = plan()
    value['steps'] = [
        {'op': 'filter', 'table': 'sales', 'into': 'negative', 'conditions': [{'column': '数量', 'operator': '<', 'value': 0}]},
        {'op': 'select', 'table': 'negative', 'into': 'small', 'columns': ['条码', '数量'], 'rename': {'条码': '规格编码'}},
        {'op': 'concat', 'tables': ['small', 'sales'], 'into': 'combined'}]
    value['exports'] = [{'table': 'combined', 'sheet': '追加'}]
    receipt = transform_files(value, files)
    assert receipt['sheets'][0]['rows'] == 6
    (Path(files['workspace_directory']) / 'formula.csv').write_text('a\n=HYPERLINK("https://example.com")\n')
    receipt = transform_files({'sources': {'a': {'path': 'formula.csv'}}, 'exports': [{'table': 'a', 'sheet': '文本'}]}, files)
    with open(os.path.join(files['workspace_directory'], receipt['path']), 'rb') as stream:
        book = load_workbook(stream); assert book['文本']['A2'].data_type == 's'; book.close()


@pytest.mark.parametrize('expression', ['__import__("os").system("id")', 'open(x)', 'x**1000', 'x[0]', '(lambda: x)()', 'sqrt(x)'])
def test_expressions_do_not_execute_code(files, expression):
    value = plan(); value['steps'] = [{'op': 'calculate', 'table': 'sales', 'into': 'a', 'columns': [
        {'name': '计算', 'expression': expression, 'variables': {'x': '数量'}}]}]
    value['exports'] = [{'table': 'a', 'sheet': 'a'}]
    with pytest.raises(ToolError): transform_files(value, files)


@pytest.mark.parametrize('path', ['../other/secret.xlsx', '/etc/passwd', 'https://example.com/a.xlsx', '/account-cache/../other/a.json', 'a%2fb.csv'])
def test_paths_are_scoped(path):
    with pytest.raises(ToolError): normalize({'source': {'path': path}}, 'inspect')


def test_symlinks_hardlinks_and_missing_cache_denied(files, tmp_path):
    root = Path(files['workspace_directory'])
    other = tmp_path / 'other.csv'; other.write_text('a\nsecret\n')
    (root / 'link.csv').symlink_to(other)
    os.link(other, root / 'hard.csv')
    (root / 'jump').symlink_to(tmp_path, target_is_directory=True)
    for path in ['link.csv', 'hard.csv', 'jump/other.csv']:
        with pytest.raises(ToolError) as error: inspect_file({'source': {'path': path}}, files)
        assert error.value.code == 'path_denied'


def test_nested_json_and_jsonl_sources_and_upload_alias(files):
    root = Path(files['workspace_directory'])
    (root / 'library' / 'nested.json').write_text('{"data":{"items":[{"info":{"code":"001"},"amount":0.1},{"info":{"code":"002"}}]}}')
    value = inspect_file({'source': {'path': '/account-files/nested.json', 'record_path': 'data.items',
        'columns': {'条码': 'info.code', '金额': 'amount'}}, 'totals': ['金额']}, files)
    assert value['rows'] == 2 and value['totals']['金额']['sum'] == '0.1'
    (root / 'records.jsonl').write_text('{"code":"001","n":1}\n{"code":"002","n":0}\n')
    assert inspect_file({'source': {'path': 'records.jsonl'}}, files)['rows'] == 2


def test_large_table_stays_on_disk_with_small_receipt(files):
    root = Path(files['workspace_directory'])
    with (root / 'large.csv').open('w') as out:
        out.write(','.join('c'+str(i) for i in range(32))+'\n')
        for i in range(6513): out.write(','.join(str(i) for _ in range(32))+'\n')
    result = transform_files({'sources': {'a': {'path': 'large.csv'}}, 'exports': [{'table': 'a', 'sheet': '完整明细'}]}, files)
    assert result['sheets'][0]['rows'] == 6513 and len(json.dumps(result)) < 700
    assert inspect_file({'source': {'path': result['path']}, 'preview_rows': 0}, files)['rows'] == 6513


def test_registered_input_output_schemas():
    registry = load_registry()
    assert registry.get('excel.inspect').category == 'excel'
    assert registry.get('excel.transform').category == 'excel'


def test_rounding_division_by_zero_and_missing_are_auditable(files):
    root = Path(files['workspace_directory'])
    (root / 'round.csv').write_text('price,qty,amount\n1.005,1,9\n0,0,0\n,0,3\n-,1,1\n')
    value = {'sources': {'a': {'path': 'round.csv'}}, 'steps': [{'op': 'calculate', 'table': 'a', 'into': 'a', 'columns': [
        {'name': 'cost', 'expression': 'p*q', 'variables': {'p': 'price', 'q': 'qty'}, 'status_column': 'reason'},
        {'name': 'unit', 'expression': 'a/q', 'variables': {'a': 'amount', 'q': 'qty'}, 'status_column': 'unit_reason'}]}],
        'exports': [{'table': 'a', 'sheet': 'a'}]}
    result = transform_files(value, files)
    checked = inspect_file({'source': {'path': result['path']}, 'preview_rows': 4}, files)
    assert checked['preview'][0]['cost'] == 1.01
    assert checked['preview'][1]['cost'] == 0
    assert checked['preview'][2]['cost'] is None and checked['preview'][2]['reason'] == 'missing_value'
    assert checked['preview'][3]['cost'] is None and checked['preview'][3]['reason'] == 'invalid_number'
    assert checked['preview'][1]['unit_reason'] == 'division_by_zero'


def test_blank_trailing_headers_and_duplicate_names_preserve_all_cells(files):
    root = Path(files['workspace_directory'])
    from openpyxl import Workbook
    b = Workbook(); ws = b.active
    ws.append(['姓名', '部门', '部门']); ws.append(['甲', 'A', 'B', None, '旁列备注'])
    b.save(root/'headers.xlsx'); b.close()
    result = inspect_file({'source': {'path': 'headers.xlsx'}}, files)
    assert result['columns'] == ['姓名','部门','部门__2','未命名列4','未命名列5']
    assert result['preview'][0]['未命名列5'] == '旁列备注'


def test_preview_is_bounded_without_shortening_saved_data(files):
    root = Path(files['workspace_directory'])
    text = '长文本'*1000
    (root/'long.json').write_text(json.dumps([{'note': text}], ensure_ascii=False))
    inspected = inspect_file({'source': {'path': 'long.json'}}, files)
    assert inspected['preview_truncated'] and len(inspected['preview'][0]['note']) == 201
    result = transform_files({'sources': {'a': {'path': 'long.json'}}, 'exports': [{'table':'a','sheet':'a'}]}, files)
    b=load_workbook(root/result['path']); assert b.active['A2'].value == text; b.close()
