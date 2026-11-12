"""Versioned design themes, independent of school data, GUI and rendering.

Theme selection only produces a configuration copy. It never activates a theme
or changes the user's school, credentials, dates, cache or wallpaper.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import re
from uuid import uuid4

from core.storage import atomic_json, desktop_transaction

SCHEMA_VERSION = 1
EXPORT_KIND = 'school-wallpaper-theme'
MAX_IMPORT_BYTES = 2 * 1024 * 1024
STYLE_KEYS = frozenset(('bg_photo_mode', 'bg_photo_idx', 'custom_background',
                        'dday_count', 'font_colors', 'sections', 'text_effects'))
SECTION_IDS = frozenset(('school_info', 'timetable', 'meals', 'date_info', 'dday'))
COLOR_KEYS = frozenset(('school_name', 'school_name_muted', 'timetable_title',
    'timetable_period', 'timetable_subject', 'meals_title', 'meals_type',
    'meals_dish', 'date_weekday', 'date_calendar', 'dday_label', 'dday_name'))
SECTION_KEYS = frozenset(('x', 'y', 'show', 'font_size', 'stroke_width',
                          'shadow_blur', 'shadow_opacity'))
EFFECT_KEYS = frozenset(('stroke_width', 'stroke_color', 'shadow_blur', 'shadow_opacity'))
COLOR_PATTERN = re.compile(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\Z')


def _mapping(value, allowed, label):
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise ValueError(f'{label}: 지원하지 않는 테마 항목입니다.')


def _number(value, minimum, maximum, label, *, integer=False):
    valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    try:
        valid = valid and math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{label}: {minimum}~{maximum} 범위의 숫자가 필요합니다.')
    if integer and not isinstance(value, int):
        raise ValueError(f'{label}: 정수가 필요합니다.')


def _color(value, label):
    if not isinstance(value, str) or not COLOR_PATTERN.fullmatch(value):
        raise ValueError(f'{label}: #RRGGBB 또는 #RRGGBBAA 색상이 필요합니다.')


def _background_path(path):
    """Validate syntax without stat/open, which could contact a remote share."""
    if path is None:
        return
    if (not isinstance(path, str) or not path or path != path.strip()
            or any(ord(char) < 32 for char in path)):
        raise ValueError('배경 사진은 로컬 파일의 절대 경로를 사용해주세요.')
    normalized = path.replace('\\', '/')
    # Includes mixed separators, UNC, Win32 device and extended-length paths.
    if normalized.startswith('//') or path.startswith('\\'):
        raise ValueError('네트워크 또는 장치 경로는 배경 사진으로 사용할 수 없습니다.')
    if re.match(r'^[A-Za-z]:/', normalized):
        remainder = normalized[3:]
        if any(char in remainder for char in ':<>"|?*'):
            raise ValueError('올바른 로컬 배경 사진 경로를 선택해주세요.')
        for part in remainder.split('/'):
            stem = part.rstrip(' .').split('.')[0].upper()
            if stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(?:COM|LPT)[1-9]', stem):
                raise ValueError('장치 경로는 배경 사진으로 사용할 수 없습니다.')
        return
    if path.startswith('/') and '\\' not in path:
        return  # POSIX absolute paths remain valid portable/local data.
    raise ValueError('배경 사진은 상대 경로나 URL이 아닌 로컬 절대 경로여야 합니다.')


def validate_style(style):
    """Validate a full or partial style and reject identity-bearing fields."""
    _mapping(style, STYLE_KEYS, '디자인')
    if 'bg_photo_mode' in style and style['bg_photo_mode'] not in ('auto', 'preset', 'custom'):
        raise ValueError('지원하지 않는 배경 모드입니다.')
    if 'bg_photo_idx' in style:
        _number(style['bg_photo_idx'], 0, 7, '배경 번호', integer=True)
    if 'custom_background' in style:
        _background_path(style['custom_background'])
    if 'dday_count' in style:
        _number(style['dday_count'], 1, 10, 'D-Day 표시 개수', integer=True)
    if 'font_colors' in style:
        _mapping(style['font_colors'], COLOR_KEYS, '글자 색상')
        for key, color in style['font_colors'].items():
            _color(color, key)
    if 'sections' in style:
        _mapping(style['sections'], SECTION_IDS, '배치')
        for name, section in style['sections'].items():
            _mapping(section, SECTION_KEYS, name)
            for key, value in section.items():
                if key == 'show':
                    if not isinstance(value, bool):
                        raise ValueError(f'{name}: 표시 여부는 참/거짓이어야 합니다.')
                elif key in ('x', 'y'):
                    _number(value, 0, 100, f'{name}.{key}')
                elif key == 'font_size':
                    _number(value, 50, 200, f'{name}.{key}')
                elif key == 'stroke_width':
                    _number(value, 0, 5, f'{name}.{key}', integer=True)
                elif key == 'shadow_blur':
                    _number(value, 0, 10, f'{name}.{key}')
                elif key == 'shadow_opacity':
                    _number(value, 0, 255, f'{name}.{key}', integer=True)
    if 'text_effects' in style:
        effects = style['text_effects']
        _mapping(effects, EFFECT_KEYS, '글자 효과')
        for key, value in effects.items():
            if key == 'stroke_color':
                _color(value, key)
            elif key == 'stroke_width':
                _number(value, 0, 5, key, integer=True)
            elif key == 'shadow_blur':
                _number(value, 0, 10, key)
            elif key == 'shadow_opacity':
                _number(value, 0, 255, key, integer=True)


def extract_style(cfg):
    """Return an independent allowlisted design snapshot, without school data."""
    if not isinstance(cfg, dict):
        raise ValueError('설정은 객체여야 합니다.')
    return deepcopy({key: value for key, value in cfg.items() if key in STYLE_KEYS})


def _merge(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def apply_style(cfg, style):
    """Compose a draft configuration. The supplied objects remain unchanged."""
    if not isinstance(cfg, dict):
        raise ValueError('설정은 객체여야 합니다.')
    validate_style(style)
    return _merge(deepcopy(cfg), style)


def builtins(default_cfg):
    """Three complete designs; each call returns fresh independent dictionaries."""
    base = extract_style(default_cfg)
    validate_style(base)
    school = deepcopy(base)
    school.update(bg_photo_mode='preset', bg_photo_idx=3, custom_background=None, dday_count=3)
    for name in SECTION_IDS:
        school['sections'][name]['show'] = name != 'date_info'

    focus = deepcopy(base)
    focus.update(bg_photo_mode='preset', bg_photo_idx=1, custom_background=None, dday_count=1)
    for name in SECTION_IDS:
        focus['sections'][name]['show'] = name in ('date_info', 'dday')
    focus['sections']['date_info'].update(x=50, y=32, font_size=150)
    focus['sections']['dday'].update(x=92, y=80, font_size=130)
    focus['font_colors'].update(date_weekday='#DAE8E0', date_calendar='#FFFFFF',
                                dday_label='#9FE0BC', dday_name='#FFFFFF')

    photo = deepcopy(base)
    photo.update(bg_photo_mode='preset', bg_photo_idx=2, custom_background=None, dday_count=1)
    for name in SECTION_IDS:
        photo['sections'][name]['show'] = name == 'date_info'
    photo['sections']['date_info'].update(x=50, y=83, font_size=90, shadow_opacity=130)
    photo['font_colors'].update(date_weekday='#FFF4E4', date_calendar='#FFFFFF')

    result = [dict(id='builtin-school', name='수업용', style=school),
              dict(id='builtin-focus', name='집중용', style=focus),
              dict(id='builtin-photo', name='배경 중심', style=photo)]
    for theme in result:
        validate_style(theme['style'])
    return result


def _name(value):
    if not isinstance(value, str):
        raise ValueError('테마 이름을 입력해주세요.')
    value = value.strip()
    if not value or len(value) > 40 or any(ord(char) < 32 for char in value):
        raise ValueError('테마 이름은 1~40자이며 줄바꿈을 포함할 수 없습니다.')
    return value


class ThemeStore:
    """Local design library. Reading/selecting never writes the active config."""
    def __init__(self, path, defaults):
        self.path = Path(path)
        self.defaults = extract_style(defaults)
        validate_style(self.defaults)

    def _read(self):
        if not self.path.exists():
            return []
        try:
            document = json.loads(self.path.read_text(encoding='utf-8'))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('테마 파일을 읽을 수 없습니다. 파일을 백업하고 내용을 확인해주세요.') from error
        if not isinstance(document, dict) or set(document) != {'schema_version', 'themes'}:
            raise ValueError('테마 파일 형식이 올바르지 않습니다.')
        if type(document['schema_version']) is not int or document['schema_version'] != SCHEMA_VERSION:
            raise ValueError('지원하지 않는 테마 파일 버전입니다. 최신 프로그램에서 확인해주세요.')
        themes = document['themes']
        if not isinstance(themes, list):
            raise ValueError('테마 목록 형식이 올바르지 않습니다.')
        ids = {theme['id'] for theme in builtins(self.defaults)}
        names = {theme['name'].casefold() for theme in builtins(self.defaults)}
        validated = []
        for item in themes:
            if not isinstance(item, dict) or set(item) != {'id', 'name', 'style'}:
                raise ValueError('저장된 테마 형식이 올바르지 않습니다.')
            identifier = item['id']
            name = _name(item['name'])
            if not isinstance(identifier, str) or not identifier.startswith('user-') or identifier in ids:
                raise ValueError('저장된 테마 식별자가 올바르지 않거나 중복됩니다.')
            if name.casefold() in names:
                raise ValueError('저장된 테마 이름이 중복됩니다.')
            style = apply_style(self.defaults, item['style'])
            validated.append({'id': identifier, 'name': name, 'style': style})
            ids.add(identifier)
            names.add(name.casefold())
        return validated

    def list_themes(self):
        return builtins(self.defaults) + self._read()

    def _write(self, saved):
        atomic_json(str(self.path), {'schema_version': SCHEMA_VERSION, 'themes': saved})

    def _find(self, themes, identifier):
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('테마를 선택해주세요.')
        for theme in themes:
            if theme['id'] == identifier:
                return theme
        raise ValueError('선택한 테마를 찾을 수 없습니다. 목록을 다시 확인해주세요.')

    def _check_name(self, saved, name, exclude_id=None):
        names = {item['name'].casefold() for item in builtins(self.defaults) + saved
                 if item['id'] != exclude_id}
        if name.casefold() in names:
            raise ValueError('같은 이름의 테마가 있습니다. 다른 이름을 입력해주세요.')

    def _append(self, saved, name, style):
        """Caller owns the store transaction; never acquire another file lock."""
        self._check_name(saved, name)
        theme = {'id': 'user-' + uuid4().hex, 'name': name,
                 'style': apply_style(self.defaults, style)}
        self._write(saved + [theme])
        return deepcopy(theme)

    def _save_new(self, name, style):
        name = _name(name)
        validate_style(style)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with desktop_transaction(str(self.path.parent)):
            return self._append(self._read(), name, style)

    def save(self, name, cfg):
        return self._save_new(name, extract_style(cfg))

    def rename(self, identifier, name):
        name = _name(name)
        if isinstance(identifier, str) and identifier in {item['id'] for item in builtins(self.defaults)}:
            raise ValueError('기본 테마는 이름을 변경할 수 없습니다. 복제한 뒤 변경해주세요.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with desktop_transaction(str(self.path.parent)):
            saved = self._read()
            theme = self._find(saved, identifier)
            self._check_name(saved, name, exclude_id=identifier)
            theme['name'] = name
            self._write(saved)
            return deepcopy(theme)

    def duplicate(self, identifier, name):
        name = _name(name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with desktop_transaction(str(self.path.parent)):
            saved = self._read()
            source = self._find(builtins(self.defaults) + saved, identifier)
            return self._append(saved, name, source['style'])

    def delete(self, identifier):
        if isinstance(identifier, str) and identifier in {item['id'] for item in builtins(self.defaults)}:
            raise ValueError('기본 테마는 삭제할 수 없습니다.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with desktop_transaction(str(self.path.parent)):
            saved = self._read()
            self._find(saved, identifier)
            self._write([item for item in saved if item['id'] != identifier])

    def export_theme(self, identifier, path):
        """Write a design-only snapshot. Photos are referenced, not bundled."""
        theme = self._find(self.list_themes(), identifier)
        target = Path(path)
        if target.resolve() == self.path.resolve() or target.name == '.sync.lock':
            raise ValueError('테마 저장소와 다른 내보내기 파일을 선택해주세요.')
        style = extract_style(theme['style'])
        validate_style(style)
        document = {'kind': EXPORT_KIND, 'schema_version': SCHEMA_VERSION,
                    'name': theme['name'], 'style': style}
        target.parent.mkdir(parents=True, exist_ok=True)
        with desktop_transaction(str(target.parent)):
            atomic_json(str(target), document)

    def import_theme(self, path, name=None):
        """Validate an untrusted, bounded JSON document before touching the store."""
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_IMPORT_BYTES + 1)
        if len(data) > MAX_IMPORT_BYTES:
            raise ValueError('가져올 테마 파일은 2MB 이하여야 합니다.')
        try:
            document = json.loads(data.decode('utf-8-sig'))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('가져올 테마 파일이 올바른 JSON 형식이 아닙니다.') from error
        if not isinstance(document, dict) or set(document) != {'kind', 'schema_version', 'name', 'style'}:
            raise ValueError('가져올 테마 파일 형식이 올바르지 않습니다.')
        if document['kind'] != EXPORT_KIND:
            raise ValueError('School Wallpaper Sync 테마 파일이 아닙니다.')
        if type(document['schema_version']) is not int or document['schema_version'] != SCHEMA_VERSION:
            raise ValueError('지원하지 않는 테마 파일 버전입니다.')
        original_name = _name(document['name'])
        validate_style(document['style'])
        return self._save_new(original_name if name is None else name, document['style'])
