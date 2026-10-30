"""Regression test suite for verified defect fixes (REG-01 ~ REG-08).

Based on diagnosis in next/aps_solver_evolution.md.
"""
from __future__ import annotations

import unittest
from datetime import datetime

from src.audit.engine import (
    AuditConfig,
    PlanRow,
    ShiftInfo,
    _check_due_part,
    audit_schedule_part_level,
)
from src.data_loader import (
    CustomerOrder,
    ProductionInputSnapshot,
    ProductParam,
    build_requirement_plan,
    load_aps_csv,
    load_aps_database,
)
from src.domain.shift import ShiftSlot
from src.scheduler import APSScheduler, ScheduledTask
from src.shift_manager import get_topology


class TestApsRegressions(unittest.TestCase):
    """Regression test suite for core algorithm fixes."""

    def test_reg01_shared_sf_future_surplus_not_prematurely_consumed(self):
        """REG-01: FG tasks must not borrow from future SF production before it is produced."""
        o_b = CustomerOrder("ORD-B", "PART_B", "B", "10/14", datetime(2026, 10, 14, 15, 0), 10.0)
        o_a = CustomerOrder("ORD-A", "PART_A", "A", "10/15", datetime(2026, 10, 15, 15, 0), 10.0)

        snap = ProductionInputSnapshot(
            fg_params={
                "PART_A": ProductParam("WL", "L1", "PART_A", "A", 10.0, 1.0, 1.0, 2880.0),
                "PART_B": ProductParam("WL", "L1", "PART_B", "B", 10.0, 1.0, 1.0, 2880.0),
            },
            sf_params={
                "SF_1": ProductParam("WL", "SF_LINE", "SF_1", "SF", 10.0, 1.0, 100.0, 28800.0),
            },
            bom={
                "PART_A": [("SF_1", 1.0)],
                "PART_B": [("SF_1", 1.0)],
            },
            initial_stock={},
            orders=[o_a, o_b],
            planning_dates=["10/12", "10/13", "10/14", "10/15"],
        )
        plan = build_requirement_plan(snap)

        sf_task_for_b = next((t for t in plan.sf_tasks if "ORD-B" in t.parent_task_id), None)
        self.assertIsNotNone(sf_task_for_b)

        scheduler = APSScheduler(plan, enforce_utilization_95=False)
        res = scheduler.solve()
        self.assertIn(res.status_name, ("OPTIMAL", "FEASIBLE"))

        index_of = {(s.date_str, s.shift_name): s.index for s in res.shifts}
        t_sf = next(t for t in res.tasks if t.task_id == sf_task_for_b.task_id)
        t_a = next(t for t in res.tasks if "ORD-A" in t.task_id and t.stage == "FG")
        t_b = next(t for t in res.tasks if "ORD-B" in t.task_id and t.stage == "FG")

        sf_slots = [index_of[k] for k in t_sf.shift_breakdown]
        a_slots = [index_of[k] for k in t_a.shift_breakdown]
        b_slots = [index_of[k] for k in t_b.shift_breakdown]

        min_sf = min(sf_slots)
        min_a = min(a_slots)
        min_b = min(b_slots)

        # Under time-phased material ledger, neither FG A nor FG B can run in or before SF slot
        self.assertGreater(min_a, min_sf, "FG task A was scheduled before or during SF task, borrowing from future!")
        self.assertGreater(min_b, min_sf, "FG task B was scheduled before or during SF task!")

    def test_reg02_pm_and_arrival_absolute_time_mapping(self):
        """REG-02: PM and arrival slots must be resolved against absolute dates/calendar, not table column indices."""
        from src.data_loader import PreventiveMaintenance

        pm = PreventiveMaintenance(
            site="WL",
            pm_plan_no="PM-TEST-01",
            line="PGLINE",
            equipment_name="Eq 1",
            pm_type="Routine",
            start_date="2026-10-15",
            start_shift="晚班",
            end_date="2026-10-15",
            end_shift="晚班",
            downtime_hours=8.0,
            capacity_rate=0.0,
            handling_strategy="避让顺延",
        )

        dates_a = ["10/12", "10/13", "10/14", "10/15", "10/16"]
        snap_a = ProductionInputSnapshot(
            fg_params={},
            sf_params={},
            bom={},
            initial_stock={},
            orders=[],
            planning_dates=dates_a,
            pm_schedules=[pm],
        )
        plan_a = build_requirement_plan(snap_a)
        topology = get_topology("WL")
        # In WL 3 shifts/day: Day 3 (10/15)晚班 = 3*3 + 2 = slot 11
        slot_1015_night = topology.slot_index(3, "晚班")
        self.assertEqual(slot_1015_night, 11)

    def test_reg03_safety_stock_deficit_recovery_formula(self):
        """REG-03: Safety stock deficit must be replenished: opening=20, target=100, demand=10 -> produce 90."""
        fg_param = ProductParam(
            site="WL",
            line="PGLINE",
            part_no="TEST_FG_SS",
            description="SS Test Part",
            ct_seconds=10.0,
            trp=1.0,
            mpq=1.0,
            output_per_shift=2880.0,
            target_stock=100.0,
            target_stock_days=0.0,
            headcount=1.0,
        )
        snap = ProductionInputSnapshot(
            fg_params={"TEST_FG_SS": fg_param},
            sf_params={},
            bom={"TEST_FG_SS": []},
            initial_stock={"TEST_FG_SS": 20.0},
            orders=[
                CustomerOrder(
                    order_id="ORD_SS_01",
                    part_no="TEST_FG_SS",
                    description="SS Test",
                    due_date="10/15",
                    due_datetime=datetime(2026, 10, 15, 15, 0),
                    demand_quantity=10.0,
                )
            ],
            planning_dates=["10/12", "10/13", "10/14", "10/15"],
        )

        plan = build_requirement_plan(snap)
        self.assertEqual(len(plan.fg_tasks), 1)
        task = plan.fg_tasks[0]
        # Target stock = 100, opening = 20, demand = 10 -> produced is 90 (20 + 90 - 10 = 100)
        self.assertEqual(task.quantity, 90.0)

    def test_reg04_unknown_part_or_missing_master_data_raises_invalid_input(self):
        """REG-04: Orders with missing master data must be recorded as invalid orders, not silently skipped."""
        snap = load_aps_database("dataSource.xlsx", site_filter="WL")
        bad_order = CustomerOrder(
            order_id="ORD_UNKNOWN_TEST",
            part_no="UNKNOWN_PART_NONEXISTENT",
            description="Nonexistent Part",
            due_date="10/15",
            due_datetime=datetime(2026, 10, 15, 15, 0),
            demand_quantity=100.0,
        )
        snap.orders = [bad_order]

        plan = build_requirement_plan(snap)
        self.assertTrue(plan.has_invalid_orders)
        self.assertIn("ORD_UNKNOWN_TEST", plan.rejected_order_ids)

    def test_reg05_freeze_window_enforced_against_baseline(self):
        """REG-05: Baseline schedule inside the freeze window (first N shifts) must be locked."""
        o1 = CustomerOrder("ORD-1", "PART_A", "A", "10/16", datetime(2026, 10, 16, 15, 0), 10.0)
        o2 = CustomerOrder("ORD-2", "PART_B", "B", "10/16", datetime(2026, 10, 16, 15, 0), 10.0)

        snap = ProductionInputSnapshot(
            fg_params={
                "PART_A": ProductParam("WL", "L1", "PART_A", "A", 10.0, 1.0, 1.0, 2880.0),
                "PART_B": ProductParam("WL", "L1", "PART_B", "B", 10.0, 1.0, 1.0, 2880.0),
            },
            sf_params={},
            bom={},
            initial_stock={},
            orders=[o1, o2],
            planning_dates=["10/12", "10/13", "10/14", "10/15", "10/16"],
        )
        plan = build_requirement_plan(snap)

        t1 = next(t for t in plan.fg_tasks if "ORD-1" in t.order_id)
        t2 = next(t for t in plan.fg_tasks if "ORD-2" in t.order_id)

        # Baseline plan had Task 1 locked in shift 1 ("10/12 中班")
        base_task1 = ScheduledTask(
            task_id=t1.task_id,
            stage="FG",
            line="L1",
            part_no="PART_A",
            description="A",
            ct_seconds=10.0,
            trp=1.0,
            mpq=1,
            lot_size=1,
            order_id="ORD-1",
            quantity=10.0,
            lots=10,
            start_dt=datetime(2026, 10, 12, 15, 0),
            end_dt=datetime(2026, 10, 12, 23, 0),
            duration_hours=8.0,
            shift_breakdown={("10/12", "中班"): 10},
        )

        scheduler = APSScheduler(
            plan,
            enforce_utilization_95=False,
            enforce_freeze_window=True,
            baseline_tasks=[base_task1],
            freeze_shifts=3,
        )
        res = scheduler.solve()
        self.assertIn(res.status_name, ("OPTIMAL", "FEASIBLE"))

        solved_t1 = next(t for t in res.tasks if t.task_id == t1.task_id)
        solved_t2 = next(t for t in res.tasks if t.task_id == t2.task_id)

        # Task 1 must strictly retain its baseline allocation in the frozen window
        self.assertEqual(solved_t1.shift_breakdown.get(("10/12", "中班")), 10)
        # Newly inserted Task 2 cannot be scheduled inside frozen shifts (0, 1, 2)
        frozen_keys = {("10/12", "早班"), ("10/12", "中班"), ("10/12", "晚班")}
        for key in frozen_keys:
            self.assertEqual(solved_t2.shift_breakdown.get(key, 0), 0, f"New task leaked into frozen shift {key}")

    def test_reg06_due_date_audit_catches_early_order_delay(self):
        """REG-06: Independent audit must check individual order due dates, not just latest_due per part."""
        o1 = CustomerOrder("ORD-1", "PART_A", "Part A", "10/12", datetime(2026, 10, 12, 15, 0), 10.0)
        o2 = CustomerOrder("ORD-2", "PART_A", "Part A", "10/16", datetime(2026, 10, 16, 15, 0), 10.0)
        snap = ProductionInputSnapshot(
            fg_params={"PART_A": ProductParam("WL", "L1", "PART_A", "Part A", 10.0, 10.0, 1.0, 0.0, 0.0, 1.0, 2880.0)},
            sf_params={},
            bom={},
            initial_stock={},
            orders=[o1, o2],
            planning_dates=["10/12", "10/13", "10/14", "10/15", "10/16"],
        )

        shift_1015 = ShiftSlot(
            slot_index=11,
            date_str="10/15",
            shift_name="晚班",
            start_dt=datetime(2026, 10, 15, 15, 0),
            end_dt=datetime(2026, 10, 15, 23, 0),
            duration_hours=8.0,
        )
        shift_by_key = {shift_1015.key: shift_1015}
        rows = [PlanRow("T1", "FG", "L1", "PART_A", "ORD-1", shift_1015.key, 20.0)]

        # With strict FIFO matching (REG-06 fixed), Order 1 (due 10/12) is caught as late (+80.0h)
        audit_check = _check_due_part(rows, shift_by_key, snap, AuditConfig())
        self.assertFalse(audit_check.passed, "Audit must fail because Order 1 was late by 3 days")
        self.assertEqual(len(audit_check.violations), 1)
        self.assertIn("ORD-1", audit_check.violations[0])

    def test_reg07_exported_plan_manual_audit_uses_matching_demand_scope(self):
        """REG-07: Exporting a short horizon plan and uploading it must not fail audit due to far-future orders."""
        o1 = CustomerOrder("ORD-1", "PART_A", "A", "10/13", datetime(2026, 10, 13, 15, 0), 10.0)
        o2 = CustomerOrder("ORD-2", "PART_A", "A", "10/25", datetime(2026, 10, 25, 15, 0), 50.0)

        snap = ProductionInputSnapshot(
            fg_params={"PART_A": ProductParam("WL", "L1", "PART_A", "A", 10.0, 1.0, 1.0, 2880.0)},
            sf_params={},
            bom={},
            initial_stock={},
            orders=[o1, o2],
            planning_dates=["10/12", "10/13"],
        )

        shifts = [
            ShiftInfo("10/12 早班", 0, "10/12", "早班", datetime(2026, 10, 12, 7, 0), datetime(2026, 10, 12, 15, 0), 8.0),
            ShiftInfo("10/12 中班", 1, "10/12", "中班", datetime(2026, 10, 12, 15, 0), datetime(2026, 10, 12, 23, 0), 8.0),
            ShiftInfo("10/12 晚班", 2, "10/12", "晚班", datetime(2026, 10, 12, 23, 0), datetime(2026, 10, 13, 7, 0), 8.0),
            ShiftInfo("10/13 早班", 3, "10/13", "早班", datetime(2026, 10, 13, 7, 0), datetime(2026, 10, 13, 15, 0), 8.0),
            ShiftInfo("10/13 中班", 4, "10/13", "中班", datetime(2026, 10, 13, 15, 0), datetime(2026, 10, 13, 23, 0), 8.0),
            ShiftInfo("10/13 晚班", 5, "10/13", "晚班", datetime(2026, 10, 13, 23, 0), datetime(2026, 10, 14, 7, 0), 8.0),
        ]

        rows = [PlanRow("T1", "FG", "L1", "PART_A", "ORD-1", "10/12 早班", 10.0)]
        report = audit_schedule_part_level(snap, rows, shifts, AuditConfig(enforce_utilization=False))

        coverage_check = next(c for c in report.checks if "需求覆盖" in c.name)
        conservation_check = next(c for c in report.checks if "数量守恒" in c.name)

        self.assertTrue(coverage_check.passed, f"Coverage check failed: {coverage_check.violations}")
        self.assertTrue(conservation_check.passed, f"Conservation check failed: {conservation_check.violations}")

    def test_reg08_datasource_xlsx_explicit_path_loading(self):
        """REG-08: Explicit dataSource.xlsx must not be misidentified as a CSV directory."""
        snap = load_aps_database("dataSource.xlsx", site_filter="WL")
        self.assertGreater(
            len(snap.orders),
            0,
            "dataSource.xlsx was misidentified or read empty (REG-08)",
        )

    def test_reg09_rule_set_operator_wiring_and_gate_decoupling(self):
        """REG-09: RuleSet operators must compile and enforce even when legacy boolean flags are False."""
        from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope

        o = CustomerOrder("ORD-REG09", "PART_R9", "CUST", "10/12", datetime(2026, 10, 12, 15, 0), 100.0)
        snap = ProductionInputSnapshot(
            fg_params={
                "PART_R9": ProductParam("WL", "L1", "PART_R9", "FG", 50.0, 1.0, 1.0, 28800.0),
            },
            sf_params={},
            bom={},
            initial_stock={},
            orders=[o],
            planning_dates=["10/12"],
        )
        plan = build_requirement_plan(snap)
        rule_set = RuleSet(
            "REG09_SET",
            "WL",
            1,
            [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
            [
                RuleInstance("MB", "min_batch", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {}),
                RuleInstance("TW", "time_window", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"allowed_shifts": "早班"}),
            ],
        )

        sched = APSScheduler(
            plan,
            start_datetime=datetime(2026, 10, 12, 7, 0, 0),
            planning_days=1,
            site="WL",
            enforce_mpq_batch=False,  # Legacy flag False
            rule_set=rule_set,
        )
        model, x, aux = sched._build_model("hard", hard_due=True)

        compiled = getattr(sched, "_compiled_kernel_templates", set())
        self.assertIn("min_batch", compiled, "min_batch was blocked by legacy boolean flag")
        self.assertIn("time_window", compiled, "time_window was not compiled into scheduler")


if __name__ == "__main__":
    unittest.main()
