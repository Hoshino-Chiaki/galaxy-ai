"""Account-scoped file I/O and batch data operations; never eval arbitrary code."""
import ast
import csv
import datetime as dt
import io
import json
import math
import operator
import os
import re
import uuid
import zipfile
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
from pathlib import PurePosixPath

import pandas as pd
import xlsxwriter

from ..file_scope import source_file, regular_file, fingerprint, root_directory, child_directory
from ..registry import ToolError
from .vendor.expression_validator import validate_expression
from .vendor.frames import merge_frames, combine_masks


def fail(message, code='excel_input'):
    raise ToolError(code, message)


def blank(value):
    return value is None or isinstance(value, str) and value == ''


def clean_frame(df):
    # pandas joins/concatenation introduce NaN sentinels even with object input.
    # Keep them as real blanks, never numeric zero or the text "nan".
    return df.astype(object).where(pd.notna(df), None)


def number(value):
    if blank(value):
        raise ValueError('missing_value')
    if isinstance(value, bool):
        raise ValueError('invalid_number')
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (DecimalException, ValueError):
        raise ValueError('invalid_number') from None


def scalar(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=scalar)
    return value


def field(value, path):
    if isinstance(value, dict) and path in value:
        return value[path]
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def frame_from_rows(rows):
    header = next(rows, [])
    if not header:
        fail('未找到表头，请检查 sheet/header_row。')
    rows = list(rows)
    width = max([len(header), *(len(row) for row in rows)])
    # Trailing header blanks are often omitted from XLSX XML even when later
    # rows contain values there. Name these columns instead of dropping data.
    header = [*header, *([None] * (width - len(header)))]
    columns, used = [], set()
    for index, value in enumerate(header):
        base = str(value) if value is not None else '未命名列' + str(index + 1)
        name, suffix = base, 2
        while name in used:
            name = base + '__' + str(suffix); suffix += 1
        used.add(name); columns.append(name)
    data = []
    for row in rows:
        data.append([*row[:len(columns)], *([None] * max(0, len(columns) - len(row)))])
    return pd.DataFrame(data, columns=columns, dtype=object)


