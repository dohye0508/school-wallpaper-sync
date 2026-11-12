"""Portable text-color presets; never include layout, photos or school data."""
from core.themes import COLOR_KEYS, validate_style


BODY_COLORS = frozenset(('school_name_muted', 'timetable_subject', 'meals_dish', 'dday_name'))
COLOR_GROUPS = {
    'all': COLOR_KEYS,
    'body': BODY_COLORS,
    'accent': COLOR_KEYS - BODY_COLORS,
}


def color_palettes(default_colors):
    """Return fresh complete palettes, including the application's own default."""
    result = [dict(id='default', name='기본', colors=dict(default_colors))]
    for identifier, name, text, muted, primary, secondary, highlight in (
        ('ocean', '오션', '#F0F7FF', '#BFD7ED', '#81D4FA', '#80CBC4', '#FFE082'),
        ('lavender', '라벤더', '#FAF5FF', '#DDD0EF', '#CEADFF', '#F5B8D8', '#FFE0A3'),
        ('mint', '민트', '#F1FFF8', '#BCDCD0', '#8EE4BE', '#B3E5FC', '#F9E79F'),
        ('sunset', '노을', '#FFF6ED', '#EACBBC', '#FFB38A', '#F6A6B2', '#FFE08A'),
        ('light', '밝은 배경', '#202938', '#4B5563', '#075985', '#9D174D', '#854D0E'),
    ):
        colors = dict.fromkeys(COLOR_KEYS, text)
        colors.update(school_name_muted=muted, timetable_title=primary,
                      timetable_period=highlight, meals_title=secondary,
                      meals_type=secondary, date_weekday=primary,
                      date_calendar=muted, dday_label=highlight)
        result.append(dict(id=identifier, name=name, colors=colors))
    for item in result:
        validate_style({'font_colors': item['colors']})
    return result
