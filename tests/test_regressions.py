import json
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import main
from PIL import Image
from core import neis
from core.latest import LatestWorker
from core.storage import atomic_json
from core.sync import identity, resolve_data


class NeisTests(unittest.TestCase):
    def response(self, payload):
        return Mock(content=json.dumps(payload).encode('utf-8'))

    def test_key_is_preserved_and_input_not_mutated(self):
        params = {'KEY': 'user-key'}
        with patch.object(neis.requests, 'get', return_value=self.response(
                {'RESULT': {'CODE': 'INFO-200'}})) as get:
            self.assertEqual(neis.neis_get('schoolInfo', params), {})
        self.assertEqual(get.call_args.kwargs['params']['KEY'], 'user-key')
        self.assertEqual(params, {'KEY': 'user-key'})

    def test_api_errors_are_failures_not_empty(self):
        cfg = {'api_key': 'test', 'edu_code': 'B', 'school_code': '1'}
        for payload in ({'RESULT': {'CODE': 'ERROR-300'}}, {}, [],
                        {'mealServiceDietInfo': [{'head': [{'RESULT': {'CODE': 'ERROR-500'}}]}]}):
            with self.subTest(payload=payload), patch.object(neis.requests, 'get', return_value=self.response(payload)):
                self.assertIsNone(neis.fetch_meal(cfg, '20261112'))

    def test_http_failure_and_invalid_json(self):
        response = self.response({})
        response.raise_for_status.side_effect = RuntimeError('HTTP 503')
        with patch.object(neis.requests, 'get', return_value=response):
            with self.assertRaises(RuntimeError):
                neis.neis_get('x', {})
        with patch.object(neis.requests, 'get', return_value=Mock(content=b'bad json')):
            with self.assertRaises(ValueError):
                neis.neis_get('x', {})

    def test_failure_log_redacts_api_key(self):
        with self.assertLogs('core.neis', level='WARNING') as captured:
            neis.log_failure('query', RuntimeError('https://example.test?KEY=secret-value'),
                             {'api_key': 'secret-value'})
        self.assertNotIn('secret-value', captured.output[0])
        self.assertIn('<redacted>', captured.output[0])

    def test_empty_and_success(self):
        cfg = {'api_key': 'test', 'edu_code': 'B', 'school_code': '1',
               'school_kind': '중학교', 'grade': '1', 'classnm': '1'}
        with patch.object(neis.requests, 'get', return_value=self.response({'RESULT': {'CODE': 'INFO-200'}})):
            self.assertEqual(neis.fetch_timetable(cfg, '20261112'), [])
        payload = {'misTimetable': [{'head': [{'RESULT': {'CODE': 'INFO-000'}}]},
                   {'row': [{'PERIO': '2', 'ITRT_CNTNT': '영어'}, {'PERIO': '1', 'ITRT_CNTNT': '수학'}]}]}
        with patch.object(neis.requests, 'get', return_value=self.response(payload)):
            self.assertEqual(neis.fetch_timetable(cfg, '20261112')[0]['subject'], '수학')


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {'BASE_DIR': self.tmp.name, 'CONFIG_PATH': str(Path(self.tmp.name)/'config.json'),
                           'DATA_CACHE_PATH': str(Path(self.tmp.name)/'cache.json')}.items():
            p = patch.object(main, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = main.merge_defaults({'edu_code': 'B', 'school_code': '1',
                     'school_kind': '중학교', 'grade': '1', 'classnm': '1'})
        main.save_config(self.cfg)
        self.today = datetime.now().strftime('%Y%m%d')

    def test_fallback_and_valid_empty_are_different(self):
        data = resolve_data({'meals': None, 'timetable': []}, {'meals': ['밥'], 'timetable': ['수학']})
        self.assertEqual(data['meals'], ['밥'])
        self.assertEqual(data['timetable'], [])
        self.assertEqual(data['states'], {'meals': 'cached', 'timetable': 'empty'})

    def test_failed_empty_cache_is_not_promoted_but_successful_empty_cache_is(self):
        cached = {'meals': [], 'timetable': [],
                  'states': {'meals': 'unavailable', 'timetable': 'empty'}}
        data = resolve_data({'meals': None, 'timetable': None}, cached)
        self.assertEqual(data['states'], {'meals': 'unavailable', 'timetable': 'cached'})
        retried = resolve_data({'meals': None, 'timetable': None}, data)
        self.assertEqual(retried['states'], data['states'])
        recovered = resolve_data({'meals': ['밥'], 'timetable': None}, retried)
        self.assertEqual(recovered['states'], {'meals': 'fresh', 'timetable': 'cached'})

    def test_cache_scoped_to_school_class_and_day(self):
        main.save_data_cache(['수학'], [], self.today, self.cfg, [])
        self.assertIsNotNone(main.read_today_cache_if_matching(self.cfg, self.today))
        for key in ('edu_code', 'school_code', 'school_kind', 'grade', 'classnm'):
            other = dict(self.cfg, **{key: 'different'})
            self.assertIsNone(main.read_today_cache_if_matching(other, self.today))
        self.assertIsNone(main.read_today_cache_if_matching(self.cfg, '20000101'))

    def test_preview_read_does_not_fetch_or_write(self):
        with patch.object(main, 'fetch_meal') as fetch:
            main.load_data_cache(self.cfg)
            fetch.assert_not_called()
        self.assertFalse(Path(main.DATA_CACHE_PATH).exists())

    def test_changed_school_discards_result(self):
        def change(*args):
            main.save_config(dict(self.cfg, school_code='2'))
            return ['old school']
        with patch.object(main, 'fetch_meal', side_effect=change), \
             patch.object(main, 'fetch_timetable', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]), \
             patch.object(main, 'render_wallpaper') as render, \
             patch.object(main, 'set_wallpaper') as apply:
            self.assertFalse(main.update_once(self.cfg))
            render.assert_not_called()
            apply.assert_not_called()
        self.assertFalse(Path(main.DATA_CACHE_PATH).exists())

    def test_failure_retains_cached_data(self):
        main.save_data_cache(['수학'], ['밥'], self.today, self.cfg, [])
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None), \
             patch.object(main, 'render_wallpaper', return_value='unused') as render, \
             patch.object(main, 'set_wallpaper'), patch.object(main, 'ensure_autostart'):
            self.assertTrue(main.update_once(self.cfg))
            self.assertEqual(render.call_args.args[2:4], (['수학'], ['밥']))

    def test_no_cache_total_failure_does_not_apply(self):
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None), \
             patch.object(main, 'set_wallpaper') as apply:
            with self.assertRaises(RuntimeError):
                main.update_once(self.cfg)
            apply.assert_not_called()
        self.assertFalse(Path(main.DATA_CACHE_PATH).exists())

    def test_failed_atomic_replace_retains_file(self):
        before = Path(main.CONFIG_PATH).read_bytes()
        with patch('core.storage.os.replace', side_effect=OSError('busy')):
            with self.assertRaises(OSError):
                atomic_json(main.CONFIG_PATH, {'broken': True})
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), before)
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])

    def test_defaults_are_independent(self):
        a, b = main.merge_defaults({}), main.merge_defaults({})
        a['sections']['meals']['x'] = -999
        self.assertNotEqual(a['sections'], b['sections'])

    def test_corrupted_cache_is_ignored(self):
        for content in ('{broken', '[]'):
            Path(main.DATA_CACHE_PATH).write_text(content)
            self.assertIsNone(main.read_today_cache_if_matching(self.cfg, self.today))

    def test_date_rollover_discards_result(self):
        clock = Mock()
        clock.now.side_effect = [datetime(2026, 11, 12, 23, 59), datetime(2026, 11, 13)]
        with patch.object(main, 'datetime', clock), \
             patch.object(main, 'fetch_meal', return_value=[]), \
             patch.object(main, 'fetch_timetable', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]), \
             patch.object(main, 'set_wallpaper') as apply:
            self.assertFalse(main.update_once(self.cfg))
            apply.assert_not_called()

    def test_render_without_windows_or_network(self):
        for size in ((1920, 1080), (1080, 2400)):
            output = str(Path(self.tmp.name)/'render.png')
            with patch.object(main, 'WALLPAPER_PATH', output), \
                 patch.object(main, 'fetch_background', return_value=Image.new('RGB', size)), \
                 patch.object(main, 'get_screen_size', side_effect=AssertionError('OS called')):
                main.render_wallpaper(self.cfg, datetime(2026, 11, 12),
                    [{'period': '1', 'subject': '수학'}],
                    [{'name': '중식', 'dishes': '밥\n김치'}], [], size=size)
            with Image.open(output) as image:
                self.assertEqual(image.size, size)
                image.verify()


class PreviewTests(unittest.TestCase):
    def test_only_latest_request_commits(self):
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        rendered, applied = [], []
        def work(value, commit):
            rendered.append(value)
            if value == 1:
                started.set()
                release.wait(5)
            commit(lambda: applied.append(value))
            if value == 3:
                done.set()
        worker = LatestWorker(work)
        worker.submit(1)
        self.assertTrue(started.wait(5))
        worker.submit(2)
        worker.submit(3)
        release.set()
        self.assertTrue(done.wait(5))
        self.assertEqual(rendered, [1, 3])
        self.assertEqual(applied, [3])


if __name__ == '__main__':
    unittest.main()
