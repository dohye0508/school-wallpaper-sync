"""Compact editor tools. All commands edit drafts and share normal ttk buttons."""
from tkinter import ttk

import main
from core.sync import identity
from core.themes import validate_style


EFFECT_PRESETS = {
    'none': {'stroke_width': 0, 'shadow_blur': 0, 'shadow_opacity': 0},
    'soft': {'stroke_width': 0, 'shadow_blur': 5, 'shadow_opacity': 100},
    'clear': {'stroke_width': 2, 'shadow_blur': 3, 'shadow_opacity': 180},
}


class EditorShortcuts:
    def _tool_group(self, parent, title):
        if title != '요소별 글자 색상 설정':
            shell = ttk.Frame(parent)
            shell.pack(fill='x', padx=15, pady=6)
            return self._fold_tools(shell, title)
        group = ttk.LabelFrame(parent, text=f' {title} ')
        group.pack(fill='x', padx=15, pady=6)
        return group

    def _fold_tools(self, shell, title):
        content = ttk.Frame(shell)
        opened = False
        def toggle():
            nonlocal opened
            opened = not opened
            heading.configure(text=('▾ ' if opened else '▸ ') + title)
            if opened:
                content.pack(fill='x', pady=(4, 0))
            else:
                content.pack_forget()
        heading = ttk.Button(shell, text='▸ ' + title, command=toggle)
        heading.pack(anchor='w')
        return content

    def _tool_cell(self, parent, row, column):
        """Matching columns and row spacing without enlarging any control."""
        for index in range(2):
            parent.columnconfigure(index, weight=1, uniform='tool_columns')
        cell = ttk.Frame(parent)
        cell.grid(row=row, column=column, sticky='ew', padx=6, pady=4)
        cell.rowconfigure(0, minsize=30)
        cell.columnconfigure(0, minsize=104)
        return cell

    def _edit_draft(self, edit, label):
        if self._applying:
            return
        self._remember()
        self._initializing = True
        try:
            edit()
        finally:
            self._initializing = False
        self.history.record(self._collect_draft())
        self.trigger_live_apply()
        self.status_var.set(f'{label} · 적용 전')

    def _build_basic_tools(self, parent):
        group = self._tool_group(parent, '빠른 설정')
        self.basic_tool_buttons = {}
        rows = (
            ('학급', (('previous_class', '이전 반', lambda: self.on_step_class(-1)),
                      ('next_class', '다음 반', lambda: self.on_step_class(1)),
                      ('restore_class', '학급 복원', self.on_restore_class))),
            ('배경 사진', (('previous_photo', '이전 사진', lambda: self.on_step_photo(-1)),
                          ('next_photo', '다음 사진', lambda: self.on_step_photo(1)),
                          ('restore_photo', '배경 복원', self.on_restore_photo))),
        )
        for index, (label, commands) in enumerate(rows):
            row = ttk.Frame(group)
            row.pack(fill='x', padx=6, pady=4)
            row.rowconfigure(0, minsize=30)
            row.columnconfigure(0, minsize=100)
            ttk.Label(row, text=label).grid(row=0, column=0, sticky='w')
            for column, (key, text, command) in enumerate(commands, 1):
                button = ttk.Button(row, text=text, command=command)
                button.grid(row=0, column=column, sticky='w', padx=(0, 6))
                self.basic_tool_buttons[key] = button

    def _change_class(self, grade, classnm):
        cleared = False
        def edit():
            nonlocal cleared
            self.grade_var.set(grade)
            self.class_var.set(classnm)
            current = identity(self._collect_draft())
            if current != self._manual_identity:
                cleared = bool(self.manual_timetable)
                self.manual_timetable = []
                self._manual_identity = current
        self._edit_draft(edit, '학급 변경')
        if cleared:
            self.status_var.set('학급 변경 · 수동 시간표 초기화됨')

    def on_step_class(self, offset):
        if self._applying:
            return
        text = self.class_var.get().strip()
        if text and (not text.isascii() or not text.isdigit()):
            self.status_var.set('반을 숫자로 입력하세요')
            return
        current = int(text) if text else 1
        self._change_class(self.grade_var.get(), str(max(1, current + offset) if text else 1))

    def on_restore_class(self):
        self._change_class(self.cfg.get('grade', ''), self.cfg.get('classnm', ''))

    def on_step_photo(self, offset):
        if self._applying:
            return
        count = len(self.preset_combo.cget('values'))
        if not count:
            return
        current = max(0, self.preset_combo.current())
        if self.photo_mode.get() == 'auto':
            current = main.datetime.now().timetuple().tm_yday % count
        def edit():
            self.preset_combo.current((current + offset) % count)
            self.photo_mode.set('preset')
            self.update_bg_ui_state()
        self._edit_draft(edit, '배경 사진 변경')

    def on_restore_photo(self):
        def edit():
            self.photo_mode.set(self.cfg.get('bg_photo_mode', 'auto'))
            self.preset_combo.current(self.cfg.get('bg_photo_idx', 0))
            self.photo_path.set(self.cfg.get('custom_background') or '')
            self.update_bg_ui_state()
        self._edit_draft(edit, '저장된 배경 복원')

    def _build_section_tools(self, parent, section, row):
        shell = ttk.Frame(parent)
        shell.grid(row=row, column=0, columnspan=3, sticky='ew', padx=10, pady=6)
        group = self._fold_tools(shell, '빠른 조정')
        buttons = self.section_tool_buttons[section] = {}
        rows = (
            ('가로 위치', (('left', '10%', lambda: self._quick_section(section, {'x': 10})),
                           ('center_x', '50%', lambda: self._quick_section(section, {'x': 50})),
                           ('right', '90%', lambda: self._quick_section(section, {'x': 90})))),
            ('세로 위치', (('top', '10%', lambda: self._quick_section(section, {'y': 10})),
                           ('center_y', '50%', lambda: self._quick_section(section, {'y': 50})),
                           ('bottom', '80%', lambda: self._quick_section(section, {'y': 80})))),
            ('글자 크기', (('smaller', '−10%', lambda: self.on_step_size(section, -10)),
                           ('normal_size', '100%', lambda: self._quick_section(section, {'font_size': 100})),
                           ('larger', '+10%', lambda: self.on_step_size(section, 10)))),
            ('글자 효과', (('none', '없음', lambda: self.on_effect_preset(section, 'none')),
                           ('soft', '부드럽게', lambda: self.on_effect_preset(section, 'soft')),
                           ('clear', '선명하게', lambda: self.on_effect_preset(section, 'clear')))),
        )
        for index, (label, commands) in enumerate(rows):
            line = ttk.Frame(group)
            line.pack(fill='x', padx=6, pady=4)
            line.rowconfigure(0, minsize=30)
            line.columnconfigure(0, minsize=100)
            ttk.Label(line, text=label).grid(row=0, column=0, sticky='w')
            for column, (key, text, command) in enumerate(commands, 1):
                button = ttk.Button(line, text=text, command=command)
                button.grid(row=0, column=column, sticky='w', padx=(0, 6))
                buttons[key] = button

    def _quick_section(self, section, changes, targets=None):
        targets = (section,) if targets is None else targets
        validate_style({'sections': {key: changes for key in targets}})
        def edit():
            for target in targets:
                for key, value in changes.items():
                    self.sec_vars[target][key].set(value)
                    self.label_vars[target][key].set(str(value))
        self._edit_draft(edit, '배치·효과 변경')

    def on_step_size(self, section, offset):
        value = self.sec_vars[section]['font_size'].get()
        self._quick_section(section, {'font_size': min(200, max(50, value + offset))})

    def on_effect_preset(self, section, preset):
        targets = tuple(self.sec_vars) if self.sync_effects.get() else (section,)
        self._quick_section(section, EFFECT_PRESETS[preset], targets)
