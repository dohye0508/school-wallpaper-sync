"""Display-date and manual timetable rules shared by preview and background sync."""
from copy import deepcopy
from datetime import datetime, timedelta
import re


def target_day(cfg, now):
    """Keep the supplied timezone and clock while choosing today or tomorrow."""
    return now + timedelta(days=1) if cfg.get('display_day') == 'tomorrow' else now


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{8}', value):
        raise ValueError('시간표 날짜는 YYYYMMDD 형식이어야 합니다.')
    try:
        datetime.strptime(value, '%Y%m%d')
    except ValueError as error:
        raise ValueError('존재하지 않는 시간표 날짜입니다.') from error
    return value


def _period(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('교시는 1~20 사이의 정수여야 합니다.')
    if isinstance(value, str) and not re.fullmatch(r'[0-9]{1,2}', value):
        raise ValueError('교시는 1~20 사이의 정수여야 합니다.')
    number = int(value)
    if not 1 <= number <= 20:
        raise ValueError('교시는 1~20 사이의 정수여야 합니다.')
    return number


def validate_overrides(items):
    """Validate date/period/subject overrides without mutating the input."""
    if not isinstance(items, list):
        raise ValueError('수동 시간표는 목록이어야 합니다.')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'date', 'period', 'subject'}:
            raise ValueError('수동 시간표에는 날짜, 교시, 과목만 입력할 수 있습니다.')
        day = _date(item['date'])
        period = _period(item['period'])
        subject = item['subject']
        if (not isinstance(subject, str) or not 1 <= len(subject.strip()) <= 100
                or any(ord(char) < 32 for char in subject)):
            raise ValueError('과목 이름은 줄바꿈 없는 1~100자여야 합니다.')
        key = (day, period)
        if key in seen:
            raise ValueError('같은 날짜와 교시의 수동 시간표가 중복됩니다.')
        seen.add(key)


def apply_overrides(rows, cfg, ymd):
    """Replace matching periods and add manual-only periods for one date.

    Unmodified rows keep their metadata. Duplicate source rows for an untouched
    period are retained; a manual override replaces all rows in that period.
    """
    _date(ymd)
    items = cfg.get('manual_timetable', [])
    validate_overrides(items)
    overrides = {
        _period(item['period']): {'period': str(_period(item['period'])),
                                 'subject': item['subject'].strip(), 'manual': True}
        for item in items if item['date'] == ymd
    }
    result = [deepcopy(row) for row in (rows or []) if int(row['period']) not in overrides]
    result.extend(overrides.values())
    result.sort(key=lambda row: int(row['period']))
    return result
