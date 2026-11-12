"""NEIS adapter; reusable independently of Tkinter and Windows."""
import json
import logging
import re
from datetime import datetime, timedelta
import requests

NEIS_BASE = "https://open.neis.go.kr/hub"
SCHOOL_LEVEL_ENDPOINT = {"초등학교": "elsTimetable", "중학교": "misTimetable", "고등학교": "hisTimetable"}

class NeisError(Exception):
    pass

def log_failure(label, error, cfg):
    # Requests exceptions can contain the URL, including the API key.
    message = str(error)
    key = cfg.get('api_key')
    if key:
        message = message.replace(str(key), '<redacted>')
    logging.getLogger(__name__).warning('%s: %s: %s', label, type(error).__name__, message)

def neis_get(endpoint, params):
    response = requests.get(f"{NEIS_BASE}/{endpoint}", params=dict(params), timeout=10)
    response.raise_for_status()
    data = json.loads(response.content.decode("utf-8"))
    if not isinstance(data, dict):
        raise NeisError("Invalid NEIS response")
    results = [data["RESULT"]] if "RESULT" in data else []
    blocks = data.get(endpoint, [])
    if not isinstance(blocks, list):
        raise NeisError("Invalid NEIS rows")
    for block in blocks:
        for head in block.get("head", []):
            if "RESULT" in head:
                results.append(head["RESULT"])
    for result in results:
        code = result.get("CODE")
        if code == "INFO-200":
            return {}
        if code != "INFO-000":
            raise NeisError(f"NEIS error: {code}")
    if len(blocks) < 2 or not isinstance(blocks[1].get("row"), list):
        raise NeisError("Missing NEIS rows")
    return data

def search_school(api_key, name):
    data = neis_get("schoolInfo", {"KEY": api_key, "Type": "json", "SCHUL_NM": name, "pSize": 100})
    return data["schoolInfo"][1]["row"] if "schoolInfo" in data else []

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
            raw_dishes = re.sub(r'<br\s*/?>', '\n', r['DDISH_NM'], flags=re.IGNORECASE)
            # 알레르기 표시 괄호 제거 (예: "김치찌개 (5.6.9)")
            dishes = re.sub(r"\s*\([0-9.]+\)", "", raw_dishes)
            meals.append({
                'name': r['MMEAL_SC_NM'], 'dishes': dishes,
                'raw_dishes': raw_dishes,
                'calories': r.get('CAL_INFO') or '',
                'nutrition': re.sub(r'<br\s*/?>', '\n', r.get('NTR_INFO') or '', flags=re.IGNORECASE),
                'origin': re.sub(r'<br\s*/?>', '\n', r.get('ORPLC_INFO') or '', flags=re.IGNORECASE),
            })
        return meals
    except Exception as e:
        log_failure('급식 조회 실패', e, cfg)
        return None  # 네트워크/API 실패와 "정상 응답이지만 급식이 없음"을 구분한다


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
        log_failure('시간표 조회 실패', e, cfg)
        return None  # 네트워크/API 실패와 "정상 응답이지만 시간표가 없음"을 구분한다


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
        log_failure('학사일정 조회 실패', e, cfg)
        return None  # 네트워크/API 실패와 "정상 응답이지만 일정이 없음"을 구분한다

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


