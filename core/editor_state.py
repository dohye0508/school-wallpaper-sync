"""Editor history, portable settings backups and honest cache status labels.

No Tkinter, wallpaper or network dependencies. Loading a backup returns a draft;
only the caller's explicit Apply action may change active configuration.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re

from core.storage import atomic_json
from core.themes import STYLE_KEYS, apply_style, validate_style
from core.schoolday import validate_overrides
from core.sync import identity

BACKUP_KIND = 'school-wallpaper-settings'
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 2 * 1024 * 1024
IDENTITY_KEYS = frozenset(('edu_code', 'school_code', 'school_name', 'school_kind', 'grade', 'classnm'))
BACKUP_KEYS = IDENTITY_KEYS | STYLE_KEYS | {'manual_dday', 'display_day', 'manual_timetable'}
_DATE = re.compile(r'[0-9]{8}\Z')
_COLOR = re.compile(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\Z')


class EditHistory:
    """Bounded deep-copy snapshots, including the initial state in the limit."""
    def __init__(self, initial, limit=60):
        if type(limit) is not int or limit < 1:
            raise ValueError('편집 기록 한도는 1 이상의 정수여야 합니다.')
        self.limit = limit
        self.reset(initial)

    @property
    def can_undo(self):
        return self._index > 0

    @property
    def can_redo(self):
        return self._index < len(self._snapshots) - 1

    def reset(self, snapshot):
        self._snapshots = [deepcopy(snapshot)]
        self._index = 0

    def record(self, snapshot):
        if snapshot == self._snapshots[self._index]:
            return False
        self._snapshots = self._snapshots[:self._index + 1]
        self._snapshots.append(deepcopy(snapshot))
        if len(self._snapshots) > self.limit:
            self._snapshots = self._snapshots[-self.limit:]
        self._index = len(self._snapshots) - 1
        return True

    def undo(self):
        if not self.can_undo:
            return None
        self._index -= 1
        return deepcopy(self._snapshots[self._index])

    def redo(self):
        if not self.can_redo:
            return None
        self._index += 1
        return deepcopy(self._snapshots[self._index])


def _date(value):
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError('일정 날짜는 YYYYMMDD 형식이어야 합니다.')
    try:
        datetime.strptime(value, '%Y%m%d')
    except ValueError as error:
        raise ValueError('존재하지 않는 일정 날짜입니다.') from error


def _validate_settings(settings):
    if not isinstance(settings, dict) or not set(settings).issubset(BACKUP_KEYS):
        raise ValueError('백업에 지원하지 않는 설정 항목이 있습니다.')
    style = {key: value for key, value in settings.items() if key in STYLE_KEYS}
    validate_style(style)
    if 'display_day' in settings and settings['display_day'] not in ('today', 'tomorrow'):
        raise ValueError('표시 날짜는 today 또는 tomorrow여야 합니다.')
    if 'manual_timetable' in settings:
        validate_overrides(settings['manual_timetable'])
    for key in IDENTITY_KEYS & settings.keys():
        value = settings[key]
        maximum = 200 if key == 'school_name' else 32
        if (not isinstance(value, str) or len(value) > maximum
                or any(ord(char) < 32 for char in value)):
            raise ValueError(f'{key}: 올바른 학교 설정 문자열이 필요합니다.')
    if 'school_kind' in settings and settings['school_kind'] not in ('', '초등학교', '중학교', '고등학교'):
        raise ValueError('지원하지 않는 학교급입니다.')
    if 'manual_dday' in settings:
        entries = settings['manual_dday']
        if not isinstance(entries, list):
            raise ValueError('직접 등록한 일정은 목록이어야 합니다.')
        for entry in entries:
            if (not isinstance(entry, dict) or not {'name', 'date'}.issubset(entry)
                    or not set(entry).issubset({'name', 'date', 'color'})):
                raise ValueError('직접 등록한 일정의 형식이 올바르지 않습니다.')
            name = entry['name']
            if (not isinstance(name, str) or not name.strip() or len(name) > 200
                    or any(ord(char) < 32 for char in name)):
                raise ValueError('일정 이름은 줄바꿈 없는 1~200자 문자열이어야 합니다.')
            _date(entry['date'])
            color = entry.get('color')
            if color is not None and (not isinstance(color, str) or not _COLOR.fullmatch(color)):
                raise ValueError('일정 색상은 #RRGGBB 또는 #RRGGBBAA 형식이어야 합니다.')


def backup_settings(path, cfg):
    """Atomically export allowlisted settings; credentials are never exported."""
    if not isinstance(cfg, dict):
        raise ValueError('설정은 객체여야 합니다.')
    settings = deepcopy({key: value for key, value in cfg.items() if key in BACKUP_KEYS})
    _validate_settings(settings)
    document = {'schema_version': BACKUP_VERSION, 'kind': BACKUP_KIND, 'settings': settings}
    encoded = json.dumps(document, ensure_ascii=False, indent=2).encode('utf-8')
    if len(encoded) > MAX_BACKUP_BYTES:
        raise ValueError('설정 백업은 2MB를 초과할 수 없습니다.')
    destination = Path(path)
    atomic_json(str(destination), document)
    return destination


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('백업에 중복된 설정 키가 있습니다.')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('백업에 올바르지 않은 숫자가 있습니다.')


def read_settings_backup(path, current_cfg):
    """Validate at most 2MB and merge into an independent draft configuration."""
    if not isinstance(current_cfg, dict):
        raise ValueError('현재 설정은 객체여야 합니다.')
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BACKUP_BYTES + 1)
    if len(raw) > MAX_BACKUP_BYTES:
        raise ValueError('설정 백업은 2MB를 초과할 수 없습니다.')
    try:
        document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('올바른 UTF-8 JSON 설정 백업을 선택하세요.') from error
    if not isinstance(document, dict) or set(document) != {'schema_version', 'kind', 'settings'}:
        raise ValueError('설정 백업 형식이 올바르지 않습니다.')
    if type(document['schema_version']) is not int or document['schema_version'] != BACKUP_VERSION:
        raise ValueError('지원하지 않는 설정 백업 버전입니다.')
    if document['kind'] != BACKUP_KIND:
        raise ValueError('학교 배경화면 설정 백업 파일이 아닙니다.')
    incoming = document['settings']
    _validate_settings(incoming)
    result = apply_style(current_cfg, {key: value for key, value in incoming.items() if key in STYLE_KEYS})
    for key in (IDENTITY_KEYS | {'manual_dday', 'display_day', 'manual_timetable'}) & incoming.keys():
        result[key] = deepcopy(incoming[key])
    if identity(result) != identity(current_cfg) and 'manual_timetable' not in incoming:
        result['manual_timetable'] = []
    return result


def describe_cache(cache, now_ymd):
    """Describe cache contents; updated_at is a write time, not remote success."""
    if not isinstance(cache, dict) or cache.get('date') != now_ymd or not cache:
        return '오늘 데이터 없음'
    try:
        _date(now_ymd)
    except ValueError:
        return '오늘 데이터 없음'
    states = cache.get('states')
    if not isinstance(states, dict):
        states = {}
    labels = {'fresh': '조회됨', 'empty': '자료 없음', 'cached': '캐시 사용', 'unavailable': '조회 실패'}
    parts = []
    for key, label in (('timetable', '시간표'), ('meals', '급식'), ('schedule', '학사일정')):
        state = states.get(key)
        status = labels.get(state, '상태 미확인') if isinstance(state, str) else '상태 미확인'
        parts.append(f'{label}: {status}')
    recorded = cache.get('updated_at')
    if isinstance(recorded, str):
        try:
            datetime.strptime(recorded, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
        else:
            parts.append(f'캐시 기록: {recorded}')
    return ' · '.join(parts)
