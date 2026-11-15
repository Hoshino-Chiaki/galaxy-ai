"""Structured workbook export, with server-controlled output storage."""
import datetime as dt
import math
import os
import re
import uuid

import xlsxwriter
from jsonschema import Draft202012Validator

from ...file_scope import root_directory, child_directory
from ...context import read_context
from ...registry import ToolError

TYPES = ['text', 'number', 'integer', 'currency', 'percent', 'date', 'datetime', 'boolean']
SCALAR = {'type': ['string', 'number', 'boolean', 'null'], 'maxLength': 32767}
COLUMN = {'type': 'object', 'properties': {
    'key': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'title': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'type': {'enum': TYPES, 'default': 'text'},
    'width': {'type': 'number', 'minimum': 8, 'maximum': 60}},
    'required': ['key', 'title'], 'additionalProperties': False}
CHART = {'type': 'object', 'properties': {
    'type': {'enum': ['column', 'bar', 'line', 'pie']},
    'title': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'category': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'values': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'uniqueItems': True, 'items': {'type': 'string'}}},
    'required': ['type', 'category', 'values'], 'additionalProperties': False}
INPUT = {'type': 'object', 'properties': {
    'title': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    'subtitle': {'type': 'string', 'maxLength': 500},
    'sheets': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {
        'type': 'object', 'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 31},
            'columns': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'items': COLUMN},
            'rows': {'type': 'array', 'maxItems': 10000, 'items': {'type': 'object', 'additionalProperties': SCALAR}},
            'charts': {'type': 'array', 'maxItems': 4, 'items': CHART}},
        'required': ['name', 'columns', 'rows'], 'additionalProperties': False}}},
    'required': ['title', 'sheets'], 'additionalProperties': False}
OUTPUT = {'type': 'object', 'properties': {
    'path': {'type': 'string'}, 'filename': {'type': 'string'}, 'size': {'type': 'integer'},
    'sheets': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'name': {'type': 'string'}, 'rows': {'type': 'integer'}}, 'required': ['name', 'rows'], 'additionalProperties': False}}},
    'required': ['path', 'filename', 'size', 'sheets'], 'additionalProperties': False}
DESCRIPTION = (
    '将结构化数据生成带统一排版、筛选、冻结表头和可选图表的 Excel 文件，调用 galaxy-cli excel create --input JSON。'
    '必填 title（1—100 字符）和 sheets（1—12 个工作表），可选 subtitle（最多 500 字符）。'
    '每个工作表必填 name、columns、rows；name 最多 31 字符，名称不能重复，不能含 []:*?/\\，不能以单引号开头或结尾，不能为 History；'
    'columns 为 1—50 个列定义，每列必填 key（数据键）和 title（显示列名），同表 key 和 title 均不可重复；'
    '可选 type 为 text/number/integer/currency/percent/date/datetime/boolean，默认 text，可选 width 为 8—60，省略自动估算列宽。'
    'rows 是对象数组，每表最多 10000 行、整份文件最多 100000 个数据单元格；对象键对应 columns.key，未定义键会报错，缺失键和 null 显示空白。'
    'text 使用字符串，手机号和 ID 应使用 text；number/currency/percent 使用 JSON 数字，integer 使用整数，boolean 使用 true/false；'
    'percent 使用小数（0.25 显示 25%），currency 显示人民币金额且不换算单位，分为单位的数据需先除以 100；'
    'date 为 YYYY-MM-DD，datetime 为 YYYY-MM-DD HH:mm:ss 或 YYYY-MM-DDTHH:mm:ss，时间不自动转换时区。'
    '可选 charts 为最多 4 个图表，每个必填 type（column/bar/line/pie）、category（分类列 key）、values（数值列 key 数组，1—6 项），可选 title；'
    'pie 仅支持一个数值列，有图表时至少一行数据。图表使用传入的行，不自动分组求和或补造数据。'
    '字符串按文本写入，不执行公式或自动创建外链。返回 {path,filename,size,sheets:[{name,rows}]}，size 单位为字节；'
    'path 是当前账号工作目录内的相对路径；需要提供下载时，再调用 file.publish，传入该 path，取得附件信息。失败返回 error.code 和 error.message。'
)


