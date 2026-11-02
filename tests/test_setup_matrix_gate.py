"""HC-04: missing setup edges must not silently fall back to 60 minutes."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import ProductionTask, RequirementPlan
from src.scheduler import APSScheduler, MissingSetupEdgeError
from src.shift_manager import CalendarMode


def _task(tid, part, mold):
    return ProductionTask(
        task_id=tid, stage="FG", line="PGLINKAGE", part_no=part,
        description=part, ct_seconds=60, trp=1, mpq=100,
        lot_size=100, lots=1, quantity=100,
        lot_millis=100 * 60 * 1000, order_id="O1",
        due_datetime=datetime(2026, 10, 17, 7), mold_id=mold,
    )


class TestSetupMatrixGate(unittest.TestCase):
    def test_missing_edge_raises_when_strict(self):
        plan = RequirementPlan(
            fg_tasks=[_task("A", "P-A", "M1"), _task("B", "P-B", "M2")],
            sf_tasks=[], stock_movements=[], site="WL",
        )
        sched = APSScheduler(
            plan, start_datetime=datetime(2026, 10, 12, 7), planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            enforce_utilization_95=False, enforce_material_balance=False,
            sequence_setup=True, setup_matrix={}, setup_matrix_strict=True,
        )
        with self.assertRaises(MissingSetupEdgeError):
            sched.solve(time_limit_seconds=5)


if __name__ == "__main__":
    unittest.main()
