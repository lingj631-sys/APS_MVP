"""Tests for the fixed rule kernel (Phase 1 / ACT-20/21)."""
import json
import os
import sys
import unittest
from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple

from ortools.sat.python import cp_model

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.kernel import (
    IMPLEMENTED_TEMPLATES,
    TEMPLATES,
    GroupMode,
    Nature,
    PenaltyBook,
    RuleGroup,
    RuleInstance,
    RuleSet,
    Scope,
    Target,
    compile_rules,
    resolve,
    solve_lexicographic,
    validate_params,
    validate_rule_set,
)

HOUR_MS = 3600 * 1000
LOT_MS = 2 * HOUR_MS
CAP_MS = 8 * HOUR_MS


@dataclass
class ToyTask:
    task_id: str
    stage: str
    line: str
    part_no: str
    mpq: int
    lot_size: int
    lots: int
    due_slot: Optional[int]
    window: Tuple[int, int]
    customer_tier: str = "A"
    crew_required: float = 0


class ToyContext:
    """Minimal CompileContext: one line, N shifts, lots of equal duration."""

    def __init__(self, tasks, num_shifts: int = 2, labor_available=None, labor_pool_map=None, weekend: bool = False):
        self.model = cp_model.CpModel()
        self.num_shifts = num_shifts
        self._tasks = list(tasks)
        self._labor_available = labor_available
        self._labor_pools = labor_pool_map or {}
        self._weekend = weekend
        self._x = {}
        self._a = {}
        self._st = {}
        self._end = {}
        self._load = {}
        self._open = {}
        self._tail = {}

        for task in self._tasks:
            lo, hi = task.window
            for slot in range(lo, hi + 1):
                x = self.model.NewIntVar(0, task.lots, f"x.{task.task_id}.{slot}")
                a = self.model.NewBoolVar(f"a.{task.task_id}.{slot}")
                self.model.Add(x >= 1 * a)
                self.model.Add(x <= task.lots * a)
                self._x[task.task_id, slot] = x
                self._a[task.task_id, slot] = a
            self.model.Add(sum(self._x[task.task_id, s] for s in range(lo, hi + 1)) == task.lots)
            self._st[task.task_id, lo] = self._a[task.task_id, lo]
            for slot in range(lo + 1, hi + 1):
                st = self.model.NewBoolVar(f"st.{task.task_id}.{slot}")
                self.model.Add(st >= self._a[task.task_id, slot] - self._a[task.task_id, slot - 1])
                self.model.Add(st <= self._a[task.task_id, slot])
                self.model.Add(st <= 1 - self._a[task.task_id, slot - 1])
                self._st[task.task_id, slot] = st
            end = self.model.NewIntVar(lo, hi, f"end.{task.task_id}")
            self.model.AddMaxEquality(end, [s * self._a[task.task_id, s] for s in range(lo, hi + 1)])
            self._end[task.task_id] = end

        for line in self.lines():
            line_tasks = [t for t in self._tasks if t.line == line]
            for slot in range(num_shifts):
                terms = [self._x[t.task_id, slot] * LOT_MS for t in line_tasks if (t.task_id, slot) in self._x]
                load = self.model.NewIntVar(0, CAP_MS, f"load.{line}.{slot}")
                self.model.Add(load == (sum(terms) if terms else 0))
                opened = self.model.NewBoolVar(f"open.{line}.{slot}")
                self.model.Add(load >= 1).OnlyEnforceIf(opened)
                self.model.Add(load <= CAP_MS * opened)
                self._load[line, slot] = load
                self._open[line, slot] = opened
            tails = []
            for slot in range(num_shifts):
                tail = self.model.NewBoolVar(f"tail.{line}.{slot}")
                self.model.Add(tail <= self._open[line, slot])
                for later in range(slot + 1, num_shifts):
                    self.model.Add(tail <= 1 - self._open[line, later])
                self._tail[line, slot] = tail
                tails.append(tail)
            self.model.Add(sum(tails) <= 1)

    def lines(self):
        return sorted({t.line for t in self._tasks})

    def tasks(self):
        return self._tasks

    def window(self, task):
        return task.window

    def x(self, task, slot):
        return self._x[task.task_id, slot]

    def active(self, task, slot):
        return self._a[task.task_id, slot]

    def started(self, task, slot):
        return self._st[task.task_id, slot]

    def end_of(self, task):
        return self._end[task.task_id]

    def load(self, line, slot):
        return self._load[line, slot]

    def open(self, line, slot):
        return self._open[line, slot]

    def tail(self, line, slot):
        return self._tail[line, slot]

    def capacity_ms(self, line):
        return CAP_MS

    def min_shift_ms(self, line):
        return int(CAP_MS * 0.95)

    def load_upper_bound(self, line):
        return CAP_MS

    def due_slot(self, task):
        return task.due_slot

    def due_key(self, task):
        return task.due_slot

    def mold_id(self, task):
        return None

    def pm_slots(self, line):
        return []

    def material_ready_slot(self, task):
        return 0

    def is_splittable(self, task):
        return False

    def children(self, task):
        return []

    def bom_ratio(self, task):
        return 1

    def stock_covered(self, task):
        return 0.0

    def cumulative(self, task, slot):
        return 0

    def unit_pieces(self, task):
        return 1

    def customer_tier(self, task):
        return task.customer_tier

    def crew_required(self, task):
        return task.crew_required or None

    def labor_available(self, line, slot):
        return self._labor_available

    def labor_pool(self, line):
        return self._labor_pools.get(line, f"__line__:{line}")

    def shift_weekday(self, slot):
        return 5 if self._weekend else 0

    def shift_name(self, slot):
        return ["早班", "中班", "晚班"][slot % 3]

    def shifts_per_day(self):
        return 3

    def co_product_partner(self, task):
        part = task.part_no
        if part.endswith("L"):
            partner_part = part[:-1] + "R"
        elif part.endswith("R"):
            partner_part = part[:-1] + "L"
        else:
            return None
        for other in self._tasks:
            if other.task_id != task.task_id and other.part_no == partner_part and other.line == task.line:
                return other
        return None