def read_table(source, context):
    directory = context.get('workspace_directory')
    if not isinstance(directory, str):
        fail('请通过当前账号的 galaxy_cli 使用表格工具。', 'server_context_required')
    path = source['path']
    if path.startswith('/account-files/'):
        path = 'library/' + path[len('/account-files/'):]
    elif path.startswith('/account-cache/'):
        path = path[len('/account-cache/'):]; directory = context.get('cache_directory')
    elif path.startswith('/workspace/'):
        path = path[len('/workspace/'):]
    if not isinstance(directory, str):
        fail('当前账号缓存目录不可用。')
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in ('.xlsx', '.xls', '.csv', '.json', '.jsonl'):
        fail('仅支持 XLSX/XLS/CSV/JSON/JSONL 文件。')
    formula_cells, sheets, sheet = 0, [], ''
    with source_file(directory, path) as descriptor:
        before = regular_file(descriptor)
        limit = context.get('source_limit_bytes', 512 * 1024 * 1024)
        if not 0 < before.st_size <= limit:
            fail('文件为空或超过当前账号文件容量设置。')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            if suffix == '.xlsx':
                from openpyxl import load_workbook
                with zipfile.ZipFile(stream) as archive:
                    if sum(f.file_size for f in archive.infolist()) > limit:
                        fail('工作簿解压内容超过当前账号文件容量设置。')
                stream.seek(0)
                book = load_workbook(stream, read_only=True, data_only=False, keep_links=False)
                try:
                    sheets = book.sheetnames
                    sheet = source.get('sheet', sheets[0])
                    if sheet not in sheets:
                        fail('工作表不存在，请先检查 sheets。')
                    ws = book[sheet]
                    # Some program-generated workbooks omit dimensions; stream actual rows.
                    ws.reset_dimensions()
                    def rows():
                        nonlocal formula_cells
                        for cells in ws.iter_rows(min_row=source.get('header_row', 1)):
                            row = []
                            for cell in cells:
                                value = cell.value
                                if cell.data_type == 'f': formula_cells += 1
                                if isinstance(value, (int, float)) and not isinstance(value, bool) and re.fullmatch('0+', cell.number_format or '') and value == int(value):
                                    value = str(int(value)).zfill(len(cell.number_format))
                                row.append(value)
                            yield row
                    df = frame_from_rows(iter(rows()))
                finally:
                    book.close()
            elif suffix == '.xls':
                from python_calamine import CalamineWorkbook
                with CalamineWorkbook.from_filelike(stream) as book:
                    sheets = book.sheet_names
                    sheet = source.get('sheet', sheets[0])
                    if sheet not in sheets: fail('工作表不存在。')
                    rows = book.get_sheet_by_name(sheet).to_python(skip_empty_area=False)
                    df = frame_from_rows(iter(rows[source.get('header_row', 1) - 1:]))
            elif suffix == '.csv':
                text = io.TextIOWrapper(stream, encoding=source.get('encoding', 'utf-8-sig'), newline='')
                rows = csv.reader(text)
                for _ in range(source.get('header_row', 1) - 1): next(rows, None)
                df = frame_from_rows(rows)
            else:
                text = io.TextIOWrapper(stream, encoding='utf-8-sig')
                records = ([json.loads(line, parse_float=Decimal) for line in text if line.strip()]
                           if suffix == '.jsonl' else json.load(text, parse_float=Decimal))
                if source.get('record_path'): records = field(records, source['record_path'])
                if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                    fail('JSON 需为对象记录数组；嵌套数组请指定 record_path。')
                mapping = source.get('columns')
                if mapping:
                    records = [{key: field(row, path) for key, path in mapping.items()} for row in records]
                df = pd.DataFrame(records, columns=list(mapping) if mapping else None, dtype=object)
                df = clean_frame(df)
                for name in df.columns:
                    df[name] = df[name].map(lambda v: scalar(v) if isinstance(v, (dict, list)) else v)
        if fingerprint(before) != fingerprint(regular_file(descriptor)):
            fail('读取期间源文件发生变化，请重试。', 'source_changed')
    for col in source.get('text_columns', []):
        require_columns(df, [col])
        df[col] = df[col].map(lambda v: None if blank(v) else str(v))
    return clean_frame(df), {'sheet': sheet, 'sheets': sheets, 'formula_cells': formula_cells}


def require_columns(df, columns):
    if any(col not in df.columns for col in columns):
        fail('列不存在：' + '、'.join(str(col) for col in columns if col not in df.columns))


def totals(df, columns):
    require_columns(df, columns)
    result = {}
    with localcontext() as ctx:
        ctx.prec = 50
        for col in columns:
            total, count, missing, invalid = Decimal(0), 0, 0, 0
            for val in df[col]:
                try:
                    total += number(val); count += 1
                except ValueError as error:
                    if str(error) == 'missing_value': missing += 1
                    else: invalid += 1
            result[col] = {'sum': format(total, 'f') if count else None, 'numeric': count, 'blank': missing, 'invalid': invalid}
    return result


def inspect_file(arguments, context):
    try:
        df, meta = read_table(arguments['source'], context)
        preview, remaining, truncated = [], 12000, False
        for record in df.head(arguments.get('preview_rows', 3)).to_dict('records'):
            sample = {}
            for key, val in record.items():
                val = scalar(val)
                if isinstance(val, str) and len(val) > 200:
                    val = val[:200] + '…'; truncated = True
                cost = len(str(key)) + len(str(val))
                if cost > remaining:
                    truncated = True; break
                sample[key] = val; remaining -= cost
            preview.append(sample)
            if remaining <= 0: break
        return {**meta, 'rows': len(df), 'columns': list(df.columns),
                'preview': preview, 'preview_truncated': truncated,
                'totals': totals(df, arguments.get('totals', []))}
    except ToolError:
        raise
    except Exception:
        fail('文件解析失败，请检查格式、编码和表头；源文件未修改。', 'excel_read_failed')