def fail(message):
    raise ToolError('invalid_input', message)


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        fail('需要 title 和 sheets；请按工具说明填写工作表、列、数据行及可选图表。')
    names, cells = set(), 0
    for sheet in arguments['sheets']:
        name = sheet['name']
        if not name.strip() or re.search(r'[\[\]:*?/\\\x00-\x1f]', name) or name.startswith("'") or name.endswith("'") or name.casefold() in names | {'history'}:
            fail('工作表名称无效或重复。')
        names.add(name.casefold())
        keys = [c['key'] for c in sheet['columns']]
        titles = [c['title'].casefold() for c in sheet['columns']]
        if len(set(keys)) != len(keys) or len(set(titles)) != len(titles) or any(not c['title'].strip() for c in sheet['columns']):
            fail('同一工作表的列 key 和 title 不能重复或为空。')
        cells += len(keys) * len(sheet['rows'])
        for row in sheet['rows']:
            if set(row) - set(keys):
                fail('数据行包含未定义的列，请补充 columns 或移除多余数据键。')
            for column in sheet['columns']:
                value = row.get(column['key'])
                if value is not None:
                    cell_value(value, column.get('type', 'text'))
        types = {c['key']: c.get('type', 'text') for c in sheet['columns']}
        for chart in sheet.get('charts', []):
            if not sheet['rows'] or chart['category'] not in keys or any(types.get(k) not in ('number', 'integer', 'currency', 'percent') for k in chart['values']) or chart['type'] == 'pie' and len(chart['values']) != 1:
                fail('图表需要非空数据、有效分类列和数值列；饼图仅能选择一个数值列。')
    if cells > 100000:
        fail('整份文件最多 100000 个数据单元格，请拆分导出。')
    return arguments


def cell_value(value, kind):
    if kind == 'text':
        if not isinstance(value, str):
            fail('text 列需传字符串；数值列请指定 type。')
        return value
    if kind == 'boolean':
        if not isinstance(value, bool):
            fail('boolean 列需传 true 或 false。')
        return value
    if kind in ('number', 'integer', 'currency', 'percent'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or isinstance(value, float) and not math.isfinite(value) or abs(value) >= 1e15 or kind == 'integer' and value != int(value):
            fail('数值列需要有限数字；超长数字请使用 text 防止精度丢失。')
        return value
    try:
        if not isinstance(value, str):
            raise ValueError()
        pattern = r'\d{4}-\d{2}-\d{2}' if kind == 'date' else r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}'
        if not re.fullmatch(pattern, value):
            raise ValueError()
        parsed = dt.datetime.fromisoformat(value)
        if parsed.year < 1900:
            raise ValueError()
        return parsed
    except ValueError:
        fail('日期需为 1900 年起有效的 YYYY-MM-DD，日期时间需为 YYYY-MM-DD HH:mm:ss。')


