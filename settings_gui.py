# -*- coding: utf-8 -*-
"""
학교 바탕화면 설정용 소형 GUI.
학교/반/배경 사진을 편하게 설정하고 "저장 및 지금 적용"으로 바로 반영한다.
실제 시간표/급식 갱신 엔진(main.py 기반 SchoolWallpaper.exe)은 건드리지 않고,
그 엔진이 읽는 dist/SchoolWallpaper/config.json 을 대신 편집해준다.
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser

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
    resp = requests.get(
        f"{NEIS_BASE}/schoolInfo",
        params={"KEY": main.API_KEY, "Type": "json", "SCHUL_NM": name, "pSize": 20},
        timeout=10,
    )
    data = json.loads(resp.content.decode("utf-8"))
    if "schoolInfo" not in data:
        return []
    return data["schoolInfo"][1]["row"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("학교 바탕화면 설정")
        self.resizable(False, False)
        # 창 크기 조정 (Y축 길이를 늘려 저장/초기화/닫기 버튼이 시원하게 보이도록 여백 확보)
        self.geometry("590x660")
        try:
            icon_path = os.path.join(main.ROOT_DIR, "icon.ico")
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
        self.search_results = []
        self.selected_school = None
        self.pending_apply = None

        # GUI 실행 시 오늘 날짜의 시간표/급식 데이터가 없으면 백그라운드로 자동 갱신
        if self.cfg.get("school_code"):
            threading.Thread(target=self._initial_auto_sync, daemon=True).start()

        # 탭 레이아웃 구성
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        tab_basic = ttk.Frame(notebook)
        tab_design = ttk.Frame(notebook)

        notebook.add(tab_basic, text=" 기본 설정 ")
        notebook.add(tab_design, text=" 배치 및 디자인 설정 ")

        pad = {"padx": 15, "pady": 4}

        # ----------------------------------------------------
        # Tab 1: 기본 설정 (학교/학급 + 배경 사진 통합)
        # ----------------------------------------------------
        ttk.Label(tab_basic, text="학교 이름", font=("맑은 고딕", 11, "bold")).pack(anchor="w", **pad)
        search_row = ttk.Frame(tab_basic)
        search_row.pack(anchor="w", padx=15, fill="x")
        self.school_name_var = tk.StringVar(value=self.cfg.get("school_name", ""))
        ttk.Entry(search_row, textvariable=self.school_name_var, width=38).pack(side="left")
        ttk.Button(search_row, text="검색", command=self.on_search).pack(side="left", padx=6)

        self.result_list = tk.Listbox(tab_basic, height=4, width=54, font=("맑은 고딕", 11))
        self.result_list.pack(anchor="w", padx=15, pady=4)

        gc_row = ttk.Frame(tab_basic)
        gc_row.pack(anchor="w", padx=15, pady=4, fill="x")
        ttk.Label(gc_row, text="학년").pack(side="left")
        self.grade_var = tk.StringVar(value=self.cfg.get("grade", ""))
        ttk.Entry(gc_row, textvariable=self.grade_var, width=6).pack(side="left", padx=(4, 20))
        ttk.Label(gc_row, text="반").pack(side="left")
        self.class_var = tk.StringVar(value=self.cfg.get("classnm", ""))
        ttk.Entry(gc_row, textvariable=self.class_var, width=6).pack(side="left", padx=4)

        # 구분선 추가
        ttk.Separator(tab_basic, orient="horizontal").pack(fill="x", padx=15, pady=8)

        ttk.Label(tab_basic, text="배경 사진 선택 모드", font=("맑은 고딕", 11, "bold")).pack(anchor="w", **pad)
        
        self.photo_mode = tk.StringVar(value=self.cfg.get("bg_photo_mode", "auto"))
        
        ttk.Radiobutton(tab_basic, text="자동으로 매일 순환 (감성 사진 8종)", value="auto",
                         variable=self.photo_mode, command=self.update_bg_ui_state).pack(anchor="w", padx=15, pady=2)
                         
        ttk.Radiobutton(tab_basic, text="감성 사진 8종 중 직접 고정 선택", value="preset",
                         variable=self.photo_mode, command=self.update_bg_ui_state).pack(anchor="w", padx=15, pady=2)
                         
        preset_row = ttk.Frame(tab_basic)
        preset_row.pack(anchor="w", padx=30, pady=2, fill="x")
        self.preset_combo = ttk.Combobox(preset_row, values=BACKGROUND_PHOTOS, state="readonly", width=34)
        self.preset_combo.pack(side="left")
        initial_idx = self.cfg.get("bg_photo_idx", 0)
        self.preset_combo.current(initial_idx if 0 <= initial_idx < 8 else 0)
        
        ttk.Radiobutton(tab_basic, text="내 컴퓨터 사진 파일 사용", value="custom",
                         variable=self.photo_mode, command=self.update_bg_ui_state).pack(anchor="w", padx=15, pady=2)
                         
        photo_row = ttk.Frame(tab_basic)
        photo_row.pack(anchor="w", padx=30, pady=2, fill="x")
        self.photo_path = tk.StringVar(value=self.cfg.get("custom_background") or "")
        self.photo_entry = ttk.Entry(photo_row, textvariable=self.photo_path, width=36, state="readonly")
        self.photo_entry.pack(side="left")
        self.photo_browse_btn = ttk.Button(photo_row, text="찾아보기...", command=self.on_browse_photo)
        self.photo_browse_btn.pack(side="left", padx=6)
        
        self.update_bg_ui_state()

        # ----------------------------------------------------
        # Tab 3: 배치 및 디자인 설정
        # ----------------------------------------------------
        color_frame = ttk.LabelFrame(tab_design, text=" 요소별 글자 색상 설정 ")
        color_frame.pack(fill="x", padx=15, pady=6)
        
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
            tk.Label(parent, text=label_text, font=("맑은 고딕", 11)).grid(row=r, column=c*2, sticky="w", padx=6, pady=3)
            color_var = tk.StringVar(value=val)
            self.colors_vars[key] = color_var
            
            btn = tk.Button(parent, text=val, width=9, relief="groove", font=("Consolas", 9, "bold"))
            btn.grid(row=r, column=c*2+1, sticky="w", padx=(2, 10), pady=3)
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
        ]
        
        for label, key, r, col in color_items:
            val = colors_cfg.get(key, DEFAULT_COLORS.get(key, "#FFFFFF"))
            make_color_btn(color_frame, label, key, val, r, col)

        # 섹션별 배치 및 효과 설정 영역
        layout_frame = ttk.LabelFrame(tab_design, text=" 섹션 배치 및 개별 효과 설정 ")
        layout_frame.pack(fill="both", expand=True, padx=15, pady=6)

        # 일괄 동일 적용 체크박스
        self.sync_effects = tk.BooleanVar(value=True)
        ttk.Checkbutton(layout_frame, text="텍스트 효과(테두리/그림자)를 모든 섹션에 동일하게 적용", 
                        variable=self.sync_effects).pack(anchor="w", padx=10, pady=4)

        # 섹션별 하위 탭 Notebook
        sub_notebook = ttk.Notebook(layout_frame)
        sub_notebook.pack(fill="both", expand=True, padx=8, pady=4)

        sections_cfg = self.cfg.get("sections", DEFAULT_SECTIONS)
        self.sec_vars = {}
        self.label_vars = {}

        sections_info = [
            ("학교 정보", "school_info"),
            ("시간표", "timetable"),
            ("급식", "meals"),
            ("날짜 정보", "date_info")
        ]

        for kr_name, sec_key in sections_info:
            sec_frame = ttk.Frame(sub_notebook)
            sub_notebook.add(sec_frame, text=f" {kr_name} ")

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
            ttk.Label(sec_frame, text="그림자 블러 (Blur)").grid(row=5, column=0, sticky="w", padx=10, pady=2)
            blur_slider = ttk.Scale(sec_frame, from_=0, to=10, variable=vars_dict["shadow_blur"], orient="horizontal", length=220)
            blur_slider.grid(row=5, column=1, padx=10, pady=2)
            blur_slider.config(command=self.make_slider_callback(sec_key, "shadow_blur", vars_dict["shadow_blur"]))
            blur_slider.set(vars_dict["shadow_blur"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["shadow_blur"]).grid(row=5, column=2, padx=5, sticky="w")

            # 6. 그림자 투명도 슬라이더
            ttk.Label(sec_frame, text="그림자 투명도 (Opacity)").grid(row=6, column=0, sticky="w", padx=10, pady=2)
            op_slider = ttk.Scale(sec_frame, from_=0, to=255, variable=vars_dict["shadow_opacity"], orient="horizontal", length=220)
            op_slider.grid(row=6, column=1, padx=10, pady=2)
            op_slider.config(command=self.make_slider_callback(sec_key, "shadow_opacity", vars_dict["shadow_opacity"]))
            op_slider.set(vars_dict["shadow_opacity"].get())
            ttk.Label(sec_frame, textvariable=self.label_vars[sec_key]["shadow_opacity"]).grid(row=6, column=2, padx=5, sticky="w")

        # ----------------------------------------------------
        # 공통 하단 영역
        # ----------------------------------------------------
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, foreground="#1a7a1a").pack(anchor="w", padx=15)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=10)
        self.apply_btn = ttk.Button(btn_row, text="저장 및 지금 적용", command=self.on_apply)
        self.apply_btn.pack(side="left", padx=6)
        ttk.Button(btn_row, text="초기화", command=self.on_reset).pack(side="left", padx=6)
        ttk.Button(btn_row, text="닫기", command=self.destroy).pack(side="left", padx=6)

        # 실시간 바탕화면 연동 (Live Apply) 바인딩 시작
        self.bind_variables_for_live_apply()

    def make_slider_callback(self, sec_name, key, var):
        def callback(val):
            val_int = int(float(val))
            var.set(val_int)
            self.label_vars[sec_name][key].set(str(val_int))
            
            # 테두리/그림자 효과 일괄 적용 옵션이 켜져 있을 때 동기화
            if self.sync_effects.get() and key in ["stroke_width", "shadow_blur", "shadow_opacity"]:
                for other_sec in ["school_info", "timetable", "meals", "date_info"]:
                    if other_sec != sec_name:
                        self.sec_vars[other_sec][key].set(val_int)
                        self.label_vars[other_sec][key].set(str(val_int))
            self.trigger_live_apply()
        return callback

    def bind_variables_for_live_apply(self):
        # 1. 배경 설정 관련
        self.photo_mode.trace_add("write", self.trigger_live_apply)
        self.preset_combo.bind("<<ComboboxSelected>>", self.trigger_live_apply)
        self.photo_path.trace_add("write", self.trigger_live_apply)
        
        # 2. 색상 관련
        for v in self.colors_vars.values():
            v.trace_add("write", self.trigger_live_apply)
            
        # 3. 각 섹션별 표시 여부 체크박스만 바인딩 (슬라이더는 command 콜백에서 자체 호출)
        for s in ["school_info", "timetable", "meals", "date_info"]:
            self.sec_vars[s]["show"].trace_add("write", self.trigger_live_apply)

    def trigger_live_apply(self, *args):
        if self.pending_apply:
            self.after_cancel(self.pending_apply)
        # 80ms 디바운스 적용으로 드래그 시 렉 줄임
        self.pending_apply = self.after(80, self.perform_live_apply)

    def perform_live_apply(self):
        self.pending_apply = None
        
        # 임시 설정 객체 취합
        font_colors = {k: v.get() for k, v in self.colors_vars.items()}
        
        sections_data = {}
        for s in ["school_info", "timetable", "meals", "date_info"]:
            sections_data[s] = {
                "show": self.sec_vars[s]["show"].get(),
                "x": self.sec_vars[s]["x"].get(),
                "y": self.sec_vars[s]["y"].get(),
                "font_size": self.sec_vars[s]["font_size"].get(),
                "stroke_width": self.sec_vars[s]["stroke_width"].get(),
                "shadow_blur": self.sec_vars[s]["shadow_blur"].get(),
                "shadow_opacity": self.sec_vars[s]["shadow_opacity"].get()
            }
            
        cfg = {
            "school_name": self.school_name_var.get().strip() or self.cfg.get("school_name", ""),
            "grade": self.grade_var.get().strip(),
            "classnm": self.class_var.get().strip(),
            "bg_photo_mode": self.photo_mode.get(),
            "bg_photo_idx": self.preset_combo.current(),
            "custom_background": self.photo_path.get().strip() or None,
            "font_colors": font_colors,
            "sections": sections_data
        }
        
        # API 호출 없이 로컬 데이터 캐시만을 이용해 바탕화면 실시간 반영 (100ms 이내)
        try:
            cache = main.load_data_cache(self.cfg)
            now = main.datetime.now()
            path = main.render_wallpaper(cfg, now, cache.get("timetable", []), cache.get("meals", []))
            main.set_wallpaper(path)
            self.status_var.set("바탕화면 실시간 반영됨 ✓ (네트워크 호출 없음)")
        except Exception as e:
            print(f"[실시간 적용 실패] {e}")

    def _initial_auto_sync(self):
        """프로그램 시작 시 오늘 날짜의 시간표와 급식이 캐시되어 있지 않으면 실시간 동기화 수행"""
        try:
            today = main.datetime.now().strftime("%Y%m%d")
            cache = main.load_data_cache()
            if cache.get("date") != today or not cache.get("timetable"):
                timetable = main.fetch_timetable(self.cfg, today)
                meals = main.fetch_meal(self.cfg, today)
                main.save_data_cache(timetable, meals, today)
                now = main.datetime.now()
                path = main.render_wallpaper(self.cfg, now, timetable, meals)
                main.set_wallpaper(path)
                main.ensure_autostart()
                self.after(0, lambda: self.status_var.set("오늘 날짜 시간표/급식 동기화 완료 ✓"))
        except Exception as e:
            print(f"[초기 동기화 오류] {e}")
        finally:
            self.after(60000, self._periodic_check)

    def _periodic_check(self):
        """설정 창이 켜져 있는 동안 매 1분마다 자정 지남(날짜 변경) 여부를 확인하여 자동 갱신"""
        try:
            today = main.datetime.now().strftime("%Y%m%d")
            cache = main.load_data_cache()
            if cache.get("date") != today:
                threading.Thread(target=self._initial_auto_sync, daemon=True).start()
                return
        except Exception:
            pass
        self.after(60000, self._periodic_check)

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
        for s in ["school_info", "timetable", "meals", "date_info"]:
            sec_def = DEFAULT_SECTIONS[s]
            for key, val in sec_def.items():
                if key in self.sec_vars[s]:
                    self.sec_vars[s][key].set(val)
                if key in self.label_vars[s]:
                    self.label_vars[s][key].set(str(val))
                    
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

    def on_search(self):
        name = self.school_name_var.get().strip()
        if not name:
            messagebox.showwarning("입력 필요", "학교 이름을 입력해주세요.")
            return
        try:
            self.search_results = search_school(name)
        except Exception as e:
            messagebox.showerror("검색 실패", str(e))
            return
        self.result_list.delete(0, tk.END)
        if not self.search_results:
            self.result_list.insert(tk.END, "(검색 결과 없음)")
            return
        for r in self.search_results:
            self.result_list.insert(tk.END, f"{r['SCHUL_NM']}  [{r['SCHUL_KND_SC_NM']}]  {r['ORG_RDNMA']}")

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
        api_key = main.API_KEY
        grade = self.grade_var.get().strip()
        classnm = self.class_var.get().strip()

        if not grade or not classnm:
            messagebox.showwarning("입력 필요", "학년, 반을 모두 입력해주세요.")
            return

        selection = self.result_list.curselection()
        if selection and self.search_results:
            self.selected_school = self.search_results[selection[0]]

        if self.selected_school:
            school = self.selected_school
            kind = school["SCHUL_KND_SC_NM"]
            if kind not in SCHOOL_LEVEL_ENDPOINT:
                kind = "중학교"
            new_cfg = {
                "api_key": api_key,
                "edu_code": school["ATPT_OFCDC_SC_CODE"],
                "school_code": school["SD_SCHUL_CODE"],
                "school_name": school["SCHUL_NM"],
                "school_kind": kind,
                "grade": grade,
                "classnm": classnm,
            }
        elif all(k in self.cfg for k in ("edu_code", "school_code", "school_name", "school_kind")):
            new_cfg = dict(self.cfg)
            new_cfg.update({"api_key": api_key, "grade": grade, "classnm": classnm})
        else:
            messagebox.showwarning("학교 선택 필요", "검색 후 목록에서 학교를 선택해주세요.")
            return

        new_cfg["bg_photo_mode"] = self.photo_mode.get()
        new_cfg["bg_photo_idx"] = self.preset_combo.current()
        new_cfg["custom_background"] = self.photo_path.get().strip() or None
        
        new_cfg["font_colors"] = {k: v.get() for k, v in self.colors_vars.items()}
        
        # 섹션 설정 수집
        sections_data = {}
        for s in ["school_info", "timetable", "meals", "date_info"]:
            sections_data[s] = {
                "show": self.sec_vars[s]["show"].get(),
                "x": self.sec_vars[s]["x"].get(),
                "y": self.sec_vars[s]["y"].get(),
                "font_size": self.sec_vars[s]["font_size"].get(),
                "stroke_width": self.sec_vars[s]["stroke_width"].get(),
                "shadow_blur": self.sec_vars[s]["shadow_blur"].get(),
                "shadow_opacity": self.sec_vars[s]["shadow_opacity"].get()
            }
        new_cfg["sections"] = sections_data
        
        # 하위 호환성을 위한 전역 text_effects 유지
        new_cfg["text_effects"] = {
            "stroke_width": self.sec_vars["school_info"]["stroke_width"].get(),
            "stroke_color": "#000000",
            "shadow_blur": self.sec_vars["school_info"]["shadow_blur"].get(),
            "shadow_opacity": self.sec_vars["school_info"]["shadow_opacity"].get()
        }

        self.cfg = new_cfg
        save_config(new_cfg)

        self.apply_btn.config(state="disabled")
        self.status_var.set("적용 중... (사진/시간표/급식을 가져오는 중)")
        threading.Thread(target=self._run_engine, daemon=True).start()

    def _run_engine(self):
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            
            # Use sys.executable (which is this .exe if bundled) with --background flag
            cmd = [sys.executable, "--background"]
            if not getattr(sys, "frozen", False):
                cmd = [sys.executable, "app.py", "--background"]
                
            result = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=creationflags)
            ok = result.returncode == 0
        except Exception as e:
            ok = False
            print(e)
        self.after(0, self._on_engine_done, ok)

    def _on_engine_done(self, ok):
        self.apply_btn.config(state="normal")
        if ok:
            self.status_var.set("바탕화면에 적용되었습니다 ✓")
        else:
            self.status_var.set("적용 실패 — 설정을 다시 확인해주세요")
            messagebox.showerror("적용 실패", "바탕화면 적용 중 오류가 발생했습니다. 입력값을 확인해주세요.")


if __name__ == "__main__":
    App().mainloop()
