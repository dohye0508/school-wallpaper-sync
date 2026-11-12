"""Real Tk widget regression tests, with every desktop/network side effect mocked."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image
import main
import settings_gui


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {
            'BASE_DIR': str(self.directory),
            'CONFIG_PATH': str(self.directory / 'config.json'),
            'DATA_CACHE_PATH': str(self.directory / 'data_cache.json'),
            'WALLPAPER_PATH': str(self.directory / 'wallpaper.png'),
            'BG_CACHE_DIR': str(self.directory / 'bg_cache'),
        }.items():
            self.stack.enter_context(patch.object(main, name, value))
        self.cfg = main.merge_defaults({
            'api_key': 'private-test-key', 'edu_code': 'E10', 'school_code': '1234',
            'school_kind': '고등학교', 'school_name': '테스트학교', 'grade': '2', 'classnm': '4',
            'manual_dday': [{'name': '내 일정', 'date': '20990101', 'color': '#ABCDEF'}],
        })
        main.save_config(self.cfg)
        self.saved_bytes = Path(main.CONFIG_PATH).read_bytes()
        self.set_wallpaper = self.stack.enter_context(patch.object(main, 'set_wallpaper'))
        self.autostart = self.stack.enter_context(patch.object(main, 'ensure_autostart'))
        self.engine = self.stack.enter_context(patch.object(main, 'update_once'))
        self.apply = self.stack.enter_context(patch.object(
            main, 'apply_configuration', return_value={'states': {}, 'autostart_error': None}, create=True))
        self.refresh = self.stack.enter_context(patch.object(main, 'refresh_data', create=True, return_value={
            'date': main.datetime.now().strftime('%Y%m%d'), 'timetable': [], 'meals': [], 'schedule': [],
            'states': {'timetable': 'empty', 'meals': 'empty', 'schedule': 'empty'},
        }))
        self.compose = self.stack.enter_context(patch.object(
            main, 'compose_wallpaper', side_effect=lambda *args, **kwargs: Image.new('RGB', (640, 360), '#183040'),
            create=True))
        self.stack.enter_context(patch.object(main, 'render_wallpaper', side_effect=AssertionError('Preview wrote wallpaper')))
        self.stack.enter_context(patch.object(main, 'get_screen_size', return_value=(640, 360)))
        self.stack.enter_context(patch.object(main, 'fetch_background', return_value=Image.new('RGB', (640, 360))))
        for name in ('fetch_meal', 'fetch_timetable', 'fetch_school_schedule'):
            self.stack.enter_context(patch.object(main, name, return_value=[]))
        self.search = self.stack.enter_context(patch.object(settings_gui, 'search_school', return_value=[]))
        # Opening a test editor must not launch v1.3's old initial synchronization path.
        self.stack.enter_context(patch.object(settings_gui.App, '_initial_auto_sync', create=True))
        for name in ('deiconify', 'lift', 'focus_force'):
            self.stack.enter_context(patch.object(settings_gui.App, name))
        self.stack.enter_context(patch.object(settings_gui.App, 'state', return_value='withdrawn'))
        geometry_by_test = {
            'test_layout_default_size': '1200x780+0+0',
            'test_layout_small_size': '1020x650+0+0',
        }
        if self._testMethodName in geometry_by_test:
            # A withdrawn Tk window keeps its first realized geometry; force that
            # initial geometry instead of pretending a later resize took effect.
            original_geometry = settings_gui.App.geometry
            requested = geometry_by_test[self._testMethodName]
            self.stack.enter_context(patch.object(
                settings_gui.App, 'geometry', autospec=True,
                side_effect=lambda window, value=None: original_geometry(window, requested if value else None)))
        self.error_dialog = self.stack.enter_context(patch.object(settings_gui.messagebox, 'showerror'))
        self.stack.enter_context(patch.object(settings_gui.messagebox, 'showwarning'))
        self.stack.enter_context(patch.object(settings_gui.messagebox, 'showinfo'))
        self.confirm_dialog = self.stack.enter_context(patch.object(settings_gui.messagebox, 'askyesno', return_value=True))
        self.stack.enter_context(patch.object(settings_gui.messagebox, 'askokcancel', return_value=False))
        self.stack.enter_context(patch.object(settings_gui.messagebox, 'askyesnocancel', return_value=None))
        for name in ('askstring', 'askinteger', 'askfloat'):
            self.stack.enter_context(patch.object(settings_gui.simpledialog, name, return_value=None))
        for name in ('askopenfilename', 'asksaveasfilename'):
            self.stack.enter_context(patch.object(settings_gui.filedialog, name, return_value=''))
        self.app = None
        try:
            self.app = settings_gui.App()
        except tk.TclError as error:
            # A missing Tcl installation/display is an infrastructure limitation, not a passed GUI test.
            if 'no display' in str(error).lower() or 'init.tcl' in str(error).lower():
                self.skipTest(str(error))
            raise
        self.addCleanup(self.close_app)
        self.callback_errors = []
        self.app.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.pump_until(lambda: self.app._preview_image is not None)

    def close_app(self):
        if self.app is None:
            return
        self.app._preview_worker.invalidate()
        deadline = time.monotonic() + 2
        while self.app._preview_worker.running and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        try:
            for identifier in self.app.tk.call('after', 'info'):
                self.app.after_cancel(identifier)
            # Cleanup is not a user close action: never leave a modal confirmation
            # open, and never apply an unsaved test draft while disposing widgets.
            self.app._closing = True
            tk.Tk.destroy(self.app)
        except tk.TclError:
            pass

    def pump_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.update()
            if predicate():
                self.assertFalse(self.callback_errors)
                return
            time.sleep(0.01)
        self.fail('Editor event loop did not reach the expected state')

    def assert_saved_unchanged(self):
        self.assertEqual(Path(main.CONFIG_PATH).read_bytes(), self.saved_bytes)
        self.set_wallpaper.assert_not_called()
        self.autostart.assert_not_called()
        self.engine.assert_not_called()
        self.assertFalse(Path(main.WALLPAPER_PATH).exists())

    def test_opening_editor_previews_without_applying(self):
        self.assertGreaterEqual(len(self.app.themes), 3)
        self.assertEqual(self.app._preview_image.size, (640, 360))
        self.compose.assert_called()
        self.apply.assert_not_called()
        self.assert_saved_unchanged()

    def test_theme_selection_preserves_school_and_manual_events(self):
        self.app.theme_combo.current(1)
        self.app.on_theme_selected()
        draft = self.app._collect_draft()
        for key in ('api_key', 'edu_code', 'school_code', 'school_kind', 'school_name', 'grade', 'classnm', 'manual_dday'):
            self.assertEqual(draft[key], self.cfg[key], key)
        self.assertFalse(draft['sections']['meals']['show'])
        self.assert_saved_unchanged()

    def test_cancel_restores_last_applied_widgets_and_discards_edits(self):
        self.app.colors_vars['school_name'].set('#112233')
        self.app.sec_vars['meals']['show'].set(False)
        self.app.grade_var.set('3')
        self.app.manual_dday.append({'name': '未保存', 'date': '20990202', 'color': None})
        self.app.on_cancel()
        draft = self.app._collect_draft()
        for key in ('font_colors', 'sections', 'grade', 'classnm', 'manual_dday'):
            self.assertEqual(draft[key], self.cfg[key], key)
        self.assert_saved_unchanged()

    def test_saving_theme_is_durable_but_does_not_apply(self):
        self.app.colors_vars['school_name'].set('#112233')
        with patch.object(settings_gui.simpledialog, 'askstring', return_value='내 테스트 테마'):
            self.app.on_save_theme()
        saved = next(item for item in self.app.theme_store.list_themes() if item['name'] == '내 테스트 테마')
        self.assertEqual(saved['style']['font_colors']['school_name'], '#112233')
        for key in ('api_key', 'school_code', 'manual_dday'):
            self.assertNotIn(key, saved['style'])
        self.assertIn('내 테스트 테마', [item['name'] for item in self.app.themes])
        self.apply.assert_not_called()
        self.assert_saved_unchanged()

    def test_old_queued_preview_cannot_replace_latest_revision(self):
        self.app._preview_revision += 2
        current = self.app._preview_revision
        newest = Image.new('RGB', (640, 360), '#00FF00')
        stale = Image.new('RGB', (640, 360), '#FF0000')
        self.app._ui_events.put(('preview', current, newest, None))
        self.app._ui_events.put(('preview', current - 1, stale, None))
        self.app._drain_ui_events()
        self.assertEqual(self.app._preview_image.getpixel((0, 0)), (0, 255, 0))
        self.assert_saved_unchanged()

    def test_apply_failure_keeps_saved_state_and_editable_draft(self):
        self.apply.side_effect = RuntimeError('test apply failure')
        self.app.colors_vars['school_name'].set('#112233')
        self.app.on_apply()
        self.pump_until(lambda: self.error_dialog.called)
        self.assertEqual(self.app.cfg['font_colors'], self.cfg['font_colors'])
        self.assertEqual(self.app._collect_draft()['font_colors']['school_name'], '#112233')
        self.assert_saved_unchanged()

    def test_successful_apply_becomes_cancel_baseline(self):
        self.app.colors_vars['school_name'].set('#112233')
        self.app.on_apply()
        self.pump_until(lambda: self.app.cfg['font_colors']['school_name'] == '#112233')
        self.apply.assert_called_once()
        submitted = self.apply.call_args.args[0]
        self.assertEqual(submitted['api_key'], self.cfg['api_key'])
        self.assertEqual(submitted['manual_dday'], self.cfg['manual_dday'])
        self.app.colors_vars['school_name'].set('#445566')
        self.app.on_cancel()
        self.assertEqual(self.app._collect_draft()['font_colors']['school_name'], '#112233')

    def check_layout(self, width, height):
        # Withdrawn native Canvas widgets do not receive a real Configure event.
        self.app._on_preview_resize()
        self.app.update_idletasks()
        self.assertEqual((self.app.winfo_width(), self.app.winfo_height()), (width, height))
        for name in ('theme_combo', 'save_theme_btn', 'apply_btn', 'cancel_btn', 'preview_area', 'preview_label',
                     'refresh_btn', 'meal_btn', 'timetable_btn'):
            widget = getattr(self.app, name)
            # Canvas.winfo_rootx() is zero until a native window is mapped. Its
            # geometry and parent allocations are still available without mapping.
            left, top = 0, 0
            current = widget
            while current is not self.app:
                left += current.winfo_x()
                top += current.winfo_y()
                current = current.master
            right, bottom = left + widget.winfo_width(), top + widget.winfo_height()
            with self.subTest(widget=name):
                self.assertGreater(widget.winfo_width(), 1)
                self.assertGreater(widget.winfo_height(), 1)
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(top, 0)
                self.assertLessEqual(right, width)
                self.assertLessEqual(bottom, height)
        self.assertLessEqual(self.app.preview_note.winfo_reqwidth(), self.app.preview_note.winfo_width(),
                             'Preview help text is wider than its allocated label')
        self.assertEqual(self.app.title(), '학교 바탕화면')

    def test_layout_default_size(self):
        self.check_layout(1200, 780)

    def test_layout_small_size(self):
        self.check_layout(1020, 650)

    def test_rapid_builtin_switches_show_last_theme_without_applying(self):
        def compose(cfg, *args, **kwargs):
            # Make the rendered pixel identify the requested layout.
            color = '#00FF00' if cfg['sections']['timetable']['show'] else '#0000FF'
            return Image.new('RGB', (640, 360), color)
        self.compose.side_effect = compose
        for index in (0, 1, 2, 0, 2, 1, 2, 0):
            self.app.theme_combo.current(index)
            self.app.on_theme_selected()
        self.pump_until(lambda: self.app._preview_image.getpixel((0, 0)) == (0, 255, 0))
        self.assertEqual(self.app.theme_combo.current(), 0)
        self.assertTrue(self.app._collect_draft()['sections']['timetable']['show'])
        self.assert_saved_unchanged()

    def test_saved_theme_survives_store_restart(self):
        self.app.colors_vars['school_name'].set('#112233')
        with patch.object(settings_gui.simpledialog, 'askstring', return_value='다시 열 테마'):
            self.app.on_save_theme()
        restarted = settings_gui.ThemeStore(self.directory / 'themes.json', main.merge_defaults({}))
        saved = next(item for item in restarted.list_themes() if item['name'] == '다시 열 테마')
        self.assertEqual(saved['style']['font_colors']['school_name'], '#112233')
        self.assert_saved_unchanged()

    def settle_preview(self):
        self.pump_until(lambda: self.app.pending_apply is None
                        and not self.app._preview_worker.running and self.app._ui_events.empty())

    def save_named_theme(self, name='새 사용자 테마'):
        with patch.object(settings_gui.simpledialog, 'askstring', return_value=name):
            self.app.on_save_theme()
        index = next(index for index, item in enumerate(self.app.themes) if item['name'] == name)
        self.app.theme_combo.current(index)
        return index

    def test_undo_redo_then_new_edit_discards_redo(self):
        original = self.app.colors_vars['school_name'].get()
        self.app.colors_vars['school_name'].set('#112233')
        self.settle_preview()
        self.app.on_undo()
        self.assertEqual(self.app.colors_vars['school_name'].get(), original)
        self.app.on_redo()
        self.assertEqual(self.app.colors_vars['school_name'].get(), '#112233')
        self.app.on_undo()
        self.app.colors_vars['school_name'].set('#445566')
        self.settle_preview()
        self.app.on_redo()
        self.assertEqual(self.app.colors_vars['school_name'].get(), '#445566')
        self.assert_saved_unchanged()

    def test_theme_rename_duplicate_delete_without_applying(self):
        self.save_named_theme('원본 테마')
        with patch.object(settings_gui.simpledialog, 'askstring', return_value='바꾼 이름'):
            self.app.on_rename_theme()
        names = [item['name'] for item in self.app.themes]
        self.assertNotIn('원본 테마', names)
        self.assertIn('바꾼 이름', names)
        self.app.theme_combo.current(names.index('바꾼 이름'))
        with patch.object(settings_gui.simpledialog, 'askstring', return_value='복제된 테마'):
            self.app.on_duplicate_theme()
        names = [item['name'] for item in self.app.themes]
        self.assertIn('복제된 테마', names)
        original = next(item for item in self.app.themes if item['name'] == '바꾼 이름')
        copied = next(item for item in self.app.themes if item['name'] == '복제된 테마')
        self.assertEqual(copied['style'], original['style'])
        self.assertNotEqual(copied['id'], original['id'])
        self.app.theme_combo.current(names.index('복제된 테마'))
        self.confirm_dialog.return_value = False
        self.app.on_delete_theme()
        self.assertIn('복제된 테마', [item['name'] for item in self.app.themes])
        self.confirm_dialog.return_value = True
        self.app.on_delete_theme()
        self.assertNotIn('복제된 테마', [item['name'] for item in self.app.themes])
        self.assert_saved_unchanged()

    def test_theme_export_import_excludes_private_school_settings(self):
        self.save_named_theme('공유할 테마')
        exported = self.directory / 'theme-export.json'
        with patch.object(settings_gui.filedialog, 'asksaveasfilename', return_value=str(exported)):
            self.app.on_export_theme()
        text = exported.read_text(encoding='utf-8')
        for private in ('private-test-key', 'api_key', 'manual_dday', 'school_code'):
            self.assertNotIn(private, text)
        self.app.on_delete_theme()
        with patch.object(settings_gui.filedialog, 'askopenfilename', return_value=str(exported)):
            self.app.on_import_theme()
        self.assertIn('공유할 테마', [item['name'] for item in self.app.themes])
        self.assert_saved_unchanged()

    def test_backup_restore_restores_draft_without_api_key_or_apply(self):
        exported = self.directory / 'settings-backup.json'
        original = self.app._collect_draft()
        with patch.object(settings_gui.filedialog, 'asksaveasfilename', return_value=str(exported)):
            self.app.on_backup()
        self.assertNotIn('private-test-key', exported.read_text(encoding='utf-8'))
        self.app.colors_vars['school_name'].set('#112233')
        self.app.grade_var.set('3')
        self.app.day_var.set('tomorrow')
        self.app.manual_dday = []
        with patch.object(settings_gui.filedialog, 'askopenfilename', return_value=str(exported)):
            self.app.on_restore()
        restored = self.app._collect_draft()
        for key in ('school_name', 'school_code', 'grade', 'classnm', 'font_colors', 'manual_dday'):
            self.assertEqual(restored[key], original[key], key)
        self.assertEqual(restored['api_key'], 'private-test-key')
        self.assert_saved_unchanged()

    def test_export_image_contains_wallpaper_without_preview_overlays(self):
        exported = self.directory / 'wallpaper-export.png'
        self.app.zoom_var.set('150%')
        self.app.grid_var.set(True)
        self.app.compare_var.set(True)
        self.app.update()
        with patch.object(settings_gui.filedialog, 'asksaveasfilename', return_value=str(exported)):
            self.app.on_export_image()
        self.pump_until(exported.exists)
        with Image.open(exported) as image:
            self.assertEqual(image.size, (640, 360))
            self.assertEqual(image.convert('RGB').getpixel((0, 0)), (24, 48, 64))
        self.assert_saved_unchanged()

    def test_display_day_and_manual_timetable_survive_theme_selection(self):
        manual = [{'date': '20990101', 'period': '1', 'subject': '바꾼 과목'}]
        self.app.day_var.set('tomorrow')
        self.app.manual_timetable = deepcopy(manual)
        self.app.theme_combo.current(1)
        self.app.on_theme_selected()
        draft = self.app._collect_draft()
        self.assertEqual(draft['display_day'], 'tomorrow')
        self.assertEqual(draft['manual_timetable'], manual)
        self.assert_saved_unchanged()

    def test_dirty_close_can_be_cancelled(self):
        self.app.colors_vars['school_name'].set('#112233')
        self.settle_preview()
        self.confirm_dialog.return_value = False
        self.app.destroy()
        self.assertTrue(self.app.winfo_exists())
        self.confirm_dialog.assert_called()
        self.assert_saved_unchanged()

    def test_refresh_updates_data_without_committing_draft(self):
        self.app.colors_vars['school_name'].set('#112233')
        self.settle_preview()
        with patch.object(self.app._ui_events, 'put', wraps=self.app._ui_events.put) as events:
            self.app.on_refresh_data()
            self.pump_until(lambda: any(call.args[0][0] == 'refreshed' for call in events.call_args_list))
            self.app._drain_ui_events()
        self.refresh.assert_called_once()
        self.assertEqual(self.app._collect_draft()['font_colors']['school_name'], '#112233')
        self.assertEqual(self.app.cfg['font_colors'], self.cfg['font_colors'])
        self.assert_saved_unchanged()

    def test_refresh_failure_preserves_draft_and_reports_error(self):
        self.refresh.side_effect = RuntimeError('offline test')
        self.app.colors_vars['school_name'].set('#112233')
        self.settle_preview()
        with patch.object(self.app._ui_events, 'put', wraps=self.app._ui_events.put) as events:
            self.app.on_refresh_data()
            self.pump_until(lambda: any(call.args[0][0] == 'refreshed' for call in events.call_args_list))
            self.app._drain_ui_events()
        self.assertIn('offline test', self.app.status_var.get())
        self.assertEqual(self.app._collect_draft()['font_colors']['school_name'], '#112233')
        self.assert_saved_unchanged()

    def test_export_commands_refuse_active_config_and_theme_library_paths(self):
        self.save_named_theme('보호 경로 검사')
        protected = (Path(main.CONFIG_PATH), self.directory / 'themes.json')
        snapshots = {path: path.read_bytes() for path in protected}
        self.compose.reset_mock()
        for path in protected:
            for method in (self.app.on_backup, self.app.on_export_theme, self.app.on_export_image):
                with self.subTest(path=path.name, command=method.__name__), patch.object(
                        settings_gui.filedialog, 'asksaveasfilename', return_value=str(path)):
                    method()
                for saved_path, data in snapshots.items():
                    self.assertEqual(saved_path.read_bytes(), data)
        self.compose.assert_not_called()
        self.assert_saved_unchanged()

    def test_zoom_grid_and_comparison_do_not_change_draft_or_clean_image(self):
        original = self.app._collect_draft()
        pixels = self.app._preview_image.tobytes()
        self.app.zoom_var.set('100%')
        self.app._display_preview()
        region100 = tuple(map(float, self.app.preview_label.cget('scrollregion').split()))
        self.app.zoom_var.set('150%')
        self.app.grid_var.set(True)
        self.app._display_preview()
        region150 = tuple(map(float, self.app.preview_label.cget('scrollregion').split()))
        self.assertGreater(region150[2], region100[2])
        self.assertGreaterEqual(region150[3], region100[3])
        self.assertEqual((self.app._preview_photo.width(), self.app._preview_photo.height()), (960, 540))
        self.assertEqual(self.app._preview_image.tobytes(), pixels)
        self.app.compare_var.set(True)
        self.settle_preview()
        self.assertEqual(self.app._collect_draft(), original)
        self.assert_saved_unchanged()

    def test_original_native_menu_has_readable_font_and_working_export(self):
        from tkinter import font
        self.assertIsInstance(self.app._menu_bar, tk.Menu)
        self.assertEqual(set(self.app._menus), {'파일', '편집', '테마', '도움말'})
        for menu in self.app._menus.values():
            self.assertIsInstance(menu, tk.Menu)
            self.assertGreaterEqual(font.Font(root=self.app, font=menu.cget('font')).actual('size'), 11)
        with patch.object(settings_gui.filedialog, 'asksaveasfilename', return_value='') as choose:
            self.app._menus['파일'].invoke(2)
            choose.assert_called_once()
        self.assert_saved_unchanged()

    def test_color_palettes_change_only_colors_and_preserve_school_search(self):
        self.app.photo_mode.set('preset')
        self.app.preset_combo.current(2)
        self.app.sec_vars['school_info']['x'].set(31)
        self.app.selected_school = {
            'ATPT_OFCDC_SC_CODE': self.cfg['edu_code'], 'SD_SCHUL_CODE': self.cfg['school_code'],
            'SCHUL_NM': self.cfg['school_name'], 'SCHUL_KND_SC_NM': self.cfg['school_kind'],
        }
        selection = self.app.selected_school
        self.app.result_list.insert('end', '검색 결과 유지')
        original = self.app._collect_draft()
        self.assertEqual(len(self.app.color_palettes), 6)
        for palette in self.app.color_palettes:
            with self.subTest(palette=palette['name']):
                self.app.palette_buttons[palette['id']].invoke()
                expected = deepcopy(original)
                expected['font_colors'] = palette['colors']
                self.assertEqual(self.app._collect_draft(), expected)
                self.assertIs(self.app.selected_school, selection)
                self.assertEqual(self.app.result_list.get(0), '검색 결과 유지')
                for key, color in palette['colors'].items():
                    self.assertEqual(self.app.color_buttons[key].cget('background'), color)
        self.settle_preview()
        self.assertEqual(self.compose.call_args.args[0]['font_colors'], expected['font_colors'])
        self.apply.assert_not_called()
        self.assert_saved_unchanged()

    def test_color_palette_undo_redo_is_one_step_even_before_preview_finishes(self):
        original = self.app._collect_draft()
        self.app.palette_buttons['ocean'].invoke()
        ocean = self.app._collect_draft()
        self.app.palette_buttons['mint'].invoke()
        mint = self.app._collect_draft()
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft(), ocean)
        self.app.on_redo()
        self.assertEqual(self.app._collect_draft(), mint)
        self.app.on_undo()
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft(), original)
        self.assert_saved_unchanged()

    def test_group_color_changes_only_selected_text_and_cancel_is_noop(self):
        original = self.app._collect_draft()
        # Explicit expectations protect the group boundaries, not just the widgets.
        body = {'school_name_muted', 'timetable_subject', 'meals_dish', 'dday_name'}
        all_keys = set(original['font_colors'])
        for group, keys in (('body', body), ('accent', all_keys - body), ('all', all_keys)):
            with self.subTest(group=group), patch.object(settings_gui.colorchooser, 'askcolor',
                                                       return_value=((18, 52, 86), '#123456')):
                self.app.group_color_buttons[group].invoke()
                expected = deepcopy(original)
                expected['font_colors'].update(dict.fromkeys(keys, '#123456'))
                self.assertEqual(self.app._collect_draft(), expected)
                self.app.on_undo()
                self.assertEqual(self.app._collect_draft(), original)
        undo_available = self.app.history.can_undo
        with patch.object(settings_gui.colorchooser, 'askcolor', return_value=(None, None)):
            self.app.group_color_buttons['all'].invoke()
        self.assertEqual(self.app._collect_draft(), original)
        self.assertEqual(self.app.history.can_undo, undo_available)
        self.assert_saved_unchanged()

    def test_color_reset_preserves_other_edits_and_can_be_undone(self):
        self.app.palette_buttons['lavender'].invoke()
        self.app.sec_vars['timetable']['x'].set(24)
        self.app.photo_mode.set('preset')
        before = self.app._collect_draft()
        self.app.reset_colors_btn.invoke()
        expected = deepcopy(before)
        expected['font_colors'] = main.DEFAULT_COLORS
        self.assertEqual(self.app._collect_draft(), expected)
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft(), before)
        self.assert_saved_unchanged()

    def test_color_tools_do_not_change_draft_during_apply(self):
        original = self.app._collect_draft()
        self.app._applying = True
        try:
            with patch.object(settings_gui.colorchooser, 'askcolor') as choose:
                self.app.group_color_buttons['all'].invoke()
                choose.assert_not_called()
            self.app.palette_buttons['sunset'].invoke()
            self.app.reset_colors_btn.invoke()
            self.assertEqual(self.app._collect_draft(), original)
        finally:
            self.app._applying = False
        self.assert_saved_unchanged()

    def test_alignment_changes_only_target_section_and_is_undoable(self):
        original = self.app._collect_draft()
        self.app.align_section('timetable', 'x', 10)
        self.settle_preview()
        edited = self.app._collect_draft()
        self.assertEqual(edited['sections']['timetable']['x'], 10)
        for section in ('meals', 'dday', 'school_info', 'date_info'):
            self.assertEqual(edited['sections'][section], original['sections'][section])
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft()['sections'], original['sections'])
        self.assert_saved_unchanged()

    def test_quick_class_change_clears_overrides_and_undo_restores_them(self):
        self.app.manual_timetable = [{'date': '20990101', 'period': '1', 'subject': '직접 입력'}]
        before = self.app._collect_draft()
        self.app.basic_tool_buttons['next_class'].invoke()
        expected = deepcopy(before)
        expected.update(classnm='5', manual_timetable=[])
        self.assertEqual(self.app._collect_draft(), expected)
        self.assertIn('수동 시간표 초기화', self.app.status_var.get())
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft(), before)
        self.settle_preview()
        self.assertEqual(self.app._collect_draft(), before)
        self.app.on_redo()
        self.assertEqual(self.app._collect_draft(), expected)
        self.app.basic_tool_buttons['restore_class'].invoke()
        self.assertEqual(self.app.class_var.get(), self.cfg['classnm'])
        self.assertEqual(self.app.grade_var.get(), self.cfg['grade'])
        self.assert_saved_unchanged()

    def test_quick_class_controls_handle_empty_invalid_and_lower_bound(self):
        for text, direction, expected in (('', 1, '1'), ('1', -1, '1'), ('2', -1, '1')):
            with self.subTest(text=text):
                self.app.class_var.set(text)
                self.app.on_step_class(direction)
                self.assertEqual(self.app.class_var.get(), expected)
        self.app.class_var.set('잘못된 입력')
        before = self.app._collect_draft()
        self.app.on_step_class(1)
        self.assertEqual(self.app._collect_draft(), before)
        self.assertIn('숫자', self.app.status_var.get())
        self.assert_saved_unchanged()

    def test_quick_background_navigation_wraps_and_undo_preserves_photo(self):
        from unittest.mock import Mock
        from datetime import datetime
        self.app.photo_mode.set('custom')
        self.app.photo_path.set(str(self.directory / 'my-photo.png'))
        self.app.preset_combo.current(7)
        before = self.app._collect_draft()
        self.app.basic_tool_buttons['next_photo'].invoke()
        expected = deepcopy(before)
        expected.update(bg_photo_mode='preset', bg_photo_idx=0)
        self.assertEqual(self.app._collect_draft(), expected)
        self.assertEqual(str(self.app.preset_combo.cget('state')), 'readonly')
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft(), before)
        self.assertEqual(str(self.app.photo_entry.cget('state')), 'readonly')
        self.app.photo_mode.set('auto')
        fixed = datetime(2026, 11, 12)
        with patch.object(main, 'datetime', Mock(now=lambda: fixed)):
            self.app.on_step_photo(-1)
        self.assertEqual(self.app.preset_combo.current(), (fixed.timetuple().tm_yday - 1) % 8)
        self.assert_saved_unchanged()

    def test_quick_background_restore_changes_only_background_fields(self):
        self.app.basic_tool_buttons['next_photo'].invoke()
        self.app.colors_vars['school_name'].set('#123456')
        before = self.app._collect_draft()
        self.app.basic_tool_buttons['restore_photo'].invoke()
        expected = deepcopy(before)
        for key in ('bg_photo_mode', 'bg_photo_idx', 'custom_background'):
            expected[key] = self.cfg[key]
        self.assertEqual(self.app._collect_draft(), expected)
        self.app.on_undo()
        self.assertEqual(self.app._collect_draft(), before)
        self.assert_saved_unchanged()

    def test_quick_section_positions_affect_only_that_axis_and_are_undoable(self):
        before = self.app._collect_draft()
        for button, axis, value in (('left', 'x', 10), ('center_x', 'x', 50), ('right', 'x', 90),
                                     ('top', 'y', 10), ('center_y', 'y', 50), ('bottom', 'y', 80)):
            with self.subTest(button=button):
                self.app.section_tool_buttons['meals'][button].invoke()
                expected = deepcopy(before)
                expected['sections']['meals'][axis] = value
                self.assertEqual(self.app._collect_draft(), expected)
                self.assertEqual(self.app.label_vars['meals'][axis].get(), str(value))
                if expected != before:
                    self.app.on_undo()
                    self.assertEqual(self.app._collect_draft(), before)
        self.assert_saved_unchanged()

    def test_quick_effects_follow_sync_toggle_and_undo_as_one_step(self):
        before = self.app._collect_draft()
        for together in (False, True):
            with self.subTest(together=together):
                self.app.sync_effects.set(together)
                self.app.section_tool_buttons['timetable']['clear'].invoke()
                expected = deepcopy(before)
                targets = tuple(before['sections']) if together else ('timetable',)
                for target in targets:
                    expected['sections'][target].update(stroke_width=2, shadow_blur=3, shadow_opacity=180)
                    self.assertEqual(self.app.label_vars[target]['stroke_width'].get(), '2')
                self.assertEqual(self.app._collect_draft(), expected)
                self.settle_preview()
                self.app.on_undo()
                self.assertEqual(self.app._collect_draft(), before)
        self.assert_saved_unchanged()

    def test_quick_font_size_stays_in_slider_range(self):
        before = self.app._collect_draft()
        for initial, action, expected in ((50, 'smaller', 50), (55, 'smaller', 50),
                                           (195, 'larger', 200), (200, 'larger', 200),
                                           (175, 'normal_size', 100)):
            with self.subTest(initial=initial, action=action):
                self.app.sec_vars['school_info']['font_size'].set(initial)
                self.app.section_tool_buttons['school_info'][action].invoke()
                self.assertEqual(self.app.sec_vars['school_info']['font_size'].get(), expected)
                for section in ('timetable', 'meals', 'date_info', 'dday'):
                    self.assertEqual(self.app._collect_draft()['sections'][section], before['sections'][section])
        self.assert_saved_unchanged()

    def test_new_quick_tools_leave_draft_unchanged_during_apply(self):
        before = self.app._collect_draft()
        self.app._applying = True
        try:
            for button in self.app.basic_tool_buttons.values():
                button.invoke()
            for section in self.app.section_tool_buttons.values():
                for button in section.values():
                    button.invoke()
            self.assertEqual(self.app._collect_draft(), before)
        finally:
            self.app._applying = False
        self.assert_saved_unchanged()

    def descendants(self, widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(self.descendants(child))
        return result

    def open_hidden_dialog(self, action):
        original = tk.Toplevel
        def construct(*args, **kwargs):
            dialog = original(*args, **kwargs)
            dialog.withdraw()
            return dialog
        with patch.object(original, 'grab_set'), patch.object(tk, 'Toplevel', side_effect=construct):
            action()
        return [child for child in self.app.winfo_children() if isinstance(child, original)][-1]

    def invoke_dialog_button(self, dialog, caption):
        buttons = [widget for widget in self.descendants(dialog)
                   if widget.winfo_class() == 'TButton' and widget.cget('text') == caption]
        self.assertEqual(len(buttons), 1)
        buttons[0].invoke()

    def populate_manual_dialog(self, dialog):
        entries = [widget for widget in self.descendants(dialog) if widget.winfo_class() == 'TEntry']
        self.assertEqual(len(entries), 3)
        for entry, text in zip(entries, ('2099-01-01', '3', '보정 과목')):
            entry.delete(0, 'end')
            entry.insert(0, text)
        self.invoke_dialog_button(dialog, '추가/수정')

    def test_manual_timetable_dialog_add_delete_then_cancel_does_not_change_draft(self):
        original = deepcopy(self.app.manual_timetable)
        dialog = self.open_hidden_dialog(self.app.on_manual_timetable)
        self.populate_manual_dialog(dialog)
        rows = next(widget for widget in self.descendants(dialog) if widget.winfo_class() == 'Listbox')
        self.assertEqual(rows.size(), len(original) + 1)
        rows.selection_set(rows.size() - 1)
        self.invoke_dialog_button(dialog, '선택 삭제')
        self.assertEqual(rows.size(), len(original))
        self.populate_manual_dialog(dialog)
        self.invoke_dialog_button(dialog, '취소')
        self.assertEqual(self.app.manual_timetable, original)
        self.assert_saved_unchanged()

    def test_manual_timetable_dialog_commit_changes_only_draft_until_apply(self):
        dialog = self.open_hidden_dialog(self.app.on_manual_timetable)
        self.populate_manual_dialog(dialog)
        self.invoke_dialog_button(dialog, '편집기에 반영')
        self.assertIn({'date': '20990101', 'period': '3', 'subject': '보정 과목'}, self.app.manual_timetable)
        self.assert_saved_unchanged()
        self.app.on_cancel()
        self.assertEqual(self.app.manual_timetable, self.cfg.get('manual_timetable', []))

    def test_manual_timetable_dialog_filters_previous_class_before_preview_debounce(self):
        original = [{'date': '20990101', 'period': '1', 'subject': '이전 학급 과목'}]
        baseline = deepcopy(self.cfg)
        baseline['manual_timetable'] = deepcopy(original)
        for changed in ('grade', 'school'):
            with self.subTest(changed=changed):
                self.app._load_widgets(baseline)
                self.settle_preview()
                if changed == 'grade':
                    self.app.grade_var.set('3')
                else:
                    self.app.selected_school = self.school_result()
                    self.app.trigger_live_apply()
                # Do not pump Tk: reproduce opening the dialog before the pending
                # preview callback has discarded the old class's corrections.
                self.assertEqual(self.app.manual_timetable, original)
                self.assertEqual(self.app._collect_draft()['manual_timetable'], [])
                dialog = self.open_hidden_dialog(self.app.on_manual_timetable)
                rows = next(widget for widget in self.descendants(dialog) if widget.winfo_class() == 'Listbox')
                self.assertEqual(rows.size(), 0)
                self.invoke_dialog_button(dialog, '취소')
                self.assert_saved_unchanged()

    def test_meal_details_shows_original_allergen_and_nutrition_text(self):
        cache = {'date': main.datetime.now().strftime('%Y%m%d'), 'states': {'meals': 'fresh'},
                 'meals': [{'name': '중식', 'dishes': '우유', 'raw_dishes': '우유 (2)',
                            'calories': '700 Kcal', 'nutrition': '단백질 20g', 'origin': '국내산'}]}
        with patch.object(self.app, '_preview_cache', return_value=cache):
            dialog = self.open_hidden_dialog(self.app.on_meal_details)
        text = next(widget for widget in self.descendants(dialog) if widget.winfo_class() == 'Text').get('1.0', 'end')
        for expected in ('우유 (2)', '700 Kcal', '단백질 20g', '국내산'):
            self.assertIn(expected, text)
        dialog.destroy()
        self.assert_saved_unchanged()

    @staticmethod
    def school_result():
        return {'ATPT_OFCDC_SC_CODE': 'B10', 'SD_SCHUL_CODE': '5678',
                'SCHUL_NM': '새 테스트고등학교', 'SCHUL_KND_SC_NM': '고등학교',
                'ORG_RDNMA': '테스트 주소'}

    def test_school_search_ignores_stale_query_and_blocks_duplicate_search_and_apply(self):
        started, release = Event(), Event()

        def delayed_search(_name):
            started.set()
            release.wait(5)
            return [self.school_result()]

        self.search.side_effect = delayed_search
        original_results = deepcopy(self.app.search_results)
        original_rows = self.app.result_list.get(0, tk.END)
        self.app.school_name_var.set('이전 검색어')
        self.app.on_search()
        try:
            self.assertTrue(started.wait(2))
            self.assertTrue(self.app._searching)
            self.assertEqual(str(self.app.search_btn.cget('state')), 'disabled')
            self.app.on_search()
            self.app.on_apply()
            self.search.assert_called_once_with('이전 검색어')
            self.apply.assert_not_called()
            self.app.school_name_var.set('다른 검색어')
        finally:
            release.set()
        self.pump_until(lambda: not self.app._searching)
        self.assertEqual(str(self.app.search_btn.cget('state')), 'normal')
        self.assertEqual(self.app.search_results, original_results)
        self.assertEqual(self.app.result_list.get(0, tk.END), original_rows)
        self.assertIsNone(self.app.selected_school)
        self.assert_saved_unchanged()

    def test_school_search_selection_can_be_applied_after_list_focus_is_lost(self):
        school = self.school_result()
        self.search.return_value = [school]
        self.app.school_name_var.set('새 테스트')
        self.app.on_search()
        self.pump_until(lambda: not self.app._searching)
        self.search.assert_called_once_with('새 테스트')
        self.assertEqual(self.app.search_results, [school])
        self.assertEqual(self.app.result_list.size(), 1)
        self.assertIn(school['SCHUL_NM'], self.app.result_list.get(0))
        self.app.result_list.selection_set(0)
        self.app.on_result_select()
        self.assertEqual(self.app.selected_school, school)
        self.app.result_list.selection_clear(0, tk.END)
        self.app.on_apply()
        self.pump_until(lambda: not self.app._applying)
        self.apply.assert_called_once()
        submitted = self.apply.call_args.args[0]
        self.assertEqual(submitted['school_name'], school['SCHUL_NM'])
        self.assertEqual(submitted['school_code'], school['SD_SCHUL_CODE'])
        self.assertEqual(submitted['edu_code'], school['ATPT_OFCDC_SC_CODE'])
        self.assertEqual(self.app.cfg['school_code'], school['SD_SCHUL_CODE'])
        # Another search clears selected_school. The newly applied school must
        # already be the draft baseline, including while the request is pending.
        self.search.return_value = []
        self.app.on_search()
        self.assertIsNone(self.app.selected_school)
        self.assertEqual(self.app._collect_draft()['school_code'], school['SD_SCHUL_CODE'])
        self.pump_until(lambda: not self.app._searching)
        self.assertEqual(self.app._collect_draft()['school_code'], school['SD_SCHUL_CODE'])
        self.assert_saved_unchanged()

    def test_school_search_error_restores_button_and_preserves_saved_settings(self):
        self.search.side_effect = RuntimeError('test search failure')
        self.app.on_search()
        self.pump_until(lambda: not self.app._searching)
        self.assertEqual(str(self.app.search_btn.cget('state')), 'normal')
        self.assertIn('test search failure', self.app.status_var.get())
        self.apply.assert_not_called()
        self.assert_saved_unchanged()


if __name__ == '__main__':
    unittest.main()
