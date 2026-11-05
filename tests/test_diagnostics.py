# -*- coding: utf-8 -*-
"""Unit tests for the infeasibility diagnosis payload."""
import os
import unittest
from datetime import datetime

from src.data_loader import build_requirement_plan, load_aps_database
from src.diagnostics import build_diagnosis
from src.shift_manager import CalendarMode, build_topology, generate_shift_calendar

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


class TestDiagnosis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = load_aps_database(DATA_DIR, site_filter="WL")
        geom = (8.0, 3)
        topo = build_topology("WL", shift_hours=geom[0], shifts_per_day=geom[1])
        cls.start = datetime(2026, 10, 12, topo.shift_boundaries[0][0])
        cls.shifts = generate_shift_calendar(
            cls.start, 10, calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=geom[0], shifts_per_day=geom[1],
        )
        horizon_end = cls.start.replace(hour=0) + __import__("datetime").timedelta(days=10)
        cls.snap.orders = [o for o in cls.snap.orders if o.due_datetime <= horizon_end]
        cls.plan = build_requirement_plan(cls.snap)
        cls.diag = build_diagnosis(
            plan=cls.plan, snapshot=cls.snap, shifts=cls.shifts,
            start_dt=cls.start, shift_hours=geom[0], pulled_orders=0,
        )

    def test_payload_shape(self):
        for key in ("primary_cause", "headline", "summary", "order_gaps",
                    "line_details", "data_checks", "rule_checks", "algorithm_checks"):
            self.assertIn(key, self.diag)
        self.assertEqual(self.diag["summary"]["shifts"], len(self.shifts))

    def test_order_gap_rows(self):
        rows = self.diag["order_gaps"]
        self.assertTrue(rows, "expected at least one FG order row")
        row = rows[0]
        for key in ("line", "order_id", "part_no", "due", "demand_qty", "opening_stock",
                    "cum_required_hours", "cum_capacity_hours", "gap_hours", "earliest_finish"):
            self.assertIn(key, row)
        # cumulative capacity can never exceed the horizon
        horizon = sum(s.duration_hours for s in self.shifts)
        for row in rows:
            self.assertLessEqual(row["cum_capacity_hours"], horizon + 1e-6)
            self.assertGreaterEqual(row["gap_hours"], 0.0)

    def test_gap_rows_sort_first(self):
        flags = [r["gap_hours"] > 0.05 for r in self.diag["order_gaps"]]
        self.assertEqual(flags, sorted(flags, reverse=True), "gap rows must come first")

    def test_line_details_classify_stage(self):
        lines = self.diag["line_details"]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn(line["stage_label"], ("成品线", "半成品线", "成品+半成品混线"))
            self.assertAlmostEqual(
                sum(p["hours"] for p in line["parts"]), line["required_hours"], delta=0.3
            )

    def test_horizon_capacity_matches_shifts(self):
        line = self.diag["line_details"][0]
        self.assertAlmostEqual(line["capacity_hours"], len(self.shifts) * 8.0, places=3)

    def test_empty_shifts_is_safe(self):
        diag = build_diagnosis(plan=self.plan, snapshot=self.snap, shifts=[],
                               start_dt=self.start, shift_hours=8.0)
        self.assertEqual(diag["order_gaps"], [])


if __name__ == "__main__":
    unittest.main()
