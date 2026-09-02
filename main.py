# -*- coding: utf-8 -*-
"""
학교 시간표 + 급식 자동 바탕화면 동기화 프로그램

동작 방식:
1. 처음 실행 시 학교 이름/학년/반을 입력받아 config.json에 저장 (이후 재실행 시 재사용)
2. 실행될 때마다 NEIS Open API에서 오늘의 시간표/급식/학사일정을 가져와 바탕화면 이미지를
   새로 그리고 적용한 뒤 종료 (캐시로 건너뛰지 않고 매번 새로 조회함)
3. 주기적인 자동 갱신은 ensure_autostart()가 스스로 등록하는 Windows 작업 스케줄러가
   이 프로그램을 `--background` 인자로 15분마다 실행시키는 방식으로 처리한다 (배터리
   전원이어도 멈추지 않도록, 절전에서 깨어나면 바로 따라잡도록 설정을 강제한다).
   사용자가 수동으로 작업 스케줄러를 등록할 필요는 없다.
   `--reset` 인자로 실행하면 저장된 학교/반 설정을 지우고 다시 설정할 수 있음
"""

import ctypes
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ROOT_DIR 및 BASE_DIR 경로 통일
if getattr(sys, "frozen", False):
    ROOT_DIR = os.path.dirname(sys.executable)
    BASE_DIR = ROOT_DIR
    MEIPASS_DIR = sys._MEIPASS
else:
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = ROOT_DIR
    MEIPASS_DIR = ROOT_DIR

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_CACHE_PATH = os.path.join(BASE_DIR, "data_cache.json")
WALLPAPER_PATH = os.path.join(BASE_DIR, "wallpaper.png")

NEIS_BASE = "https://open.neis.go.kr/hub"

SCHOOL_LEVEL_ENDPOINT = {
    "초등학교": "elsTimetable",
    "중학교": "misTimetable",
    "고등학교": "hisTimetable",
}

FONT_DIR = os.path.join(MEIPASS_DIR, "fonts")

# KOPUBWORLD_OTF_FONTS2026 최우선 폰트 탐색 로직 개선
possible_paths = [
    os.path.join(MEIPASS_DIR, "KOPUBWORLD_OTF_FONTS2026"),
    os.path.join(ROOT_DIR, "KOPUBWORLD_OTF_FONTS2026"),
    FONT_DIR,
]

KOPUB_BOLD = None
KOPUB_MEDIUM = None
KOPUB_LIGHT = None

for p in possible_paths:
    b_path = os.path.join(p, "KoPubWorld Dotum_Pro Bold.otf")
    m_path = os.path.join(p, "KoPubWorld Dotum_Pro Medium.otf")
    l_path = os.path.join(p, "KoPubWorld Dotum_Pro Light.otf")
    if os.path.exists(b_path) and os.path.exists(m_path) and os.path.exists(l_path):
        KOPUB_BOLD = b_path
        KOPUB_MEDIUM = m_path
        KOPUB_LIGHT = l_path
        break

if KOPUB_BOLD:
    FONT_BOLD = KOPUB_BOLD
    FONT_SEMIBOLD = KOPUB_BOLD
    FONT_EXTRABOLD = KOPUB_BOLD
else:
    FONT_BOLD = os.path.join(FONT_DIR, "Pretendard-Bold.otf")
    FONT_SEMIBOLD = os.path.join(FONT_DIR, "Pretendard-SemiBold.otf")
    FONT_EXTRABOLD = os.path.join(FONT_DIR, "Pretendard-ExtraBold.otf")

if KOPUB_MEDIUM:
    FONT_MEDIUM = KOPUB_MEDIUM
    FONT_REGULAR = KOPUB_MEDIUM
else:
    FONT_MEDIUM = os.path.join(FONT_DIR, "Pretendard-Medium.otf")
    FONT_REGULAR = os.path.join(FONT_DIR, "Pretendard-Regular.otf")

if KOPUB_LIGHT:
    FONT_LIGHT = KOPUB_LIGHT
    FONT_THIN = KOPUB_LIGHT
else:
    FONT_LIGHT = os.path.join(FONT_DIR, "Pretendard-Light.otf")
    FONT_THIN = os.path.join(FONT_DIR, "Pretendard-Thin.otf")

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# 디자인/색상 기본값 및 유틸리티
# ---------------------------------------------------------------------------

DEFAULT_COLORS = {
    "school_name": "#FFFFFF",
    "school_name_muted": "#ffffff",
    "timetable_title": "#80ffff",
    "timetable_period": "#fff9bf",
    "timetable_subject": "#FFFFFF",
    "meals_title": "#FF968C",
    "meals_type": "#FF968C",
    "meals_dish": "#FFFFFF",
    "date_weekday": "#CDC3FF",
    "date_calendar": "#EBECF1",
    "dday_label": "#FFD166",
    "dday_name": "#FFFFFF"
}

