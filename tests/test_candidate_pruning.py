"""C2-b-3c: prune line candidates and keep sequence in the main model."""
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


def _task(tid, mold):
    start = datetime(2026, 10, 12, 7, 0, 0)
    return ProductionTask(
        task_id=tid, stage="FG", line="PGLINKAGE", part_no=f"P-{tid}",
        description=tid, ct_seconds=60, trp=1, mpq=50, lot_size=50,
        lots=2, quantity=100, lot_millis=2 * 3600 * 1000,
        order_id=f"O-{tid}", due_datetime=start + timedelta(days=3),
        mold_id=mold,
    )


def _sched(tasks, options):
    start = datetime(2026, 10, 12, 7, 0, 0)
    plan = RequirementPlan(fg_tasks=tasks, sf_tasks=[], stock_movements=[], site="WL")
    return APSScheduler(
        plan,
        start_datetime=start,
        planning_days=5,
        calendar_mode=CalendarMode.SEVEN_ZERO,
        site="WL",
        shift_hours=8,
        shifts_per_day=3,
        enforce_utilization_95=False,
        enforce_material_balance=False,
        sequence_setup=True,
        line_options=options,
    )


class TestCandidatePruning(unittest.TestCase):
    def test_three_optional_tasks_stay_in_main_model(self):
        tasks = [_task(f"T{i}", f"M{i}") for i in range(1, 4)]
        sched = _sched(tasks, {t.task_id: ["PGLINKAGE", "轴套6"] for t in tasks})
        self.assertTrue(sched.assign_in_model)
        self.assertTrue(sched.sequence_setup)
        self.assertFalse(sched._two_stage_sequence)
        self.assertEqual(sched.candidate_pruning["tasks_with_options"], 3)
        self.assertEqual(sched.candidate_pruning["candidate_slots"], 6)

    def test_more_than_six_optional_tasks_use_candidate_pool(self):
        tasks = [_task(f"T{i}", f"M{i}") for i in range(1, 8)]
        sched = _sched(tasks, {t.task_id: ["PGLINKAGE", "轴套6"] for t in tasks})
        self.assertTrue(sched.candidate_pruning["pool_limited"])
        self.assertEqual(len(sched.line_options), 6)
        self.assertFalse(sched._two_stage_sequence)

    def test_candidates_are_pruned_to_two(self):
        tasks = [_task("T1", "M1"), _task("T2", "M2")]
        sched = _sched(
            tasks,
            {
                "T1": ["PGLINKAGE", "轴套6", "PU07"],
                "T2": ["PGLINKAGE", "轴套6"],
            },
        )
        self.assertEqual(sched.line_options["T1"], ["PGLINKAGE", "轴套6"])
        self.assertEqual(sched.candidate_pruning["pruned_tasks"], 1)


if __name__ == "__main__":
    unittest.main()
