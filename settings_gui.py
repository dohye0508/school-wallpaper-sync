# -*- coding: utf-8 -*-
"""
학교 바탕화면 v1.4 편집기.
편집과 테마 선택은 창 안 미리보기에만 반영하고 명시적으로 적용한다.
"""

import json
import os
import subprocess
import sys
import threading
import queue
from PIL import Image, ImageTk, ImageDraw
from editor_tools import EditorTools
from editor_shortcuts import EditorShortcuts
from core.editor_state import EditHistory
from core.palettes import COLOR_GROUPS, color_palettes
from core.schoolday import target_day, validate_overrides
from core.themes import ThemeStore, apply_style, extract_style, validate_style
from copy import deepcopy
from core.latest import LatestWorker
from core.sync import identity
from core.storage import desktop_transaction
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser, simpledialog

import requests
import main

NEIS_BASE = "https://open.neis.go.kr/hub"
SCHOOL_LEVEL_ENDPOINT = {"초등학교": "elsTimetable", "중학교": "misTimetable", "고등학교": "hisTimetable"}

DEFAULT_COLORS = main.DEFAULT_COLORS
DEFAULT_TEXT_EFFECTS = main.DEFAULT_TEXT_EFFECTS
DEFAULT_SECTIONS = main.DEFAULT_SECTIONS
BACKGROUND_PHOTOS = main.BACKGROUND_PHOTOS if hasattr(main, "BACKGROUND_PHOTOS") else [
    "구름바다 위 산 일출",
    "안개 낀 초록 절벽",
    "실루엣 산 너머 일몰",
    "별이 쏟아지는 밤하늘 설산",
    "파스텔톤 해변 일출",
    "붉은 노을",
    "잔잔한 바다 수면",
    "오로라와 침엽수림"
]

def load_config():
    return main.load_config()

def save_config(cfg):
    main.save_config(cfg)


def search_school(name):
    cfg = main.load_config() or {}
    return main.search_school(cfg.get("api_key", main.API_KEY), name)


