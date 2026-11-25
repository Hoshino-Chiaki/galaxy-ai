"""Business parameters only; SQL, data sources and identities are server-owned."""
from jsonschema import Draft202012Validator
from ..file_scope import relative_parts
from ..registry import ToolError

CODE = {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': r'^[^\x00-\x1f\x7f]+$'}
INPUT = {'type': 'object', 'properties': {
    'barcodes': {'type': 'array', 'minItems': 1, 'items': {'oneOf': [CODE,
        {'type': 'object', 'properties': {'barcode': CODE, 'product_name': {'type': 'string', 'maxLength': 1000}},
         'required': ['barcode'], 'additionalProperties': False}]}},
    'source_path': {'type': 'string', 'minLength': 1, 'maxLength': 2048},
    'barcode_column': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'name_column': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'sheet': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'header_row': {'type': 'integer', 'minimum': 1},
    'organization_id': {'type': 'string', 'pattern': r'^\d{1,100}$'},
    'warehouse_id': {'type': 'string', 'pattern': r'^\d{1,100}$'},
    'resume_id': {'type': 'string', 'pattern': r'^[0-9a-f]{32}$'}},
    'oneOf': [{'required': ['barcodes']}, {'required': ['source_path']}, {'required': ['resume_id']}],
    'additionalProperties': False}


def normalize(value):
    if not isinstance(value, dict) or not Draft202012Validator(INPUT).is_valid(value):
        raise ToolError('invalid_input', '请传 barcodes、source_path 或 resume_id 其中一种；不能传 SQL、数据库、凭证或他人身份。')
    if 'resume_id' in value and set(value) != {'resume_id'}:
        raise ToolError('invalid_input', '续跑只接受真实返回的 resume_id，不能更换原任务条件。')
    if 'source_path' not in value and set(value) & {'barcode_column', 'name_column', 'sheet', 'header_row'}:
        raise ToolError('invalid_input', '列名、工作表和标题行参数仅用于文件输入。')
    if 'source_path' in value:
        path = value['source_path']
        for prefix in ('/workspace/', '/account-files/', '/account-cache/'):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        relative_parts(path)
    return dict(value)
