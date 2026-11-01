"""BS-06 full loop: real late solve -> truncate orders -> solve remaining."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.batching import (
    CampaignBatch,
    build_campaign_batches,
    build_truncation_suggestions,
    select_deferred_orders,
)
from src.data_loader import ProductionTask, RequirementPlan
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _task(idx):
    start = datetime(2026, 10, 12, 7, 0, 0)
    return ProductionTask(
        task_id=f"T{idx}",
        stage="FG",
        line="PGLINKAGE",
        part_no="P1",
        description="P1",
        ct_seconds=60.0,
        trp=1.0,
        mpq=50,
        lot_size=100,
        lots=1,
        quantity=100.0,
        lot_millis=100 * 60 * 1000,
        order_id=f"O{idx}",
        due_datetime=start + timedelta(hours=2),
        mold_id="M1",
    )


class TestBS06SolverLoop(unittest.TestCase):
    def test_late_solve_truncates_and_replans(self):
        tasks = [_task(i) for i in range(10)]
        plan = RequirementPlan(fg_tasks=tasks, sf_tasks=[], stock_movements=[], site="WL")
        start = datetime(2026, 10, 12, 7, 0, 0)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=1,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_on_time_delivery=False,
        )
        result = sched.solve(time_limit_seconds=10.0)
        late = [t for t in result.tasks if t.end_dt > t.due_datetime]
        self.assertTrue(late, "test setup must produce at least one late task")

        batches = build_campaign_batches(
            plan.tasks, {}, site="WL", lookahead_days=30, min_tasks=2
        )
        self.assertTrue(batches)
        suggestions = build_truncation_suggestions(result.tasks, batches)
        self.assertTrue(suggestions)
        deferred = select_deferred_orders(suggestions)
        self.assertTrue(deferred)
        deferred_ids = {d["order_id"] for d in deferred}

        remaining = [t for t in tasks if t.order_id not in deferred_ids]
        reduced = RequirementPlan(
            fg_tasks=remaining, sf_tasks=[], stock_movements=[], site="WL"
        )
        sched2 = APSScheduler(
            reduced,
            start_datetime=start,
            planning_days=1,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_on_time_delivery=False,
        )
        result2 = sched2.solve(time_limit_seconds=10.0)
        self.assertFalse(deferred_ids & {t.order_id for t in result2.tasks})
        self.assertEqual(len(result2.tasks), len(remaining))


if __name__ == "__main__":
    unittest.main()
