from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from core import neis
from core.schoolday import target_day, validate_overrides, apply_overrides
from core.themes import extract_style, apply_style


class SchoolDayTests(unittest.TestCase):
    def test_default_today_and_tomorrow_preserve_timezone(self):
        now = datetime(2026, 11, 12, 23, 50, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(target_day({}, now), now)
        tomorrow = target_day({'display_day': 'tomorrow'}, now)
        self.assertEqual(tomorrow, datetime(2026, 11, 13, 23, 50, tzinfo=now.tzinfo))

    def test_tomorrow_crosses_month_year_and_leap_day(self):
        for now, expected in ((datetime(2026, 12, 31), datetime(2027, 1, 1)),
                              (datetime(2028, 2, 28), datetime(2028, 2, 29)),
                              (datetime(2026, 2, 28), datetime(2026, 3, 1))):
            self.assertEqual(target_day({'display_day': 'tomorrow'}, now), expected)

    def test_manual_override_replaces_and_adds_only_selected_date(self):
        source = [{'period': '2', 'subject': '과학', 'extra': {'group': 1}},
                  {'period': '1', 'subject': '수학'}]
        cfg = {'manual_timetable': [
            {'date': '20261112', 'period': 1, 'subject': '  체육  '},
            {'date': '20261112', 'period': '3', 'subject': '자습'},
            {'date': '20261113', 'period': '2', 'subject': '음악'},
        ]}
        original, original_cfg = deepcopy(source), deepcopy(cfg)
        result = apply_overrides(source, cfg, '20261112')
        self.assertEqual(result, [{'period': '1', 'subject': '체육', 'manual': True},
                                 source[0], {'period': '3', 'subject': '자습', 'manual': True}])
        result[1]['extra']['group'] = 99
        self.assertEqual(source, original)
        self.assertEqual(cfg, original_cfg)

    def test_manual_timetable_works_without_remote_rows(self):
        cfg = {'manual_timetable': [{'date': '20261112', 'period': 5, 'subject': '동아리'}]}
        self.assertEqual(apply_overrides([], cfg, '20261112'),
                         [{'period': '5', 'subject': '동아리', 'manual': True}])
        self.assertEqual(apply_overrides([], cfg, '20261113'), [])

    def test_base_duplicate_periods_are_preserved_unless_overridden(self):
        source = [{'period': '1', 'subject': '선택 A'}, {'period': '1', 'subject': '선택 B'}]
        self.assertEqual(apply_overrides(source, {}, '20261112'), source)
        cfg = {'manual_timetable': [{'date': '20261112', 'period': 1, 'subject': '공통 수업'}]}
        self.assertEqual(len(apply_overrides(source, cfg, '20261112')), 1)

    def test_override_validation_checks_dates_periods_subject_and_duplicates(self):
        valid = {'date': '20261112', 'period': 1, 'subject': '수학'}
        invalid = [None, {}, [dict(valid, date='2026-11-12')], [dict(valid, date='20260229')],
                   [dict(valid, date='2026111')], [dict(valid, period=True)],
                   [dict(valid, period=1.0)], [dict(valid, period=21)], [dict(valid, period=0)],
                   [dict(valid, period='1.0')], [dict(valid, subject=' ')],
                   [dict(valid, subject='x'*101)], [dict(valid, subject='수학\n영어')],
                   [dict(valid, extra=True)], [valid, dict(valid, period='01')]]
        for items in invalid:
            with self.subTest(items=items), self.assertRaises(ValueError):
                validate_overrides(items)
        validate_overrides([valid, dict(valid, date='20261113')])

    def test_theme_changes_preserve_day_selection_and_manual_timetable(self):
        cfg = {'display_day': 'tomorrow', 'manual_timetable': [
            {'date': '20261113', 'period': 1, 'subject': '체육'}], 'dday_count': 2}
        style = extract_style(cfg)
        self.assertNotIn('display_day', style)
        self.assertNotIn('manual_timetable', style)
        result = apply_style(cfg, {'dday_count': 1})
        self.assertEqual(result['manual_timetable'], cfg['manual_timetable'])
        self.assertEqual(result['display_day'], 'tomorrow')


class MealDetailsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'api_key': 'key', 'edu_code': 'E10', 'school_code': '123'}

    def response(self, row):
        return {'mealServiceDietInfo': [{}, {'row': [row]}]}

    def test_details_preserve_allergy_numbers_and_existing_clean_display(self):
        row = {'MMEAL_SC_NM': '중식', 'DDISH_NM': '밥<br/>국 (1.5.6)<BR />김치 (9)',
               'CAL_INFO': '780 Kcal', 'NTR_INFO': '탄수화물:100<br>단백질:30',
               'ORPLC_INFO': '쌀:국내산<br />배추:국내산'}
        with patch.object(neis, 'neis_get', return_value=self.response(row)) as query:
            meal = neis.fetch_meal(self.cfg, '20261113')[0]
        self.assertEqual(meal['dishes'], '밥\n국\n김치')
        self.assertEqual(meal['raw_dishes'], '밥\n국 (1.5.6)\n김치 (9)')
        self.assertEqual(meal['calories'], '780 Kcal')
        self.assertEqual(meal['nutrition'], '탄수화물:100\n단백질:30')
        self.assertEqual(meal['origin'], '쌀:국내산\n배추:국내산')
        self.assertEqual(query.call_args.args[1]['MLSV_YMD'], '20261113')

    def test_optional_meal_details_default_to_empty_strings(self):
        row = {'MMEAL_SC_NM': '중식', 'DDISH_NM': '밥', 'CAL_INFO': None}
        with patch.object(neis, 'neis_get', return_value=self.response(row)):
            meal = neis.fetch_meal(self.cfg, '20261112')[0]
        self.assertEqual(meal['raw_dishes'], '밥')
        self.assertEqual((meal['calories'], meal['nutrition'], meal['origin']), ('', '', ''))


if __name__ == '__main__':
    unittest.main()