def lookup(left, right, step):
    lkeys, rkeys = step['left_on'], step['right_on']
    require_columns(left, lkeys); require_columns(right, [*rkeys, *step['columns']])
    if len(lkeys) != len(rkeys): fail('left_on 与 right_on 必须等长。')
    names = [*step['columns'].values(), *([step['match_column']] if step.get('match_column') else [])]
    if len(set(names)) != len(names) or set(names) & set(left.columns):
        fail('匹配输出列不能重名或覆盖主表已有列。')
    # Nulls must never match nulls (pandas merge otherwise does this).
    selected = right.loc[~right[rkeys].map(blank).any(axis=1)].copy()
    duplicated = selected.duplicated(rkeys, keep=False)
    duplicate_rows = int(duplicated.sum())
    if duplicate_rows and step.get('multiple', 'error') == 'error':
        fail('匹配表存在重复键，已停止，未放大主表行数。请补充匹配键或明确 multiple=join。', 'excel_duplicate_keys')
    # Temporary names cannot collide with user-controlled headers.
    prefix = '__galaxy_' + uuid.uuid4().hex
    keycols = [prefix + str(i) for i in range(len(rkeys))]
    projected = pd.DataFrame({keycols[i]: selected[key].tolist() for i, key in enumerate(rkeys)}, dtype=object)
    for index, key in enumerate(step['columns']): projected[prefix + 'v' + str(index)] = selected[key].tolist()
    marker = prefix + 'matched'; projected[marker] = True
    if duplicate_rows:
        def collect(series):
            values = list(dict.fromkeys(str(v) for v in series if not blank(v)))
            return step.get('separator', '、').join(values) if values else None
        grouped = projected.groupby(keycols, sort=False, dropna=False)
        projected = grouped.agg({col: ('first' if col == marker else collect)
                                 for col in projected.columns if col not in keycols}).reset_index()
    base = left.copy()
    # Preserve nulls and native value types with object dtype throughout.
    merged = merge_frames(base, projected, lkeys, keycols)
    matched = merged[marker].map(lambda v: v is True)
    result = merged[list(left.columns)].copy()
    for index, new in enumerate(step['columns'].values()):
        result[new] = merged[prefix + 'v' + str(index)]
    if step.get('match_column'): result[step['match_column']] = matched
    if len(result) != len(left): fail('匹配行数校验失败。')
    return clean_frame(result), {'matched': int(matched.sum()), 'unmatched': int((~matched).sum()), 'duplicate_source_rows': duplicate_rows}


def filter_rows(df, step):
    masks = []
    for condition in step['conditions']:
        col, op = condition['column'], condition['operator']; require_columns(df, [col])
        if op not in ('is_blank', 'not_blank') and 'value' not in condition: fail('筛选条件缺少 value。')
        value = condition.get('value')
        if op in ('in', 'not_in') and not isinstance(value, list): fail('in/not_in 的 value 必须为数组。')
        def test(cell):
            if op == 'is_blank': return blank(cell)
            if op == 'not_blank': return not blank(cell)
            if op in ('in', 'not_in'): return (cell in value) == (op == 'in')
            if op in ('contains', 'startswith', 'endswith'):
                if blank(cell): return False
                if op == 'contains': return str(value) in str(cell)
                return getattr(str(cell), op)(str(value))
            if blank(cell) or value is None:
                return (blank(cell) and value is None) if op == '==' else (not blank(cell) if value is None else False) if op == '!=' else False
            compare = {'==': operator.eq, '!=': operator.ne, '>': operator.gt, '<': operator.lt, '>=': operator.ge, '<=': operator.le}[op]
            try:
                if isinstance(value, (int, float)) and not isinstance(value, bool): return compare(number(cell), number(value))
                return compare(cell, value)
            except (TypeError, ValueError): return False
        masks.append(df[col].map(test).astype(bool))
    return df[combine_masks(masks, step.get('logic', 'AND'))].reset_index(drop=True)


OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Gt: operator.gt, ast.Lt: operator.lt, ast.GtE: operator.ge, ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}


