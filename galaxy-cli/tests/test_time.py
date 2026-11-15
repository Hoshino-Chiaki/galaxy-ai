import calendar
import json
from datetime import datetime
from unittest.mock import patch

import pytest

from galaxy_cli.cli import main
from galaxy_cli.registry import ToolError, load_registry
from galaxy_cli.tools.time.now import now


def test_one_sample_preserves_exact_milliseconds_and_beijing_midnight():
    with patch('galaxy_cli.tools.time.now.time.time_ns', return_value=1_794_326_400_123_999_999) as clock:
        result = now({})
    assert clock.call_count == 1
    assert result == {'timestamp_ms': 1794326400123, 'datetime': '2026-11-11T00:00:00.123+08:00',
                      'timezone': 'Asia/Shanghai', 'today': '2026-11-11', 'yesterday': '2026-11-10'}


def test_same_instant_can_have_different_dates_in_different_timezones():
    with patch('galaxy_cli.tools.time.now.time.time_ns', return_value=1_794_326_400_123_999_999):
        result = now({'timezone': 'UTC'})
    assert result == {'timestamp_ms': 1794326400123, 'datetime': '2026-11-10T16:00:00.123+00:00',
                      'timezone': 'UTC', 'today': '2026-11-10', 'yesterday': '2026-11-09'}


def test_yesterday_is_a_calendar_date_across_daylight_saving_transition():
    seconds = calendar.timegm((2026, 3, 9, 4, 0, 0))
    with patch('galaxy_cli.tools.time.now.time.time_ns', return_value=seconds * 1_000_000_000):
        result = now({'timezone': 'America/New_York'})
    assert result['datetime'] == '2026-03-09T00:00:00.000-04:00'
    assert result['yesterday'] == '2026-03-08'


@pytest.mark.parametrize('value', [{'timezone': 'not/a/real-zone'}, {'timezone': '/etc/passwd'},
                                 {'timezone': '../secret'}, {'timezone': ''}, {'timezone': None},
                                 {'timestamp_ms': 0}, {'source': 'local'}, {'username': 'another'}])
def test_clock_source_or_user_identity_cannot_be_supplied(value):
    with pytest.raises(ToolError):
        load_registry().invoke('time.now', value)


def test_time_category_is_discoverable_and_cli_returns_minimal_clock_fields(capsys):
    assert load_registry().invoke('help', {'category': 'time'})['data'][0]['tool'] == 'time.now'
    assert main(['time', 'now']) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {'timestamp_ms', 'datetime', 'timezone', 'today', 'yesterday'}
    assert datetime.fromisoformat(result['datetime']).date().isoformat() == result['today']
