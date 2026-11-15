"""Read one system-clock sample and derive every returned date from it."""
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator

from ...registry import ToolError


INPUT = {'type': 'object', 'properties': {
    'timezone': {'type': 'string', 'minLength': 1, 'maxLength': 128}},
    'additionalProperties': False}
OUTPUT = {'type': 'object', 'properties': {
    'timestamp_ms': {'type': 'integer', 'minimum': 0},
    'datetime': {'type': 'string'}, 'timezone': {'type': 'string'},
    'today': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
    'yesterday': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}},
    'required': ['timestamp_ms', 'datetime', 'timezone', 'today', 'yesterday'],
    'additionalProperties': False}
DESCRIPTION = (
    '获取服务器调用瞬间的实时时间；询问现在、今天、昨天或查询相对日期的数据时，先调用 galaxy-cli time now，'
    '不要根据旧消息日期推断，也不需要让用户提供今天日期。输入可省略；可选 --input JSON 中的 timezone 为 IANA 时区名称，'
    '默认 Asia/Shanghai（北京时间），例如 {"timezone":"UTC"}。'
    '输出 timestamp_ms 为 Unix 毫秒整数，datetime 为含时区偏移且保留三位毫秒的 ISO 8601 时间，'
    'timezone 为实际使用的时区，today、yesterday 为该时区的 YYYY-MM-DD 日期；所有字段来自同一次时钟采样。'
    '业务接口需要 YYYYMMDD 时，将 today 或 yesterday 的连字符去掉后转换为整数。'
    '结果表示这次调用时刻；后续需要实时日期时重新调用，不把历史工具结果当作当前时间。'
)


def normalize(arguments):
    if not isinstance(arguments, dict) or not Draft202012Validator(INPUT).is_valid(arguments):
        raise ToolError('invalid_input', '时间工具只接受可选的 timezone 时区名称。')
    name = arguments.get('timezone', 'Asia/Shanghai')
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError('invalid_timezone', '时区无效，请使用 Asia/Shanghai、UTC 等 IANA 时区名称。') from None
    return {'timezone': name}


def now(arguments):
    arguments = normalize(arguments)
    zone = ZoneInfo(arguments['timezone'])
    timestamp_ms = time.time_ns() // 1_000_000
    seconds, millis = divmod(timestamp_ms, 1000)
    current = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=millis * 1000).astimezone(zone)
    return {'timestamp_ms': timestamp_ms, 'datetime': current.isoformat(timespec='milliseconds'),
            'timezone': arguments['timezone'], 'today': current.date().isoformat(),
            'yesterday': (current.date() - timedelta(days=1)).isoformat()}


def register(registry):
    registry.register(name='time.now', category='time', description=DESCRIPTION,
                      input_schema=INPUT, output_schema=OUTPUT, handler=now,
                      examples=('galaxy-cli time now', 'galaxy-cli time now --input \'{"timezone":"UTC"}\''))
