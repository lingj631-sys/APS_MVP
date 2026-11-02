"""HC-14: low assembly coverage exempts L/R SF overstock."""
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


def _task(tid, part, parent=None, sim="Y"):
    return ProductionTask(
        task_id=tid, stage="SF" if parent else "FG", line="PGLINKAGE",
        part_no=part, description=part, ct_seconds=60, trp=1, mpq=50,
        lot_size=1 if parent else 50, lots=100 if parent else 2,
        quantity=100, lot_millis=60 * 1000, order_id="O1",
        due_datetime=datetime(2026, 10, 17, 7), parent_task_id=parent,
        mold_id="M1", simultaneous_production=sim,
    )


class TestHC14Exemption(unittest.TestCase):
    def test_low_coverage_pair_is_exempt(self):
        fg = _task("FG1", "FG-P1")
        left = _task("SF-L", "P-L", "FG1")
        right = _task("SF-R", "P-R", "FG1")
        plan = RequirementPlan(
            fg_tasks=[fg], sf_tasks=[left, right], stock_movements=[],
            site="WL", initial_stock={},
        )
        sched = APSScheduler(
            plan, start_datetime=datetime(2026, 10, 12, 7), planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            demand_by_date={"2026-10-12": {"FG-P1": 100}},
        )
        sched._compute_hc14_exemptions()
        self.assertEqual(sched.hc14_exempt_parts, {"P-L", "P-R"})
        self.assertTrue(sched.hc14_warnings)


if __name__ == "__main__":
    unittest.main()