class App(EditorTools, EditorShortcuts, tk.Tk):
    def __init__(self):
        super().__init__()
        # 위젯을 다 만들기 전에는 창을 화면에 보여주지 않는다. (슬라이더/색상 버튼
        # 등 내용물이 다 붙기 전에 geometry()를 호출하면, Tk가 내용물 크기에 맞춰
        # 창을 먼저 크게 띄웠다가 첫 클릭(리드로우) 시점에야 지정한 크기로 줄어드는
        # 현상이 있었다 — 완성된 뒤에 크기를 다시 맞추고 그때 보여주면 이 깜빡임이 없다)
        self.withdraw()
        self.title("학교 바탕화면")
        self.resizable(True, True)
        # 창 크기 조정 (Y축 길이를 늘려 저장/초기화/닫기 버튼이 시원하게 보이도록 여백 확보)
        self.geometry("1200x760")
        try:
            icon_path = os.path.join(getattr(sys, '_MEIPASS', main.ROOT_DIR), "icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        # ttk 스타일 설정 (전체적으로 글자 크기를 11pt로 시원하게 확대)
        style = ttk.Style()
        style.configure(".", font=("맑은 고딕", 11))
        style.configure("TNotebook.Tab", font=("맑은 고딕", 11, "bold"))
        style.configure("TLabelframe.Label", font=("맑은 고딕", 11, "bold"))

        self.cfg = load_config()
        if not self.cfg:
            self.cfg = main.merge_defaults({})
        self.cfg = deepcopy(self.cfg)
        self._draft_base = deepcopy(self.cfg)
        self._refreshing = False
        self._searching = False
        self._session_cache = None
        self.manual_timetable = deepcopy(self.cfg.get('manual_timetable', []))
        self._manual_identity = identity(self.cfg)
        self._closing = False
        self._applying = False
        self._preview_revision = 0
        self._preview_image = None
        self._ui_events = queue.Queue()
        self._poll_id = None
        self._display_after = None
        self.screen_size = main.get_screen_size()
        self.theme_store = ThemeStore(os.path.join(main.BASE_DIR, "themes.json"), main.merge_defaults({}))
        self.search_results = []
        self.selected_school = None
        self.pending_apply = None
        self._preview_worker = LatestWorker(self._render_live_apply)
        # 위젯을 만드는 동안 슬라이더 초기값 세팅이 command 콜백을 트리거해서
        # 창을 열자마자 원치 않는 실시간 적용이 발생하는 것을 막는 플래그
        self._initializing = True

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=10, pady=8)
        editor = ttk.Frame(self.body, width=580)
        editor.pack(side="left", fill="both")
        editor.pack_propagate(False)
        # 탭 레이아웃 구성
        toolbar = ttk.Frame(editor)
        toolbar.pack(side="bottom", fill="x", padx=8, pady=(8, 4))
        ttk.Label(toolbar, text="테마", font=("맑은 고딕", 11, "bold")).pack(side="left")
        self.theme_combo = ttk.Combobox(toolbar, state="readonly", width=26)
        self.theme_combo.pack(side="left", padx=8)
        self.theme_combo.bind("<<ComboboxSelected>>", self.on_theme_selected)
        self.save_theme_btn = ttk.Button(toolbar, text="현재 디자인 저장", command=self.on_save_theme)
        self.save_theme_btn.pack(side="left")
        utility = ttk.Frame(editor)
        utility.pack(side='bottom', fill='x', padx=8, pady=4)
        self.day_var = tk.StringVar(value=self.cfg.get('display_day', 'today'))
        for title, value in (('오늘', 'today'), ('내일', 'tomorrow')):
            ttk.Radiobutton(utility, text=title, value=value, variable=self.day_var).pack(side='left', padx=3)
        self.refresh_btn = ttk.Button(utility, text='데이터 새로고침 (F5)', command=self.on_refresh_data)
        self.refresh_btn.pack(side='left', padx=6)
        self.meal_btn = ttk.Button(utility, text='급식 상세', command=self.on_meal_details)
        self.meal_btn.pack(side='left', padx=3)
        self.timetable_btn = ttk.Button(utility, text='수동 시간표', command=self.on_manual_timetable)
        self.timetable_btn.pack(side='left', padx=3)
        self.dirty_var = tk.StringVar(value='저장된 설정')
        self.preview_area = ttk.LabelFrame(self.body, text=" 적용 전 미리보기 ")
        self.preview_area.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.preview_note = ttk.Label(self.preview_area, text='', wraplength=470)
        self.preview_note.pack(side="bottom", fill="x", padx=12, pady=(4, 8))
        controls = ttk.Frame(self.preview_area)
        controls.pack(side='bottom', fill='x', padx=10, pady=(4, 2))
        self.zoom_var = tk.StringVar(value='화면에 맞춤')
        self.grid_var = tk.BooleanVar(value=False)
        self.compare_var = tk.BooleanVar(value=False)
        ttk.Combobox(controls, textvariable=self.zoom_var, values=['화면에 맞춤', '100%', '150%'],
                     state='readonly', width=12).pack(side='left')
        ttk.Checkbutton(controls, text='격자', variable=self.grid_var).pack(side='left', padx=4)
        ttk.Checkbutton(controls, text='저장본 보기', variable=self.compare_var).pack(side='left')
        surface = ttk.Frame(self.preview_area)
        surface.pack(fill='both', expand=True, padx=10, pady=8)
        surface.rowconfigure(0, weight=1)
        surface.columnconfigure(0, weight=1)
        self.preview_label = tk.Canvas(surface, background='#20252c', highlightthickness=0, width=100, height=100)
        self.preview_label.grid(row=0, column=0, sticky='nsew')
        horizontal = ttk.Scrollbar(surface, orient='horizontal', command=self.preview_label.xview)
        vertical = ttk.Scrollbar(surface, orient='vertical', command=self.preview_label.yview)
        horizontal.grid(row=1, column=0, sticky='ew')
        vertical.grid(row=0, column=1, sticky='ns')
        self.preview_label.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.preview_label.bind("<Configure>", self._on_preview_resize)
        notebook = self.notebook = ttk.Notebook(editor)
        notebook.pack(fill="both", expand=True)

        tab_basic = ttk.Frame(notebook)
        tab_colors = ttk.Frame(notebook)
        tab_design = ttk.Frame(notebook)
        self.tabs = dict(basic=tab_basic, colors=tab_colors, layout=tab_design)

        notebook.add(tab_basic, text=" 기본 설정 ")
        notebook.add(tab_colors, text=" 글자 색상 설정 ")
        notebook.add(tab_design, text=" 배치 및 효과 설정 ")

        pad = {"padx": 15, "pady": 4}

        # ----------------------------------------------------
        # Tab 1: 기본 설정 (학교/학급 + 배경 사진 통합)
        # ----------------------------------------------------
        basic_container, basic_content = self.make_scrollable_tab(tab_basic)
        basic_container.pack(fill='both', expand=True)
        ttk.Label(basic_content, text="학교 이름", font=("맑은 고딕", 11, "bold")).pack(anchor="w", **pad)
        search_row = ttk.Frame(basic_content)
        search_row.pack(anchor="w", padx=15, fill="x")
        self.school_name_var = tk.StringVar(value=self.cfg.get("school_name", ""))
        ttk.Entry(search_row, textvariable=self.school_name_var, width=38).pack(side="left")
        self.search_btn = ttk.Button(search_row, text="검색", command=self.on_search)
        self.search_btn.pack(side="left", padx=6)

        self.result_list = tk.Listbox(basic_content, height=4, width=54, font=("맑은 고딕", 11))
        self.result_list.pack(anchor="w", padx=15, pady=4)
        self.result_list.bind("<<ListboxSelect>>", self.on_result_select)

        self.selected_school_var = tk.StringVar(value=self._selected_school_label())
        ttk.Label(basic_content, textvariable=self.selected_school_var, foreground="#1a5fb4",
                  font=("맑은 고딕", 10, "bold")).pack(anchor="w", padx=15, pady=(0, 4))

        gc_row = ttk.Frame(basic_content)
        gc_row.pack(anchor="w", padx=15, pady=4, fill="x")
        ttk.Label(gc_row, text="학년").pack(side="left")
        self.grade_var = tk.StringVar(value=self.cfg.get("grade", ""))
        ttk.Entry(gc_row, textvariable=self.grade_var, width=6).pack(side="left", padx=(4, 20))
        ttk.Label(gc_row, text="반").pack(side="left")
        self.class_var = tk.StringVar(value=self.cfg.get("classnm", ""))
        ttk.Entry(gc_row, textvariable=self.class_var, width=6).pack(side="left", padx=4)

        # 구분선 추가
        ttk.Separator(basic_content, orient="horizontal").pack(fill="x", padx=15, pady=8)

        ttk.Label(basic_content, text="배경 사진", font=("맑은 고딕", 11, "bold")).pack(anchor="w", **pad)
        
        self.photo_mode = tk.StringVar(value=self.cfg.get("bg_photo_mode", "auto"))
        
        ttk.Radiobutton(basic_content, text="자동으로 매일 바꾸기", value="auto",
                         variable=self.photo_mode, command=self.update_bg_ui_state).pack(anchor="w", padx=15, pady=2)
                         
        ttk.Radiobutton(basic_content, text="기본 사진 고정", value="preset",
                         variable=self.photo_mode, command=self.update_bg_ui_state).pack(anchor="w", padx=15, pady=2)
                         
        preset_row = ttk.Frame(basic_content)
        preset_row.pack(anchor="w", padx=30, pady=2, fill="x")
        self.preset_combo = ttk.Combobox(preset_row, values=BACKGROUND_PHOTOS, state="readonly", width=34)
        self.preset_combo.pack(side="left")
        initial_idx = self.cfg.get("bg_photo_idx", 0)
        self.preset_combo.current(initial_idx if 0 <= initial_idx < 8 else 0)
        
        ttk.Radiobutton(basic_content, text="내 사진 선택", value="custom",
                         variable=self.photo_mode, command=self.update_bg_ui_state).pack(anchor="w", padx=15, pady=2)
                         
        photo_row = ttk.Frame(basic_content)
        photo_row.pack(anchor="w", padx=30, pady=2, fill="x")
        self.photo_path = tk.StringVar(value=self.cfg.get("custom_background") or "")
        self.photo_entry = ttk.Entry(photo_row, textvariable=self.photo_path, width=36, state="readonly")
        self.photo_entry.pack(side="left")
        self.photo_browse_btn = ttk.Button(photo_row, text="찾아보기...", command=self.on_browse_photo)
        self.photo_browse_btn.pack(side="left", padx=6)
        
        self.update_bg_ui_state()
        self._build_basic_tools(basic_content)

        # ----------------------------------------------------
        # Tab 2: 글자 색상 설정
        # ----------------------------------------------------
        color_container, color_content = self.make_scrollable_tab(tab_colors)
        color_container.pack(fill='both', expand=True)
        color_frame = self._tool_group(color_content, '요소별 글자 색상 설정')
        
        self.colors_vars = {}
        self.color_buttons = {}
        
        def is_light_color(hex_str):
            hex_str = hex_str.lstrip('#')
            if len(hex_str) == 6:
                r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
                brightness = (r * 299 + g * 587 + b * 114) / 1000
                return brightness > 128
            return True
            
        def make_color_btn(parent, label_text, key, val, r, c):
            cell = self._tool_cell(parent, r, c)
            ttk.Label(cell, text=label_text, font=("맑은 고딕", 11)).grid(row=0, column=0, sticky="w")
            color_var = tk.StringVar(value=val)
            self.colors_vars[key] = color_var
            
            btn = tk.Button(cell, text=val, width=9, relief="groove", font=("Consolas", 9, "bold"))
            btn.grid(row=0, column=1, sticky="w")
            self.color_buttons[key] = btn
            
            def update_btn():
                hex_val = color_var.get()
                btn.config(text=hex_val, bg=hex_val, fg="#000000" if is_light_color(hex_val) else "#FFFFFF")
                
            def choose_color():
                _, hex_val = colorchooser.askcolor(color_var.get(), parent=self, title=f"{label_text} 색상 선택")
                if hex_val:
                    color_var.set(hex_val)
                    update_btn()
                    
            btn.config(command=choose_color)
            update_btn()
            
        colors_cfg = self.cfg.get("font_colors", DEFAULT_COLORS)
        color_items = [
            ("학교 이름", "school_name", 0, 0),
            ("학년/반", "school_name_muted", 0, 1),
            ("시간표 제목", "timetable_title", 1, 0),
            ("교시 번호", "timetable_period", 1, 1),
            ("과목 이름", "timetable_subject", 2, 0),
            ("급식 제목", "meals_title", 2, 1),
            ("식사 구분", "meals_type", 3, 0),
            ("급식 메뉴", "meals_dish", 3, 1),
            ("요일 텍스트", "date_weekday", 4, 0),
            ("날짜 텍스트", "date_calendar", 4, 1),
            ("D-Day 라벨", "dday_label", 5, 0),
            ("학사일정 이름", "dday_name", 5, 1),
        ]
        
        for label, key, r, col in color_items:
            val = colors_cfg.get(key, DEFAULT_COLORS.get(key, "#FFFFFF"))
            make_color_btn(color_frame, label, key, val, r, col)

        self._build_color_tools(color_content)

        # ----------------------------------------------------
        # Tab 3: 배치 및 효과 설정
        # ----------------------------------------------------
        layout_frame = ttk.LabelFrame(tab_design, text=" 섹션 배치 및 개별 효과 설정 ")
        layout_frame.pack(fill="both", expand=True, padx=15, pady=6)

        # 일괄 동일 적용 체크박스
        self.sync_effects = tk.BooleanVar(value=True)
        ttk.Checkbutton(layout_frame, text="테두리·그림자 함께 적용",
                        variable=self.sync_effects).pack(anchor="w", padx=10, pady=4)

        # 섹션별 하위 탭 Notebook
        sub_notebook = self.section_tabs = ttk.Notebook(layout_frame)
        sub_notebook.pack(fill="both", expand=True, padx=8, pady=4)

        sections_cfg = self.cfg.get("sections", DEFAULT_SECTIONS)
        self.sec_vars = {}
        self.label_vars = {}
        self.section_tool_buttons = {}

        sections_info = [
            ("학교 정보", "school_info"),
            ("시간표", "timetable"),
            ("급식", "meals"),
            ("날짜 정보", "date_info"),
            ("학사일정 D-Day", "dday")
        ]

        for kr_name, sec_key in sections_info:
            tab_container, sec_frame = self.make_scrollable_tab(sub_notebook)
            sub_notebook.add(tab_container, text=f" {kr_name} ")

            sec_cfg = sections_cfg.get(sec_key, DEFAULT_SECTIONS[sec_key])
            
            # 각 섹션용 UI 변수 바인딩
            vars_dict = {
                "show": tk.BooleanVar(value=sec_cfg.get("show", True)),
                "x": tk.IntVar(value=sec_cfg.get("x", 50)),
                "y": tk.IntVar(value=sec_cfg.get("y", 50)),
                "font_size": tk.IntVar(value=sec_cfg.get("font_size", 100)),
                "stroke_width": tk.IntVar(value=sec_cfg.get("stroke_width", 0)),
                "shadow_blur": tk.IntVar(value=sec_cfg.get("shadow_blur", 3)),
                "shadow_opacity": tk.IntVar(value=sec_cfg.get("shadow_opacity", 200))
            }
            self.sec_vars[sec_key] = vars_dict

            # 정수 레이블용 문자형 변수 바인딩 (소수점 지저분한 문자 잘림 해결)
            self.label_vars[sec_key] = {}
            for k in ["x", "y", "font_size", "stroke_width", "shadow_blur", "shadow_opacity"]:
                self.label_vars[sec_key][k] = tk.StringVar(value=str(sec_cfg.get(k, DEFAULT_SECTIONS[sec_key][k])))

            # 0. 표시 여부
            ttk.Checkbutton(sec_frame, text="바탕화면에 이 섹션 표시", variable=vars_dict["show"]).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=3)

            # 1. 가로 위치 X (%)
            ttk.Label(sec_frame, text="가로 위치 X (%)").grid(row=1, column=0, sticky="w", padx=10, pady=2)
            x_scale = ttk.Scale(sec_frame, from_=0, to=100, variable=vars_dict["x"], orient="horizontal", length=220)
            x_scale.grid(row=1, column=1, padx=10, pady=2)
            x_scale.config(command=self.make_slider_callback(sec_key, "x", vars_dict["x"]))
            x_scale.set(vars_dict["x"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["x"]).grid(row=1, column=2, padx=5, sticky="w")

            # 2. 세로 위치 Y (%)
            ttk.Label(sec_frame, text="세로 위치 Y (%)").grid(row=2, column=0, sticky="w", padx=10, pady=2)
            y_scale = ttk.Scale(sec_frame, from_=0, to=100, variable=vars_dict["y"], orient="horizontal", length=220)
            y_scale.grid(row=2, column=1, padx=10, pady=2)
            y_scale.config(command=self.make_slider_callback(sec_key, "y", vars_dict["y"]))
            y_scale.set(vars_dict["y"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["y"]).grid(row=2, column=2, padx=5, sticky="w")

            # 3. 글자 크기 (%) 슬라이더
            ttk.Label(sec_frame, text="글자 크기 (%)").grid(row=3, column=0, sticky="w", padx=10, pady=2)
            fs_scale = ttk.Scale(sec_frame, from_=50, to=200, variable=vars_dict["font_size"], orient="horizontal", length=220)
            fs_scale.grid(row=3, column=1, padx=10, pady=2)
            fs_scale.config(command=self.make_slider_callback(sec_key, "font_size", vars_dict["font_size"]))
            fs_scale.set(vars_dict["font_size"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["font_size"]).grid(row=3, column=2, padx=5, sticky="w")

            # 4. 테두리 두께 슬라이더
            ttk.Label(sec_frame, text="테두리 두께 (px)").grid(row=4, column=0, sticky="w", padx=10, pady=2)
            stroke_slider = ttk.Scale(sec_frame, from_=0, to=5, variable=vars_dict["stroke_width"], orient="horizontal", length=220)
            stroke_slider.grid(row=4, column=1, padx=10, pady=2)
            stroke_slider.config(command=self.make_slider_callback(sec_key, "stroke_width", vars_dict["stroke_width"]))
            stroke_slider.set(vars_dict["stroke_width"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["stroke_width"]).grid(row=4, column=2, padx=5, sticky="w")

            # 5. 그림자 블러 슬라이더
            ttk.Label(sec_frame, text="그림자 흐림").grid(row=5, column=0, sticky="w", padx=10, pady=2)
            blur_slider = ttk.Scale(sec_frame, from_=0, to=10, variable=vars_dict["shadow_blur"], orient="horizontal", length=220)
            blur_slider.grid(row=5, column=1, padx=10, pady=2)
            blur_slider.config(command=self.make_slider_callback(sec_key, "shadow_blur", vars_dict["shadow_blur"]))
            blur_slider.set(vars_dict["shadow_blur"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["shadow_blur"]).grid(row=5, column=2, padx=5, sticky="w")

            # 6. 그림자 투명도 슬라이더
            ttk.Label(sec_frame, text="그림자 진하기").grid(row=6, column=0, sticky="w", padx=10, pady=2)
            op_slider = ttk.Scale(sec_frame, from_=0, to=255, variable=vars_dict["shadow_opacity"], orient="horizontal", length=220)
            op_slider.grid(row=6, column=1, padx=10, pady=2)
            op_slider.config(command=self.make_slider_callback(sec_key, "shadow_opacity", vars_dict["shadow_opacity"]))
            op_slider.set(vars_dict["shadow_opacity"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["shadow_opacity"]).grid(row=6, column=2, padx=5, sticky="w")

            # 7. 학사일정만 표시 개수 지정 가능 (최대 10개, 기본 3개)
            if sec_key == "dday":
                ttk.Label(sec_frame, text="표시 개수 (최대 10)").grid(row=7, column=0, sticky="w", padx=10, pady=2)
                self.dday_count_var = tk.IntVar(value=self.cfg.get("dday_count", 3))
                count_spin = ttk.Spinbox(sec_frame, from_=1, to=10, textvariable=self.dday_count_var, width=5,
                                          command=self.trigger_live_apply)
                count_spin.grid(row=7, column=1, sticky="w", padx=10, pady=2)

                # 8. 수동 학사일정 (NEIS에 없는 시험 등을 직접 추가) — 있으면 자동 조회분과
                #    합쳐서 날짜순으로 표시된다. 개별 색상 지정 가능(기본은 노란색과 동일)
                ttk.Separator(sec_frame, orient="horizontal").grid(row=8, column=0, columnspan=3, sticky="ew", padx=10, pady=6)
                ttk.Label(sec_frame, text="직접 추가한 일정",
                          font=("맑은 고딕", 10, "bold")).grid(row=9, column=0, columnspan=3, sticky="w", padx=10)

                self.manual_dday = [dict(m) for m in (self.cfg.get("manual_dday") or [])]
                self.manual_list = tk.Listbox(sec_frame, height=4, width=48, font=("맑은 고딕", 10))
                self.manual_list.grid(row=10, column=0, columnspan=3, sticky="w", padx=10, pady=3)
                self._refresh_manual_dday_list()

                add_row = ttk.Frame(sec_frame)
                add_row.grid(row=11, column=0, columnspan=3, sticky="w", padx=10, pady=2)
                ttk.Label(add_row, text="이름").pack(side="left")
                self.manual_name_var = tk.StringVar()
                ttk.Entry(add_row, textvariable=self.manual_name_var, width=12).pack(side="left", padx=(4, 10))
                ttk.Label(add_row, text="날짜(YYYY-MM-DD)").pack(side="left")
                self.manual_date_var = tk.StringVar()
                ttk.Entry(add_row, textvariable=self.manual_date_var, width=12).pack(side="left", padx=(4, 10))

                self.manual_color_var = tk.StringVar(value="")  # 비어있으면 기본 노란색
                self.manual_color_btn = tk.Button(add_row, text="기본색", width=7, command=self.on_pick_manual_color)
                self.manual_color_btn.pack(side="left", padx=(0, 10))
                self._default_button_bg = self.manual_color_btn.cget("bg")

                btn_row2 = ttk.Frame(sec_frame)
                btn_row2.grid(row=12, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))
                ttk.Button(btn_row2, text="추가", command=self.on_add_manual_dday).pack(side="left", padx=(0, 6))
                ttk.Button(btn_row2, text="선택 삭제", command=self.on_remove_manual_dday).pack(side="left")

            self._build_section_tools(sec_frame, sec_key, 13 if sec_key == 'dday' else 7)

        # ----------------------------------------------------
        # 공통 하단 영역
        # ----------------------------------------------------
        self.status_var = tk.StringVar(value="")
        status_row = ttk.Frame(self)
        status_row.pack(fill='x', padx=15, pady=(2, 0))
        ttk.Label(status_row, textvariable=self.dirty_var, foreground='#956000').pack(side='right', padx=(10, 0))
        self.status_label = ttk.Label(status_row, textvariable=self.status_var, foreground='#1a7a1a', width=1)
        self.status_label.pack(side='left', fill='x', expand=True)
        self.status_label.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(100, event.width)))

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=10)
        self.apply_btn = ttk.Button(btn_row, text="저장 및 지금 적용", command=self.on_apply)
        self.apply_btn.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(btn_row, text="변경 취소", command=self.on_cancel)
        self.cancel_btn.pack(side="left", padx=6)
        ttk.Button(btn_row, text="디자인 기본값", command=self.on_reset).pack(side="left", padx=6)
        ttk.Button(btn_row, text="닫기", command=self.destroy).pack(side="left", padx=6)

        # 실시간 바탕화면 연동 (Live Apply) 바인딩 시작
        self.bind_variables_for_live_apply()
        self.history = EditHistory(self._collect_draft())
        self._build_commands()
        # 위젯 구성이 끝났으니 이제부터는 사용자의 실제 조작만 실시간 적용으로 취급한다
        self._initializing = False

        # 모든 위젯이 다 붙은 뒤 실제 크기를 계산시키고, 원하는 크기로 다시
        # 고정한 다음 화면 가운데에 배치하고서야 창을 보여준다 (깜빡임 방지).
        # update_idletasks()만으로는 Notebook처럼 크기 협상이 한 틱 늦게 끝나는
        # 위젯이 있어 완전히 반영되지 않는 경우가 있어 update()로 강제로 다 처리한다.
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1200, sw - 60), min(780, sh - 80)
        self.minsize(min(1020, w), min(650, h))
        self.geometry(f"{w}x{h}+{max(0, (sw-w)//2)}+{max(0, (sh-h)//2)}")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._refresh_themes()
        self._poll_id = self.after(50, self._drain_ui_events)
        self.deiconify()
        self.trigger_live_apply()

    def destroy(self):
        if getattr(self, "_applying", False):
            messagebox.showinfo("적용 중", "적용이 끝난 뒤 창을 닫아주세요.", parent=self)
            return
        if (hasattr(self, 'history') and self._collect_draft() != self.cfg
                and not messagebox.askyesno('적용하지 않은 변경', '변경을 적용하지 않고 닫을까요?', parent=self)):
            return
        self._closing = True
        if hasattr(self, "_preview_worker"):
            self._preview_worker.invalidate()
        for timer in (getattr(self, "pending_apply", None), getattr(self, "_poll_id", None), getattr(self, "_display_after", None)):
            if timer:
                self.after_cancel(timer)
        super().destroy()

    def _refresh_themes(self, selected=None):
        try:
            self.themes = self.theme_store.list_themes()
        except (ValueError, OSError) as error:
            from core.themes import builtins
            self.themes = builtins(main.merge_defaults({}))
            self.status_var.set(f"테마 파일을 읽지 못했습니다: {error}")
        self.theme_combo.configure(values=[item['name'] for item in self.themes])
        self.theme_combo.set(selected or "테마를 선택하세요")

    def _build_color_tools(self, parent):
        self.color_palettes = color_palettes(DEFAULT_COLORS)
        palettes = self._tool_group(parent, '색상 조합')
        self.palette_buttons = {}
        for index, palette in enumerate(self.color_palettes):
            row = self._tool_cell(palettes, index // 2, index % 2)
            button = ttk.Button(row, text=palette['name'],
                                command=lambda item=palette: self.on_color_palette(item))
            button.grid(row=0, column=0, sticky='w')
            self.palette_buttons[palette['id']] = button
            swatches = ttk.Frame(row)
            swatches.grid(row=0, column=1, sticky='w')
            for key in ('school_name', 'school_name_muted', 'timetable_title', 'meals_title', 'dday_label'):
                tk.Frame(swatches, width=19, height=14, background=palette['colors'][key],
                         borderwidth=1, relief='solid').pack(side='left', padx=1)

        grouped = self._tool_group(parent, '한 번에 변경')
        self.group_color_buttons = {}
        for index, (group, label) in enumerate((('all', '전체 글자…'), ('body', '본문…'),
                                               ('accent', '제목·강조…'))):
            row = self._tool_cell(grouped, index // 2, index % 2)
            button = ttk.Button(row, text=label,
                                command=lambda key=group: self.on_group_color(key))
            button.grid(row=0, column=0, sticky='w')
            self.group_color_buttons[group] = button
        row = self._tool_cell(grouped, 1, 1)
        self.reset_colors_btn = ttk.Button(row, text='색상 초기화',
                                           command=lambda: self._change_colors(DEFAULT_COLORS, '색상 초기화'))
        self.reset_colors_btn.grid(row=0, column=0, sticky='w')

    def _change_colors(self, colors, label):
        if self._applying:
            return
        validate_style({'font_colors': colors})
        def edit():
            for key, color in colors.items():
                self.colors_vars[key].set(color)
                self.update_color_button_visuals(key)
        self._edit_draft(edit, label)

    def on_color_palette(self, palette):
        self._change_colors(palette['colors'], f"{palette['name']} 색상")

    def on_group_color(self, group):
        if self._applying:
            return
        keys = COLOR_GROUPS[group]
        labels = {'all': '전체 글자', 'body': '본문', 'accent': '제목·강조'}
        first = next(key for key in self.colors_vars if key in keys)
        _, color = colorchooser.askcolor(self.colors_vars[first].get(), parent=self,
                                         title=f'{labels[group]} 색상 선택')
        if color:
            self._change_colors(dict.fromkeys(keys, color), f'{labels[group]} 색상 변경')

    def on_theme_selected(self, event=None):
        if self._applying:
            return
        index = self.theme_combo.current()
        if 0 <= index < len(self.themes):
            try:
                draft = apply_style(self._collect_draft(), self.themes[index]['style'])
                self._remember()
                self._load_widgets(draft)
                self.status_var.set("테마 미리보기 · 적용 전")
            except (ValueError, tk.TclError) as error:
                messagebox.showerror("테마 선택 실패", str(error), parent=self)

    def on_save_theme(self):
        if self._applying:
            return
        name = simpledialog.askstring("내 테마 저장", "디자인 이름 (최대 40자)", parent=self)
        if name is None:
            return
        try:
            item = self.theme_store.save(name, self._collect_draft())
            self._refresh_themes(item['name'])
            self.status_var.set("디자인 저장 완료")
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror("테마 저장 실패", str(error), parent=self)

    def _load_widgets(self, cfg):
        self._initializing = True
        try:
            self._draft_base = deepcopy(cfg)
            self.selected_school = None
            self.search_results = []
            self.result_list.delete(0, 'end')
            self.day_var.set(cfg.get('display_day', 'today'))
            self.manual_timetable = deepcopy(cfg.get('manual_timetable', []))
            self._manual_identity = identity(cfg)
            self.selected_school_var.set(f"현재 선택된 학교: {cfg.get('school_name') or '없음'}")
            for key, variable in (("school_name", self.school_name_var), ("grade", self.grade_var), ("classnm", self.class_var)):
                variable.set(cfg.get(key, ""))
            self.photo_mode.set(cfg.get('bg_photo_mode', 'auto'))
            self.preset_combo.current(cfg.get('bg_photo_idx', 0))
            self.photo_path.set(cfg.get('custom_background') or '')
            self.dday_count_var.set(cfg.get('dday_count', 3))
            self.manual_dday = deepcopy(cfg.get('manual_dday', []))
            self._refresh_manual_dday_list()
            for key, variable in self.colors_vars.items():
                variable.set(cfg['font_colors'][key])
                self.update_color_button_visuals(key)
            for section, variables in self.sec_vars.items():
                for key, variable in variables.items():
                    variable.set(cfg['sections'][section][key])
                    if key in self.label_vars[section]:
                        self.label_vars[section][key].set(str(cfg['sections'][section][key]))
            self.update_bg_ui_state()
        finally:
            self._initializing = False
        self.trigger_live_apply()

    def on_cancel(self):
        if self._applying:
            return
        self._remember()
        self.selected_school = None
        self.search_results = []
        self.result_list.delete(0, tk.END)
        self._load_widgets(deepcopy(self.cfg))
        self.selected_school_var.set(self._selected_school_label())
        self.theme_combo.set("마지막 적용 상태")
        self.status_var.set("변경 취소 완료")

    def make_scrollable_tab(self, parent):
        """섹션 탭 안에 위젯이 많아져도(예: 수동 학사일정 목록) 창 높이를 넘어가면
        스크롤로 볼 수 있도록 캔버스+스크롤바로 감싼 컨테이너를 만든다.
        parent에 넣을 컨테이너와, 그 안에 실제 위젯을 채울 내부 프레임을 반환한다."""
        container = ttk.Frame(parent)
        canvas = tk.Canvas(container, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return container, inner

    def make_slider_callback(self, sec_name, key, var):
        def callback(val):
            if getattr(self, '_initializing', False):
                return
            val_int = int(float(val))
            var.set(val_int)
            self.label_vars[sec_name][key].set(str(val_int))
            
            # 테두리/그림자 효과 일괄 적용 옵션이 켜져 있을 때 동기화
            # (창 초기화 중에는 아직 생성되지 않은 섹션이 있을 수 있으므로 존재 여부를 확인한다)
            if self.sync_effects.get() and key in ["stroke_width", "shadow_blur", "shadow_opacity"]:
                for other_sec in ["school_info", "timetable", "meals", "date_info", "dday"]:
                    if other_sec != sec_name and key in self.sec_vars.get(other_sec, {}):
                        self.sec_vars[other_sec][key].set(val_int)
                        self.label_vars[other_sec][key].set(str(val_int))
            self.trigger_live_apply()
        return callback

    def bind_variables_for_live_apply(self):
        for variable in (self.day_var, self.school_name_var, self.grade_var, self.class_var):
            variable.trace_add('write', self.trigger_live_apply)
        self.zoom_var.trace_add('write', lambda *args: self._display_preview())
        self.grid_var.trace_add('write', lambda *args: self._display_preview())
        self.compare_var.trace_add('write', self.trigger_live_apply)
        # 1. 배경 설정 관련
        self.photo_mode.trace_add("write", self.trigger_live_apply)
        self.preset_combo.bind("<<ComboboxSelected>>", self.trigger_live_apply)
        self.photo_path.trace_add("write", self.trigger_live_apply)

        # 2. 색상 관련
        for v in self.colors_vars.values():
            v.trace_add("write", self.trigger_live_apply)
            
        # 3. 각 섹션별 표시 여부 체크박스만 바인딩 (슬라이더는 command 콜백에서 자체 호출)
        for s in ["school_info", "timetable", "meals", "date_info", "dday"]:
            self.sec_vars[s]["show"].trace_add("write", self.trigger_live_apply)

        # 4. 학사일정 표시 개수 (스핀박스에 직접 숫자를 입력하는 경우까지 반영)
        self.dday_count_var.trace_add("write", self.trigger_live_apply)

    def trigger_live_apply(self, *args):
        if getattr(self, "_initializing", False):
            return  # 창을 여는 중 위젯 초기값 세팅으로 인한 트리거는 무시
        if hasattr(self, 'history'):
            self._update_dirty()
        self._preview_revision += 1
        self._preview_worker.invalidate()
        if self.pending_apply:
            self.after_cancel(self.pending_apply)
        # 80ms 디바운스 적용으로 드래그 시 렉 줄임
        self.pending_apply = self.after(80, self.perform_live_apply)

    def _safe_dday_count(self):
        # 스핀박스에 직접 숫자를 지우고 타이핑하는 중처럼 값이 비어있거나
        # 숫자가 아닐 때 IntVar.get()이 TclError를 던지는 걸 방지한다
        try:
            return max(1, min(10, int(self.dday_count_var.get())))
        except (tk.TclError, ValueError):
            return self.cfg.get("dday_count", 3)

    def _refresh_manual_dday_list(self):
        self.manual_list.delete(0, tk.END)
        for m in self.manual_dday:
            date_disp = m["date"]
            if len(date_disp) == 8:
                date_disp = f"{date_disp[:4]}-{date_disp[4:6]}-{date_disp[6:]}"
            color_disp = m["color"] if m.get("color") else "기본색"
            self.manual_list.insert(tk.END, f"{m['name']}  ({date_disp})  [{color_disp}]")

    def on_pick_manual_color(self):
        _, hex_val = colorchooser.askcolor(self.manual_color_var.get() or "#FF6B6B",
                                            parent=self, title="이 학사일정만의 색상 선택")
        if hex_val:
            self.manual_color_var.set(hex_val)
            self.manual_color_btn.config(text=hex_val, bg=hex_val)

    def on_add_manual_dday(self):
        name = self.manual_name_var.get().strip()
        date_raw = self.manual_date_var.get().strip().replace("-", "").replace("/", "").replace(".", "")
        if not name or not date_raw:
            messagebox.showwarning("입력 필요", "이름과 날짜를 모두 입력해주세요.")
            return
        try:
            main.datetime.strptime(date_raw, "%Y%m%d")
        except ValueError:
            messagebox.showwarning("날짜 형식 오류", "날짜는 YYYY-MM-DD 형식으로 입력해주세요. (예: 2026-10-20)")
            return

        self.manual_dday.append({
            "name": name,
            "date": date_raw,
            "color": self.manual_color_var.get() or None,
        })
        self._refresh_manual_dday_list()
        self.manual_name_var.set("")
        self.manual_date_var.set("")
        self.manual_color_var.set("")
        self.manual_color_btn.config(text="기본색", bg=self._default_button_bg)
        self.trigger_live_apply()

    def on_remove_manual_dday(self):
        selection = self.manual_list.curselection()
        if not selection:
            return
        del self.manual_dday[selection[0]]
        self._refresh_manual_dday_list()
        self.trigger_live_apply()

    def _collect_draft(self):
        cfg = deepcopy(self._draft_base)
        if self.selected_school:
            school = self.selected_school
            cfg.update(edu_code=school['ATPT_OFCDC_SC_CODE'], school_code=school['SD_SCHUL_CODE'],
                       school_name=school['SCHUL_NM'], school_kind=school['SCHUL_KND_SC_NM'])
        else:
            cfg['school_name'] = self.school_name_var.get().strip()
        cfg.update(grade=self.grade_var.get().strip(), classnm=self.class_var.get().strip(),
                   bg_photo_mode=self.photo_mode.get(), bg_photo_idx=max(0, self.preset_combo.current()),
                   custom_background=self.photo_path.get().strip() or None,
                   dday_count=self._safe_dday_count(), manual_dday=deepcopy(self.manual_dday))
        cfg['display_day'] = self.day_var.get()
        cfg['manual_timetable'] = deepcopy(self.manual_timetable) if identity(cfg) == self._manual_identity else []
        cfg['font_colors'] = {key: variable.get() for key, variable in self.colors_vars.items()}
        cfg['sections'] = {name: {key: variable.get() for key, variable in variables.items()}
                           for name, variables in self.sec_vars.items()}
        return cfg

    def perform_live_apply(self):
        self.pending_apply = None
        try:
            cfg = self._collect_draft()
            # School/class-specific corrections must not silently follow another class.
            if identity(cfg) != self._manual_identity:
                self.manual_timetable = []
                cfg['manual_timetable'] = []
                self._manual_identity = identity(cfg)
                self.status_var.set('학급 변경 · 수동 시간표 초기화됨')
            self.history.record(cfg)
            self._update_dirty()
            render_cfg = deepcopy(self.cfg) if self.compare_var.get() else deepcopy(cfg)
            cache = self._preview_cache(render_cfg)
            day = target_day(render_cfg, main.datetime.now()).strftime('%Y%m%d')
            if not render_cfg.get('school_code'):
                note = '학교 미설정 · 예시 데이터'
            else:
                states = cache.get('states', {})
                states = states if isinstance(states, dict) else {}
                state_labels = {'fresh': '조회', 'cached': '저장', 'empty': '없음', 'unavailable': '실패'}
                status = ' · '.join(f'{label} {state_labels.get(states.get(key), "미확인")}'
                                    for key, label in (('timetable', '시간표'), ('meals', '급식'), ('schedule', '일정')))
                if cache.get('date') != day:
                    status = '저장 데이터 없음 · F5 새로고침'
                note = ('저장본' if self.compare_var.get() else '편집본') + ' · ' + (
                    '내일' if render_cfg.get('display_day') == 'tomorrow' else '오늘') + '\n' + status
            self.preview_note.configure(text=note)
            render_cfg['_preview_revision'] = self._preview_revision
            self._preview_worker.submit(render_cfg)
        except (ValueError, tk.TclError) as error:
            self.status_var.set(f"입력값을 확인해주세요: {error}")

    def _render_live_apply(self, cfg, commit):
        revision = cfg.pop('_preview_revision', self._preview_revision)
        try:
            image = self._compose_preview(cfg)
            commit(lambda: self._ui_events.put(('preview', revision, image, None)))
        except Exception as error:
            commit(lambda: self._ui_events.put(('preview', revision, None, str(error))))

    def _compose_preview(self, cfg):
        cfg = deepcopy(cfg)
        cache = self._preview_cache(cfg)
        if not cfg.get('school_code'):
            cfg['school_name'] = '우리 학교 · 예시'
            cache = {'timetable': [{'period': '1', 'subject': '국어'}, {'period': '2', 'subject': '수학'}],
                     'meals': [{'name': '중식', 'dishes': '잡곡밥\n김치찌개\n과일'}], 'schedule': []}
        return main.compose_wallpaper(cfg, main.datetime.now(), cache['timetable'], cache['meals'],
                                      cache.get('schedule'), size=self.screen_size)

    def _drain_ui_events(self):
        if self._closing:
            return
        try:
            while True:
                kind, a, b, error = self._ui_events.get_nowait()
                if kind == 'preview':
                    if a != self._preview_revision:
                        continue
                    if error:
                        self.status_var.set(f"미리보기 실패: {error}")
                    else:
                        self._preview_image = b
                        self._display_preview()
                elif kind == 'applied':
                    self._applying = False
                    for button in (self.apply_btn, self.cancel_btn, self.save_theme_btn):
                        button.configure(state='normal')
                    self.theme_combo.configure(state='readonly')
                    if error:
                        self.status_var.set(f"적용 실패: {error}")
                        messagebox.showerror("적용 실패", error, parent=self)
                    else:
                        current_draft = self._collect_draft()
                        self.cfg = deepcopy(a)
                        self._session_cache = None
                        self._load_widgets(current_draft)
                        warning = b.get('autostart_error') or b.get('warning') or ' / '.join(b.get('warnings', []))
                        self.status_var.set(f"배경 적용 완료 · {warning}" if warning else "배경 적용 완료")
                        self.trigger_live_apply()
                elif kind == 'refreshed':
                    self._refreshing = False
                    self.refresh_btn.configure(state='normal')
                    current = self._collect_draft()
                    current_key = (identity(current), target_day(current, main.datetime.now()).strftime('%Y%m%d'))
                    if error:
                        self.status_var.set(f'새로고침 실패: {error}')
                    elif a == current_key:
                        self._session_cache = (a, deepcopy(b))
                        self.status_var.set('데이터 새로고침 완료')
                        self.trigger_live_apply()
                    else:
                        self.status_var.set('선택 변경됨 · 다시 새로고침하세요')
                elif kind == 'exported':
                    self.status_var.set(f'이미지 내보내기 실패: {error}' if error else f'이미지를 저장했습니다: {a}')
                elif kind == 'searched':
                    self._searching = False
                    self.search_btn.configure(state='normal')
                    if a != self.school_name_var.get().strip():
                        continue
                    if error:
                        self.status_var.set(f'학교 검색 실패: {error}')
                        continue
                    self.search_results = b
                    self.selected_school = None
                    self.selected_school_var.set('검색 결과에서 학교를 선택해주세요')
                    self.result_list.delete(0, tk.END)
                    for school in b:
                        self.result_list.insert(tk.END, f"{school['SCHUL_NM']}  [{school['SCHUL_KND_SC_NM']}]  {school.get('ORG_RDNMA', '')}")
                    if not b:
                        self.result_list.insert(tk.END, '(검색 결과 없음)')
                    self.status_var.set(f'학교 {len(b)}곳을 찾았습니다.')
        except queue.Empty:
            pass
        self._poll_id = self.after(50, self._drain_ui_events)

    def _on_preview_resize(self, event=None):
        self.preview_note.configure(wraplength=max(100, self.preview_area.winfo_width() - 32))
        if self._display_after:
            self.after_cancel(self._display_after)
        self._display_after = self.after(100, self._display_preview)

    def _display_preview(self):
        self._display_after = None
        if self._preview_image is None or self._closing:
            return
        image = self._preview_image.copy()
        if self.grid_var.get():
            draw = ImageDraw.Draw(image)
            for step in range(1, 10):
                x, y = image.width * step // 10, image.height * step // 10
                draw.line((x, 0, x, image.height), fill='#7eabb3', width=1)
                draw.line((0, y, image.width, y), fill='#7eabb3', width=1)
        width, height = max(100, self.preview_label.winfo_width()), max(100, self.preview_label.winfo_height())
        zoom = self.zoom_var.get()
        if zoom == '화면에 맞춤':
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
        elif zoom == '150%':
            image = image.resize((image.width * 3 // 2, image.height * 3 // 2), Image.Resampling.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(image, master=self)
        self.preview_label.delete('all')
        x, y = max(0, (width-image.width)//2), max(0, (height-image.height)//2)
        self.preview_label.create_image(x, y, image=self._preview_photo, anchor='nw')
        self.preview_label.configure(scrollregion=(0, 0, max(width, image.width), max(height, image.height)))

    def update_color_button_visuals(self, key):
        if key in self.color_buttons:
            btn = self.color_buttons[key]
            hex_val = self.colors_vars[key].get()
            
            def is_light_color(hex_str):
                hex_str = hex_str.lstrip('#')
                if len(hex_str) == 6:
                    r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
                    brightness = (r * 299 + g * 587 + b * 114) / 1000
                    return brightness > 128
                return True
                
            btn.config(text=hex_val, bg=hex_val, fg="#000000" if is_light_color(hex_val) else "#FFFFFF")

    def on_reset(self):
        # 1. 색상 기본값으로 되돌리기
        for k, v in self.colors_vars.items():
            v.set(DEFAULT_COLORS.get(k, "#FFFFFF"))
            self.update_color_button_visuals(k)
            
        # 2. 일괄 동기화 체크박스 켜기
        self.sync_effects.set(True)
        
        # 3. 각 섹션 변수를 DEFAULT_SECTIONS 기본값으로 리셋
        for s in ["school_info", "timetable", "meals", "date_info", "dday"]:
            sec_def = DEFAULT_SECTIONS[s]
            for key, val in sec_def.items():
                if key in self.sec_vars[s]:
                    self.sec_vars[s][key].set(val)
                if key in self.label_vars[s]:
                    self.label_vars[s][key].set(str(val))

        self.dday_count_var.set(3)

        self.status_var.set("기본 설정으로 초기화되었습니다 ✓")
        # 즉시 바탕화면에 변경 사항 반영
        self.trigger_live_apply()

    def update_bg_ui_state(self):
        mode = self.photo_mode.get()
        if mode == "auto":
            self.preset_combo.config(state="disabled")
            self.photo_entry.config(state="disabled")
            self.photo_browse_btn.config(state="disabled")
        elif mode == "preset":
            self.preset_combo.config(state="readonly")
            self.photo_entry.config(state="disabled")
            self.photo_browse_btn.config(state="disabled")
        elif mode == "custom":
            self.preset_combo.config(state="disabled")
            self.photo_entry.config(state="readonly")
            self.photo_browse_btn.config(state="normal")

    def _selected_school_label(self):
        cur = self.selected_school if hasattr(self, "selected_school") else None
        if cur:
            return f"현재 선택된 학교: {cur['SCHUL_NM']} ({cur['SCHUL_KND_SC_NM']})"
        cfg_name = self.cfg.get("school_name") if hasattr(self, "cfg") else ""
        if cfg_name:
            return f"현재 선택된 학교: {cfg_name} (저장된 설정)"
        return "선택된 학교 없음 — 검색 후 목록에서 클릭해주세요"

    def on_result_select(self, event=None):
        # 검색 결과를 클릭하는 즉시 선택을 확정한다 (Apply 시점까지 미루면
        # 포커스가 옮겨가는 등의 이유로 curselection()이 비어 예전 학교로
        # 조용히 되돌아가는 문제가 있었다)
        selection = self.result_list.curselection()
        if selection and self.search_results and selection[0] < len(self.search_results):
            self.selected_school = self.search_results[selection[0]]
            self.selected_school_var.set(self._selected_school_label())
            self.trigger_live_apply()

    def on_search(self):
        if self._searching or self._applying:
            return
        name = self.school_name_var.get().strip()
        if not name:
            messagebox.showwarning("입력 필요", "학교 이름을 입력해주세요.")
            return
        self._searching = True
        self.selected_school = None
        self.search_btn.configure(state='disabled')
        self.status_var.set('학교를 검색하고 있습니다…')
        def search():
            try:
                self._ui_events.put(('searched', name, search_school(name), None))
            except Exception as error:
                self._ui_events.put(('searched', name, None, str(error)))
        threading.Thread(target=search, daemon=True).start()

    def on_browse_photo(self):
        path = filedialog.askopenfilename(
            title="배경으로 사용할 사진 선택",
            filetypes=[("이미지 파일", "*.jpg *.jpeg *.png *.bmp"), ("모든 파일", "*.*")],
        )
        if path:
            self.photo_path.set(path)
            self.photo_mode.set("custom")
            self.update_bg_ui_state()

    def on_apply(self):
        if self._applying or self._searching:
            return
        try:
            cfg = self._collect_draft()
            if not cfg.get('school_code') or not cfg.get('grade') or not cfg.get('classnm'):
                raise ValueError("학교를 검색·선택하고 학년과 반을 입력해주세요.")
            if not self.selected_school and cfg['school_name'] != self._draft_base.get('school_name'):
                raise ValueError("학교 이름을 바꿨다면 검색 결과에서 학교를 선택해주세요.")
            # Validate display values without including private school data in themes.
            cfg = apply_style(cfg, extract_style(cfg))
            if identity(cfg) != self._manual_identity:
                cfg['manual_timetable'] = []
                self.manual_timetable = []
                self._manual_identity = identity(cfg)
            validate_overrides(cfg.get('manual_timetable', []))
            if cfg.get('bg_photo_mode') == 'custom':
                if not cfg.get('custom_background'):
                    raise ValueError("배경 사진을 선택해주세요.")
                with Image.open(cfg['custom_background']) as photo:
                    photo.verify()
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showwarning("입력 확인", str(error), parent=self)
            return
        self._applying = True
        for button in (self.apply_btn, self.cancel_btn, self.save_theme_btn):
            button.configure(state='disabled')
        self.theme_combo.configure(state='disabled')
        self.status_var.set("디자인을 저장하고 바탕화면에 적용하고 있습니다…")
        threading.Thread(target=self._run_engine, args=(deepcopy(cfg),), daemon=True).start()

    def _run_engine(self, cfg):
        try:
            result = main.apply_configuration(cfg)
            self._ui_events.put(('applied', cfg, result, None))
        except Exception as error:
            self._ui_events.put(('applied', cfg, None, str(error)))


if __name__ == "__main__":
    App().mainloop()
