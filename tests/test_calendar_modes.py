"""Calendar modes (4 tiers) and workbook-driven shift geometry."""
import os
import unittest
from datetime import datetime

from src.shift_manager import (
    CalendarMode,
    WORKING_DAYS_BY_MODE,
    build_topology,
    decide_calendar_mode,
    generate_shift_calendar,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCalendarModes(unittest.TestCase):
    def test_01_four_tiers_working_days(self):
        self.assertEqual(WORKING_DAYS_BY_MODE[CalendarMode.FOUR_THREE], {0, 1, 2, 3})
        self.assertEqual(WORKING_DAYS_BY_MODE[CalendarMode.FIVE_TWO], {0, 1, 2, 3, 4})
        self.assertEqual(WORKING_DAYS_BY_MODE[CalendarMode.SIX_ONE], {0, 1, 2, 3, 4, 5})
        self.assertEqual(WORKING_DAYS_BY_MODE[CalendarMode.SEVEN_ZERO], {0, 1, 2, 3, 4, 5, 6})

    def test_02_decide_mode_thresholds(self):
        full = 168.0
        self.assertEqual(decide_calendar_mode(full * 4 / 7 - 1, full), CalendarMode.FOUR_THREE)
        self.assertEqual(decide_calendar_mode(full * 0.65, full), CalendarMode.FIVE_TWO)
        self.assertEqual(decide_calendar_mode(full * 0.80, full), CalendarMode.SIX_ONE)
        self.assertEqual(decide_calendar_mode(full * 0.95, full), CalendarMode.SEVEN_ZERO)

    def test_03_six_one_skips_sunday(self):
        # 2026-10-12 is a Monday
        shifts = generate_shift_calendar(
            datetime(2026, 10, 12), 7, calendar_mode=CalendarMode.SIX_ONE,
            site="WL", shift_hours=8.0, shifts_per_day=3,
        )
        days = sorted({s.date_str for s in shifts})
        self.assertEqual(len(days), 6)
        self.assertNotIn("10/18", days)  # Sunday
        self.assertEqual(len(shifts), 18)

    def test_04_two_shift_geometry(self):
        topology = build_topology("WL", shift_hours=12.0, shifts_per_day=2)
        self.assertEqual(topology.shifts_per_day, 2)
        self.assertEqual(topology.shift_hours, 12.0)
        self.assertEqual(topology.shift_names, ("早班", "晚班"))
        shifts = generate_shift_calendar(
            datetime(2026, 10, 12), 1, calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL", shift_hours=12.0, shifts_per_day=2,
        )
        self.assertEqual(len(shifts), 2)
        self.assertEqual(shifts[0].start_dt.hour, 8)
        self.assertEqual(shifts[1].start_dt.hour, 20)
        self.assertEqual(shifts[0].duration_hours, 12.0)

    def test_05_three_shift_geometry_is_unchanged(self):
        topology = build_topology("WL", shift_hours=8.0, shifts_per_day=3)
        self.assertEqual(topology.shift_hours, 8.0)
        self.assertEqual(topology.shifts_per_day, 3)
        shifts = generate_shift_calendar(
            datetime(2026, 10, 12), 1, calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL", shift_hours=8.0, shifts_per_day=3,
        )
        self.assertEqual([s.start_dt.hour for s in shifts], [7, 15, 23])
        self.assertEqual(shifts[-1].end_dt.hour, 7)


class TestLineBaseGeometry(unittest.TestCase):
    def test_loader_reads_shift_geometry_from_line_base(self):
        import openpyxl

        from src.data_loader import load_aps_database
        from src.master_schema import SHEET_BY_KEY, resolve_sheet

        src = os.path.join(BASE_DIR, "APS_DataSource_Template.xlsx")
        wb = openpyxl.load_workbook(src)
        try:
            ws = resolve_sheet(wb, SHEET_BY_KEY["line_base"])
            ws.delete_rows(2, ws.max_row)
            ws.append(["PGLINKAGE", 0.71, 12, 2])
            ws.append(["轴套6", 0.85, 12, 2])
            tmp = os.path.join(BASE_DIR, "data", "uploads", "test_line_base.xlsx")
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            wb.save(tmp)
        finally:
            wb.close()
        try:
            snap = load_aps_database(tmp, site_filter="WL")
            self.assertEqual(snap.fg_params["WA14208"].hours_per_shift, 12.0)
            self.assertEqual(snap.fg_params["WA14208"].shifts_per_day, 2)
            self.assertEqual(snap.sf_params["W000061536"].hours_per_shift, 12.0)
            self.assertEqual(snap.sf_params["W000061536"].shifts_per_day, 2)
            self.assertEqual(snap.sf_params["W000061536"].trp, 0.85)
        finally:
            os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
