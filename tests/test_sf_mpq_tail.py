"""SC-04: SF MPQ blocks, at most one tail block."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import ProductionTask, RequirementPlan
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


class TestSFMPQTail(unittest.TestCase):
    def test_sf_blocks_are_mpq_or_one_tail(self):
        start = datetime(2026, 10, 12, 7, 0, 0)
        fg = ProductionTask(
            task_id="FG1", stage="FG", line="PGLINKAGE", part_no="FG-P1",
            description="FG-P1", ct_seconds=60.0, trp=1.0, mpq=100,
            lot_size=100, lots=1, quantity=100.0,
            lot_millis=100 * 60 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=4),
        )
        task = ProductionTask(
            task_id="SF1", stage="SF", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60.0, trp=1.0, mpq=100,
            lot_size=1, lots=250, quantity=250.0,
            lot_millis=60 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=3), parent_task_id="FG1",
        )
        plan = RequirementPlan(fg_tasks=[fg], sf_tasks=[task], stock_movements=[], site="WL")
        sched = APSScheduler(
            plan, start_datetime=start, planning_days=3,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            enforce_utilization_95=False, enforce_material_balance=False,
            sf_mpq_tail_enabled=True,
        )
        result = sched.solve(time_limit_seconds=10.0)
        blocks = list(next(t for t in result.tasks if t.stage == "SF").shift_breakdown.values())
        self.assertEqual(sum(blocks), 250)
        non_mpq = [q for q in blocks if q % 100 != 0]
        self.assertLessEqual(len(non_mpq), 1)
        if non_mpq:
            self.assertLess(non_mpq[0], 100)


if __name__ == "__main__":
    unittest.main()
