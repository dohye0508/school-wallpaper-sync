# -*- coding: utf-8 -*-
"""
학교 시간표 + 급식 자동 바탕화면 동기화 프로그램

동작 방식:
1. 처음 실행 시 학교 이름/학년/반을 입력받아 config.json에 저장 (이후 재실행 시 재사용)
2. 실행될 때마다 NEIS Open API에서 오늘의 시간표/급식을 가져와 바탕화면 이미지를 새로 그리고 적용한 뒤 종료
3. 주기적인 자동 갱신은 Windows 작업 스케줄러가 이 프로그램을 5분마다 실행시키는 방식으로 처리
   (`schtasks.bat` 참고). `--reset` 인자로 실행하면 저장된 학교/반 설정을 지우고 다시 설정할 수 있음
"""

import ctypes
import json
import os
import re
import sys
from datetime import datetime
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
    "date_calendar": "#D2D5E0"
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
        "y": 63,
        "show": True,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 135
    },
    "date_info": {
        "x": 50,
        "y": 54,
        "show": False,
        "stroke_width": 0,
        "shadow_blur": 5,
        "shadow_opacity": 100,
        "font_size": 157
    }
}

DEFAULT_CONFIG = {
    "api_key": "",
    "edu_code": "",
    "school_code": "",
    "school_name": "",
    "school_kind": "",
    "grade": "",
    "classnm": "",
    "custom_background": None,
    "bg_photo_mode": "auto",
    "bg_photo_idx": 0,
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

def save_data_cache(timetable, meals):
    os.makedirs(os.path.dirname(DATA_CACHE_PATH), exist_ok=True)
    cache = {
        "timetable": timetable,
        "meals": meals,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(DATA_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def load_data_cache():
    if os.path.exists(DATA_CACHE_PATH):
        try:
            with open(DATA_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"timetable": [], "meals": []}


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
    api_key = input("NEIS Open API 인증키(KEY)를 입력하세요: ").strip()

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


def render_wallpaper(cfg, now, timetable, meals):
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

    def draw_two_tone_with_effects(part1, font1, color1, part2, font2, color2, x, y, section_cfg, gap=10):
        w1, _ = text_size(sdraw, part1, font1)
        w2, _ = text_size(sdraw, part2, font2)
        total = w1 + gap + w2
        x_start = x - total / 2
        draw_text_with_effects(part1, x_start, y, font1, color1, section_cfg, align="left")
        draw_text_with_effects(part2, x_start + w1 + gap, y, font2, color2, section_cfg, align="left")

    # 개선된 폰트 크기 및 두께 정의 (KoPubWorld 폰트 최적화 + 개별 font_size 배율 반영)
    s_info = sections.get("school_info", DEFAULT_SECTIONS["school_info"])
    s_tt = sections.get("timetable", DEFAULT_SECTIONS["timetable"])
    s_meals = sections.get("meals", DEFAULT_SECTIONS["meals"])
    s_date = sections.get("date_info", DEFAULT_SECTIONS["date_info"])

    info_scale = float(s_info.get("font_size", 100)) / 100.0
    tt_scale = float(s_tt.get("font_size", 100)) / 100.0
    meals_scale = float(s_meals.get("font_size", 100)) / 100.0
    date_scale = float(s_date.get("font_size", 100)) / 100.0

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
            draw_text_with_effects("등록된 급식 정보가 없습니다", dish_font, colors["meals_dish"], s_meals)

    # ---- 4. 날짜 정보 섹션 ----
    if s_date.get("show", True):
        dx = int(W * s_date.get("x", 50) / 100)
        dy = int(H * s_date.get("y", 88) / 100)
        weekday_str = WEEKDAY_EN[now.weekday()].upper()
        date_str = now.strftime("%Y-%m-%d")
        draw_two_tone_with_effects(weekday_str, date_weekday_font, colors["date_weekday"],
                                   date_str, date_calendar_font, colors["date_calendar"],
                                   dx, dy, s_date, gap=int(16 * date_scale))

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
    SPI_SETDESKWALLPAPER = 20
    SPIF_UPDATEINIFILE = 0x01
    SPIF_SENDCHANGE = 0x02
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    )


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

def update_once(cfg):
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    meals = fetch_meal(cfg, ymd)
    timetable = fetch_timetable(cfg, ymd)
    save_data_cache(timetable, meals) # 로컬 캐시 갱신
    path = render_wallpaper(cfg, now, timetable, meals)
    set_wallpaper(path)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 바탕화면 갱신 완료 "
          f"(시간표 {len(timetable)}건, 급식 {len(meals)}건)")


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
