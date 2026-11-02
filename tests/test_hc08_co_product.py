"""HC-08: same-mold L/R SF tasks must produce synchronously."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import ProductionTask, RequirementPlan
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _task(tid, part, parent=None, mold="M1", sim="Y"):
    start = datetime(2026, 10, 12, 7, 0, 0)
    return ProductionTask(
        task_id=tid, stage="SF" if parent else "FG", line="PGLINKAGE",
        part_no=part, description=part, ct_seconds=60, trp=1, mpq=50,
        lot_size=1 if parent else 50, lots=100 if parent else 2,
        quantity=100, lot_millis=60 * 1000, order_id="O1",
        due_datetime=start + timedelta(days=3), parent_task_id=parent,
        mold_id=mold, simultaneous_production=sim,
    )


class TestCoProductSync(unittest.TestCase):
    def test_same_mold_pair_runs_same_shifts(self):
        fg = _task("FG1", "FG-P1")
        left = _task("SF-L", "P-L", parent="FG1")
        right = _task("SF-R", "P-R", parent="FG1")
        plan = RequirementPlan(fg_tasks=[fg], sf_tasks=[left, right], stock_movements=[], site="WL")
        rule_set = RuleSet(
            "HC08_TEST", "WL", 1,
            [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
            [RuleInstance("HC08", "co_product", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"ratio": "1:1"})],
        )
        sched = APSScheduler(
            plan, start_datetime=datetime(2026, 10, 12, 7, 0, 0), planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            enforce_utilization_95=False, enforce_material_balance=False,
            rule_set=rule_set, kernel_templates={"co_product"},
        )
        result = sched.solve(time_limit_seconds=10.0)
        by_part = {t.part_no: t.shift_breakdown for t in result.tasks}
        self.assertEqual(by_part["P-L"], by_part["P-R"])


if __name__ == "__main__":
    unittest.main()