def evaluate(node, variables):
    if isinstance(node, ast.Expression): return evaluate(node.body, variables)
    if isinstance(node, ast.Name): return number(variables[node.id])
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool): return node.value
        return number(node.value)
    if isinstance(node, ast.BinOp):
        left, right = evaluate(node.left, variables), evaluate(node.right, variables)
        if isinstance(node.op, ast.Div) and right == 0: raise ZeroDivisionError()
        return OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp): return (-1 if isinstance(node.op, ast.USub) else 1) * evaluate(node.operand, variables)
    if isinstance(node, ast.IfExp): return evaluate(node.body if evaluate(node.test, variables) else node.orelse, variables)
    if isinstance(node, ast.Compare):
        values = [evaluate(node.left, variables), *(evaluate(n, variables) for n in node.comparators)]
        return all(OPS[type(op)](values[i], values[i + 1]) for i, op in enumerate(node.ops))
    if isinstance(node, ast.BoolOp):
        return (all if isinstance(node.op, ast.And) else any)(evaluate(n, variables) for n in node.values)
    if isinstance(node, ast.Call) and node.func.id == 'abs' and len(node.args) == 1:
        return abs(evaluate(node.args[0], variables))
    fail('计算表达式仅支持十进制四则运算、比较、条件表达式和 abs。')


def calculate(df, step):
    result, report = df.copy(), {}
    for spec in step['columns']:
        name, variables = spec['name'], spec['variables']
        status_column = spec.get('status_column')
        if name in result.columns or status_column and (status_column in result.columns or status_column == name):
            fail('计算结果列不能覆盖已有列。')
        require_columns(result, variables.values())
        if any(not key.isidentifier() or key == 'abs' for key in variables): fail('变量名必须是有效标识符且不能为 abs。')
        try:
            tree = validate_expression(spec['expression'], allowed_names=frozenset(variables))
            # Narrow the upstream expression language to deterministic decimal operations.
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and type(node.op) not in OPS or isinstance(node, ast.Call) and (node.func.id != 'abs' or len(node.args) != 1):
                    raise ValueError()
                if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)): raise ValueError()
        except ValueError: fail('表达式无效；仅支持 + - * /、比较、条件表达式和 abs。')
        values, states, counts = [], [], {}
        with localcontext() as ctx:
            ctx.prec = 50
            for row in result[list(variables.values())].itertuples(index=False, name=None):
                try:
                    val = evaluate(tree, dict(zip(variables, row)))
                    if isinstance(val, bool): val = Decimal(int(val))
                    val = val.quantize(Decimal(1).scaleb(-spec.get('decimals', 2)), rounding=ROUND_HALF_UP)
                    state = 'ok'
                except (ValueError, DecimalException, ZeroDivisionError, KeyError) as error:
                    val = None
                    state = str(error) if isinstance(error, ValueError) else 'division_by_zero' if isinstance(error, (ZeroDivisionError,)) else 'invalid_number'
                values.append(val); states.append(state); counts[state] = counts.get(state, 0) + 1
        result[name] = pd.Series(values, dtype=object)
        if status_column: result[status_column] = states
        report[name] = counts
    return result, report