def _rules():
    return RuleSet(
        rule_set_id="TEST",
        site="WL",
        version=1,
        groups=[
            RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
            RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
        ],
        rules=[
            RuleInstance("DUE", "due_date", Scope(stage="FG"), Nature.HARD, 1, "G1_DELIVERY", 10, {"mode": "hard"}),
            RuleInstance(
                "CAP",
                "capacity_utilization",
                Scope(line="L1"),
                Nature.SOFT,
                100,
                "G2_CAPACITY",
                20,
                {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
            ),
        ],
    )


def _solve(ctx, compiled):
    ctx.model.Minimize(compiled.total_objective())
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    solver.parameters.num_workers = 1
    status = solver.Solve(ctx.model)
    return solver, status


def _rules_lex():
    return RuleSet(
        rule_set_id="TEST_LEX",
        site="WL",
        version=1,
        groups=[
            RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
            RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
            RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
        ],
        rules=[
            RuleInstance("DUE", "due_date", Scope(stage="FG"), Nature.HARD, 1, "G1_DELIVERY", 10, {"mode": "hard"}),
            RuleInstance(
                "CAP",
                "capacity_utilization",
                Scope(line="L1"),
                Nature.SOFT,
                100,
                "G2_CAPACITY",
                20,
                {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
            ),
            RuleInstance("CO", "changeover", Scope(line="L1"), Nature.SOFT, 100, "G3_CHANGEOVER", 30, {}),
        ],
    )


class TestRuleKernel(unittest.TestCase):
    def test_01_registry_has_19_templates(self):
        self.assertEqual(len(TEMPLATES), 19)
        self.assertTrue(IMPLEMENTED_TEMPLATES.issubset(set(TEMPLATES)))
        self.assertIn("capacity_utilization", TEMPLATES)
        self.assertIn("labor_capacity", TEMPLATES)

    def test_02_param_validation(self):
        resolved = validate_params("capacity_utilization", {"min_util": 0.9})
        self.assertEqual(resolved["max_util"], 1.0)
        self.assertFalse(resolved["include_setup"])
        with self.assertRaises(ValueError):
            validate_params("capacity_utilization", {"min_util": 1.5})
        with self.assertRaises(ValueError):
            validate_params("capacity_utilization", {"unknown": 1})

    def test_03_scope_replace(self):
        rule_set = RuleSet(
            "S",
            "WL",
            1,
            [RuleGroup("G2_CAPACITY", 20, GroupMode.WEIGHTED)],
            [
                RuleInstance("SITE", "capacity_utilization", Scope(stage="FG"), Nature.SOFT, 100, "G2_CAPACITY", 20, {}),
                RuleInstance(
                    "LINE", "capacity_utilization", Scope(line="PGLINKAGE"), Nature.SOFT, 120, "G2_CAPACITY", 20, {}, inherit="replace"
                ),
            ],
        )
        line = resolve(rule_set, Target("WL", "PGLINKAGE", "FG", "X"), date(2026, 10, 25))
        other = resolve(rule_set, Target("WL", "轴套6", "FG", "X"), date(2026, 10, 25))
        self.assertEqual([r.rule_id for r in line], ["LINE"])
        self.assertEqual([r.rule_id for r in other], ["SITE"])

    def test_04_scope_add(self):
        rule_set = RuleSet(
            "S",
            "WL",
            1,
            [RuleGroup("G2_CAPACITY", 20, GroupMode.WEIGHTED)],
            [
                RuleInstance("SITE", "capacity_utilization", Scope(stage="FG"), Nature.SOFT, 100, "G2_CAPACITY", 20, {}),
                RuleInstance(
                    "LINE", "capacity_utilization", Scope(line="PGLINKAGE"), Nature.SOFT, 50, "G2_CAPACITY", 20, {}, inherit="add"
                ),
            ],
        )
        got = resolve(rule_set, Target("WL", "PGLINKAGE", "FG", "X"), date(2026, 10, 25))
        self.assertEqual(sorted(r.rule_id for r in got), ["LINE", "SITE"])

    def test_05_effectivity(self):
        rule_set = RuleSet(
            "S",
            "WL",
            1,
            [RuleGroup("G2_CAPACITY", 20, GroupMode.WEIGHTED)],
            [
                RuleInstance("SITE", "capacity_utilization", Scope(), Nature.SOFT, 100, "G2_CAPACITY", 20, {}),
                RuleInstance(
                    "FUTURE",
                    "capacity_utilization",
                    Scope(line="PGLINKAGE"),
                    Nature.SOFT,
                    120,
                    "G2_CAPACITY",
                    20,
                    {},
                    effective_from=date(2027, 1, 1),
                ),
            ],
        )
        got = resolve(rule_set, Target("WL", "PGLINKAGE", "FG", "X"), date(2026, 10, 25))
        self.assertEqual([r.rule_id for r in got], ["SITE"])

    def test_06_json_rule_sets_load(self):
        for name, site in (("WL_2026Q4.json", "WL"), ("SY38_2026Q4.json", "SY38")):
            path = os.path.join(BASE_DIR, "config", "rule_sets", name)
            with open(path, encoding="utf-8") as handle:
                rule_set = RuleSet.from_dict(json.load(handle))
            self.assertEqual(rule_set.site, site)
            self.assertGreater(len(rule_set.rules), 0)
            warnings = validate_rule_set(rule_set)  # must not raise
            self.assertIsInstance(warnings, list)

    def test_07_bad_rule_set_raises(self):
        rule_set = RuleSet(
            "BAD",
            "WL",
            1,
            [RuleGroup("G0", 0, GroupMode.HARD)],
            [RuleInstance("R", "capacity_utilization", Scope(), Nature.SOFT, 1, "MISSING_GROUP", 0, {})],
        )
        with self.assertRaises(ValueError):
            validate_rule_set(rule_set)

    def test_08_compile_due_and_capacity(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4, due_slot=1, window=(0, 1))
        ctx = ToyContext([task])
        compiled = compile_rules(ctx, _rules())
        self.assertIn("DUE", compiled.hard_rules)
        solver, status = _solve(ctx, compiled)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertLessEqual(solver.Value(ctx.end_of(task)), 1)
        self.assertAlmostEqual(solver.ObjectiveValue(), 0.0, places=6)

    def test_09_underfilled_split_is_penalised(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4, due_slot=1, window=(0, 1))
        ctx = ToyContext([task])
        ctx.model.Add(ctx.x(task, 0) == 2)
        ctx.model.Add(ctx.x(task, 1) == 2)
        compiled = compile_rules(ctx, _rules())
        solver, status = _solve(ctx, compiled)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver.ObjectiveValue(), 0.0, "4h in each shift must trigger the 95% gap")

    def test_10_due_is_hard(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4, due_slot=0, window=(0, 1))
        ctx = ToyContext([task])
        compiled = compile_rules(ctx, _rules())
        solver, status = _solve(ctx, compiled)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(solver.Value(ctx.end_of(task)), 0)

    def test_11_unimplemented_template_warns(self):
        rule_set = RuleSet(
            "T",
            "WL",
            1,
            [RuleGroup("G0", 0, GroupMode.HARD)],
            [
                RuleInstance("ROUTE", "routing", Scope(), Nature.HARD, 1, "G0", 0, {"max_candidates": 2}),
            ],
        )
        task = ToyTask("T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4, due_slot=1, window=(0, 1))
        compiled = compile_rules(ToyContext([task]), rule_set)
        self.assertTrue(any("routing" in w for w in compiled.warnings))

    def test_12_weight_normalisation(self):
        model = cp_model.CpModel()
        book = PenaltyBook()
        r1 = RuleInstance("A", "changeover", Scope(), Nature.SOFT, 100, "G", 0, {})
        r2 = RuleInstance("B", "changeover", Scope(), Nature.SOFT, 300, "G", 0, {})
        v1 = model.NewIntVar(0, 10, "v1")
        v2 = model.NewIntVar(0, 10, "v2")
        book.add(r1, v1)
        book.add(r2, v2)
        self.assertEqual(book.scaled_weights("G"), {"A": 250, "B": 750})
        single = PenaltyBook()
        single.add(r1, v1)
        self.assertEqual(single.scaled_weights("G"), {"A": 1000})

    def test_13_lexicographic_freezes_priority(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4, due_slot=1, window=(0, 1))
        ctx = ToyContext([task])
        compiled = compile_rules(ctx, _rules_lex())
        result = solve_lexicographic(ctx.model, compiled, time_limit_seconds=5)
        self.assertTrue(result.feasible, result.format_text())
        self.assertEqual(result.group_value("G2_CAPACITY"), 0.0)
        self.assertEqual(result.group_value("G3_CHANGEOVER"), 1000.0)
        self.assertLessEqual(result.solver.Value(ctx.end_of(task)), 1)

    def test_14_group_order(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4, due_slot=1, window=(0, 1))
        compiled = compile_rules(ToyContext([task]), _rules_lex())
        self.assertEqual(
            [g.group_id for g in compiled.ordered_groups()],
            ["G1_DELIVERY", "G2_CAPACITY", "G3_CHANGEOVER"],
        )

    def test_15_customer_priority_weights(self):
        for tier, expected in (("STRATEGIC", 10.0), ("A", 3.0), ("B", 1.0)):
            task = ToyTask(
                "T1", "FG", "L1", "P1", mpq=10, lot_size=10, lots=4,
                due_slot=0, window=(0, 1), customer_tier=tier,
            )
            ctx = ToyContext([task])
            ctx.model.Add(ctx.end_of(task) == 1)
            rule_set = RuleSet(
                "CP",
                "WL",
                1,
                [RuleGroup("G1_DELIVERY", 10, GroupMode.WEIGHTED)],
                [
                    RuleInstance(
                        "CP",
                        "customer_priority",
                        Scope(stage="FG"),
                        Nature.SOFT,
                        100,
                        "G1_DELIVERY",
                        10,
                        {"tier_weights": "STRATEGIC:10,A:3,B:1"},
                    )
                ],
            )
            compiled = compile_rules(ctx, rule_set)
            ctx.model.Minimize(compiled.total_objective(scale=1))
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 5
            solver.parameters.num_workers = 1
            status = solver.Solve(ctx.model)
            self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
            self.assertAlmostEqual(solver.ObjectiveValue(), expected, places=6)

    def test_16_concurrency_hard_and_soft(self):
        tasks = [
            ToyTask(f"C{i}", "FG", "L1", f"P{i}", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1))
            for i in range(4)
        ]

        def build(nature):
            ctx = ToyContext(tasks)
            for task in tasks:
                ctx.model.Add(ctx.x(task, 0) == 1)
            rule_set = RuleSet(
                "CC",
                "WL",
                1,
                [RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED)],
                [
                    RuleInstance(
                        "CC",
                        "concurrency",
                        Scope(line="L1"),
                        nature,
                        100,
                        "G3_CHANGEOVER",
                        30,
                        {"max_models": 3},
                    )
                ],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx, _ = build(Nature.HARD)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx.model), cp_model.INFEASIBLE)

        ctx_soft, compiled_soft = build(Nature.SOFT)
        ctx_soft.model.Minimize(compiled_soft.total_objective())
        solver_soft = cp_model.CpSolver()
        solver_soft.parameters.max_time_in_seconds = 5
        solver_soft.parameters.num_workers = 1
        status = solver_soft.Solve(ctx_soft.model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver_soft.ObjectiveValue(), 0.0)

    def test_17_labor_capacity_and_shared_pool(self):
        tasks = [
            ToyTask("L1", "FG", "L1", "P1", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1), crew_required=12),
            ToyTask("L2", "FG", "L2", "P2", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1), crew_required=12),
        ]

        def build(available, pools=None):
            ctx = ToyContext(tasks, labor_available=available, labor_pool_map=pools)
            for task in tasks:
                ctx.model.Add(ctx.x(task, 0) == 1)
            rule_set = RuleSet(
                "LAB",
                "WL",
                1,
                [RuleGroup("G2_CAPACITY", 20, GroupMode.HARD)],
                [
                    RuleInstance(
                        "LAB",
                        "labor_capacity",
                        Scope(),
                        Nature.HARD,
                        1,
                        "G2_CAPACITY",
                        20,
                        {"available_headcount": 0},
                    )
                ],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx, _ = build(20)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx.model), cp_model.INFEASIBLE)

        ctx_ok, _ = build(30)
        solver_ok = cp_model.CpSolver()
        solver_ok.parameters.max_time_in_seconds = 5
        solver_ok.parameters.num_workers = 1
        self.assertIn(solver_ok.Solve(ctx_ok.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))

        ctx_pool, _ = build(30, {"L1": "POOL-A", "L2": "POOL-A"})
        solver_pool = cp_model.CpSolver()
        solver_pool.parameters.max_time_in_seconds = 5
        solver_pool.parameters.num_workers = 1
        self.assertEqual(solver_pool.Solve(ctx_pool.model), cp_model.INFEASIBLE)


    def test_18_contiguity_blocks(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=1, lot_size=1, lots=4, due_slot=2, window=(0, 2))

        def build(max_blocks, nature):
            ctx = ToyContext([task], num_shifts=3)
            ctx.model.Add(ctx.x(task, 0) == 2)
            ctx.model.Add(ctx.x(task, 1) == 0)
            ctx.model.Add(ctx.x(task, 2) == 2)
            rule_set = RuleSet(
                "CT",
                "WL",
                1,
                [RuleGroup("G0", 0, GroupMode.HARD)],
                [RuleInstance("CT", "contiguity", Scope(), nature, 100, "G0", 0, {"max_blocks": max_blocks})],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx_hard, _ = build(1, Nature.HARD)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx_hard.model), cp_model.INFEASIBLE)

        ctx_soft, compiled_soft = build(1, Nature.SOFT)
        ctx_soft.model.Minimize(compiled_soft.total_objective())
        solver_soft = cp_model.CpSolver()
        solver_soft.parameters.max_time_in_seconds = 5
        solver_soft.parameters.num_workers = 1
        self.assertIn(solver_soft.Solve(ctx_soft.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver_soft.ObjectiveValue(), 0.0)

        ctx_ok, _ = build(2, Nature.HARD)
        solver_ok = cp_model.CpSolver()
        solver_ok.parameters.max_time_in_seconds = 5
        solver_ok.parameters.num_workers = 1
        self.assertIn(solver_ok.Solve(ctx_ok.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))

    def test_19_calendar_and_time_window(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1))

        def build(template, nature, weekend=False, force_slot=0):
            ctx = ToyContext([task], num_shifts=2, weekend=weekend)
            ctx.model.Add(ctx.x(task, force_slot) == 1)
            params = {"mode": "FIVE_TWO"} if template == "calendar" else {"allowed_shifts": "早班"}
            rule_set = RuleSet(
                "TW",
                "WL",
                1,
                [RuleGroup("G0", 0, GroupMode.HARD)],
                [RuleInstance("TW", template, Scope(), nature, 100, "G0", 0, params)],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx_cal, _ = build("calendar", Nature.HARD, weekend=True)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx_cal.model), cp_model.INFEASIBLE)

        ctx_cal_soft, compiled_cal = build("calendar", Nature.SOFT, weekend=True)
        ctx_cal_soft.model.Minimize(compiled_cal.total_objective())
        solver_cal = cp_model.CpSolver()
        solver_cal.parameters.max_time_in_seconds = 5
        solver_cal.parameters.num_workers = 1
        self.assertIn(solver_cal.Solve(ctx_cal_soft.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver_cal.ObjectiveValue(), 0.0)

        ctx_tw, _ = build("time_window", Nature.HARD, force_slot=1)
        solver_tw = cp_model.CpSolver()
        solver_tw.parameters.max_time_in_seconds = 5
        solver_tw.parameters.num_workers = 1
        self.assertEqual(solver_tw.Solve(ctx_tw.model), cp_model.INFEASIBLE)

    def test_20_inventory_bound(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=1, lot_size=1, lots=1, due_slot=4, window=(0, 4))

        def build(nature):
            ctx = ToyContext([task], num_shifts=5)
            ctx.model.Add(ctx.x(task, 0) == 1)
            rule_set = RuleSet(
                "INV",
                "WL",
                1,
                [RuleGroup("G4", 40, GroupMode.WEIGHTED)],
                [RuleInstance("INV", "inventory_bound", Scope(stage="FG"), nature, 100, "G4", 40, {"max_days": 1})],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx_hard, _ = build(Nature.HARD)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx_hard.model), cp_model.INFEASIBLE)

        ctx_soft, compiled_soft = build(Nature.SOFT)
        ctx_soft.model.Minimize(compiled_soft.total_objective())
        solver_soft = cp_model.CpSolver()
        solver_soft.parameters.max_time_in_seconds = 5
        solver_soft.parameters.num_workers = 1
        self.assertIn(solver_soft.Solve(ctx_soft.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver_soft.ObjectiveValue(), 0.0)


    def test_21_freeze_window(self):
        task = ToyTask("T1", "FG", "L1", "P1", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1))

        def build(nature):
            ctx = ToyContext([task], num_shifts=2)
            ctx.model.Add(ctx.x(task, 0) == 1)
            rule_set = RuleSet(
                "FRZ",
                "WL",
                1,
                [RuleGroup("G0", 0, GroupMode.HARD)],
                [RuleInstance("FRZ", "freeze", Scope(), nature, 100, "G0", 0, {"fence_shifts": 1})],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx_hard, _ = build(Nature.HARD)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx_hard.model), cp_model.INFEASIBLE)

        ctx_soft, compiled_soft = build(Nature.SOFT)
        ctx_soft.model.Minimize(compiled_soft.total_objective())
        solver_soft = cp_model.CpSolver()
        solver_soft.parameters.max_time_in_seconds = 5
        solver_soft.parameters.num_workers = 1
        self.assertIn(solver_soft.Solve(ctx_soft.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver_soft.ObjectiveValue(), 0.0)

    def test_22_co_product_sync(self):
        left = ToyTask("L", "FG", "L1", "P1L", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1))
        right = ToyTask("R", "FG", "L1", "P1R", mpq=1, lot_size=1, lots=1, due_slot=1, window=(0, 1))

        def build(nature, right_qty):
            ctx = ToyContext([left, right], num_shifts=2)
            ctx.model.Add(ctx.x(left, 0) == 1)
            ctx.model.Add(ctx.x(right, 0) == right_qty)
            rule_set = RuleSet(
                "CO",
                "WL",
                1,
                [RuleGroup("G0", 0, GroupMode.HARD)],
                [RuleInstance("CO", "co_product", Scope(), nature, 100, "G0", 0, {"ratio": "1:1"})],
            )
            return ctx, compile_rules(ctx, rule_set)

        ctx_hard, _ = build(Nature.HARD, 0)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_workers = 1
        self.assertEqual(solver.Solve(ctx_hard.model), cp_model.INFEASIBLE)

        ctx_ok, _ = build(Nature.HARD, 1)
        solver_ok = cp_model.CpSolver()
        solver_ok.parameters.max_time_in_seconds = 5
        solver_ok.parameters.num_workers = 1
        self.assertIn(solver_ok.Solve(ctx_ok.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))

        ctx_soft, compiled_soft = build(Nature.SOFT, 0)
        ctx_soft.model.Minimize(compiled_soft.total_objective())
        solver_soft = cp_model.CpSolver()
        solver_soft.parameters.max_time_in_seconds = 5
        solver_soft.parameters.num_workers = 1
        self.assertIn(solver_soft.Solve(ctx_soft.model), (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertGreater(solver_soft.ObjectiveValue(), 0.0)


if __name__ == "__main__":
    unittest.main()
