from __future__ import annotations

import datetime as dt
import unittest

from aps.calendar.geometry import (
    DAY_START_HOUR,
    MS_PER_HOUR,
    LineGeometry,
    Slot,
    SlotKey,
    build_slots,
)
from aps.calendar.pattern import (
    DEFAULT_AUTO_THRESHOLDS,
    REST_WEEKDAYS,
    WORK_DAYS,
    CalendarMode,
    is_working_day,
    recommend_mode,
    working_days,
)
from aps.data.issues import IssueLog

FAST = True

MONDAY = dt.date(2026, 11, 2)      # 周一
SATURDAY = dt.date(2026, 11, 7)
SUNDAY = dt.date(2026, 11, 8)
FRIDAY = dt.date(2026, 11, 6)
THURSDAY = dt.date(2026, 11, 5)

WL_LINE = LineGeometry("PGLINKAGE", 8.0, 3, 0.85)
SY_LINE = LineGeometry("1400T-6", 12.0, 2, 0.90)


class TestRecommendMode(unittest.TestCase):
    def test_thresholds_are_four_sevenths_family(self):
        self.assertEqual(0.5714, DEFAULT_AUTO_THRESHOLDS["4ON3OFF"])
        self.assertEqual(0.7143, DEFAULT_AUTO_THRESHOLDS["5ON2OFF"])
        self.assertEqual(0.8571, DEFAULT_AUTO_THRESHOLDS["6ON1OFF"])

    def test_low_ratio_picks_four_on_three_off(self):
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.1))
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.5714))

    def test_medium_ratio_picks_five_on_two_off(self):
        self.assertEqual(CalendarMode.FIVE_ON_TWO_OFF, recommend_mode(0.58))
        self.assertEqual(CalendarMode.FIVE_ON_TWO_OFF, recommend_mode(0.7143))

    def test_high_ratio_picks_six_on_one_off(self):
        self.assertEqual(CalendarMode.SIX_ON_ONE_OFF, recommend_mode(0.72))
        self.assertEqual(CalendarMode.SIX_ON_ONE_OFF, recommend_mode(0.8571))

    def test_overload_picks_continuous(self):
        self.assertEqual(CalendarMode.CONTINUOUS, recommend_mode(0.86))
        self.assertEqual(CalendarMode.CONTINUOUS, recommend_mode(3.0))

    def test_custom_thresholds_override_defaults(self):
        custom = {"4ON3OFF": 0.1, "5ON2OFF": 0.2, "6ON1OFF": 0.3}
        self.assertEqual(CalendarMode.CONTINUOUS, recommend_mode(0.5, custom))
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.05, custom))

    def test_zero_ratio(self):
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.0))


class TestWorkingDays(unittest.TestCase):
    def test_work_days_table(self):
        self.assertEqual(7, WORK_DAYS[CalendarMode.CONTINUOUS])
        self.assertEqual(6, WORK_DAYS[CalendarMode.SIX_ON_ONE_OFF])
        self.assertEqual(5, WORK_DAYS[CalendarMode.FIVE_ON_TWO_OFF])
        self.assertEqual(4, WORK_DAYS[CalendarMode.FOUR_ON_THREE_OFF])

    def test_rest_weekdays_are_iso_prefix_complement(self):
        self.assertEqual(frozenset(), REST_WEEKDAYS[CalendarMode.CONTINUOUS])
        self.assertEqual(frozenset({6}), REST_WEEKDAYS[CalendarMode.SIX_ON_ONE_OFF])
        self.assertEqual(frozenset({5, 6}), REST_WEEKDAYS[CalendarMode.FIVE_ON_TWO_OFF])
        self.assertEqual(frozenset({4, 5, 6}), REST_WEEKDAYS[CalendarMode.FOUR_ON_THREE_OFF])

    def test_continuous_covers_every_day(self):
        days = working_days(CalendarMode.CONTINUOUS, MONDAY, 10)
        self.assertEqual(10, len(days))
        self.assertEqual(MONDAY, days[0])
        self.assertEqual(dt.date(2026, 11, 11), days[-1])

    def test_five_on_two_off_skips_weekend(self):
        days = working_days(CalendarMode.FIVE_ON_TWO_OFF, MONDAY, 7)
        self.assertNotIn(SATURDAY, days)
        self.assertNotIn(SUNDAY, days)
        self.assertEqual(5, len(days))

    def test_four_on_three_off_skips_fri_sat_sun(self):
        days = working_days(CalendarMode.FOUR_ON_THREE_OFF, MONDAY, 7)
        self.assertEqual((MONDAY, dt.date(2026, 11, 3), dt.date(2026, 11, 4), THURSDAY), days)

    def test_six_on_one_off_skips_sunday_only(self):
        days = working_days(CalendarMode.SIX_ON_ONE_OFF, MONDAY, 7)
        self.assertIn(SATURDAY, days)
        self.assertNotIn(SUNDAY, days)
        self.assertEqual(6, len(days))

    def test_horizon_counts_calendar_days_not_working_days(self):
        # 窗口 = 10 个自然日（11-02 ~ 11-11），工作日数由模式决定
        self.assertEqual(10, len(working_days(CalendarMode.CONTINUOUS, MONDAY, 10)))
        self.assertEqual(8, len(working_days(CalendarMode.FIVE_ON_TWO_OFF, MONDAY, 10)))
        self.assertEqual(9, len(working_days(CalendarMode.SIX_ON_ONE_OFF, MONDAY, 10)))
        self.assertEqual(7, len(working_days(CalendarMode.FOUR_ON_THREE_OFF, MONDAY, 10)))

    def test_is_working_day(self):
        self.assertTrue(is_working_day(CalendarMode.FIVE_ON_TWO_OFF, MONDAY))
        self.assertFalse(is_working_day(CalendarMode.FIVE_ON_TWO_OFF, SUNDAY))
        self.assertTrue(is_working_day(CalendarMode.CONTINUOUS, SUNDAY))

    def test_zero_horizon(self):
        self.assertEqual((), working_days(CalendarMode.CONTINUOUS, MONDAY, 0))


