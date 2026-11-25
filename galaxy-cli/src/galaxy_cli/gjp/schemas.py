"""Fixed read-only commands; credentials and raw request overrides are excluded."""
from jsonschema import Draft202012Validator
from ..registry import ToolError
from .cost_schema import INPUT as COST_INPUT, normalize as normalize_cost

ID = {'type': 'string', 'pattern': r'^\d{0,100}$'}
PAGE = {'page': {'type': 'integer', 'minimum': 1, 'maximum': 1000000},
        'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 200}}
INPUTS = {
    'inventory-cost': COST_INPUT,
    'inventory': {'type': 'object', 'properties': {
        'action': {'enum': ['query', 'options']},
        **{k: ID for k in ('organization_id', 'warehouse_id', 'product_id', 'supplier_id')},
        'barcode': {'type': 'string', 'maxLength': 100, 'pattern': r'^[^\x00-\x1f]*$'},
        'include_zero': {'type': 'boolean'}, 'unique_barcode': {'type': 'boolean'},
        'stock_type': {'enum': ['0', '1', '3']}, **PAGE}, 'additionalProperties': False},
    'organizations': {'type': 'object', 'properties': {'parent_id': ID, **PAGE}, 'additionalProperties': False},
    'product-info': {'type': 'object', 'properties': {
        'action': {'enum': ['attributes', 'products']}, 'parent_id': ID, **PAGE}, 'additionalProperties': False},
}
OUTPUT = {'type': 'object', 'properties': {'data': {'type': 'object'}},
          'required': ['data'], 'additionalProperties': False}


def normalize(name, body):
    if name == 'inventory-cost':
        return normalize_cost(body)
    if name not in INPUTS or not Draft202012Validator(INPUTS[name]).is_valid(body):
        raise ToolError('invalid_input', '管家婆工具参数无效，请查看 gjp 分类工具说明；不能传入凭证或原始请求。')
    defaults = {'page': 1, 'page_size': 100}
    if name == 'inventory':
        defaults.update(action='query', organization_id='', warehouse_id='', product_id='', supplier_id='',
                        barcode='', include_zero=False, unique_barcode=False, stock_type='0')
    elif name == 'organizations':
        defaults['parent_id'] = ''
    else:
        defaults.update(action='attributes', parent_id='')
    return {**defaults, **body}
