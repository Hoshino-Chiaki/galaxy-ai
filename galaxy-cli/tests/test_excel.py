import contextlib
import copy
import io
import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.excel.create import create, normalize

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
SPEC = {'title': '客户消费报告', 'subtitle': '演示数据', 'sheets': [{'name': '消费明细', 'columns': [
    {'key': 'name', 'title': '客户'}, {'key': 'phone', 'title': '手机号'},
    {'key': 'amount', 'title': '金额', 'type': 'currency'}, {'key': 'rate', 'title': '占比', 'type': 'percent'},
    {'key': 'day', 'title': '日期', 'type': 'date'}, {'key': 'active', 'title': '有效', 'type': 'boolean'}],
    'rows': [{'name': '=HYPERLINK("https://invalid.example")', 'phone': '0013800000000', 'amount': 12.50, 'rate': 0.25, 'day': '2026-11-08', 'active': False},
             {'name': 'https://invalid.example', 'phone': None, 'amount': 0, 'rate': None}],
    'charts': [{'type': 'column', 'category': 'name', 'values': ['amount'], 'title': '消费金额'}]}]}


class ExcelTests(unittest.TestCase):
    def test_workbook_preserves_types_text_and_visual_features(self):
        with tempfile.TemporaryDirectory() as directory:
            result = create(copy.deepcopy(SPEC), directory)
            path = Path(directory) / result['path']
            self.assertEqual(result['size'], path.stat().st_size)
            self.assertEqual(result['filename'], '客户消费报告.xlsx')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with zipfile.ZipFile(path) as z:
                sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
                self.assertIsNotNone(sheet.find('s:sheetViews/s:sheetView/s:pane', NS))
                self.assertIsNotNone(sheet.find('s:autoFilter', NS))
                self.assertIsNotNone(sheet.find('s:mergeCells', NS))
                self.assertFalse(sheet.findall('.//s:f', NS))
                self.assertFalse(sheet.findall('.//s:hyperlink', NS))
                self.assertEqual(sheet.find('.//s:c[@r="F5"]', NS).get('t'), 'b')
                self.assertEqual(sheet.find('.//s:c[@r="F5"]/s:v', NS).text, '0')
                self.assertEqual(sheet.find('.//s:c[@r="C6"]/s:v', NS).text, '0')
                strings = z.read('xl/sharedStrings.xml').decode()
                self.assertIn('0013800000000', strings)
                self.assertIn('HYPERLINK', strings)
                self.assertIn('xl/charts/chart1.xml', z.namelist())

    def test_rejects_invalid_names_keys_types_dates_and_chart_references(self):
        mutations = [lambda s: s['sheets'][0].update(name='../private'),
            lambda s: s['sheets'].append(copy.deepcopy(s['sheets'][0])),
            lambda s: s['sheets'][0]['rows'][0].update(phone=13800000000),
            lambda s: s['sheets'][0]['rows'][0].update(amount=True),
            lambda s: s['sheets'][0]['rows'][0].update(amount=10 ** 1000),
            lambda s: s['sheets'][0]['rows'][0].update(day='2026-02-30'),
            lambda s: s['sheets'][0]['rows'][0].update(extra='unexpected'),
            lambda s: s['sheets'][0]['charts'][0].update(values=['phone']),
            lambda s: s.update(output_directory='/tmp/outside')]
        for mutate in mutations:
            spec = copy.deepcopy(SPEC)
            mutate(spec)
            with self.assertRaises(ToolError): normalize(spec)

    def test_pipe_input_and_output_have_no_local_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            out = io.StringIO()
            with patch('sys.stdin', io.StringIO(json.dumps({'input': SPEC, 'workspace_directory': directory}))), contextlib.redirect_stdout(out):
                result = main(['excel', 'create', '--input-stdin'])
            self.assertEqual(result, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(set(data), {'path', 'filename', 'size', 'sheets'})
            self.assertNotIn(directory, out.getvalue())

    def test_descriptions_only_explain_input_output(self):
        for tool in load_registry().invoke('help', {})['data']:
            self.assertNotIn('HAR', tool['description'])
            self.assertNotIn('抓包', tool['description'])

    def test_multiple_sheets_empty_rows_and_pie_chart(self):
        spec = copy.deepcopy(SPEC)
        spec['sheets'][0]['charts'][0]['type'] = 'pie'
        spec['sheets'].append({'name': '空表', 'columns': [{'key': 'a', 'title': '空列'}], 'rows': []})
        with tempfile.TemporaryDirectory() as directory:
            result = create(spec, directory)
            self.assertEqual(result['sheets'], [{'name': '消费明细', 'rows': 2}, {'name': '空表', 'rows': 0}])