class TestSlotGeometry(unittest.TestCase):
    def test_day_start_hour_is_seven(self):
        self.assertEqual(7, DAY_START_HOUR)
        self.assertEqual(3_600_000, MS_PER_HOUR)

    def test_wl_three_shift_windows(self):
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(3, len(slots))
        windows = [(s.start, s.end) for s in slots]
        self.assertEqual([
            (dt.datetime(2026, 11, 2, 7, 0), dt.datetime(2026, 11, 2, 15, 0)),
            (dt.datetime(2026, 11, 2, 15, 0), dt.datetime(2026, 11, 2, 23, 0)),
            (dt.datetime(2026, 11, 2, 23, 0), dt.datetime(2026, 11, 3, 7, 0)),
        ], windows)

    def test_sy38_two_shift_windows_same_formula(self):
        slots = build_slots([SY_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(2, len(slots))
        self.assertEqual((dt.datetime(2026, 11, 2, 7, 0), dt.datetime(2026, 11, 2, 19, 0)),
                         (slots[0].start, slots[0].end))
        self.assertEqual((dt.datetime(2026, 11, 2, 19, 0), dt.datetime(2026, 11, 3, 7, 0)),
                         (slots[1].start, slots[1].end))

    def test_capacity_ms(self):
        wl = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertTrue(all(s.capacity_ms == 8 * MS_PER_HOUR for s in wl))
        sy = build_slots([SY_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertTrue(all(s.capacity_ms == 12 * MS_PER_HOUR for s in sy))

    def test_absolute_index_is_per_line(self):
        slots = build_slots([WL_LINE], MONDAY, 2, CalendarMode.CONTINUOUS)
        self.assertEqual([0, 1, 2, 3, 4, 5], [s.index for s in slots])
        self.assertEqual(0, slots[3].key.shift_index)
        self.assertEqual(dt.date(2026, 11, 3), slots[3].key.work_date)

    def test_rest_day_slots_exist_but_closed(self):
        slots = build_slots([WL_LINE], MONDAY, 7, CalendarMode.FIVE_ON_TWO_OFF)
        self.assertEqual(21, len(slots))
        saturday = [s for s in slots if s.key.work_date == SATURDAY]
        self.assertEqual(3, len(saturday))
        self.assertTrue(all(not s.is_open for s in saturday))
        monday = [s for s in slots if s.key.work_date == MONDAY]
        self.assertTrue(all(s.is_open for s in monday))

    def test_multiple_lines_sorted_by_line_then_index(self):
        slots = build_slots([WL_LINE, SY_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(["1400T-6", "1400T-6", "PGLINKAGE", "PGLINKAGE", "PGLINKAGE"],
                         [s.key.line_code for s in slots])

    def test_slot_key_is_hashable_and_frozen(self):
        key = SlotKey("L1", MONDAY, 0)
        self.assertEqual({key: True}[SlotKey("L1", MONDAY, 0)], True)
        with self.assertRaises(Exception):
            key.shift_index = 1

    def test_override_closes_an_otherwise_open_slot(self):
        log = IssueLog()
        overrides = {SlotKey("PGLINKAGE", MONDAY, 1): False}
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS, overrides, log)
        target = [s for s in slots if s.key.shift_index == 1][0]
        self.assertFalse(target.is_open)
        self.assertTrue(all(s.is_open for s in slots if s.key.shift_index != 1))

    def test_override_opens_a_rest_day_slot_and_warns(self):
        log = IssueLog()
        overrides = {SlotKey("PGLINKAGE", SUNDAY, 0): True}
        slots = build_slots([WL_LINE], SATURDAY, 2, CalendarMode.FIVE_ON_TWO_OFF,
                            overrides, log)
        target = [s for s in slots if s.key.work_date == SUNDAY and s.key.shift_index == 0][0]
        self.assertTrue(target.is_open)
        self.assertTrue(any("工作日历" in i.message for i in log.warnings))

    def test_agreeing_override_does_not_warn(self):
        log = IssueLog()
        overrides = {SlotKey("PGLINKAGE", MONDAY, 0): True}
        build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS, overrides, log)
        self.assertEqual((), log.warnings)

    def test_override_for_unknown_line_is_ignored(self):
        log = IssueLog()
        overrides = {SlotKey("GHOST", MONDAY, 0): False}
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS, overrides, log)
        self.assertEqual(3, len(slots))
        self.assertTrue(all(s.is_open for s in slots))

    def test_fractional_shift_hours_round_to_integer_ms(self):
        line = LineGeometry("L", 7.5, 2, 0.9)
        slots = build_slots([line], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(27_000_000, slots[0].capacity_ms)
        self.assertIsInstance(slots[0].capacity_ms, int)

    def test_slots_are_deterministic(self):
        a = build_slots([WL_LINE, SY_LINE], MONDAY, 10, CalendarMode.FIVE_ON_TWO_OFF)
        b = build_slots([SY_LINE, WL_LINE], MONDAY, 10, CalendarMode.FIVE_ON_TWO_OFF)
        self.assertEqual(a, b)

    def test_returns_slot_instances(self):
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertIsInstance(slots[0], Slot)


if __name__ == "__main__":
    unittest.main()
