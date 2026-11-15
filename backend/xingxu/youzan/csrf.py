"""Parse Youzan page CSRF values without executing page scripts."""
import json
import re
from ..auth import AuthFailure
from .constants import MESSAGES


def extract_csrf(html):
    # Parse the JSON string literal to support escaped values; never execute page JS.
    match = re.search(r'"csrf_token"\s*:\s*("(?:[^"\\]|\\.)*")', html)
    if not match:
        raise AuthFailure("csrf_not_found", MESSAGES["csrf_not_found"], 502)
    value = json.loads(match.group(1))
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise AuthFailure("csrf_not_found", MESSAGES["csrf_not_found"], 502)
    return value