DEFAULT_TEXT_EFFECTS = {
    "stroke_width": 0,
    "stroke_color": "#000000",
    "shadow_blur": 5,
    "shadow_opacity": 100
}

DEFAULT_SECTIONS = {
    "school_info": {
        "x": 50,
        "y": 7,
        "show": True,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 135
    },
    "timetable": {
        "x": 50,
        "y": 15,
        "show": True,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 137
    },
    "meals": {
        "x": 50,
        "y": 55,
        "show": True,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 135
    },
    "date_info": {
        "x": 50,
        "y": 49,
        "show": False,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 157
    },
    "dday": {
        "x": 98,
        "y": 90,
        "show": True,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 100
    }
}

API_KEY = "75f40bb14ddd41d1b5ecda3389258cb1"

DEFAULT_CONFIG = {
    "api_key": API_KEY,
    "edu_code": "",
    "school_code": "",
    "school_name": "",
    "school_kind": "",
    "grade": "",
    "classnm": "",
    "custom_background": None,
    "bg_photo_mode": "auto",
    "bg_photo_idx": 0,
    "dday_count": 3,
    "manual_dday": [],  # [{"name": "중간고사", "date": "20261020", "color": "#FF6B6B" 또는 None}, ...]
    "font_colors": DEFAULT_COLORS,
    "text_effects": DEFAULT_TEXT_EFFECTS,
    "sections": DEFAULT_SECTIONS
}

def merge_defaults(cfg):
    if not cfg:
        cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if sub_k not in cfg[k]:
                    cfg[k][sub_k] = sub_v
                elif isinstance(sub_v, dict) and isinstance(cfg[k][sub_k], dict):
                    # Deep merge sections properties
                    for s_k, s_v in sub_v.items():
                        if s_k not in cfg[k][sub_k]:
                            cfg[k][sub_k][s_k] = s_v
    return cfg

def hex_to_rgba(hex_str, alpha=255):
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 6:
        r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
        return (r, g, b, alpha)
    elif len(hex_str) == 8:
        r, g, b, a = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16), int(hex_str[6:8], 16)
        return (r, g, b, a)
    return (255, 255, 255, alpha)

