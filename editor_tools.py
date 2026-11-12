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
        bar = tk.Menu(self)
        self.configure(menu=bar)
        menus = (
            ('파일', [('설정 백업…', self.on_backup, ''), ('설정 복원…', self.on_restore, 'Ctrl+O'),
                      ('배경 이미지 내보내기…', self.on_export_image, ''), ('닫기', self.destroy, '')]),
            ('편집', [('실행 취소', self.on_undo, 'Ctrl+Z'), ('다시 실행', self.on_redo, 'Ctrl+Y'),
                      ('마지막 적용 상태로', self.on_cancel, ''), ('위치 정렬…', self.on_align, '')]),
            ('테마', [('현재 디자인 저장…', self.on_save_theme, 'Ctrl+S'), ('선택 테마 복제…', self.on_duplicate_theme, ''),
                      ('선택 테마 이름 변경…', self.on_rename_theme, ''), ('선택 테마 삭제', self.on_delete_theme, ''),
                      ('선택 테마 내보내기…', self.on_export_theme, ''), ('테마 가져오기…', self.on_import_theme, '')]),
            ('도움말', [('단축키와 사용 안내', self.on_help, 'F1')]),
        )
        for label, commands in menus:
            menu = tk.Menu(bar, tearoff=False)
            bar.add_cascade(label=label, menu=menu)
            for text, callback, accelerator in commands:
                menu.add_command(label=text, command=callback, accelerator=accelerator)
        for key, command in (('<Control-z>', self.on_undo), ('<Control-y>', self.on_redo),
                             ('<Control-s>', self.on_save_theme), ('<Control-o>', self.on_restore),
                             ('<Control-Return>', self.on_apply), ('<F5>', self.on_refresh_data),
                             ('<F1>', self.on_help), ('<Control-0>', self.on_fit)):
            # Root bindings do not steal shortcuts from independent dialogs.
            self.bind(key, lambda event, action=command: self._shortcut(event, action))

    def _shortcut(self, event, command):
        if event.widget.winfo_toplevel() == self:
            command()
            return 'break'

    def _update_dirty(self):
        dirty = self._collect_draft() != self.cfg
        self.dirty_var.set('● 적용하지 않은 변경' if dirty else '저장된 설정')
        self.title('학교 바탕화면 설정 · v1.4' + (' *' if dirty else ''))

    def _remember(self):
        self.history.record(self._collect_draft())

    def on_undo(self):
        if self._applying:
            return
        self._remember()
        snapshot = self.history.undo()
        if snapshot is not None:
            self._load_widgets(snapshot)
            self.status_var.set('이전 편집으로 돌아왔습니다.')

    def on_redo(self):
        if self._applying:
            return
        self._remember()
        snapshot = self.history.redo()
        if snapshot is not None:
            self._load_widgets(snapshot)
            self.status_var.set('편집을 다시 실행했습니다.')

    def _chosen_theme(self):
        index = self.theme_combo.current()
        if not 0 <= index < len(self.themes):
            messagebox.showinfo('테마 선택', '위쪽 목록에서 관리할 테마를 선택해주세요.', parent=self)
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
            self.status_var.set('테마 목록을 변경했습니다.')
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
            self.status_var.set('테마를 삭제했습니다. 편집 중인 디자인은 유지됩니다.')
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
                self.status_var.set('테마를 내보냈습니다. 사용자 사진 파일은 별도로 전달해주세요.')
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
                self.status_var.set('학교·꾸미기·수동 일정을 백업했습니다. API 키·사진 원본·테마 목록은 제외됩니다.')
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
                self.status_var.set('백업을 편집기에 불러왔습니다. 확인 후 적용하세요.')
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
        self.status_var.set('현재 디자인 이미지를 만들고 있습니다…')

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
        self.status_var.set('선택한 날짜의 학교 데이터를 새로 가져오고 있습니다…')
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

    def _text_dialog(self, title, content):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry('650x530')
        text = tk.Text(dialog, wrap='word', padx=15, pady=12, font=('맑은 고딕', 11))
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
        ttk.Label(dialog, text='같은 날짜·교시의 학교 시간표를 보정합니다. 배경 적용은 메인 창에서 확정하세요.',
                  wraplength=530).pack(padx=15, pady=10)
        entries = deepcopy(self._collect_draft().get('manual_timetable', []))
        rows = tk.Listbox(dialog, width=65, height=10, exportselection=False)
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
        ttk.Label(dialog, text='요소의 기준점을 화면 비율에 맞춥니다.\n각 요소의 글자 정렬 방식은 유지됩니다.').pack(padx=20)
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
        self._text_dialog('사용 안내 · 단축키',
            '편집 → 미리보기 확인 → 저장 및 지금 적용 순서로 사용하세요.\n\n'
            'Ctrl+Z / Ctrl+Y : 실행 취소 / 다시 실행 (최근 60개 상태)\n'
            'Ctrl+S : 현재 디자인을 테마로 저장\nCtrl+Enter : 저장 및 배경 적용\n'
            'Ctrl+O : 설정 백업 불러오기\nCtrl+0 : 미리보기 화면에 맞춤\nF5 : 학교 데이터 새로고침\nF1 : 이 안내\n\n'
            '확대 보기에서는 스크롤 막대로 이동합니다. 격자와 저장본 비교는 실제 배경·내보낸 PNG에 포함되지 않습니다.\n\n'
            '오늘/내일 선택은 시간표·급식·날짜 표시에 적용됩니다. D-Day 계산은 실제 오늘 기준입니다. '
            '수동 시간표는 지정 날짜의 교시를 덮어씁니다. 학교·학급을 바꾸면 이전 수동 보정은 비워집니다.\n\n'
            '테마에는 꾸미기만 저장됩니다. 설정 백업에는 학교와 수동 일정도 포함되며 API 키는 제외됩니다. '
            '사용자 사진 원본과 저장한 테마 목록은 별도로 보관해주세요.')
