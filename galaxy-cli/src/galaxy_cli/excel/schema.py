"""Only file references and declarative operations cross the tool boundary."""
from jsonschema import Draft202012Validator
from ..file_scope import relative_parts
from ..registry import ToolError

TEXT = {'type': 'string', 'minLength': 1}
STRINGS = {'type': 'array', 'items': TEXT, 'minItems': 1, 'uniqueItems': True}
MAP = {'type': 'object', 'additionalProperties': TEXT, 'minProperties': 1}


def obj(properties, required):
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


SOURCE = obj({'path': TEXT, 'sheet': TEXT, 'header_row': {'type': 'integer', 'minimum': 1},
              'encoding': {'enum': ['utf-8-sig', 'utf-8', 'gb18030']}, 'record_path': TEXT,
              'columns': MAP, 'text_columns': STRINGS}, ['path'])
CONDITION = obj({'column': TEXT, 'operator': {'enum': ['==', '!=', '>', '<', '>=', '<=', 'contains',
    'startswith', 'endswith', 'in', 'not_in', 'is_blank', 'not_blank']},
    'value': {'type': ['string', 'number', 'boolean', 'null', 'array'],
              'items': {'type': ['string', 'number', 'boolean', 'null']}}}, ['column', 'operator'])
CALCULATION = obj({'name': TEXT, 'expression': TEXT, 'variables': MAP,
                  'decimals': {'type': 'integer', 'minimum': 0, 'maximum': 12}, 'status_column': TEXT},
                 ['name', 'expression', 'variables'])
STEPS = [
    obj({'op': {'const': 'lookup'}, 'table': TEXT, 'from': TEXT, 'into': TEXT,
         'left_on': STRINGS, 'right_on': STRINGS, 'columns': MAP,
         'multiple': {'enum': ['error', 'join']}, 'separator': TEXT, 'match_column': TEXT},
        ['op', 'table', 'from', 'into', 'left_on', 'right_on', 'columns']),
    obj({'op': {'const': 'filter'}, 'table': TEXT, 'into': TEXT,
         'conditions': {'type': 'array', 'minItems': 1, 'items': CONDITION}, 'logic': {'enum': ['AND', 'OR']}},
        ['op', 'table', 'into', 'conditions']),
    obj({'op': {'const': 'calculate'}, 'table': TEXT, 'into': TEXT,
         'columns': {'type': 'array', 'minItems': 1, 'items': CALCULATION}}, ['op', 'table', 'into', 'columns']),
    obj({'op': {'const': 'select'}, 'table': TEXT, 'into': TEXT, 'columns': STRINGS, 'rename': MAP},
        ['op', 'table', 'into', 'columns']),
    obj({'op': {'const': 'concat'}, 'tables': STRINGS, 'into': TEXT}, ['op', 'tables', 'into'])]
EXPORT = obj({'table': TEXT, 'sheet': TEXT, 'totals': STRINGS,
              'formats': {'type': 'object', 'additionalProperties': {'enum': ['text', 'number', 'currency', 'integer', 'percent']}}},
             ['table', 'sheet'])
INSPECT_INPUT = obj({'source': SOURCE, 'preview_rows': {'type': 'integer', 'minimum': 0, 'maximum': 10},
                     'totals': STRINGS}, ['source'])
TRANSFORM_INPUT = obj({'sources': {'type': 'object', 'minProperties': 1, 'additionalProperties': SOURCE},
                      'steps': {'type': 'array', 'items': {'oneOf': STEPS}},
                      'exports': {'type': 'array', 'minItems': 1, 'items': EXPORT}, 'filename': TEXT},
                     ['sources', 'exports'])
INSPECT_OUTPUT = obj({'rows': {'type': 'integer'}, 'sheet': {'type': 'string'},
    'sheets': {'type': 'array', 'items': {'type': 'string'}}, 'columns': {'type': 'array', 'items': {'type': 'string'}},
    'preview': {'type': 'array', 'items': {'type': 'object'}}, 'preview_truncated': {'type': 'boolean'}, 'totals': {'type': 'object'},
    'formula_cells': {'type': 'integer'}}, ['rows', 'sheet', 'sheets', 'columns', 'preview', 'preview_truncated', 'totals', 'formula_cells'])
TRANSFORM_OUTPUT = obj({'path': TEXT, 'filename': TEXT, 'size': {'type': 'integer'},
    'sheets': {'type': 'array', 'items': {'type': 'object'}}, 'summary': {'type': 'array', 'items': {'type': 'object'}}},
    ['path', 'filename', 'size', 'sheets', 'summary'])


def normalize(value, kind):
    schema = INSPECT_INPUT if kind == 'inspect' else TRANSFORM_INPUT
    if not isinstance(value, dict) or not Draft202012Validator(schema).is_valid(value):
        raise ToolError('invalid_input', 'Excel 参数无效，请查看对应工具 help。')
    sources = [value['source']] if kind == 'inspect' else list(value['sources'].values())
    for source in sources:
        path = source['path']
        for prefix in ('/workspace/', '/account-files/', '/account-cache/'):
            if path.startswith(prefix):
                path = path[len(prefix):]; break
        relative_parts(path)
    return value
