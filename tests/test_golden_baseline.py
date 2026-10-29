"""Golden regression baseline (ACT-14, first slice).

The baseline stores invariants, not an exact schedule: netting (task count and
total quantity) and compliance (OTD, 95% floor, calendar coverage). A change that
moves a lot from one shift to another is fine; a change that drops a task, changes
the net requirement, or breaks the 95% floor is not.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.audit import AuditConfig, PlanRow, ShiftInfo, audit_schedule
from src.data_loader import build_requirement_plan, load_aps_database
from src.kernel import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode, DEFAULT_CHANGEOVER_MINUTES
from src.validator import validate_schedule

DATA_DIR = os.path.join(BASE_DIR, "data")
FIXTURE = os.path.join(BASE_DIR, "tests", "fixtures", "wl_10d_baseline.json")
START_DT = datetime(2026, 10, 12, 7, 0, 0)
HORIZON_DAYS = 10


def _rows(result):
    rows = []
    for task in result.tasks:
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
    return rows


def _shifts(result):
    return [
        ShiftInfo(
            f"{s.date_str} {s.shift_name}",
            s.index,
            s.date_str,
            s.shift_name,
            s.start_dt,
            s.end_dt,
            s.duration_hours,
        )
        for s in result.shifts
    ]


class TestGoldenBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as handle:
            cls.baseline = json.load(handle)

        snap = load_aps_database(DATA_DIR, site_filter="WL")
        snap.orders = [o for o in snap.orders if o.due_datetime <= START_DT + timedelta(days=HORIZON_DAYS)]
        cls.snapshot = snap
        cls.plan = build_requirement_plan(snap)
        sched = APSScheduler(
            cls.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
        )
        cls.result = sched.solve(time_limit_seconds=60.0)
        cls.checks = validate_schedule(
            cls.plan,
            cls.result,
            site="WL",
            allow_105_fg=getattr(cls.result, "allow_105_fg", False),
            allow_105_sf=getattr(cls.result, "allow_105_sf", False),
        )

    def test_01_netting_matches_baseline(self):
        expected = self.baseline["expected"]
        self.assertEqual(len(self.plan.tasks), expected["task_count"])
        self.assertEqual(int(round(sum(t.quantity for t in self.plan.tasks))), expected["total_quantity"])
        self.assertEqual(len(self.result.tasks), expected["task_count"])

    def test_02_compliance_matches_baseline(self):
        expected = self.baseline["expected"]
        delivery = next(c for c in self.checks if "R-WL-20" in c.rule_ids)
        utilization = next(c for c in self.checks if "利用率" in c.name and "R-WL-26" in c.rule_ids)
        self.assertGreaterEqual(delivery.metrics["on_time_delivery_rate_pct"], expected["on_time_delivery_pct"])
        self.assertEqual(utilization.metrics["violating_shifts"], expected["below_95_shifts"])

    def test_03_calendar_coverage_in_band(self):
        expected = self.baseline["expected"]
        lines = {t.line for t in self.result.tasks}
        opened = {
            (t.line, key)
            for t in self.result.tasks
            for key, qty in t.shift_breakdown.items()
            if qty > 0
        }
        coverage = len(opened) / max(1, len(lines) * len(self.result.shifts))
        self.assertGreaterEqual(coverage, expected["min_calendar_coverage"])
        self.assertLessEqual(coverage, expected["max_calendar_coverage"])

    def test_04_independent_audit_passes(self):
        report = audit_schedule(
            self.snapshot,
            _rows(self.result),
            _shifts(self.result),
            AuditConfig(setup_minutes=DEFAULT_CHANGEOVER_MINUTES),
        )
        self.assertTrue(report.passed, report.format_text())

    def test_05_same_part_multiple_orders_are_kept(self):
        counts = {}
        for task in self.result.tasks:
            if task.stage == "FG":
                counts[task.part_no] = counts.get(task.part_no, 0) + 1
        self.assertTrue(any(n > 1 for n in counts.values()), "expected at least one part with multiple orders")

    def test_06_empty_demand_is_safe(self):
        snap = load_aps_database(DATA_DIR, site_filter="WL")
        snap.orders = []
        plan = build_requirement_plan(snap)
        self.assertEqual(len(plan.tasks), 0)
        sched = APSScheduler(
            plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
        )
        result = sched.solve(time_limit_seconds=5.0)
        self.assertEqual(len(result.tasks), 0)
        checks = validate_schedule(plan, result, site="WL")
        self.assertTrue(all(c.passed for c in checks if c.metrics.get("applicable", True) is not False))

    def test_07_overload_is_reported(self):
        """Tripling demand must not silently produce a plan: precheck or solve reports it."""
        from src.prechecker import precheck_capacity
        from src.shift_manager import generate_shift_calendar

        snap = load_aps_database(DATA_DIR, site_filter="WL")
        snap.orders = [o for o in snap.orders if o.due_datetime <= START_DT + timedelta(days=HORIZON_DAYS)]
        for order in snap.orders:
            order.demand_quantity *= 3
        plan = build_requirement_plan(snap)
        shifts = generate_shift_calendar(START_DT, HORIZON_DAYS, calendar_mode=CalendarMode.SEVEN_ZERO, site="WL")
        precheck = precheck_capacity(plan, shifts)
        self.assertFalse(precheck.is_feasible, "tripled demand should be flagged as a capacity overload")

    def test_08_kernel_capacity_matches_legacy(self):
        """ACT-22 step 1: the compiled capacity rule must reproduce the legacy plan."""
        rule_set = RuleSet(
            "CAP_TEST",
            "WL",
            1,
            [RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC)],
            [
                RuleInstance(
                    "CAP95",
                    "capacity_utilization",
                    Scope(),
                    Nature.HARD,
                    100,
                    "G2_CAPACITY",
                    20,
                    {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
                )
            ],
        )
        sched = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
            use_kernel_capacity=True,
        )
        self.assertTrue(sched.use_kernel_capacity)
        result = sched.solve(time_limit_seconds=120.0)

        # The compiled rule must reproduce the legacy *compliance*, not necessarily
        # the identical tie-break: the kernel adds a redundant ceiling constraint,
        # which can move CP-SAT to a different but equally valid plan.
        legacy_quantities = {t.task_id: int(round(t.quantity)) for t in self.result.tasks}
        kernel_quantities = {t.task_id: int(round(t.quantity)) for t in result.tasks}
        self.assertEqual(kernel_quantities, legacy_quantities)
        self.assertLessEqual(
            abs(result.objective_value - self.result.objective_value),
            abs(self.result.objective_value) * 0.2,
            f"objective moved too far: legacy={self.result.objective_value} kernel={result.objective_value}",
        )

        checks = validate_schedule(
            self.plan,
            result,
            site="WL",
            allow_105_fg=getattr(result, "allow_105_fg", False),
            allow_105_sf=getattr(result, "allow_105_sf", False),
        )
        utilization = next(c for c in checks if "利用率" in c.name and "R-WL-26" in c.rule_ids)
        self.assertEqual(utilization.metrics["violating_shifts"], 0)
        self.assertTrue(all(c.passed for c in checks if c.metrics.get("applicable", True) is not False))

    def test_09_kernel_min_batch_and_due_match_legacy(self):
        """ACT-22 step 2: min_batch + hard due compiled by the kernel stay compliant."""
        rule_set = RuleSet(
            "K3_TEST",
            "WL",
            1,
            [
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
                RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
            ],
            [
                RuleInstance("MB", "min_batch", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"unit": "MPQ"}),
                RuleInstance("DUE", "due_date", Scope(stage="FG"), Nature.HARD, 1, "G1_DELIVERY", 10, {"mode": "hard"}),
                RuleInstance(
                    "CAP",
                    "capacity_utilization",
                    Scope(),
                    Nature.HARD,
                    100,
                    "G2_CAPACITY",
                    20,
                    {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
                ),
            ],
        )
        sched = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
            kernel_templates={"capacity_utilization", "min_batch", "due_date"},
        )
        result = sched.solve(time_limit_seconds=180.0)
        kernel_quantities = {t.task_id: int(round(t.quantity)) for t in result.tasks}
        legacy_quantities = {t.task_id: int(round(t.quantity)) for t in self.result.tasks}
        self.assertEqual(kernel_quantities, legacy_quantities)

        checks = validate_schedule(
            self.plan,
            result,
            site="WL",
            allow_105_fg=getattr(result, "allow_105_fg", False),
            allow_105_sf=getattr(result, "allow_105_sf", False),
        )
        utilization = next(c for c in checks if "利用率" in c.name and "R-WL-26" in c.rule_ids)
        self.assertEqual(utilization.metrics["violating_shifts"], 0)
        self.assertTrue(all(c.passed for c in checks if c.metrics.get("applicable", True) is not False))

    def test_10_kernel_changeover_matches_legacy(self):
        """ACT-22 step 3: changeover priced by the kernel keeps delivery and block count."""
        rule_set = RuleSet(
            "K4_TEST",
            "WL",
            1,
            [
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
                RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
            ],
            [
                RuleInstance("MB", "min_batch", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"unit": "MPQ"}),
                RuleInstance("DUE", "due_date", Scope(stage="FG"), Nature.HARD, 1, "G1_DELIVERY", 10, {"mode": "hard"}),
                RuleInstance(
                    "CAP",
                    "capacity_utilization",
                    Scope(),
                    Nature.HARD,
                    100,
                    "G2_CAPACITY",
                    20,
                    {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
                ),
                RuleInstance("CO", "changeover", Scope(), Nature.SOFT, 100, "G3_CHANGEOVER", 30, {"setup_minutes": 0}),
            ],
        )
        sched = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
            kernel_templates={"capacity_utilization", "min_batch", "due_date", "changeover"},
        )
        result = sched.solve(time_limit_seconds=180.0)
        kernel_quantities = {t.task_id: int(round(t.quantity)) for t in result.tasks}
        legacy_quantities = {t.task_id: int(round(t.quantity)) for t in self.result.tasks}
        self.assertEqual(kernel_quantities, legacy_quantities)

        checks = validate_schedule(
            self.plan,
            result,
            site="WL",
            allow_105_fg=getattr(result, "allow_105_fg", False),
            allow_105_sf=getattr(result, "allow_105_sf", False),
        )
        utilization = next(c for c in checks if "利用率" in c.name and "R-WL-26" in c.rule_ids)
        self.assertEqual(utilization.metrics["violating_shifts"], 0)
        kernel_blocks = next(c for c in checks if "R-WL-11" in c.rule_ids).metrics["total_blocks"]
        legacy_blocks = next(c for c in self.checks if "R-WL-11" in c.rule_ids).metrics["total_blocks"]
        self.assertLessEqual(kernel_blocks, legacy_blocks + 5, f"blocks {kernel_blocks} vs legacy {legacy_blocks}")
        self.assertTrue(all(c.passed for c in checks if c.metrics.get("applicable", True) is not False))

    def test_11_kernel_physical_constraints(self):
        """ACT-22 step 4: sequence/tooling/material_ready/pm compiled by the kernel."""
        rule_set = RuleSet(
            "K8_TEST",
            "WL",
            1,
            [
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
                RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
            ],
            [
                RuleInstance("MB", "min_batch", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"unit": "MPQ"}),
                RuleInstance("SEQ", "sequence", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"order_by": "due"}),
                RuleInstance("TL", "tooling_exclusivity", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"available_qty": 1}),
                RuleInstance("PM", "pm_block", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"handling": "avoid"}),
                RuleInstance("MR", "material_ready", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"source": "PO"}),
                RuleInstance(
                    "MBAL",
                    "material_balance",
                    Scope(),
                    Nature.HARD,
                    1,
                    "G0_PHYSICAL",
                    0,
                    {"buffer_shifts": 0, "include_stock": True},
                ),
                RuleInstance("CC", "concurrency", Scope(), Nature.HARD, 100, "G3_CHANGEOVER", 30, {"max_models": 3}),
                RuleInstance("DUE", "due_date", Scope(stage="FG"), Nature.HARD, 1, "G1_DELIVERY", 10, {"mode": "hard"}),
                RuleInstance(
                    "CAP",
                    "capacity_utilization",
                    Scope(),
                    Nature.HARD,
                    100,
                    "G2_CAPACITY",
                    20,
                    {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
                ),
                RuleInstance("CO", "changeover", Scope(), Nature.SOFT, 100, "G3_CHANGEOVER", 30, {"setup_minutes": 0}),
            ],
        )
        sched = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
            kernel_templates={
                "capacity_utilization",
                "min_batch",
                "due_date",
                "changeover",
                "sequence",
                "tooling_exclusivity",
                "material_ready",
                "material_balance",
                "pm_block",
                "concurrency",
            },
        )
        result = sched.solve(time_limit_seconds=180.0)
        kernel_quantities = {t.task_id: int(round(t.quantity)) for t in result.tasks}
        legacy_quantities = {t.task_id: int(round(t.quantity)) for t in self.result.tasks}
        self.assertEqual(kernel_quantities, legacy_quantities)

        checks = validate_schedule(
            self.plan,
            result,
            site="WL",
            allow_105_fg=getattr(result, "allow_105_fg", False),
            allow_105_sf=getattr(result, "allow_105_sf", False),
        )
        utilization = next(c for c in checks if "利用率" in c.name and "R-WL-26" in c.rule_ids)
        self.assertEqual(utilization.metrics["violating_shifts"], 0)
        self.assertTrue(all(c.passed for c in checks if c.metrics.get("applicable", True) is not False))

    def _capacity_rule_set(self, max_util):
        return RuleSet(
            "CAP_TEST_105",
            "WL",
            1,
            [RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC)],
            [
                RuleInstance(
                    "CAP",
                    "capacity_utilization",
                    Scope(),
                    Nature.SOFT,
                    100,
                    "G2_CAPACITY",
                    20,
                    {"min_util": 0.95, "max_util": max_util, "tail_exempt": True},
                )
            ],
        )

    def test_12_rule_set_capacity_overrides_legacy_105(self):
        """The compiled capacity max_util is the source of truth, not the legacy radio."""
        scheduler_100 = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=self._capacity_rule_set(1.0),
            kernel_templates={"capacity_utilization"},
            allow_105_utilization_fg=True,
            allow_105_utilization_sf=True,
        )
        self.assertFalse(scheduler_100.allow_105_utilization_fg)
        self.assertFalse(scheduler_100.allow_105_utilization_sf)

        scheduler_105 = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=self._capacity_rule_set(1.05),
            kernel_templates={"capacity_utilization"},
            allow_105_utilization_fg=False,
            allow_105_utilization_sf=False,
        )
        self.assertTrue(scheduler_105.allow_105_utilization_fg)
        self.assertTrue(scheduler_105.allow_105_utilization_sf)

    def test_13_soft_tier_has_group_objectives(self):
        """The soft tier must expose G1/G2/G3 objectives for lexicographic solving."""
        scheduler = APSScheduler(
            self.plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=self._capacity_rule_set(1.05),
            kernel_templates={"capacity_utilization"},
        )
        _model, _x, aux = scheduler._build_model("soft", hard_due=False)
        active_groups = {gid for gid, terms in aux["group_terms"].items() if terms}
        self.assertIn("G1_DELIVERY", active_groups)
        self.assertIn("G2_CAPACITY", active_groups)


if __name__ == "__main__":
    unittest.main()
