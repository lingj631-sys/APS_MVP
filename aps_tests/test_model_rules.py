from __future__ import annotations

import datetime as dt
import unittest

from ortools.sat.python import cp_model

from aps.calendar.geometry import MS_PER_HOUR, build_slots
from aps.calendar.pattern import CalendarMode
from aps.dataset import BomLink, DataSet, Horizon, LineGeometry, RouteOption, Task
from aps.model.context import StructureContext
from aps.model.rules import build_core_constraints

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


class TestModelRules(unittest.TestCase):
    def test_c1_single_line_assignment(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=96, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks)
        ctx = StructureContext.build(ds)
        build_core_constraints(ctx)

        solver = cp_model.CpSolver()
        status = solver.Solve(ctx.model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        assigned = [l for l in ("LINE_FG",) if solver.Value(ctx.assign[(ctx.task_ids[0], l)]) == 1]
        self.assertEqual(1, len(assigned))

    def test_c3_net_requirement_conservation(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=144, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks)
        ctx = StructureContext.build(ds)
        build_core_constraints(ctx)

        solver = cp_model.CpSolver()
        status = solver.Solve(ctx.model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))

        total_produced = sum(
            solver.Value(ctx.x[(ctx.task_ids[0], "LINE_FG", s)])
            for s in range(ds.slot_count("LINE_FG"))
        )
        self.assertEqual(144, total_produced)

    def test_c5_fg_mpq_multiple(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=144, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks)
        ctx = StructureContext.build(ds)
        build_core_constraints(ctx)

        solver = cp_model.CpSolver()
        status = solver.Solve(ctx.model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))

        for s in range(ds.slot_count("LINE_FG")):
            qty = solver.Value(ctx.x[(ctx.task_ids[0], "LINE_FG", s)])
            self.assertEqual(0, qty % 48)

    def test_c7_cross_stage_cumulative_balance(self):
        # FG01 requires SF01 with ratio 1.0. If SF01 has 0 initial stock,
        # FG01 cannot be produced in slot 0 because SF01 output is only available at slot 1.
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=48, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
            Task(pn="SF01", stage="SF", net_requirement=48, due_date=START,
                 mpq=48, moq=None, mold_id="M2", family_code="F1", description=None,
                 is_safety_topup=False, parent_pn="FG01"),
        )
        bom = (BomLink(parent_pn="FG01", child_pn="SF01", ratio=1.0),)
        ds = _make_dataset(tasks=tasks, bom=bom, days=2)
        ctx = StructureContext.build(ds)
        build_core_constraints(ctx)

        solver = cp_model.CpSolver()
        status = solver.Solve(ctx.model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))

        # FG01 cannot be produced in slot 0 without stock
        fg_t = ctx.task_ids[0]
        fg_slot_0 = solver.Value(ctx.x[(fg_t, "LINE_FG", 0)])
        self.assertEqual(0, fg_slot_0)

    def test_c9_tail_shift_exempt_from_95_percent(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=48, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=1)
        ctx = StructureContext.build(ds)
        build_core_constraints(ctx, allow_soft_utilization=False)

        solver = cp_model.CpSolver()
        status = solver.Solve(ctx.model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_assumption_literals_registered(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=48, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=1)
        ctx = StructureContext.build(ds)
        rule_names = build_core_constraints(ctx, use_assumptions=True)
        self.assertGreater(len(rule_names), 0)
        self.assertGreater(len(ctx.assumptions), 0)

    def test_infeasible_counter_example_diagnosed_via_assumptions(self):
        # 构造物理不可行反例：需求 1,000,000 件远超全厂总容量（全天仅 3 班 * 1224 件）
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=1000000, due_date=START,
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=1)
        ctx = StructureContext.build(ds)
        build_core_constraints(ctx, use_assumptions=True)

        solver = cp_model.CpSolver()
        status = solver.Solve(ctx.model)
        self.assertEqual(cp_model.INFEASIBLE, status)

        conflict = solver.SufficientAssumptionsForInfeasibility()
        self.assertGreater(len(conflict), 0)


if __name__ == "__main__":
    unittest.main()
