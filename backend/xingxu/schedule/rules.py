"""Calendar rules independent of AI and any channel integration."""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from croniter import croniter
from ..auth import AuthFailure


def invalid():
    return AuthFailure('invalid_schedule', '时间规则无效。请提供有效时区及单次时间、五段 Cron 或间隔秒数。', 422)


def normalize(value, now):
    if not isinstance(value, dict) or set(value) - {'type','timezone','expression','at','seconds','anchor'}:
        raise invalid()
    kind=value.get('type'); zone=value.get('timezone','Asia/Shanghai')
    try:ZoneInfo(zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):raise invalid() from None
    rule={'type':kind,'timezone':zone}
    try:
        if kind=='cron':
            if set(value)-{'type','timezone','expression'}:raise invalid()
            expression=value.get('expression','')
            if not isinstance(expression,str) or len(expression)>160 or len(expression.split())!=5 or not croniter.is_valid(expression):raise invalid()
            # Random/hashed expressions would make preview disagree with execution.
            if any(part.startswith(('H','R')) for part in expression.upper().split()):raise invalid()
            rule['expression']=expression.strip()
        elif kind=='once':
            if set(value)-{'type','timezone','at'}:raise invalid()
            stamp=datetime.fromisoformat(value.get('at','').replace('Z','+00:00'))
            if stamp.tzinfo is None or stamp.timestamp()<=now:raise invalid()
            rule['at']=stamp.isoformat()
        elif kind=='interval':
            if set(value)-{'type','timezone','seconds','anchor'}:raise invalid()
            seconds=value.get('seconds')
            if type(seconds) is not int or seconds<1:raise invalid()
            anchor=value.get('anchor')
            start=datetime.fromisoformat(anchor.replace('Z','+00:00')) if isinstance(anchor,str) else None
            if start and start.tzinfo is None:raise invalid()
            rule.update(seconds=seconds,anchor=start.isoformat() if start else datetime.fromtimestamp(now,timezone.utc).isoformat())
        else:raise invalid()
        occurrences(rule,now)
    except (ValueError, TypeError, OverflowError, KeyError):raise invalid() from None
    return rule


def occurrences(rule, after, count=3):
    try:
        if rule['type']=='once':
            stamp=datetime.fromisoformat(rule['at']).timestamp()
            return [stamp] if stamp>after else []
        if rule['type']=='interval':
            anchor=datetime.fromisoformat(rule['anchor']).timestamp();period=rule['seconds']
            start=anchor+max(0,math.floor((after-anchor)/period)+1)*period
            return [start+i*period for i in range(count)]
        iterator=croniter(rule['expression'],datetime.fromtimestamp(after,ZoneInfo(rule['timezone'])),max_years_between_matches=8)
        return [iterator.get_next(datetime).timestamp() for _ in range(count)]
    except (ValueError, TypeError, OverflowError, KeyError):raise invalid() from None
