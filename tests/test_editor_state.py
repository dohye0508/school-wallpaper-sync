import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main
from core.editor_state import (EditHistory, backup_settings, read_settings_backup,
                               describe_cache, MAX_BACKUP_BYTES, BACKUP_KIND)


class HistoryTests(unittest.TestCase):
    def test_undo_redo_and_boundaries(self):
        history = EditHistory({'x': 0})
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)
        self.assertIsNone(history.undo())
        self.assertIsNone(history.redo())
        self.assertTrue(history.record({'x': 1}))
        self.assertTrue(history.record({'x': 2}))
        self.assertEqual(history.undo(), {'x': 1})
        self.assertTrue(history.can_redo)
        self.assertEqual(history.undo(), {'x': 0})
        self.assertIsNone(history.undo())
        self.assertEqual(history.redo(), {'x': 1})
        self.assertEqual(history.redo(), {'x': 2})
        self.assertIsNone(history.redo())

    def test_snapshots_are_deep_copied_on_input_and_output(self):
        initial = {'colors': {'a': 'red'}}
        history = EditHistory(initial)
        initial['colors']['a'] = 'green'
        newer = {'colors': {'a': 'blue'}}
        history.record(newer)
        newer['colors']['a'] = 'black'
        old = history.undo()
        self.assertEqual(old['colors']['a'], 'red')
        old['colors']['a'] = 'white'
        self.assertEqual(history.redo()['colors']['a'], 'blue')
        self.assertEqual(history.undo()['colors']['a'], 'red')

    def test_new_branch_discards_redo_but_duplicate_does_not(self):
        history = EditHistory(0)
        history.record(1)
        history.record(2)
        history.undo()
        self.assertFalse(history.record(1))
        self.assertTrue(history.can_redo)
        history.record(3)
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo(), 1)
        self.assertEqual(history.redo(), 3)

    def test_limit_and_reset(self):
        history = EditHistory(0, limit=3)
        for value in range(1, 8):
            history.record(value)
        self.assertEqual(history.undo(), 6)
        self.assertEqual(history.undo(), 5)
        self.assertIsNone(history.undo())
        history.reset({'new': []})
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)
        self.assertFalse(history.record({'new': []}))

    def test_single_slot_and_invalid_limits(self):
        history = EditHistory(0, limit=1)
        self.assertTrue(history.record(1))
        self.assertFalse(history.can_undo)
        for value in (0, -1, True, 1.5, '60'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                EditHistory({}, value)


class SettingsBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'backup.json'
        self.cfg = main.merge_defaults({'school_name': '테스트 학교', 'school_kind': '중학교',
                        'edu_code': 'B10', 'school_code': '123', 'grade': '1', 'classnm': '2',
                        'api_key': 'DO-NOT-EXPORT', 'display_day': 'tomorrow',
                        'manual_dday': [{'name': '시험', 'date': '20261225', 'color': '#112233'}],
                        'manual_timetable': [{'date': '20261112', 'period': '1', 'subject': '수학'}]})

    def write_document(self, settings=None, **overrides):
        document = {'schema_version': 1, 'kind': BACKUP_KIND, 'settings': {} if settings is None else settings}
        document.update(overrides)
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')

    def test_round_trip_excludes_keys_and_preserves_current_credentials(self):
        self.cfg['unknown_private_value'] = 'EXCLUDE-ME'
        self.assertEqual(backup_settings(self.path, self.cfg), self.path)
        raw = self.path.read_text(encoding='utf-8')
        self.assertNotIn('DO-NOT-EXPORT', raw)
        self.assertNotIn('api_key', raw)
        self.assertNotIn('EXCLUDE-ME', raw)
        current = main.merge_defaults({'api_key': 'KEEP-ME', 'school_name': 'old'})
        before = self.path.read_bytes()
        loaded = read_settings_backup(self.path, current)
        self.assertEqual(loaded['api_key'], 'KEEP-ME')
        self.assertEqual(loaded['school_name'], '테스트 학교')
        self.assertEqual(loaded['manual_timetable'], self.cfg['manual_timetable'])
        self.assertEqual(loaded['display_day'], 'tomorrow')
        self.assertEqual(current['school_name'], 'old')
        loaded['sections']['meals']['x'] = 1
        self.assertNotEqual(current['sections']['meals']['x'], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_settings_fill_from_current_without_overwriting_nested_values(self):
        self.write_document({'font_colors': {'school_name': '#ABCDEF'}, 'grade': '3'})
        loaded = read_settings_backup(self.path, self.cfg)
        self.assertEqual(loaded['font_colors']['school_name'], '#ABCDEF')
        self.assertEqual(loaded['font_colors']['meals_title'], self.cfg['font_colors']['meals_title'])
        self.assertEqual(loaded['manual_dday'], self.cfg['manual_dday'])
        loaded['manual_dday'][0]['name'] = 'changed'
        self.assertEqual(self.cfg['manual_dday'][0]['name'], '시험')

    def test_partial_identity_restore_clears_previous_manual_timetable(self):
        for changed in ({'edu_code': 'J10'}, {'school_code': '999'},
                        {'school_kind': '고등학교'}, {'grade': '3'}, {'classnm': '4'}):
            with self.subTest(changed=changed):
                self.write_document(changed)
                loaded = read_settings_backup(self.path, self.cfg)
                self.assertEqual(loaded['manual_timetable'], [])
                self.assertEqual(self.cfg['manual_timetable'][0]['subject'], '수학')

    def test_identity_restore_preserves_explicit_manual_timetable(self):
        corrections = [{'date': '20261113', 'period': 2, 'subject': '새 학교 과목'}]
        self.write_document({'school_code': '999', 'manual_timetable': corrections})
        loaded = read_settings_backup(self.path, self.cfg)
        self.assertEqual(loaded['manual_timetable'], corrections)
        self.assertEqual(self.cfg['manual_timetable'][0]['subject'], '수학')

    def test_same_identity_partial_restore_keeps_manual_timetable(self):
        self.write_document({'school_code': self.cfg['school_code'], 'school_name': '새 표시 이름',
                             'display_day': 'today'})
        loaded = read_settings_backup(self.path, self.cfg)
        self.assertEqual(loaded['manual_timetable'], self.cfg['manual_timetable'])
        loaded['manual_timetable'][0]['subject'] = 'changed'
        self.assertEqual(self.cfg['manual_timetable'][0]['subject'], '수학')

    def test_invalid_root_keys_versions_and_kind(self):
        for override in ({'schema_version': True}, {'schema_version': 2}, {'schema_version': '1'},
                         {'kind': 'theme'}, {'unexpected': 1}, {'settings': []}, {'kind': None}):
            with self.subTest(override=override):
                self.write_document(**override)
                with self.assertRaises(ValueError):
                    read_settings_backup(self.path, self.cfg)

    def test_invalid_fields_and_styles(self):
        cases = [{'api_key': 'replace'}, {'unknown': True}, {'grade': 3}, {'school_name': 'x'*201},
                 {'school_kind': '대학교'}, {'edu_code': 'bad\nvalue'},
                 {'sections': {'meals': {'x': -1}}}, {'font_colors': {'school_name': 'red'}},
                 {'display_day': 'yesterday'}, {'manual_timetable': 'wrong'},
                 {'manual_timetable': [{'date': '20261112', 'period': '0', 'subject': '수학'}]}]
        for settings in cases:
            with self.subTest(settings=settings):
                self.write_document(settings)
                with self.assertRaises(ValueError):
                    read_settings_backup(self.path, self.cfg)

    def test_manual_events_validate_date_name_color_and_unknown_keys(self):
        base = {'name': '시험', 'date': '20261225', 'color': None}
        cases = [{'date': '20260230'}, {'date': '2026122'}, {'date': 20261225}, {'name': ''},
                 {'name': 'x'*201}, {'name': 'line\nbreak'}, {'name': 42}, {'color': 'red'},
                 {'color': 123}, {'extra': 'unexpected'}]
        for changed in cases:
            with self.subTest(changed=changed):
                self.write_document({'manual_dday': [dict(base, **changed)]})
                with self.assertRaises(ValueError):
                    read_settings_backup(self.path, self.cfg)
        for color in (None, '#123456', '#12345678'):
            self.write_document({'manual_dday': [dict(base, color=color)]})
            self.assertEqual(read_settings_backup(self.path, self.cfg)['manual_dday'][0]['color'], color)

    def test_malformed_oversized_duplicate_and_nonfinite_json_rejected(self):
        cases = [b'{bad', b'\xff', b'[]', b' ' * (MAX_BACKUP_BYTES + 1), b'[' * 10000 + b']' * 10000,
                 b'{"schema_version":1,"schema_version":1,"kind":"school-wallpaper-settings","settings":{}}',
                 b'{"schema_version":1,"kind":"school-wallpaper-settings","settings":{"sections":{"meals":{"x":NaN}}}}']
        for raw in cases:
            with self.subTest(prefix=raw[:40]):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    read_settings_backup(self.path, self.cfg)

    def test_export_validation_and_write_failure_preserve_existing_backup(self):
        backup_settings(self.path, self.cfg)
        before = self.path.read_bytes()
        invalid = dict(self.cfg, manual_dday=[{'name': 'bad', 'date': '20260230'}])
        with self.assertRaises(ValueError):
            backup_settings(self.path, invalid)
        self.assertEqual(self.path.read_bytes(), before)
        with patch('core.storage.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                backup_settings(self.path, self.cfg)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])


class CacheDescriptionTests(unittest.TestCase):
    def test_missing_stale_invalid_cache(self):
        for cache in (None, {}, [], {'date': '20261111'}):
            self.assertEqual(describe_cache(cache, '20261112'), '오늘 데이터 없음')

    def test_mixed_states_and_recorded_time_are_explicit(self):
        label = describe_cache({'date': '20261112', 'updated_at': '2026-11-12 09:00:00',
                    'states': {'timetable': 'fresh', 'meals': 'cached', 'schedule': 'empty'}}, '20261112')
        self.assertIn('시간표: 조회됨', label)
        self.assertIn('급식: 캐시 사용', label)
        self.assertIn('학사일정: 자료 없음', label)
        self.assertIn('캐시 기록: 2026-11-12 09:00:00', label)
        self.assertNotIn('성공 시각', label)
        self.assertNotIn('최신', label)

    def test_unavailable_and_legacy_unknown_are_not_reported_fresh(self):
        label = describe_cache({'date': '20261112', 'updated_at': 'not a timestamp',
                                'states': {'meals': 'unavailable', 'timetable': {}}}, '20261112')
        self.assertIn('급식: 조회 실패', label)
        self.assertIn('시간표: 상태 미확인', label)
        self.assertNotIn('캐시 기록:', label)
        self.assertNotIn('조회됨', label)


if __name__ == '__main__':
    unittest.main()