def create(arguments, directory):
    arguments = normalize(arguments)
    identifier = str(uuid.uuid4())
    filename = re.sub(r'[<>:"/\\|?*%~\x00-\x1f\x7f]', '_', arguments['title']).strip(' .')[:100] or '数据报告'
    filename = filename.encode('utf-8')[:200].decode('utf-8', errors='ignore') + '.xlsx'
    relative = 'generated/' + identifier + '/' + filename
    try:
        with root_directory(directory) as root, child_directory(root, ['generated', identifier], create=True) as parent:
            descriptor = os.open('.part', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    with xlsxwriter.Workbook(output, {'strings_to_formulas': False, 'strings_to_urls': False, 'in_memory': True}) as book:
                        book.set_properties({'title': arguments['title'], 'author': 'galaxy ai'})
                        heading = book.add_format({'font_name': 'Microsoft YaHei', 'font_size': 22, 'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#162D3D', 'valign': 'vcenter'})
                        sub = book.add_format({'font_name': 'Microsoft YaHei', 'font_size': 10, 'font_color': '#617486', 'text_wrap': True, 'valign': 'vcenter'})
                        formats = {}
                        patterns = {'number': '#,##0.00', 'integer': '#,##0', 'currency': '¥#,##0.00;[Red]-¥#,##0.00', 'percent': '0.00%', 'date': 'yyyy-mm-dd', 'datetime': 'yyyy-mm-dd hh:mm:ss', 'text': '@', 'boolean': 'General'}
                        for band in (0, 1):
                            for kind in TYPES:
                                formats[band, kind] = book.add_format({'font_name': 'Microsoft YaHei', 'font_size': 11, 'font_color': '#263C4B', 'bg_color': '#FFFFFF' if band == 0 else '#F0F5F7', 'valign': 'vcenter', 'text_wrap': kind == 'text', 'num_format': patterns[kind]})
                        header = book.add_format({'font_name': 'Microsoft YaHei', 'font_size': 11, 'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#167B83', 'valign': 'vcenter'})
                        for sheet in arguments['sheets']:
                            ws = book.add_worksheet(sheet['name'])
                            columns, rows = sheet['columns'], sheet['rows']
                            last = len(columns) - 1
                            ws.hide_gridlines(2)
                            ws.set_tab_color('#167B83')
                            ws.set_zoom(90)
                            ws.set_default_row(26)
                            ws.set_row(0, 48)
                            if last:
                                ws.merge_range(0, 0, 0, last, arguments['title'], heading)
                            else:
                                ws.write_string(0, 0, arguments['title'], heading)
                            subtitle = arguments.get('subtitle') or sheet['name']
                            if last:
                                ws.merge_range(1, 0, 1, last, subtitle, sub)
                            else:
                                ws.write_string(1, 0, subtitle, sub)
                            ws.set_row(1, 40 if len(subtitle) > 80 else 28)
                            ws.set_row(3, 32)
                            for col, column in enumerate(columns):
                                sampled = [column['title'], *(str(row.get(column['key'], '')) for row in rows[:100])]
                                estimate = max(sum(2 if ord(c) > 255 else 1 for c in s) for s in sampled) + 3
                                ws.set_column(col, col, column.get('width', min(42, max(14, estimate))))
                                ws.write_string(3, col, column['title'], header)
                            for index, row in enumerate(rows):
                                for col, column in enumerate(columns):
                                    kind, value = column.get('type', 'text'), row.get(column['key'])
                                    fmt = formats[index % 2, kind]
                                    if value is None:
                                        ws.write_blank(index + 4, col, None, fmt)
                                    else:
                                        value = cell_value(value, kind)
                                        writer = ws.write_string if kind == 'text' else ws.write_boolean if kind == 'boolean' else ws.write_datetime if kind in ('date', 'datetime') else ws.write_number
                                        writer(index + 4, col, value, fmt)
                            ws.autofilter(3, 0, 3 + len(rows), last)
                            ws.freeze_panes(4, 0)
                            ws.repeat_rows(0, 3)
                            ws.set_landscape()
                            ws.fit_to_pages(1, 0)
                            ws.set_margins(0.3, 0.3, 0.4, 0.4)
                            ws.set_footer('&Lgalaxy ai&R第 &P 页 / &N 页')
                            keys = [c['key'] for c in columns]
                            palette = ['#167B83', '#4267A2', '#DF9B46', '#8D6E97', '#66A797', '#C47D75']
                            for chart_index, spec in enumerate(sheet.get('charts', [])):
                                chart = book.add_chart({'type': spec['type']})
                                for series, key in enumerate(spec['values']):
                                    col = keys.index(key)
                                    config = {'name': [sheet['name'], 3, col], 'categories': [sheet['name'], 4, keys.index(spec['category']), 3 + len(rows), keys.index(spec['category'])], 'values': [sheet['name'], 4, col, 3 + len(rows), col], 'line': {'color': palette[series % 6]}, 'fill': {'color': palette[series % 6]}}
                                    if spec['type'] == 'pie':
                                        config['points'] = [{'fill': {'color': palette[i % 6]}} for i in range(len(rows))]
                                    chart.add_series(config)
                                chart.set_title({'name': spec.get('title', sheet['name']), 'name_font': {'name': 'Microsoft YaHei', 'size': 13}})
                                chart.set_legend({'position': 'bottom', 'font': {'name': 'Microsoft YaHei', 'size': 10}})
                                chart.set_chartarea({'border': {'none': True}, 'fill': {'color': '#FFFFFF'}})
                                chart.set_style(10)
                                chart.set_size({'width': 640, 'height': 340})
                                ws.insert_chart(4 + chart_index * 19, last + 2, chart)
                    output.flush()
                    os.fsync(output.fileno())
                    size = os.fstat(output.fileno()).st_size
                os.rename('.part', filename, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try: os.unlink('.part', dir_fd=parent)
                except FileNotFoundError: pass
        return {'path': relative, 'filename': filename, 'size': size, 'sheets': [{'name': s['name'], 'rows': len(s['rows'])} for s in arguments['sheets']]}
    except ToolError:
        raise
    except Exception:
        raise ToolError('export_failed', 'Excel 生成失败，请检查数据类型或减少导出量。', 1) from None


def register(registry):
    @registry.tool(name='excel.create', category='excel', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def export(arguments):
        directory = read_context().get('workspace_directory')
        if not isinstance(directory, str):
            raise ToolError('server_context_required', '请通过聊天中的 galaxy_cli 生成并下载 Excel。', 1)
        return create(arguments, directory)