def save_data_cache(timetable, meals, ymd=None, cfg=None, schedule=None):
    os.makedirs(os.path.dirname(DATA_CACHE_PATH), exist_ok=True)
    if ymd is None:
        ymd = datetime.now().strftime("%Y%m%d")
    cache = {
        "date": ymd,
        "timetable": timetable,
        "meals": meals,
        "schedule": schedule,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # 어느 학교/학년/반의 데이터인지 함께 기록해서, 학교를 바꿨는데도
        # "오늘 날짜"라는 이유만으로 이전 학교의 캐시를 재사용하는 것을 방지한다
        "school_code": (cfg or {}).get("school_code"),
        "grade": (cfg or {}).get("grade"),
        "classnm": (cfg or {}).get("classnm"),
    }
    with open(DATA_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def load_data_cache(cfg=None):
    today = datetime.now().strftime("%Y%m%d")
    if os.path.exists(DATA_CACHE_PATH):
        try:
            with open(DATA_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            same_school = True
            if cfg and cfg.get("school_code"):
                same_school = (
                    cache.get("school_code") == cfg.get("school_code")
                    and cache.get("grade") == cfg.get("grade")
                    and cache.get("classnm") == cfg.get("classnm")
                )
            # 오늘 날짜 + 같은 학교/학년/반의 캐시 데이터일 때만 재사용한다
            if cache.get("date") == today and same_school and (cache.get("timetable") or cache.get("meals")):
                return cache
        except Exception:
            pass

    # 오늘 날짜가 아니거나, 학교가 바뀌었거나, 캐시가 없는데 cfg가 주어졌다면
    # 실시간으로 오늘 데이터 조회 후 캐싱
    if cfg and cfg.get("school_code"):
        try:
            timetable = fetch_timetable(cfg, today)
            meals = fetch_meal(cfg, today)
            schedule = fetch_school_schedule(cfg, datetime.now())
            save_data_cache(timetable, meals, today, cfg, schedule)
            return {"date": today, "timetable": timetable, "meals": meals, "schedule": schedule,
                    "school_code": cfg.get("school_code"), "grade": cfg.get("grade"), "classnm": cfg.get("classnm")}
        except Exception as e:
            print(f"[오늘 데이터 자동 조회 실패] {e}")

    return {"date": today, "timetable": [], "meals": [], "schedule": None}


def ensure_autostart():
    """윈도우 부팅 및 주기적 백그라운드 자동 갱신 등록 (노트북을 계속 켜두어도 자정/교시 변경 시 자동 반영)"""
    try:
        # 1. 윈도우 시작 시 자동 실행 레지스트리 등록
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        if getattr(sys, "frozen", False):
            exe_target = sys.executable
            exe_cmd = f'"{sys.executable}" --background'
        else:
            exe_target = sys.executable
            exe_cmd = f'"{sys.executable}" "{os.path.abspath("app.py")}" --background'
            
        winreg.SetValueEx(key, "SchoolWallpaperSync", 0, winreg.REG_SZ, exe_cmd)
        winreg.CloseKey(key)
        
        # 2. 윈도우 작업 스케줄러(Task Scheduler)에 15분 주기 자동 갱신 등록
        # (노트북이 계속 켜져 있거나 화면이 켜졌을 때 자정 지난 시간표/급식을 자동 갱신)
        if getattr(sys, "frozen", False):
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            task_cmd = f'schtasks /create /tn "SchoolWallpaperSync" /tr "\\"{exe_target}\\" --background" /sc MINUTE /mo 15 /f'
            subprocess.run(task_cmd, shell=True, capture_output=True, creationflags=creationflags)

            # schtasks /create의 기본값은 "배터리로 동작 중이면 시작하지 않음 /
            # 배터리로 전환되면 중단함" + "놓친 실행은 따라잡지 않음"이라, 노트북이
            # 충전 중이 아니거나 절전 후 깨어난 직후에는 자동 갱신이 조용히
            # 멈춰버린다. 매번 재등록될 때마다 PowerShell로 이 설정들을 꺼서
            # 전원 상태와 무관하게 항상 15분마다 갱신되도록 강제한다.
            ps_cmd = (
                "$t = Get-ScheduledTask -TaskName 'SchoolWallpaperSync' -ErrorAction SilentlyContinue; "
                "if ($t) { "
                "$t.Settings.DisallowStartIfOnBatteries = $false; "
                "$t.Settings.StopIfGoingOnBatteries = $false; "
                "$t.Settings.StartWhenAvailable = $true; "
                "Set-ScheduledTask -InputObject $t | Out-Null }"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, creationflags=creationflags,
            )
    except Exception as e:
        print(f"[자동 시작 등록 실패] {e}")



# ---------------------------------------------------------------------------
# 설정 (학교/반 정보)
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                cfg = json.load(f)
                return merge_defaults(cfg)
            except Exception:
                return merge_defaults({})
    return None


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def neis_get(endpoint, params):
    params["KEY"] = API_KEY
    resp = requests.get(f"{NEIS_BASE}/{endpoint}", params=params, timeout=10)
    return json.loads(resp.content.decode("utf-8"))


def search_school(api_key, name):
    data = neis_get(
        "schoolInfo",
        {"KEY": api_key, "Type": "json", "SCHUL_NM": name, "pSize": 20},
    )
    if "schoolInfo" not in data:
        return []
    rows = data["schoolInfo"][1]["row"]
    return rows


def setup_wizard():
    print("=== 학교 시간표/급식 바탕화면 초기 설정 ===")
    api_key = API_KEY

    while True:
        name = input("학교 이름을 입력하세요 (예: 서울고등학교): ").strip()
        try:
            results = search_school(api_key, name)
        except Exception as e:
            print(f"검색 중 오류가 발생했습니다: {e}")
            continue

        if not results:
            print("검색 결과가 없습니다. 다시 입력해주세요.\n")
            continue

        print("\n검색 결과:")
        for i, r in enumerate(results):
            print(f"  [{i}] {r['SCHUL_NM']} ({r['SCHUL_KND_SC_NM']}, {r['ORG_RDNMA']})")

        choice = input("해당하는 학교 번호를 입력하세요: ").strip()
        try:
            school = results[int(choice)]
            break
        except (ValueError, IndexError):
            print("잘못된 선택입니다. 다시 시도해주세요.\n")

    kind = school["SCHUL_KND_SC_NM"]
    if kind not in SCHOOL_LEVEL_ENDPOINT:
        print(f"'{kind}'은(는) 지원되지 않는 학교급입니다. 중학교 기준으로 시도합니다.")
        kind = "중학교"

    grade = input("학년을 입력하세요 (예: 1): ").strip()
    classnm = input("반을 입력하세요 (예: 3): ").strip()

    cfg = {
        "api_key": api_key,
        "edu_code": school["ATPT_OFCDC_SC_CODE"],
        "school_code": school["SD_SCHUL_CODE"],
        "school_name": school["SCHUL_NM"],
        "school_kind": kind,
        "grade": grade,
        "classnm": classnm,
    }
    cfg["custom_background"] = prompt_custom_background()
    cfg["bg_photo_mode"] = "custom" if cfg["custom_background"] else "auto"
    cfg["bg_photo_idx"] = 0
    cfg = merge_defaults(cfg)
    save_config(cfg)
    print(f"\n설정 완료: {cfg['school_name']} {grade}학년 {classnm}반")
    print()
    return cfg


def prompt_custom_background():
    """배경 사진을 직접 지정할지 물어보고, 유효한 경로면 절대경로를 반환한다."""
    print("\n배경 사진을 직접 지정하시겠습니까?")
    print("(비워두고 Enter를 누르면 감성 사진이 매일 자동으로 바뀝니다)")
    path = input("사진 파일 경로 (jpg/png): ").strip().strip('"')
    if not path:
        return None
    if not os.path.exists(path):
        print("해당 경로에 파일이 없습니다. 자동 사진으로 진행합니다.")
        return None
    try:
        Image.open(path).verify()
    except Exception:
        print("이미지 파일로 인식할 수 없습니다. 자동 사진으로 진행합니다.")
        return None
    return os.path.abspath(path)


def set_photo_flow():
    """이미 설정된 프로그램에서 배경 사진만 바꾸고 즉시 미리 적용한다."""
    cfg = load_config()
    if cfg is None:
        print("먼저 프로그램을 한 번 실행해 학교/반을 설정해주세요.")
        return
    custom = prompt_custom_background()
    cfg["custom_background"] = custom
    cfg["bg_photo_mode"] = "custom" if custom else "auto"
    save_config(cfg)
    if custom:
        print(f"사진 설정 완료: {custom}")
    else:
        print("자동 사진 순환으로 되돌렸습니다.")
    update_once(cfg)
    print("바탕화면에 바로 적용했습니다.")


# ---------------------------------------------------------------------------
# NEIS 데이터 조회
# ---------------------------------------------------------------------------

def fetch_meal(cfg, ymd):
    try:
        data = neis_get(
            "mealServiceDietInfo",
            {
                "KEY": cfg["api_key"],
                "Type": "json",
                "ATPT_OFCDC_SC_CODE": cfg["edu_code"],
                "SD_SCHUL_CODE": cfg["school_code"],
                "MLSV_YMD": ymd,
            },
        )
        if "mealServiceDietInfo" not in data:
            return []
        rows = data["mealServiceDietInfo"][1]["row"]
        meals = []
        for r in rows:
            dishes = r["DDISH_NM"].replace("<br/>", "\n")
            # 알레르기 표시 괄호 제거 (예: "김치찌개 (5.6.9)")
            dishes = re.sub(r"\s*\([0-9.]+\)", "", dishes)
            meals.append({"name": r["MMEAL_SC_NM"], "dishes": dishes})
        return meals
    except Exception as e:
        print(f"[급식 조회 실패] {e}")
        return []


def fetch_timetable(cfg, ymd):
    endpoint = SCHOOL_LEVEL_ENDPOINT.get(cfg["school_kind"], "misTimetable")
    try:
        data = neis_get(
            endpoint,
            {
                "KEY": cfg["api_key"],
                "Type": "json",
                "ATPT_OFCDC_SC_CODE": cfg["edu_code"],
                "SD_SCHUL_CODE": cfg["school_code"],
                "GRADE": cfg["grade"],
                "CLASS_NM": cfg["classnm"],
                "ALL_TI_YMD": ymd,
            },
        )
        if endpoint not in data:
            return []
        rows = data[endpoint][1]["row"]
        rows.sort(key=lambda r: int(r["PERIO"]))
        return [{"period": r["PERIO"], "subject": r["ITRT_CNTNT"]} for r in rows]
    except Exception as e:
        print(f"[시간표 조회 실패] {e}")
        return []


GRADE_EVENT_FLAG = {
    "1": "ONE_GRADE_EVENT_YN", "2": "TW_GRADE_EVENT_YN", "3": "THREE_GRADE_EVENT_YN",
    "4": "FR_GRADE_EVENT_YN", "5": "FIV_GRADE_EVENT_YN", "6": "SIX_GRADE_EVENT_YN",
}
# 매주 반복되는 토요/일요 휴업일은 "다가오는 학사일정" 으로서 의미가 없어 제외한다
SCHEDULE_EXCLUDE_NAMES = {"토요휴업일", "일요휴업일"}


def fetch_school_schedule(cfg, today, limit=10):
    """오늘 이후 가까운 학사일정(시험, 방학, 공휴일 등)을 최대 limit개, D-day와 함께
    가까운 순으로 반환한다. NEIS SchoolSchedule API 사용. 등록된 일정이 없으면 빈 리스트."""
    ymd = today.strftime("%Y%m%d")
    to_ymd = (today + timedelta(days=120)).strftime("%Y%m%d")
    try:
        data = neis_get(
            "SchoolSchedule",
            {
                "KEY": cfg["api_key"],
                "Type": "json",
                "ATPT_OFCDC_SC_CODE": cfg["edu_code"],
                "SD_SCHUL_CODE": cfg["school_code"],
                "AA_FROM_YMD": ymd,
                "AA_TO_YMD": to_ymd,
            },
        )
        if "SchoolSchedule" not in data:
            return []
        rows = data["SchoolSchedule"][1]["row"]
    except Exception as e:
        print(f"[학사일정 조회 실패] {e}")
        return []

    grade_flag = GRADE_EVENT_FLAG.get(str(cfg.get("grade")))
    candidates = [
        r for r in rows
        if r.get("EVENT_NM") not in SCHEDULE_EXCLUDE_NAMES
        and (not grade_flag or r.get(grade_flag) in ("Y", "*"))
    ]
    if not candidates:
        return []

    candidates.sort(key=lambda r: r["AA_YMD"])

    # 추석/설날처럼 같은 이름의 일정이 연속된 날짜로 여러 줄 등록된 경우, 하나의
    # 일정으로 묶어서 그 시작일(가장 가까운 D-day) 기준 한 건으로만 취급한다
    merged = []
    last_name, last_date = None, None
    for r in candidates:
        d = datetime.strptime(r["AA_YMD"], "%Y%m%d").date()
        if merged and r["EVENT_NM"] == last_name and (d - last_date).days == 1:
            last_date = d
            continue
        merged.append(r)
        last_name, last_date = r["EVENT_NM"], d

    events = []
    for r in merged[:limit]:
        event_date = datetime.strptime(r["AA_YMD"], "%Y%m%d").date()
        events.append({
            "name": r["EVENT_NM"],
            "date": r["AA_YMD"],
            "dday": (event_date - today.date()).days,
        })
    return events


# ---------------------------------------------------------------------------
# 바탕화면 이미지 렌더링
# ---------------------------------------------------------------------------

def get_screen_size():
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


BG_CACHE_DIR = os.path.join(BASE_DIR, "bg_cache")

# 감성 있는 배경 사진 큐레이션 (Unsplash CDN, 날짜별로 하나씩 순환)
BACKGROUND_PHOTO_IDS = [
    "1506905925346-21bda4d32df4",  # 구름바다 위 산 일출
    "1470071459604-3b5ec3a7fe05",  # 안개 낀 초록 절벽
    "1500534623283-312aade485b7",  # 실루엣 산 너머 일몰
    "1519681393784-d120267933ba",  # 별이 쏟아지는 밤하늘 설산
    "1507525428034-b723cf961d3e",  # 파스텔톤 해변 일출
    "1495567720989-cebdbdd97913",  # 붉은 노을
    "1518837695005-2083093ee35b",  # 잔잔한 바다 수면
    "1483347756197-71ef80e95f73",  # 오로라와 침엽수림
]


def text_height(fnt):
    bbox = fnt.getbbox("가나다Ag0")
    return bbox[3] - bbox[1]


def text_size(draw, text, fnt):
    bbox = draw.textbbox((0, 0), text, font=fnt)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def fit_cover(img, W, H):
    """종횡비를 유지한 채 (W, H)를 꽉 채우도록 리사이즈 후 중앙을 잘라낸다."""
    src_ratio = img.width / img.height
    dst_ratio = W / H
    if src_ratio > dst_ratio:
        new_h = H
        new_w = int(H * src_ratio)
    else:
        new_w = W
        new_h = int(W / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - W) // 2
    top = (new_h - H) // 2
    return img.crop((left, top, left + W, top + H))


def fetch_background(cfg, now, W, H):
    """배경 사진을 가져온다: 설정된 모드에 따라 지정된 사진 또는 프리셋 사진을 반환하며 로컬에 캐시한다."""
    bg_mode = cfg.get("bg_photo_mode", "auto")
    
    if bg_mode == "custom":
        custom = cfg.get("custom_background")
        if custom and os.path.exists(custom):
            try:
                return fit_cover(Image.open(custom).convert("RGB"), W, H)
            except Exception as e:
                print(f"[사용자 지정 사진 로드 실패] {e}")
                
    os.makedirs(BG_CACHE_DIR, exist_ok=True)
    
    if bg_mode == "preset":
        idx = cfg.get("bg_photo_idx", 0)
        if not isinstance(idx, int) or idx < 0 or idx >= len(BACKGROUND_PHOTO_IDS):
            idx = 0
        photo_id = BACKGROUND_PHOTO_IDS[idx]
        cache_path = os.path.join(BG_CACHE_DIR, f"bg_preset_{idx}_{W}x{H}.jpg")
    else: # auto
        idx = now.timetuple().tm_yday % len(BACKGROUND_PHOTO_IDS)
        photo_id = BACKGROUND_PHOTO_IDS[idx]
        cache_path = os.path.join(BG_CACHE_DIR, f"bg_preset_{idx}_{W}x{H}.jpg")
        
    if not os.path.exists(cache_path):
        try:
            url = f"https://images.unsplash.com/photo-{photo_id}?w={W}&h={H}&fit=crop&q=80"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            with open(cache_path, "wb") as f:
                f.write(resp.content)
        except Exception as e:
            print(f"[배경 사진 다운로드 실패] {e}")
            
    if os.path.exists(cache_path):
        try:
            return fit_cover(Image.open(cache_path).convert("RGB"), W, H)
        except Exception:
            pass
            
    # 캐시된 파일 중 아무 파일이나 찾아 재사용 (오프라인 대비)
    cached = sorted(Path(BG_CACHE_DIR).glob("bg_preset_*.jpg"))
    if cached:
        try:
            return fit_cover(Image.open(cached[-1]).convert("RGB"), W, H)
        except Exception:
            pass
            
    return Image.new("RGB", (W, H), (30, 34, 46))


def spaced(text):
    """스타일리시한 캡스 라벨을 위해 자간을 살짝 벌린다 (예: TIMETABLE -> T I M E T A B L E)."""
    return " ".join(list(text))


def render_wallpaper(cfg, now, timetable, meals, schedule=None):
    W, H = get_screen_size()

    bg = fetch_background(cfg, now, W, H)
    canvas = bg.convert("RGBA")
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow_layer)
    text_ops = []

    # 설정 및 효과 병합
    colors_cfg = cfg.get("font_colors", DEFAULT_COLORS)
    colors = {k: hex_to_rgba(v) for k, v in colors_cfg.items()}
    sections = cfg.get("sections", DEFAULT_SECTIONS)

    # 텍스트 효과 포함 그리기 헬퍼 정의
    def draw_text_with_effects(text, x, y, fnt, fill, section_cfg, align="center"):
        stroke_w = int(section_cfg.get("stroke_width", 2))
        stroke_color = (0, 0, 0, 180) # 반투명 외곽선
        shadow_b = float(section_cfg.get("shadow_blur", 3))
        shadow_op = int(section_cfg.get("shadow_opacity", 200))
        
        w, _ = text_size(sdraw, text, fnt)
        if align == "center":
            x_pos = x - w / 2
        elif align == "right":
            x_pos = x - w
        else: # left
            x_pos = x
            
        if shadow_op > 0:
            sdraw.text((x_pos, y), text, font=fnt, fill=(0, 0, 0, shadow_op), 
                       stroke_width=stroke_w, stroke_fill=(0, 0, 0, shadow_op))
        text_ops.append(((x_pos, y), text, fnt, fill, stroke_w, stroke_color))
        return w

    def draw_two_tone_with_effects(part1, font1, color1, part2, font2, color2, x, y, section_cfg, gap=10, align="center"):
        w1, _ = text_size(sdraw, part1, font1)
        w2, _ = text_size(sdraw, part2, font2)
        total = w1 + gap + w2
        if align == "right":
            x_start = x - total
        elif align == "left":
            x_start = x
        else:
            x_start = x - total / 2
        # 두 폰트 크기가 다르면 위쪽 기준으로 그릴 때 글자가 어긋나 보이므로
        # ascent(글자 위쪽 여백) 차이만큼 보정해서 베이스라인을 맞춘다
        ascent1 = font1.getmetrics()[0]
        ascent2 = font2.getmetrics()[0]
        max_ascent = max(ascent1, ascent2)
        draw_text_with_effects(part1, x_start, y + (max_ascent - ascent1), font1, color1, section_cfg, align="left")
        draw_text_with_effects(part2, x_start + w1 + gap, y + (max_ascent - ascent2), font2, color2, section_cfg, align="left")

    def draw_multi_tone_with_effects(parts, x, y, section_cfg, align="right"):
        """parts: [(text, font, color, gap_after_px), ...] 를 한 줄로 이어서 그린다.
        여러 D-day 항목을 'D-22 추석   D-45 중간고사'처럼 한 줄에 나란히 배치할 때 쓴다."""
        widths = [text_size(sdraw, t, f)[0] for t, f, _, _ in parts]
        total = sum(widths) + sum(g for _, _, _, g in parts[:-1])
        if align == "right":
            x_start = x - total
        elif align == "left":
            x_start = x
        else:
            x_start = x - total / 2
        max_ascent = max(f.getmetrics()[0] for _, f, _, _ in parts)
        cursor = x_start
        for (text, fnt, color, gap_after), w in zip(parts, widths):
            ascent = fnt.getmetrics()[0]
            draw_text_with_effects(text, cursor, y + (max_ascent - ascent), fnt, color, section_cfg, align="left")
            cursor += w + gap_after

    # 개선된 폰트 크기 및 두께 정의 (KoPubWorld 폰트 최적화 + 개별 font_size 배율 반영)
    s_info = sections.get("school_info", DEFAULT_SECTIONS["school_info"])
    s_tt = sections.get("timetable", DEFAULT_SECTIONS["timetable"])
    s_meals = sections.get("meals", DEFAULT_SECTIONS["meals"])
    s_date = sections.get("date_info", DEFAULT_SECTIONS["date_info"])
    s_dday = sections.get("dday", DEFAULT_SECTIONS["dday"])

    info_scale = float(s_info.get("font_size", 100)) / 100.0
    tt_scale = float(s_tt.get("font_size", 100)) / 100.0
    meals_scale = float(s_meals.get("font_size", 100)) / 100.0
    date_scale = float(s_date.get("font_size", 100)) / 100.0
    dday_scale = float(s_dday.get("font_size", 100)) / 100.0

    school_name_font = font(FONT_BOLD, int(H * 0.024 * info_scale))
    class_font = font(FONT_REGULAR, int(H * 0.016 * info_scale))
    
    label_font = font(FONT_SEMIBOLD, int(H * 0.0145 * tt_scale))
    period_font = font(FONT_SEMIBOLD, int(H * 0.020 * tt_scale))
    subject_font = font(FONT_MEDIUM, int(H * 0.020 * tt_scale))
    
    meal_label_font = font(FONT_SEMIBOLD, int(H * 0.0145 * meals_scale))
    meal_head_font = font(FONT_SEMIBOLD, int(H * 0.018 * meals_scale))
    dish_font = font(FONT_REGULAR, int(H * 0.016 * meals_scale))
    
    date_weekday_font = font(FONT_BOLD, int(H * 0.022 * date_scale))
    date_calendar_font = font(FONT_REGULAR, int(H * 0.018 * date_scale))

    dday_label_font = font(FONT_BOLD, int(H * 0.024 * dday_scale))
    dday_name_font = font(FONT_REGULAR, int(H * 0.018 * dday_scale))

    tt_row_gap = int(H * 0.035 * tt_scale)
    meal_line_gap = int(H * 0.026 * meals_scale)

    # ---- 1. 학교 헤더 섹션 ----
    if s_info.get("show", True):
        sx = int(W * s_info.get("x", 50) / 100)
        sy = int(H * s_info.get("y", 15) / 100)
        draw_text_with_effects(cfg["school_name"], sx, sy, school_name_font, colors["school_name"], s_info)
        sy += int(H * 0.024 * info_scale) + int(H * 0.008 * info_scale)
        draw_text_with_effects(f"{cfg['grade']}학년 {cfg['classnm']}반", sx, sy, class_font, colors["school_name_muted"], s_info)

    # ---- 2. 시간표 섹션 ----
    if s_tt.get("show", True):
        tx = int(W * s_tt.get("x", 50) / 100)
        ty = int(H * s_tt.get("y", 26) / 100)
        draw_text_with_effects(spaced("TIMETABLE"), tx, ty, label_font, colors["timetable_title"], s_tt)
        ty += int(H * 0.0145 * tt_scale) + int(H * 0.020 * tt_scale)
        
        timetable_rows = timetable if timetable else [{"period": "-", "subject": "시간표 없음"}]
        for t in timetable_rows:
            if t["period"] == "-":
                draw_text_with_effects(t["subject"], tx, ty, subject_font, colors["timetable_subject"], s_tt)
            else:
                draw_two_tone_with_effects(f"{t['period']}교시", period_font, colors["timetable_period"],
                                           t["subject"], subject_font, colors["timetable_subject"], 
                                           tx, ty, s_tt)
            ty += tt_row_gap

    # ---- 3. 급식 섹션 ----
    if s_meals.get("show", True):
        mx = int(W * s_meals.get("x", 50) / 100)
        my = int(H * s_meals.get("y", 62) / 100)
        draw_text_with_effects(spaced("MEALS"), mx, my, meal_label_font, colors["meals_title"], s_meals)
        my += int(H * 0.0145 * meals_scale) + int(H * 0.020 * meals_scale)
        
        slot_order = ["조식", "중식", "석식"]
        active_meals = [m for m in meals if m["name"] in slot_order]
        active_meals.sort(key=lambda m: slot_order.index(m["name"]))
        
        if active_meals:
            n_meals = len(active_meals)
            if n_meals == 3:
                col_w = int(W * 0.16)
            elif n_meals == 2:
                col_w = int(W * 0.20)
            else:
                col_w = int(W * 0.25)
            total_w = col_w * n_meals
            start_x = mx - total_w / 2

            for i, m in enumerate(active_meals):
                col_cx = start_x + col_w * i + col_w / 2
                draw_text_with_effects(m["name"], col_cx, my, meal_head_font, colors["meals_type"], s_meals)
                dy = my + int(H * 0.020 * meals_scale) + int(H * 0.010 * meals_scale)
                for line in m["dishes"].split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    draw_text_with_effects(line, col_cx, dy, dish_font, colors["meals_dish"], s_meals)
                    dy += meal_line_gap
        else:
            draw_text_with_effects("등록된 급식 정보가 없습니다", mx, my, dish_font, colors["meals_dish"], s_meals)

    # ---- 4. 날짜 정보 섹션 ----
    if s_date.get("show", True):
        dx = int(W * s_date.get("x", 50) / 100)
        dy = int(H * s_date.get("y", 88) / 100)
        weekday_str = WEEKDAY_EN[now.weekday()].upper()
        date_str = now.strftime("%Y-%m-%d")
        draw_two_tone_with_effects(weekday_str, date_weekday_font, colors["date_weekday"],
                                   date_str, date_calendar_font, colors["date_calendar"],
                                   dx, dy, s_date, gap=int(16 * date_scale))

    # ---- 5. 학사일정 D-Day 섹션 (NEIS 자동 조회 + 수동 입력을 합쳐 가까운 순으로 정렬,
    #         최대 dday_count개, 한 줄에 오른쪽 정렬) ----
    # 수동으로 추가한 일정은 사용자가 일부러 채워 넣은 것(주로 NEIS에 없는 시험)이므로,
    # 표시 개수 제한 안에서 더 가까운 자동 조회 일정에 밀려 안 보이는 일이 없도록
    # 항상 우선 확보하고, 남는 자리만 자동 조회 일정으로 채운다.
    auto_events = list(schedule) if isinstance(schedule, list) else ([schedule] if schedule else [])
    manual_events = []
    for m in cfg.get("manual_dday", []) or []:
        try:
            m_date = datetime.strptime(m["date"], "%Y%m%d").date()
            dday_n = (m_date - now.date()).days
            if dday_n < 0:
                continue  # 이미 지난 수동 일정은 표시하지 않는다
            manual_events.append({
                "name": m.get("name", ""),
                "date": m["date"],
                "dday": dday_n,
                "color": m.get("color") or None,
            })
        except Exception:
            continue
    manual_events.sort(key=lambda e: e["dday"])

    dday_count = max(1, min(10, int(cfg.get("dday_count", 3))))
    if len(manual_events) >= dday_count:
        dday_events = manual_events[:dday_count]
    else:
        dday_events = manual_events + auto_events[:dday_count - len(manual_events)]
    dday_events.sort(key=lambda e: e.get("dday", 9999))

    if s_dday.get("show", True) and dday_events:
        ddx = int(W * s_dday.get("x", 90) / 100)
        ddy = int(H * s_dday.get("y", 90) / 100)
        inner_gap = int(14 * dday_scale)   # "D-22" <-> "추석" 사이
        event_gap = int(30 * dday_scale)   # 서로 다른 일정끼리의 간격
        events = dday_events[:dday_count]
        parts = []
        for i, ev in enumerate(events):
            dday_n = ev.get("dday", 0)
            label = "D-DAY" if dday_n == 0 else f"D-{dday_n}"
            is_last = i == len(events) - 1
            label_color = hex_to_rgba(ev["color"]) if ev.get("color") else colors["dday_label"]
            parts.append((label, dday_label_font, label_color, inner_gap))
            parts.append((ev.get("name", ""), dday_name_font, colors["dday_name"], 0 if is_last else event_gap))
        draw_multi_tone_with_effects(parts, ddx, ddy, s_dday, align="right")

    # 3단계: 그림자 블러 레이어 생성 및 합성 (활성화된 섹션 중 최대 블러 반경 기준)
    max_shadow_b = 0.0
    for s_name, s_cfg in sections.items():
        if s_cfg.get("show", True):
            max_shadow_b = max(max_shadow_b, float(s_cfg.get("shadow_blur", 3)))
            
    if max_shadow_b > 0:
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(max_shadow_b))
        
    canvas.alpha_composite(shadow_layer)
    draw = ImageDraw.Draw(canvas)
    
    # 각 글자 그리기 작업 실행
    for xy, text, fnt, fill, stroke_w, stroke_c in text_ops:
        draw.text(xy, text, font=fnt, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_c)

    img = canvas.convert("RGB")
    img.save(WALLPAPER_PATH, "PNG")
    return WALLPAPER_PATH


