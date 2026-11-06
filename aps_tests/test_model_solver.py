from __future__ import annotations

import datetime as dt
import unittest

from ortools.sat.python import cp_model

from aps.calendar.geometry import build_slots
from aps.calendar.pattern import CalendarMode
from aps.dataset import BomLink, DataSet, Horizon, LineGeometry, RouteOption, Task
from aps.model.context import StructureContext
from aps.model.solver import SolveSummary, solve_schedule

FAST = True

SITE = "WL"
START = dt.date(2026, 11, 3)

LINES = (
    LineGeometry("LINE_FG", 8.0, 3, 0.85),
    LineGeometry("LINE_SF", 8.0, 3, 0.90),
)

ROUTES = (
    RouteOption(
        pn="FG01", op_seq=10, line_code="LINE_FG", certified=True,
        ct_sec=20.0, unit_ms=23529, per_shift_cap=1224, headcount=5,
        changeover_min=30.0, changeover_source="routing", mold_ids=("M1",),
    ),
    RouteOption(
        pn="SF01", op_seq=10, line_code="LINE_SF", certified=True,
        ct_sec=15.0, unit_ms=16666, per_shift_cap=1728, headcount=3,
        changeover_min=20.0, changeover_source="routing", mold_ids=("M2",),
    ),
)


def _make_dataset(tasks=(), bom=(), days=2) -> DataSet:
    horizon = Horizon(SITE, START, days)
    slots = build_slots(LINES, START, days, CalendarMode.CONTINUOUS)
    return DataSet(
        horizon=horizon,
        mode=CalendarMode.CONTINUOUS,
        lines=LINES,
        slots=slots,
        tasks=tasks,
        routes=ROUTES,
        bom=bom,
        safety_stock={},
        mold_to_parts={"M1": frozenset({"FG01"}), "M2": frozenset({"SF01"})},
        initial_stock={},
        calendar_overrides={},
    )


class TestModelSolver(unittest.TestCase):
    def test_solve_feasible_schedule(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=96, due_date=START + dt.timedelta(days=1),
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=2)
        ctx = StructureContext.build(ds)

        solver, summary = solve_schedule(ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(solver)
        self.assertIn(summary.status, ("OPTIMAL", "FEASIBLE"))
        self.assertGreaterEqual(summary.wall_time_seconds, 0.0)
        self.assertEqual(0, summary.p1_lateness_shifts)
        self.assertEqual([], summary.diagnostics)

    def test_solve_infeasible_schedule_produces_diagnostics(self):
        # 构造物理不可行反例：单日 1,000,000 件超出物理容量极限
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=1000000, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=1)
        ctx = StructureContext.build(ds)

        solver, summary = solve_schedule(ctx, time_limit_seconds=5.0)
        self.assertIsNone(solver)
        self.assertEqual("INFEASIBLE", summary.status)
        self.assertGreater(len(summary.diagnostics), 0)
        diagnostic_text = " ".join(summary.diagnostics)
        self.assertTrue("FG01" in diagnostic_text or "容量" in diagnostic_text or "c3_cap" in diagnostic_text or "c2_net_req" in diagnostic_text)

    def test_solve_with_cross_stage_pipeline(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=48, due_date=START + dt.timedelta(days=1),
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
            Task(pn="SF01", stage="SF", net_requirement=48, due_date=START + dt.timedelta(days=1),
                 mpq=48, moq=None, mold_id="M2", family_code="F1", description=None,
                 is_safety_topup=False, parent_pn="FG01"),
        )
        bom = (BomLink(parent_pn="FG01", child_pn="SF01", ratio=1.0),)
        ds = _make_dataset(tasks=tasks, bom=bom, days=2)
        ctx = StructureContext.build(ds)

        solver, summary = solve_schedule(ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(solver)
        self.assertIn(summary.status, ("OPTIMAL", "FEASIBLE"))
        self.assertEqual(0, summary.p1_lateness_shifts)

    def test_p1_prioritizes_tier_a_customer_over_normal(self):
        # Both tasks take full shift capacity (1224 pcs).
        # Task Normal (weight 1) vs Task Tier A (weight 10). Both due on Day 1.
        # With weighted P1, Tier A must have 0 late shifts!
        t_normal = Task(
            pn="FG01", stage="FG", net_requirement=1224, due_date=START,
            mpq=48, moq=None, mold_id="M1", family_code="F1", description="普通订单",
            is_safety_topup=False, customer_code="CUST_NORMAL", weight=1
        )
        t_tier_a = Task(
            pn="FG01", stage="FG", net_requirement=1224, due_date=START,
            mpq=48, moq=None, mold_id="M1", family_code="F1", description="战略A级客户",
            is_safety_topup=False, customer_code="CUST_TIER_A", weight=10
        )
        # Put t_normal first in tasks list so without weighting it would naturally be preferred or tied
        ds = _make_dataset(tasks=(t_normal, t_tier_a), days=2)
        ctx = StructureContext.build(ds)

        solver, summary = solve_schedule(ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(solver)
        # The late shift variable for Tier A task must be 0
        tier_a_t_id = f"FG_FG01_{START.isoformat()}_1"
        tier_a_lateness = solver.Value(ctx.late_shifts[tier_a_t_id])
        self.assertEqual(0, tier_a_lateness)

    def test_four_layer_lexicographic_summary_fields(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=48, due_date=START + dt.timedelta(days=1),
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=2)
        ctx = StructureContext.build(ds)

        solver, summary = solve_schedule(ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(solver)
        self.assertIsInstance(summary.p1_lateness_shifts, int)
        self.assertIsInstance(summary.p2_utilization_gap_ms, int)
        self.assertIsInstance(summary.p3_changeover_blocks, int)
        self.assertIsInstance(summary.p4_wip_units, int)
        self.assertGreaterEqual(summary.p3_changeover_blocks, 1)


if __name__ == "__main__":
    unittest.main()