def write_output(tables, arguments, context, summary):
    identifier = uuid.uuid4().hex
    filename = arguments.get('filename', '数据处理结果.xlsx')
    if not filename.endswith('.xlsx') or len(filename.encode()) > 220 or re.search(r'[<>:"/\\|?*%~\x00-\x1f\x7f]', filename) or filename.startswith('.'):
        fail('filename 必须为不含路径的有效 .xlsx 文件名。')
    names, sheets = set(), []
    for spec in arguments['exports']:
        name = spec['sheet']
        if not 0 < len(name) <= 31 or re.search(r'[\[\]:*?/\\\x00-\x1f]', name) or name.startswith("'") or name.endswith("'") or name.casefold() in names | {'history'}:
            fail('输出工作表名称无效或重复。')
        names.add(name.casefold())
        if spec['table'] not in tables: fail('输出 table 别名不存在。')
        df = tables[spec['table']]
        if not len(df.columns) or len(df) > 1048575 or len(df.columns) > 16384: fail('输出超出 XLSX 工作表行列范围。')
        require_columns(df, list(spec.get('formats', {})))
        sheets.append({'name': name, 'rows': len(df), 'totals': totals(df, spec.get('totals', []))})
    relative = 'generated/' + identifier + '/' + filename
    with root_directory(context['workspace_directory']) as root, child_directory(root, ['generated', identifier], create=True) as directory:
        fd = os.open('result.part', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'w+b') as stream:
                with xlsxwriter.Workbook(stream, {'constant_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False}) as book:
                    header = book.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#167B83', 'font_name': 'Microsoft YaHei', 'valign': 'vcenter'})
                    patterns = {'text': '@', 'currency': '#,##0.00;[Red]-#,##0.00', 'number': '#,##0.00', 'integer': '#,##0', 'percent': '0.00%'}
                    formats = {kind: book.add_format({'num_format': pattern, 'font_name': 'Microsoft YaHei', 'font_size': 11}) for kind, pattern in patterns.items()}
                    for spec in arguments['exports']:
                        df = tables[spec['table']]; ws = book.add_worksheet(spec['sheet'])
                        ws.hide_gridlines(2); ws.freeze_panes(1, 0); ws.set_row(0, 30)
                        for col, name in enumerate(df.columns):
                            ws.write_string(0, col, str(name), header)
                            ws.set_column(col, col, min(42, max(14, len(str(name)) * 2 + 4)))
                        for index, row in enumerate(df.itertuples(index=False, name=None), 1):
                            for col, value in enumerate(row):
                                if blank(value): continue
                                kind = spec.get('formats', {}).get(df.columns[col])
                                if kind == 'text': value = str(scalar(value))
                                elif kind in ('number', 'currency', 'integer', 'percent'):
                                    try: value = number(value)
                                    except ValueError: pass  # Preserve invalid/missing values; never invent zero.
                                if isinstance(value, (dt.datetime, dt.date, dt.time)): value = value.isoformat()
                                if isinstance(value, float) and not math.isfinite(value): fail('输出存在无效数值。')
                                if isinstance(value, str) and len(value) > 32767: fail('单元格文本超过 XLSX 长度限制，未截断数据。')
                                if ws.write(index, col, value, formats.get(kind)) != 0: fail('单元格写入失败，未交付不完整工作簿。')
                        ws.autofilter(0, 0, len(df), len(df.columns) - 1)
                stream.flush(); os.fsync(stream.fileno()); size = os.fstat(stream.fileno()).st_size
            os.rename('result.part', filename, src_dir_fd=directory, dst_dir_fd=directory); os.fsync(directory)
        finally:
            try: os.unlink('result.part', dir_fd=directory)
            except FileNotFoundError: pass
    return {'path': relative, 'filename': filename, 'size': size, 'sheets': sheets, 'summary': summary}


def transform_files(arguments, context):
    try:
        tables = {name: read_table(source, context)[0] for name, source in arguments['sources'].items()}
        summary = []
        for step in arguments.get('steps', []):
            op = step['op']; detail = {}
            if op == 'concat':
                if any(name not in tables for name in step['tables']): fail('concat 的表别名不存在。')
                inputs = sum(len(tables[name]) for name in step['tables'])
                result = pd.concat([tables[name] for name in step['tables']], ignore_index=True, sort=False).astype(object)
                result = clean_frame(result)
            else:
                if step['table'] not in tables: fail('table 别名不存在。')
                df = tables[step['table']]; inputs = len(df)
                if op == 'lookup':
                    if step['from'] not in tables: fail('from 别名不存在。')
                    result, detail = lookup(df, tables[step['from']], step)
                elif op == 'calculate': result, detail = calculate(df, step)
                elif op == 'filter': result = filter_rows(df, step)
                else:
                    require_columns(df, step['columns'])
                    rename = step.get('rename', {}); require_columns(df[step['columns']], rename)
                    result = df[step['columns']].rename(columns=rename).copy()
                    if len(set(result.columns)) != len(result.columns): fail('重命名后列名重复。')
            tables[step['into']] = result
            summary.append({'op': op, 'into': step['into'], 'input_rows': inputs, 'output_rows': len(result), **({'details': detail} if detail else {})})
        return write_output(tables, arguments, context, summary)
    except ToolError:
        raise
    except Exception:
        fail('表格处理失败，请检查文件、列名及规则；源文件未修改。', 'excel_processing_failed')
