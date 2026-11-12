"""Desktop editor commands. Portable validation/history live in core modules."""
from copy import deepcopy
import os
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import main
from core.editor_state import backup_settings, read_settings_backup, describe_cache
from core.schoolday import target_day, validate_overrides
from core.sync import identity
from ui_widgets import CommandPopup


class EditorTools:
    def _safe_destination(self, path):
        """A file picker must not turn an export into active configuration changes."""
        destination = os.path.normcase(os.path.realpath(path))
        reserved = [main.CONFIG_PATH, main.DATA_CACHE_PATH, main.WALLPAPER_PATH]
        reserved.extend(os.path.join(main.BASE_DIR, name) for name in
                        ('themes.json', 'wallpaper_1.png', 'wallpaper_2.png', 'last_run.json', '.sync.lock', 'sync.log'))
        if destination in {os.path.normcase(os.path.realpath(item)) for item in reserved}:
            raise ValueError('앱이 사용하는 설정·캐시·배경 파일과 다른 이름을 선택해주세요.')
        return path

    def _build_commands(self):
        self.command_groups = (
            ('파일', [('설정 백업…', self.on_backup, ''), ('설정 복원…', self.on_restore, 'Ctrl+O'),
                      ('PNG 내보내기…', self.on_export_image, ''), ('도움말', self.on_help, 'F1')]),
            ('편집', [('실행 취소', self.on_undo, 'Ctrl+Z'), ('다시 실행', self.on_redo, 'Ctrl+Y'),
                      ('변경 취소', self.on_cancel, ''), ('위치 정렬…', self.on_align, ''),
                      ('디자인 초기화', self.on_reset, '')]),
            ('테마', [('저장…', self.on_save_theme, 'Ctrl+S'), ('복제…', self.on_duplicate_theme, ''),
                      ('이름 변경…', self.on_rename_theme, ''), ('삭제', self.on_delete_theme, ''),
                      ('내보내기…', self.on_export_theme, ''), ('가져오기…', self.on_import_theme, '')]),
        )
        self._command_popup = None
        self.menu_btn = ttk.Button(self.footer, text='메뉴', command=self.on_menu, width=7)
        self.menu_btn.pack(side='left', before=self.status_label, padx=(0, 12))
        for key, command in (('<Control-z>', self.on_undo), ('<Control-y>', self.on_redo),
                             ('<Control-s>', self.on_save_theme), ('<Control-o>', self.on_restore),
                             ('<Control-Return>', self.on_apply), ('<F5>', self.on_refresh_data),
                             ('<F1>', self.on_help), ('<F10>', self.on_menu), ('<Control-0>', self.on_fit)):
            # Root bindings do not steal shortcuts from independent dialogs.
            self.bind(key, lambda event, action=command: self._shortcut(event, action))

    def on_menu(self):
        if self._command_popup is not None and self._command_popup.winfo_exists():
            self._command_popup.destroy()
        else:
            self._command_popup = CommandPopup(self, self.menu_btn, self.command_groups)

    def _shortcut(self, event, command):
        if event.widget.winfo_toplevel() == self:
            command()
            return 'break'

    def _update_dirty(self):
        dirty = self._collect_draft() != self.cfg
        self.dirty_var.set('변경됨' if dirty else '')
        self.title('학교 바탕화면 설정 · v1.4.1' + (' *' if dirty else ''))

    def _remember(self):
        self.history.record(self._collect_draft())

    def on_undo(self):
        if self._applying:
            return
        self._remember()
        snapshot = self.history.undo()
        if snapshot is not None:
            self._load_widgets(snapshot)
            self.status_var.set('실행 취소됨')

    def on_redo(self):
        if self._applying:
            return
        self._remember()
        snapshot = self.history.redo()
        if snapshot is not None:
            self._load_widgets(snapshot)
            self.status_var.set('다시 실행됨')

    def _chosen_theme(self):
        index = self.theme_combo.current()
        if not 0 <= index < len(self.themes):
            self.notebook.select(self.tabs['background'])
            self.theme_combo.focus_set()
            self.status_var.set('테마를 선택하세요.')
            return None
        return self.themes[index]

    def _rename_or_copy(self, duplicate):
        if self._applying:
            return
        item = self._chosen_theme()
        if not item:
            return
        name = simpledialog.askstring('테마 복제' if duplicate else '테마 이름 변경',
                                      '새 이름 (최대 40자)', initialvalue=item['name'] + (' 복사' if duplicate else ''), parent=self)
        if name is None:
            return
        try:
            action = self.theme_store.duplicate if duplicate else self.theme_store.rename
            saved = action(item['id'], name)
            self._refresh_themes(saved['name'])
            self.status_var.set('테마 변경됨')
        except (ValueError, OSError) as error:
            messagebox.showerror('테마 변경 실패', str(error), parent=self)

    def on_rename_theme(self):
        self._rename_or_copy(False)

    def on_duplicate_theme(self):
        self._rename_or_copy(True)

    def on_delete_theme(self):
        if self._applying:
            return
        item = self._chosen_theme()
        if not item or not messagebox.askyesno('테마 삭제', f"‘{item['name']}’ 테마를 삭제할까요?", parent=self):
            return
        try:
            self.theme_store.delete(item['id'])
            self._refresh_themes()
            self.status_var.set('테마 삭제됨')
        except (ValueError, OSError) as error:
            messagebox.showerror('테마 삭제 실패', str(error), parent=self)

    def on_export_theme(self):
        item = self._chosen_theme()
        if not item:
            return
        path = filedialog.asksaveasfilename(parent=self, title='테마 내보내기', defaultextension='.json',
                                            initialfile='wallpaper-theme.json', filetypes=[('테마 JSON', '*.json')])
        if path:
            try:
                self.theme_store.export_theme(item['id'], self._safe_destination(path))
                self.status_var.set('테마 내보내기 완료')
            except (ValueError, OSError) as error:
                messagebox.showerror('내보내기 실패', str(error), parent=self)

    def on_import_theme(self):
        if self._applying:
            return
        path = filedialog.askopenfilename(parent=self, title='테마 가져오기', filetypes=[('테마 JSON', '*.json')])
        if path:
            try:
                item = self.theme_store.import_theme(path)
                self._refresh_themes(item['name'])
                self.on_theme_selected()
            except (ValueError, OSError) as error:
                messagebox.showerror('가져오기 실패', str(error), parent=self)

    def on_backup(self):
        path = filedialog.asksaveasfilename(parent=self, title='현재 편집 설정 백업', defaultextension='.json',
                                            initialfile='wallpaper-settings.json', filetypes=[('설정 JSON', '*.json')])
        if path:
            try:
                backup_settings(self._safe_destination(path), self._collect_draft())
                self.status_var.set('설정 백업 완료')
            except (ValueError, OSError, tk.TclError) as error:
                messagebox.showerror('백업 실패', str(error), parent=self)

    def on_restore(self):
        if self._applying:
            return
        path = filedialog.askopenfilename(parent=self, title='설정 백업 불러오기', filetypes=[('설정 JSON', '*.json')])
        if path:
            try:
                draft = read_settings_backup(path, self._collect_draft())
                self._remember()
                self._load_widgets(draft)
                self.status_var.set('설정 불러옴 · 적용 전')
            except (ValueError, OSError, tk.TclError) as error:
                messagebox.showerror('복원 실패', str(error), parent=self)

    def on_fit(self):
        self.zoom_var.set('화면에 맞춤')

    def on_export_image(self):
        path = filedialog.asksaveasfilename(parent=self, title='현재 디자인 PNG 내보내기', defaultextension='.png',
                                            initialfile='school-wallpaper.png', filetypes=[('PNG 이미지', '*.png')])
        if not path:
            return
        try:
            self._safe_destination(path)
            cfg = self._collect_draft()
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror('내보내기 실패', str(error), parent=self)
            return
        self.status_var.set('이미지 저장 중…')

        def export():
            temporary = None
            try:
                image = self._compose_preview(cfg)
                # Never leave a partially written image at the chosen destination.
                descriptor, temporary = tempfile.mkstemp(suffix='.png', dir=os.path.dirname(os.path.abspath(path)))
                os.close(descriptor)
                image.save(temporary, format='PNG')
                os.replace(temporary, path)
                self._ui_events.put(('exported', path, None, None))
            except Exception as error:
                self._ui_events.put(('exported', path, None, str(error)))
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        threading.Thread(target=export, daemon=True).start()

    def on_refresh_data(self):
        if self._refreshing or self._applying:
            return
        cfg = self._collect_draft()
        if not cfg.get('school_code') or not cfg.get('grade') or not cfg.get('classnm'):
            messagebox.showinfo('학교 설정 필요', '학교와 학년·반을 먼저 선택해주세요.', parent=self)
            return
        self._refreshing = True
        self.refresh_btn.configure(state='disabled')
        self.status_var.set('조회 중…')
        request = (identity(cfg), target_day(cfg, main.datetime.now()).strftime('%Y%m%d'))

        def refresh():
            try:
                cache = main.refresh_data(cfg)
                self._ui_events.put(('refreshed', request, cache, None))
            except Exception as error:
                self._ui_events.put(('refreshed', request, None, str(error)))
        threading.Thread(target=refresh, daemon=True).start()

    def _preview_cache(self, cfg):
        key = (identity(cfg), target_day(cfg, main.datetime.now()).strftime('%Y%m%d'))
        cached = self._session_cache
        if cached and cached[0] == key:
            return deepcopy(cached[1])
        return main.load_data_cache(cfg)

    def on_meal_details(self):
        cfg = self._collect_draft()
        cache = self._preview_cache(cfg)
        day = target_day(cfg, main.datetime.now()).strftime('%Y%m%d')
        chunks = [f'{day[:4]}-{day[4:6]}-{day[6:]} 급식 상세', describe_cache(cache, day).replace('오늘', '선택 날짜')]
        for meal in cache.get('meals', []):
            chunks.extend(['', meal.get('name', '급식'), meal.get('raw_dishes') or meal.get('dishes', '')])
            for key, title in (('calories', '열량'), ('nutrition', '영양 정보'), ('origin', '원산지')):
                chunks.append(f"\n{title}\n{meal.get(key) or '제공된 정보 없음'}")
        if not cache.get('meals'):
            chunks.append('\n표시할 급식이 없습니다. F5 또는 데이터 새로고침으로 확인하세요.')
        chunks.append('\n알레르기 번호는 학교가 제공한 원문입니다. 식단 변경·알레르기 관련 사항은 학교 안내를 확인하세요.')
        self._text_dialog('급식 상세', '\n'.join(chunks))

    def on_data_status(self):
        cfg = self._collect_draft()
        day = target_day(cfg, main.datetime.now()).strftime('%Y%m%d')
        status = describe_cache(self._preview_cache(cfg), day).replace('오늘', '선택 날짜')
        self._text_dialog('조회 상태', status.replace(' · ', '\n'))

    def _text_dialog(self, title, content):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        height = min(600, max(260, (content.count('\n') + 3) * 26), self.winfo_screenheight() - 100)
        dialog.geometry(f'{min(650, self.winfo_screenwidth() - 80)}x{height}')
        text = tk.Text(dialog, wrap='word', padx=20, pady=16, font=('맑은 고딕', 12))
        scroll = ttk.Scrollbar(dialog, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.insert('1.0', content)
        text.configure(state='disabled')
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        return dialog

    def on_manual_timetable(self):
        if self._applying:
            return
        dialog = tk.Toplevel(self)
        dialog.title('날짜별 수동 시간표')
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text='날짜별 시간표 수정').pack(padx=15, pady=12)
        entries = deepcopy(self._collect_draft().get('manual_timetable', []))
        rows = tk.Listbox(dialog, width=46, height=10, exportselection=False)
        rows.pack(fill='both', expand=True, padx=15)
        row = ttk.Frame(dialog)
        row.pack(padx=15, pady=12)
        date = tk.StringVar(value=target_day(self._collect_draft(), main.datetime.now()).strftime('%Y-%m-%d'))
        period = tk.StringVar(value='1')
        subject = tk.StringVar()
        for label, variable, width in (('날짜', date, 12), ('교시', period, 4), ('과목', subject, 18)):
            ttk.Label(row, text=label).pack(side='left', padx=(5, 2))
            ttk.Entry(row, textvariable=variable, width=width).pack(side='left')

        def redraw():
            entries.sort(key=lambda item: (item['date'], int(item['period'])))
            rows.delete(0, 'end')
            for item in entries:
                rows.insert('end', f"{item['date']} · {item['period']}교시 · {item['subject']}")

        def choose(event=None):
            if rows.curselection():
                item = entries[rows.curselection()[0]]
                date.set(item['date'])
                period.set(item['period'])
                subject.set(item['subject'])

        def add():
            item = dict(date=date.get().strip().replace('-', ''), period=period.get().strip(), subject=subject.get().strip())
            try:
                validate_overrides([item])
                entries[:] = [entry for entry in entries if (entry['date'], int(entry['period'])) != (item['date'], int(item['period']))]
                entries.append(item)
                redraw()
            except ValueError as error:
                messagebox.showwarning('입력 확인', str(error), parent=dialog)

        def delete():
            if rows.curselection():
                del entries[rows.curselection()[0]]
                redraw()

        def done():
            self._remember()
            self.manual_timetable = deepcopy(entries)
            self.trigger_live_apply()
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=12)
        for label, action in (('추가/수정', add), ('선택 삭제', delete), ('편집기에 반영', done), ('취소', dialog.destroy)):
            ttk.Button(buttons, text=label, command=action).pack(side='left', padx=4)
        rows.bind('<<ListboxSelect>>', choose)
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        redraw()

    def on_align(self):
        if self._applying:
            return
        dialog = tk.Toplevel(self)
        dialog.title('요소 위치 정렬')
        dialog.transient(self)
        dialog.grab_set()
        labels = {'학교 정보': 'school_info', '시간표': 'timetable', '급식': 'meals', '날짜': 'date_info', '학사일정': 'dday'}
        section = tk.StringVar(value='시간표')
        ttk.Combobox(dialog, values=list(labels), textvariable=section, state='readonly').pack(padx=20, pady=12)
        ttk.Label(dialog, text='글자의 기준 위치').pack(padx=20, pady=8)
        for title, axis, value in (('왼쪽 10%', 'x', 10), ('가로 중앙 50%', 'x', 50), ('오른쪽 90%', 'x', 90),
                                   ('위쪽 10%', 'y', 10), ('세로 중앙 50%', 'y', 50), ('아래쪽 80%', 'y', 80)):
            ttk.Button(dialog, text=title, command=lambda a=axis, v=value: self.align_section(labels[section.get()], a, v)).pack(fill='x', padx=20, pady=3)
        ttk.Button(dialog, text='닫기', command=dialog.destroy).pack(pady=10)

    def align_section(self, section, axis, value):
        if self._applying or section not in self.sec_vars or axis not in ('x', 'y'):
            return
        self._remember()
        self.sec_vars[section][axis].set(value)
        self.label_vars[section][axis].set(str(value))
        self.trigger_live_apply()

    def on_help(self):
        self._text_dialog('도움말',
            '편집 → 미리보기 → 적용\n\n'
            'Ctrl+Z / Y    실행 취소 / 다시 실행\n'
            'Ctrl+S         테마 저장\n'
            'Ctrl+Enter   배경 적용\n'
            'Ctrl+O         설정 복원\n'
            'Ctrl+0         화면에 맞춤\n'
            'F5                새로고침\n\n'
            '테마는 디자인만, 설정 백업은 학교·일정도 저장합니다.\n'
            'API 키·사진 원본·테마 목록은 백업에 포함되지 않습니다.\n'
            '조회 상태는 학교 탭에서 확인할 수 있습니다.')
