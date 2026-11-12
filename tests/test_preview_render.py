import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import main


class CompositionTests(unittest.TestCase):
    def test_explicit_preview_has_no_wallpaper_network_or_screen_side_effect(self):
        cfg = main.merge_defaults({})
        background = Image.new('RGB', (1920, 1080), '#123456')
        with patch.object(main, 'get_screen_size', side_effect=AssertionError('screen called')), \
             patch.object(main, 'fetch_background', side_effect=AssertionError('network called')), \
             patch.object(main, 'set_wallpaper', side_effect=AssertionError('OS called')), \
             patch.object(Image.Image, 'save', side_effect=AssertionError('file written')):
            result = main.compose_wallpaper(cfg, datetime(2026, 11, 12), [], [], [],
                                            size=(1920, 1080), background=background)
        self.assertEqual(result.size, (1920, 1080))
        self.assertEqual(result.mode, 'RGB')
        self.assertEqual(background.getpixel((0, 0)), (18, 52, 86))
        result.close()
        background.close()

    def test_background_loader_remains_available_without_injected_image(self):
        with patch.object(main, 'fetch_background', return_value=Image.new('RGB', (320, 180))) as load:
            image = main.compose_wallpaper(main.merge_defaults({}), datetime(2026, 11, 12),
                                           [], [], [], size=(320, 180))
            self.assertEqual(image.size, (320, 180))
            load.assert_called_once()
            image.close()

    def test_legacy_render_writes_png_but_does_not_apply_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'wallpaper.png')
            with patch.object(main, 'WALLPAPER_PATH', path), \
                 patch.object(main, 'compose_wallpaper', return_value=Image.new('RGB', (320, 180))), \
                 patch.object(main, 'set_wallpaper') as apply:
                self.assertEqual(main.render_wallpaper({}, datetime.now(), [], []), path)
                apply.assert_not_called()
            with Image.open(path) as image:
                self.assertEqual(image.size, (320, 180))
                image.verify()

    def test_tomorrow_label_manual_subject_and_dday_use_correct_dates(self):
        cfg = main.merge_defaults({'display_day': 'tomorrow', 'manual_timetable': [
            {'date': '20261113', 'period': '1', 'subject': '직접 보정한 과목'}]})
        observed = []
        original_text = main.ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            observed.append(text)
            return original_text(draw, xy, text, *args, **kwargs)
        with patch.object(main.ImageDraw.ImageDraw, 'text', autospec=True, side_effect=capture):
            image = main.compose_wallpaper(cfg, datetime(2026, 11, 12),
                [{'period': '1', 'subject': '원래 과목'}], [],
                [{'name': '시험', 'date': '20261113', 'dday': 50}], size=(1280, 720),
                background=Image.new('RGB', (1280, 720)))
        self.assertIn('내일 · 2026.11.13 (금)', observed)
        self.assertIn('직접 보정한 과목', observed)
        self.assertNotIn('원래 과목', observed)
        self.assertIn('D-1', observed)
        self.assertNotIn('D-50', observed)
        image.close()


class ApplyConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in {
                'BASE_DIR': self.tmp.name,
                'CONFIG_PATH': str(Path(self.tmp.name)/'config.json'),
                'DATA_CACHE_PATH': str(Path(self.tmp.name)/'cache.json'),
                'WALLPAPER_PATH': str(Path(self.tmp.name)/'wallpaper.png')}.items():
            context = patch.object(main, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.saved = main.merge_defaults({'school_name': '학교', 'school_code': '123', 'edu_code': 'B10',
                      'school_kind': '중학교', 'grade': '1', 'classnm': '2', 'api_key': 'test-key',
                      'manual_dday': [{'date': '20261225', 'name': '내 일정'}]})
        main.save_config(self.saved)
        self.original_bytes = Path(main.CONFIG_PATH).read_bytes()
        self.draft = deepcopy(self.saved)
        self.draft['sections']['meals']['show'] = False
        self.draft['font_colors']['school_name'] = '#ABCDEF'
        self.today = datetime.now().strftime('%Y%m%d')
        self.compose = patch.object(main, 'compose_wallpaper',
                                    side_effect=lambda *args, **kwargs: Image.new('RGB', (32, 18)))
        self.compose_mock = self.compose.start()
        self.addCleanup(self.compose.stop)
        self.os_patch = patch.object(main, 'set_wallpaper')
        self.apply_mock = self.os_patch.start()
        self.addCleanup(self.os_patch.stop)
        self.startup_patch = patch.object(main, 'ensure_autostart')
        self.startup_mock = self.startup_patch.start()
        self.addCleanup(self.startup_patch.stop)

    def seed_cache(self):
        main.save_data_cache([{'period': '1', 'subject': '수학'}],
                             [{'name': '중식', 'dishes': '밥'}], self.today, self.saved, [])

    def test_design_apply_uses_cache_and_preserves_school_and_events(self):
        self.seed_cache()
        with patch.object(main, 'fetch_timetable') as fetch:
            result = main.apply_configuration(self.draft)
        fetch.assert_not_called()
        self.apply_mock.assert_called_once_with(main.WALLPAPER_PATH)
        self.assertEqual(result['states']['meals'], 'cached')
        self.assertEqual(result['warnings'], [])
        loaded = main.load_config()
        self.assertFalse(loaded['sections']['meals']['show'])
        for key in ('api_key', 'school_code', 'grade', 'classnm', 'manual_dday'):
            self.assertEqual(loaded[key], self.saved[key])
        self.assertEqual(list(Path(self.tmp.name).glob('preview-apply-*')), [])

    def test_os_failure_restores_exact_config_and_previous_output(self):
        self.seed_cache()
        previous = b'previous wallpaper bytes'
        Path(main.WALLPAPER_PATH).write_bytes(previous)
        self.apply_mock.side_effect = OSError('desktop refused')
        with self.assertRaisesRegex(OSError, 'desktop refused'):
            main.apply_configuration(self.draft)
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.assertEqual(Path(main.WALLPAPER_PATH).read_bytes(), previous)
        self.startup_mock.assert_not_called()
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])
        self.assertEqual(list(Path(self.tmp.name).glob('preview-apply-*')), [])

    def test_config_save_failure_does_not_apply_or_replace_image(self):
        self.seed_cache()
        Path(main.WALLPAPER_PATH).write_bytes(b'old image')
        with patch.object(main, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                main.apply_configuration(self.draft)
        self.apply_mock.assert_not_called()
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.assertEqual(Path(main.WALLPAPER_PATH).read_bytes(), b'old image')

    def test_render_failure_preserves_config(self):
        self.seed_cache()
        self.compose_mock.side_effect = ValueError('invalid color')
        with self.assertRaisesRegex(ValueError, 'invalid color'):
            main.apply_configuration(self.draft)
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.apply_mock.assert_not_called()

    def test_missing_cache_and_failed_fetch_does_not_commit(self):
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None):
            with self.assertRaises(RuntimeError):
                main.apply_configuration(self.draft)
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.apply_mock.assert_not_called()

    def test_offline_manual_timetable_applies_without_claiming_remote_success(self):
        self.draft['manual_timetable'] = [
            {'date': self.today, 'period': 1, 'subject': '수동 과목'}]
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None):
            result = main.apply_configuration(self.draft)
        self.assertEqual(set(result['states'].values()), {'unavailable'})
        self.assertTrue(any('수동 시간표' in warning for warning in result['warnings']))
        self.assertEqual(self.compose_mock.call_args.args[0]['manual_timetable'],
                         self.draft['manual_timetable'])
        self.assertEqual(self.compose_mock.call_args.args[2], [])
        self.apply_mock.assert_called_once_with(main.WALLPAPER_PATH)
        self.assertEqual(main.load_data_cache(self.draft)['timetable'], [])

    def test_offline_manual_timetable_for_other_date_does_not_enable_apply(self):
        self.draft['manual_timetable'] = [
            {'date': (datetime.now() + timedelta(days=1)).strftime('%Y%m%d'),
             'period': 1, 'subject': '내일 수동 과목'}]
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '데이터 조회 실패'):
                main.apply_configuration(self.draft)
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.apply_mock.assert_not_called()

    def test_manual_only_cache_stays_unavailable_and_apply_retries_failed_remote_data(self):
        self.draft['manual_timetable'] = [
            {'date': self.today, 'period': 1, 'subject': '수동 과목'}]
        with patch.object(main, 'fetch_meal', side_effect=[None, None, []]) as meals, \
             patch.object(main, 'fetch_timetable', side_effect=[None, None, []]), \
             patch.object(main, 'fetch_school_schedule', side_effect=[None, None, []]):
            for _ in range(2):
                result = main.apply_configuration(self.draft)
                self.assertEqual(set(result['states'].values()), {'unavailable'})
                self.assertTrue(any('수동 시간표' in warning for warning in result['warnings']))
            recovered = main.apply_configuration(self.draft)
        self.assertEqual(meals.call_count, 3)
        self.assertEqual(set(recovered['states'].values()), {'empty'})
        self.assertEqual(recovered['warnings'], [])

    def test_offline_background_update_uses_latest_matching_manual_timetable(self):
        self.draft['manual_timetable'] = [
            {'date': self.today, 'period': 1, 'subject': '수동 과목'}]
        main.save_config(self.draft)
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None), \
             patch('builtins.print') as log:
            self.assertTrue(main.update_once(self.saved))
        self.assertEqual(self.compose_mock.call_args.args[0]['manual_timetable'],
                         self.draft['manual_timetable'])
        self.assertEqual(set(main.load_data_cache(self.draft)['states'].values()), {'unavailable'})
        self.assertTrue(any('수동 시간표' in str(call) for call in log.call_args_list))
        self.apply_mock.assert_called_once_with(main.WALLPAPER_PATH)

    def test_offline_background_update_without_matching_manual_timetable_preserves_wallpaper(self):
        self.draft['manual_timetable'] = [
            {'date': (datetime.now() + timedelta(days=1)).strftime('%Y%m%d'),
             'period': 1, 'subject': '내일 수동 과목'}]
        main.save_config(self.draft)
        with patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '데이터 조회 실패'):
                main.update_once(self.draft)
        self.apply_mock.assert_not_called()
        self.assertFalse(Path(main.DATA_CACHE_PATH).exists())

    def test_concurrent_configuration_change_discards_apply(self):
        changed = deepcopy(self.saved)
        changed['school_code'] = '999'
        def change(*args):
            main.save_config(changed)
            return []
        with patch.object(main, 'fetch_timetable', side_effect=change), \
             patch.object(main, 'fetch_meal', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, '설정이 변경'):
                main.apply_configuration(self.draft)
        self.assertEqual(main.load_config()['school_code'], '999')
        self.apply_mock.assert_not_called()

    def test_midnight_during_apply_discards_snapshot(self):
        self.seed_cache()
        first = datetime.now()
        clock = Mock()
        clock.now.side_effect = [first, first + timedelta(days=1)]
        with patch.object(main, 'datetime', clock):
            with self.assertRaisesRegex(RuntimeError, '날짜가 변경'):
                main.apply_configuration(self.draft)
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.apply_mock.assert_not_called()

    def test_autostart_failure_reports_warning_after_successful_apply(self):
        self.seed_cache()
        self.startup_mock.side_effect = RuntimeError('scheduler refused')
        result = main.apply_configuration(self.draft)
        self.assertEqual(result['autostart_error'], 'scheduler refused')
        self.assertTrue(result['warnings'])
        self.assertEqual(main.load_config()['font_colors']['school_name'], '#ABCDEF')
        self.apply_mock.assert_called_once()

    def test_refresh_tomorrow_fetches_target_day_without_changing_config_or_wallpaper(self):
        self.seed_cache()
        before_cache = Path(main.DATA_CACHE_PATH).read_bytes()
        self.draft['display_day'] = 'tomorrow'
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
        with patch.object(main, 'fetch_timetable', return_value=[]) as timetable, \
             patch.object(main, 'fetch_meal', return_value=[]) as meals, \
             patch.object(main, 'fetch_school_schedule', return_value=[]) as schedule:
            payload = main.refresh_data(self.draft)
        self.assertEqual(payload['date'], tomorrow)
        self.assertEqual(timetable.call_args.args[1], tomorrow)
        self.assertEqual(meals.call_args.args[1], tomorrow)
        self.assertEqual(schedule.call_args.args[1].date(), datetime.now().date())
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.original_bytes)
        self.apply_mock.assert_not_called()
        self.startup_mock.assert_not_called()
        self.compose_mock.assert_not_called()
        self.assertEqual(main.load_data_cache(self.draft)['date'], tomorrow)
        self.assertEqual(main.load_data_cache(self.saved)['timetable'][0]['subject'], '수학')
        self.assertEqual(Path(main.DATA_CACHE_PATH).read_bytes(), before_cache)

    def test_refresh_active_tomorrow_updates_its_cache(self):
        self.draft['display_day'] = 'tomorrow'
        main.save_config(self.draft)
        with patch.object(main, 'fetch_timetable', return_value=[{'period': '1', 'subject': '내일'}]), \
             patch.object(main, 'fetch_meal', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]):
            payload = main.refresh_data(self.draft)
        self.assertEqual(main.load_data_cache(self.draft)['timetable'], payload['timetable'])
        self.apply_mock.assert_not_called()

    def test_midnight_plus_tomorrow_to_today_switch_discards_background_snapshot(self):
        selected_tomorrow = dict(self.saved, display_day='tomorrow')
        main.save_config(selected_tomorrow)
        def change(*args):
            main.save_config(self.saved)
            return []
        clock = Mock()
        clock.now.side_effect = [datetime(2026, 11, 12, 23, 59), datetime(2026, 11, 13, 0, 1)]
        with patch.object(main, 'datetime', clock), \
             patch.object(main, 'fetch_timetable', side_effect=change), \
             patch.object(main, 'fetch_meal', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]):
            self.assertFalse(main.update_once(selected_tomorrow))
        self.apply_mock.assert_not_called()
        self.assertFalse(Path(main.DATA_CACHE_PATH).exists())

    def test_refresh_changed_school_returns_data_but_does_not_replace_saved_cache(self):
        self.seed_cache()
        before = Path(main.DATA_CACHE_PATH).read_bytes()
        changed = dict(self.saved, school_code='999')
        def change(*args):
            main.save_config(changed)
            return []
        with patch.object(main, 'fetch_timetable', side_effect=change), \
             patch.object(main, 'fetch_meal', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]):
            payload = main.refresh_data(self.draft)
        self.assertEqual(payload['school_code'], self.draft['school_code'])
        self.assertEqual(Path(main.DATA_CACHE_PATH).read_bytes(), before)
        self.apply_mock.assert_not_called()

    def test_refresh_all_failures_preserves_cache_using_fallback(self):
        self.seed_cache()
        with patch.object(main, 'fetch_timetable', return_value=None), \
             patch.object(main, 'fetch_meal', return_value=None), \
             patch.object(main, 'fetch_school_schedule', return_value=None):
            payload = main.refresh_data(self.draft)
        self.assertEqual(payload['states'], {'timetable': 'cached', 'meals': 'cached', 'schedule': 'cached'})
        self.assertEqual(payload['timetable'][0]['subject'], '수학')
        self.apply_mock.assert_not_called()

    def test_background_update_discards_result_if_selected_day_changes(self):
        changed = dict(self.saved, display_day='tomorrow')
        def change(*args):
            main.save_config(changed)
            return []
        with patch.object(main, 'fetch_timetable', side_effect=change), \
             patch.object(main, 'fetch_meal', return_value=[]), \
             patch.object(main, 'fetch_school_schedule', return_value=[]):
            self.assertFalse(main.update_once(self.saved))
        self.apply_mock.assert_not_called()
        self.assertFalse(Path(main.DATA_CACHE_PATH).exists())

    def test_apply_tomorrow_uses_tomorrow_cache(self):
        self.draft['display_day'] = 'tomorrow'
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
        main.save_data_cache([{'period': '1', 'subject': '내일 과목'}], [], tomorrow, self.draft, [])
        with patch.object(main, 'fetch_timetable') as fetch:
            main.apply_configuration(self.draft)
        fetch.assert_not_called()
        self.assertEqual(self.compose_mock.call_args.args[2][0]['subject'], '내일 과목')
        self.assertEqual(main.load_config()['display_day'], 'tomorrow')


if __name__ == '__main__':
    unittest.main()
