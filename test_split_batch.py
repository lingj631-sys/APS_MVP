"""ACT-06: an oversized single lot splits across consecutive shifts, no second setup."""
import os
import sys
import unittest
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.audit import AuditConfig, PlanRow, ShiftInfo, audit_schedule
from src.data_loader import CustomerOrder, build_requirement_plan, load_aps_database
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode
from src.validator import validate_schedule

DATA_DIR = os.path.join(BASE_DIR, "data")
START_DT = datetime(2026, 10, 12, 7, 0, 0)
HORIZON_DAYS = 10


class TestOversizedLotSplit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = load_aps_database(DATA_DIR, site_filter="WL")
        # WA14208S: MPQ=72, one lot is 8.45h > 8h, so it cannot fit in one shift.
        cls.snapshot.orders = [
            CustomerOrder(
                "WA-SPLIT-01",
                "WA14208S",
                "DX11 左驾前雨刮电机传动杆（备件）",
                "2026-10-15",
                datetime(2026, 10, 15, 15, 0, 0),
                72,
            )
        ]
        cls.plan = build_requirement_plan(cls.snapshot)
        cls.scheduler = APSScheduler(
            cls.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
        )
        cls.result = cls.scheduler.solve(time_limit_seconds=10.0)
        cls.task = next(t for t in cls.result.tasks if t.part_no == "WA14208S")

    def test_01_split_is_continuous_and_within_capacity(self):
        self.assertTrue(self.task.splittable)
        self.assertEqual(int(round(self.task.quantity)), 72)
        self.assertGreaterEqual(len(self.task.shift_breakdown), 2, "one 8.45h lot cannot fit in one 8h shift")
        self.assertEqual(sum(self.task.shift_breakdown.values()), 72)

        per_shift = self.scheduler._per_shift_units(self.scheduler.task_by_id[self.task.task_id])
        self.assertLess(per_shift, 72)
        for key, qty in self.task.shift_breakdown.items():
            self.assertGreater(qty, 0, key)
            self.assertLessEqual(qty, per_shift, key)

        index_of = {(s.date_str, s.shift_name): s.index for s in self.result.shifts}
        indices = sorted(index_of[key] for key in self.task.shift_breakdown)
        self.assertEqual(
            indices[-1] - indices[0] + 1,
            len(indices),
            f"split must be continuous, got shifts {indices}",
        )

    def test_02_validator_and_audit_accept_the_split(self):
        checks = validate_schedule(
            self.plan,
            self.result,
            site="WL",
            allow_105_fg=self.result.allow_105_fg,
            allow_105_sf=self.result.allow_105_sf,
        )
        self.assertTrue(
            all(c.passed for c in checks if c.metrics.get("applicable", True) is not False),
            [c.name for c in checks if not c.passed],
        )
        blocks = next(c for c in checks if "R-WL-11" in c.rule_ids).metrics["total_blocks"]
        self.assertEqual(blocks, 1, "a split continuation is one block, not a second setup")

        rows = []
        for task in self.result.tasks:
            for (date_str, shift_name), qty in task.shift_breakdown.items():
                rows.append(
                    PlanRow(
                        task.task_id,
                        task.stage,
                        task.line,
                        task.part_no,
                        task.order_id,
                        f"{date_str} {shift_name}",
                        float(qty),
                    )
                )
        shifts = [
            ShiftInfo(
                f"{s.date_str} {s.shift_name}",
                s.index,
                s.date_str,
                s.shift_name,
                s.start_dt,
                s.end_dt,
                s.duration_hours,
            )
            for s in self.result.shifts
        ]
        report = audit_schedule(self.snapshot, rows, shifts, AuditConfig())
        self.assertTrue(report.passed, [c.name for c in report.checks if not c.passed])


if __name__ == "__main__":
    unittest.main()
