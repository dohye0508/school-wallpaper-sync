from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import main
from core.themes import ThemeStore, apply_style, builtins, extract_style, validate_style, MAX_IMPORT_BYTES


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = main.merge_defaults({
            'school_name': '학교', 'school_code': '123', 'edu_code': 'E10',
            'school_kind': '고등학교', 'grade': '2', 'classnm': '4',
            'api_key': 'private-key',
            'manual_dday': [{'name': '개인 일정', 'date': '20261231'}],
        })

    def test_extract_excludes_identity_and_personal_events(self):
        style = extract_style(self.cfg)
        for key in ('school_name', 'school_code', 'edu_code', 'school_kind',
                    'grade', 'classnm', 'api_key', 'manual_dday'):
            self.assertNotIn(key, style)
        style['sections']['timetable']['show'] = False
        self.assertTrue(self.cfg['sections']['timetable']['show'])

    def test_partial_style_preserves_identity_and_unspecified_fields(self):
        before = deepcopy(self.cfg)
        style = {'sections': {'timetable': {'x': 25}}, 'font_colors': {'meals_title': '#123456'}}
        result = apply_style(self.cfg, style)
        self.assertEqual(result['sections']['timetable']['x'], 25)
        self.assertEqual(result['sections']['timetable']['y'], before['sections']['timetable']['y'])
        for key in ('school_code', 'api_key', 'manual_dday'):
            self.assertEqual(result[key], before[key])
        self.assertEqual(self.cfg, before)
        result['manual_dday'][0]['name'] = 'changed'
        self.assertEqual(self.cfg['manual_dday'][0]['name'], '개인 일정')

    def test_three_builtins_are_distinct_and_independent(self):
        presets = builtins(self.cfg)
        self.assertEqual([item['name'] for item in presets], ['수업용', '집중용', '배경 중심'])
        self.assertEqual(len({item['id'] for item in presets}), 3)
        self.assertTrue(presets[0]['style']['sections']['timetable']['show'])
        self.assertFalse(presets[1]['style']['sections']['timetable']['show'])
        self.assertTrue(presets[1]['style']['sections']['dday']['show'])
        self.assertFalse(presets[2]['style']['sections']['dday']['show'])
        presets[0]['style']['sections']['dday']['x'] = 0
        self.assertNotEqual(presets[1]['style']['sections']['dday']['x'], 0)
        self.assertNotEqual(builtins(self.cfg)[0]['style']['sections']['dday']['x'], 0)

    def test_validation_rejects_invalid_values_and_secret_fields(self):
        invalid = [
            {'api_key': 'oops'}, {'manual_dday': []}, {'school_code': 'different'},
            {'bg_photo_mode': 'unknown'}, {'bg_photo_idx': -1}, {'bg_photo_idx': True},
            {'bg_photo_idx': 8}, {'custom_background': []}, {'dday_count': 0},
            {'sections': {'unknown': {'x': 10}}},
            {'sections': {'dday': {'x': float('nan')}}},
            {'sections': {'dday': {'y': 101}}},
            {'sections': {'dday': {'show': 'false'}}},
            {'sections': {'dday': {'font_size': 0}}},
            {'sections': {'dday': {'shadow_opacity': 256}}},
            {'sections': {'dday': {'stroke_width': 0.5}}},
            {'font_colors': {'meals_title': 'red'}},
            {'font_colors': {'meals_title': '#ZZZZZZ'}},
            {'text_effects': {'stroke_color': None}},
        ]
        for style in invalid:
            with self.subTest(style=style), self.assertRaises(ValueError):
                apply_style(self.cfg, style)

    def test_valid_custom_path_and_alpha_colors_are_portable_data(self):
        style = {'custom_background': 'C:/not-installed/photo.png',
                 'font_colors': {'meals_title': '#abcdef80'}, 'sections': {'dday': {'x': 92.5}}}
        validate_style(style)
        self.assertEqual(apply_style(self.cfg, style)['custom_background'], style['custom_background'])

    def test_background_paths_only_accept_local_absolute_syntax(self):
        valid_paths = (None, r'C:\Users\사용자\사진.png', 'D:/photos/my photo.jpg', '/tmp/photo.png')
        for path in valid_paths:
            with self.subTest(path=path):
                validate_style({'custom_background': path})
        invalid_paths = (
            '', 'photos/photo.jpg', 'C:photo.png', r'\photo.png',
            r'\\server\share\photo.png', '//server/share/photo.png',
            '/\\server/share/photo.png', '\\/server/share/photo.png',
            r'\\?\C:\photo.png', r'\\.\C:\photo.png', r'\??\C:\photo.png',
            'https://example.test/photo.png', 'file:///C:/photo.png',
            r'C:\photos\NUL.png', r'C:\photos\COM1', r'C:\photo.png:stream',
        )
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_style({'custom_background': path})


class ThemeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'themes.json'
        self.cfg = main.merge_defaults({'api_key': 'secret', 'school_code': '123',
                                      'manual_dday': [{'name': 'Private', 'date': '20261231'}]})
        self.store = ThemeStore(self.path, self.cfg)

    def test_reading_and_selecting_never_write_files_or_change_config(self):
        before = deepcopy(self.cfg)
        selected = self.store.list_themes()[1]
        apply_style(self.cfg, selected['style'])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.cfg, before)

    def test_save_survives_restart_without_school_or_credentials(self):
        theme = self.store.save('  내가 만든 테마  ', self.cfg)
        self.assertEqual(theme['name'], '내가 만든 테마')
        loaded = ThemeStore(self.path, self.cfg).list_themes()
        self.assertEqual(loaded[-1], theme)
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(document['schema_version'], 1)
        self.assertEqual(set(document), {'schema_version', 'themes'})
        content = self.path.read_text(encoding='utf-8')
        self.assertNotIn('secret', content)
        self.assertNotIn('Private', content)
        self.assertNotIn('school_code', content)
        theme['style']['sections']['dday']['x'] = 0
        self.assertNotEqual(self.store.list_themes()[-1]['style']['sections']['dday']['x'], 0)

    def test_invalid_and_duplicate_names_do_not_overwrite(self):
        self.store.save('My Theme', self.cfg)
        before = self.path.read_bytes()
        for name in ('', ' ', 'x'*41, 'bad\nname', 'my theme', ' My Theme ', '수업용'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.save(name, self.cfg)
            self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_and_future_files_are_preserved(self):
        contents = ('{broken', '[]', '{"schema_version":2,"themes":[]}',
                    '{"schema_version":true,"themes":[]}',
                    '{"schema_version":1,"themes":{}}',
                    '{"schema_version":1,"themes":[],"api_key":"secret"}')
        for content in contents:
            self.path.write_text(content, encoding='utf-8')
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    self.store.list_themes()
                with self.assertRaises(ValueError):
                    self.store.save('New Theme', self.cfg)
                self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_invalid_saved_style_cannot_inject_school_settings(self):
        self.path.write_text(json.dumps({'schema_version': 1, 'themes': [
            {'id': 'user-example', 'name': 'Injected', 'style': {'api_key': 'unwanted'}}
        ]}), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.list_themes()

    def test_partial_stored_style_receives_missing_defaults(self):
        self.path.write_text(json.dumps({'schema_version': 1, 'themes': [
            {'id': 'user-example', 'name': 'Small Theme', 'style': {'dday_count': 1}}
        ]}), encoding='utf-8')
        theme = self.store.list_themes()[-1]
        self.assertEqual(theme['style']['dday_count'], 1)
        self.assertEqual(theme['style']['sections'], self.cfg['sections'])

    def test_failed_atomic_replace_keeps_saved_library(self):
        self.store.save('Original', self.cfg)
        before = self.path.read_bytes()
        with patch('core.storage.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.store.save('Second', self.cfg)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.store.list_themes()), 4)

    def test_concurrent_saves_keep_both_themes(self):
        errors = []
        def save(name):
            try:
                ThemeStore(self.path, self.cfg).save(name, self.cfg)
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=save, args=(name,)) for name in ('One', 'Two')]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(errors)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual({item['name'] for item in self.store.list_themes()[3:]}, {'One', 'Two'})

    def test_rename_preserves_identity_and_style(self):
        item = self.store.save('Original', self.cfg)
        changed = self.store.rename(item['id'], '새 이름')
        self.assertEqual(changed['id'], item['id'])
        self.assertEqual(changed['style'], item['style'])
        self.assertEqual(ThemeStore(self.path, self.cfg).list_themes()[-1]['name'], '새 이름')
        self.store.rename(item['id'], '새 이름')
        with self.assertRaises(ValueError):
            self.store.rename(item['id'], '수업용')

    def test_builtin_cannot_be_renamed_or_deleted(self):
        for identifier in ('builtin-school', 'builtin-focus', 'builtin-photo'):
            with self.assertRaises(ValueError):
                self.store.rename(identifier, 'Edited')
            with self.assertRaises(ValueError):
                self.store.delete(identifier)
        self.assertFalse(self.path.exists())

    def test_duplicate_builtin_and_user_then_delete(self):
        first = self.store.duplicate('builtin-focus', '집중 복제')
        second = self.store.duplicate(first['id'], '집중 복제 2')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['style'], second['style'])
        self.store.delete(first['id'])
        self.assertEqual([item['id'] for item in self.store.list_themes()[3:]], [second['id']])
        with self.assertRaises(ValueError):
            self.store.delete(first['id'])
        with self.assertRaises(ValueError):
            self.store.duplicate(second['id'], second['name'])

    def test_export_and_import_round_trip_design_only(self):
        item = self.store.save('Export Me', self.cfg)
        export = Path(self.tmp.name)/'export.json'
        self.store.export_theme(item['id'], export)
        document = json.loads(export.read_text(encoding='utf-8'))
        self.assertEqual(set(document), {'kind','schema_version','name','style'})
        self.assertEqual(document['kind'], 'school-wallpaper-theme')
        self.assertEqual(document['style'], item['style'])
        self.assertNotIn('secret', export.read_text(encoding='utf-8'))
        self.assertNotIn('Private', export.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            self.store.import_theme(export)
        imported = self.store.import_theme(export, 'Imported')
        self.assertNotEqual(imported['id'], item['id'])
        self.assertEqual(imported['style'], item['style'])
        self.assertEqual(imported['name'], 'Imported')

    def test_export_builtin_and_protect_store_path(self):
        export = Path(self.tmp.name)/'builtin.json'
        self.store.export_theme('builtin-school', export)
        self.assertFalse(self.path.exists())
        self.assertEqual(json.loads(export.read_text(encoding='utf-8'))['name'], '수업용')
        for target in (self.path, Path(self.tmp.name)/'.sync.lock'):
            with self.assertRaises(ValueError):
                self.store.export_theme('builtin-school', target)
        self.assertFalse(self.path.exists())

    def test_import_rejects_invalid_fields_and_versions_without_changing_store(self):
        self.store.save('Existing', self.cfg)
        before = self.path.read_bytes()
        good = {'kind':'school-wallpaper-theme','schema_version':1,'name':'Import',
                'style':{'dday_count':1}}
        documents = [dict(good, schema_version=2), dict(good, kind='different'),
                     dict(good, api_key='secret'), dict(good, style={'api_key':'secret'}),
                     dict(good, style={'sections':{'dday':{'font_size':10**1000}}}),
                     dict(good, style={'display_day':'tomorrow'}), dict(good, name='')]
        export = Path(self.tmp.name)/'invalid.json'
        for document in documents:
            export.write_text(json.dumps(document), encoding='utf-8')
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.store.import_theme(export)
            self.assertEqual(self.path.read_bytes(), before)

    def test_import_is_size_bounded(self):
        export = Path(self.tmp.name)/'oversized.json'
        export.write_bytes(b' '*(MAX_IMPORT_BYTES+1))
        with self.assertRaisesRegex(ValueError, '2MB'):
            self.store.import_theme(export)
        self.assertFalse(self.path.exists())

    def test_import_rejects_network_background_without_touching_library(self):
        self.store.save('Existing', self.cfg)
        before = self.path.read_bytes()
        export = Path(self.tmp.name)/'network-theme.json'
        document = {'kind':'school-wallpaper-theme', 'schema_version':1, 'name':'Network',
                    'style':{'bg_photo_mode':'custom','custom_background':r'\\server\share\photo.png'}}
        export.write_text(json.dumps(document), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.import_theme(export)
        self.assertEqual(self.path.read_bytes(), before)

    def test_deep_json_is_reported_as_invalid_import(self):
        export = Path(self.tmp.name)/'deep.json'
        export.write_bytes(b'['*10000 + b'0' + b']'*10000)
        with self.assertRaisesRegex(ValueError, 'JSON'):
            self.store.import_theme(export)
        self.assertFalse(self.path.exists())

    def test_failed_rename_or_delete_retains_library(self):
        item = self.store.save('Original', self.cfg)
        before = self.path.read_bytes()
        with patch('core.storage.os.replace', side_effect=OSError('disk error')):
            for action in (lambda: self.store.rename(item['id'], 'New'),
                           lambda: self.store.delete(item['id'])):
                with self.assertRaises(OSError):
                    action()
                self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