def set_wallpaper(path):
    # Toggling between wallpaper_1.png and wallpaper_2.png to force Windows to refresh the background
    dir_name = os.path.dirname(path)
    wp1 = os.path.abspath(os.path.join(dir_name, "wallpaper_1.png"))
    wp2 = os.path.abspath(os.path.join(dir_name, "wallpaper_2.png"))
    
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
        current_wp, _ = winreg.QueryValueEx(key, "Wallpaper")
        winreg.CloseKey(key)
        current_wp = os.path.abspath(current_wp)
    except Exception:
        current_wp = ""
        
    if current_wp.lower() == wp1.lower():
        target_path = wp2
    else:
        target_path = wp1
        
    import shutil
    try:
        shutil.copy2(path, target_path)
    except Exception as e:
        print(f"[오류] 바탕화면 파일 복사 실패: {e}")
        target_path = path
        
    SPI_SETDESKWALLPAPER = 20
    SPIF_UPDATEINIFILE = 0x01
    SPIF_SENDCHANGE = 0x02
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, target_path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    )


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

def update_once(cfg):
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    meals = fetch_meal(cfg, ymd)
    timetable = fetch_timetable(cfg, ymd)
    schedule = fetch_school_schedule(cfg, now)
    save_data_cache(timetable, meals, ymd, cfg, schedule) # 로컬 캐시 갱신
    path = render_wallpaper(cfg, now, timetable, meals, schedule)
    set_wallpaper(path)
    ensure_autostart() # 윈도우 시작 시 자동 실행 등록
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 바탕화면 갱신 완료 "
          f"(시간표 {len(timetable)}건, 급식 {len(meals)}건, 학사일정 {len(schedule)}건)")


def main():
    if "--set-photo" in sys.argv:
        set_photo_flow()
        return

    if "--reset" in sys.argv and os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)

    cfg = load_config()
    if cfg is None:
        cfg = setup_wizard()

    try:
        update_once(cfg)
    except Exception as e:
        print(f"[오류] 갱신 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
