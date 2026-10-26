"""Tests for the independent audit (ACT-09 / ACT-10).

The audit must fail when a planner deletes a row, changes a quantity, or
overloads a shift. A self-fulfilling audit that only passes is worse than none.
"""
import copy
import os
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.audit import AuditConfig, PlanRow, ShiftInfo, audit_schedule
from src.data_loader import build_requirement_plan, load_aps_database
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode, DEFAULT_CHANGEOVER_MINUTES

DATA_DIR = os.path.join(BASE_DIR, "data")
START_DT = datetime(2026, 10, 12, 7, 0, 0)
HORIZON_DAYS = 10


def _rows_from_result(result):
    rows = []
    for task in result.tasks:
        for (date_str, shift_name), qty in task.shift_breakdown.items():
            rows.append(
                PlanRow(
                    task_id=task.task_id,
                    stage=task.stage,
                    line=task.line,
                    part_no=task.part_no,
                    order_id=task.order_id,
                    shift_key=f"{date_str} {shift_name}",
                    quantity=float(qty),
                )
            )
    return rows


def _shifts_from_result(result):
    return [
        ShiftInfo(
            key=f"{s.date_str} {s.shift_name}",
            index=s.index,
            date_str=s.date_str,
            shift_name=s.shift_name,
            start_dt=s.start_dt,
            end_dt=s.end_dt,
            duration_hours=s.duration_hours,
        )
        for s in result.shifts
    ]


def _audit(snapshot, rows, shifts):
    return audit_schedule(
        snapshot,
        rows,
        shifts,
        AuditConfig(
            setup_minutes=DEFAULT_CHANGEOVER_MINUTES,
            allow_105=False,
            delivery_tolerance_minutes=0.0,
        ),
    )


class TestIndependentAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snap = load_aps_database(DATA_DIR, site_filter="WL")
        snap.orders = [o for o in snap.orders if o.due_datetime <= START_DT + timedelta(days=HORIZON_DAYS)]
        plan = build_requirement_plan(snap)
        sched = APSScheduler(
            plan,
            start_datetime=START_DT,
            planning_days=HORIZON_DAYS,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
        )
        result = sched.solve(time_limit_seconds=30.0)
        cls.snapshot = snap
        cls.plan = plan
        cls.result = result
        cls.rows = _rows_from_result(result)
        cls.shifts = _shifts_from_result(result)

    def _assert_failed(self, report, expected_check):
        self.assertFalse(report.passed, "audit should have failed")
        self.assertIn(expected_check, report.failed_names())

    def test_01_baseline_passes(self):
        report = _audit(self.snapshot, self.rows, self.shifts)
        self.assertTrue(report.passed, report.format_text())
        self.assertEqual(report.expected_tasks, len(self.plan.tasks))
        self.assertEqual(report.expected_tasks, report.actual_tasks)

    def test_02_deleted_order_is_caught(self):
        victim = next(r for r in self.rows if r.stage == "FG")
        rows = [r for r in self.rows if not (r.stage == "FG" and r.order_id == victim.order_id)]
        report = _audit(self.snapshot, rows, self.shifts)
        self._assert_failed(report, "需求覆盖完整性（每条需求都必须有对应工单）")

    def test_03_reduced_quantity_is_caught(self):
        victim_index = next(i for i, r in enumerate(self.rows) if r.stage == "FG")
        victim = self.rows[victim_index]
        lot = int(round(self.snapshot.fg_params[victim.part_no].mpq))
        rows = list(self.rows)
        rows[victim_index] = PlanRow(
            victim.task_id,
            victim.stage,
            victim.line,
            victim.part_no,
            victim.order_id,
            victim.shift_key,
            victim.quantity - lot,
        )
        report = _audit(self.snapshot, rows, self.shifts)
        self._assert_failed(report, "班次拆解守恒（各工单分班产量之和 = 净需求）")

    def test_04_overloaded_shift_is_caught(self):
        victim = next(r for r in self.rows if r.stage == "FG")
        lot = int(round(self.snapshot.fg_params[victim.part_no].mpq))
        rows = list(self.rows)
        rows.append(
            PlanRow(
                "OVERLOAD",
                victim.stage,
                victim.line,
                victim.part_no,
                victim.order_id,
                victim.shift_key,
                lot * 1000.0,
            )
        )
        report = _audit(self.snapshot, rows, self.shifts)
        self._assert_failed(report, "单班物理容量（加工 + 换型 ≤ 班次工时）")

    def test_05_audit_does_not_import_scheduler(self):
        import ast

        audit_dir = os.path.join(BASE_DIR, "src", "audit")
        for name in os.listdir(audit_dir):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(audit_dir, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").endswith("scheduler"), f"{name} imports scheduler")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.endswith("scheduler"), f"{name} imports scheduler")
                elif isinstance(node, ast.Name):
                    self.assertNotIn(node.id, ("ScheduledTask", "ScheduleResult"), f"{name} uses {node.id}")

    def test_06_mutation_matrix(self):
        """20 injected defects: each must be caught by the expected check."""
        rows = list(self.rows)
        shifts = self.shifts
        snap = self.snapshot
        ordered = [s.key for s in sorted(shifts, key=lambda s: s.index)]
        last_shift = ordered[-1]
        fg = [r for r in rows if r.stage == "FG"]
        sf = [r for r in rows if r.stage == "SF"]
        first_fg = fg[0]
        first_sf = sf[0]
        fg_lot = int(round(snap.fg_params[first_fg.part_no].mpq))
        sf_lot = int(round(snap.sf_params[first_sf.part_no].mpq))
        fg_key = (first_fg.stage, first_fg.order_id, first_fg.part_no)
        fg_key_rows = [r for r in rows if (r.stage, r.order_id, r.part_no) == fg_key]
        fg_total = sum(r.quantity for r in fg_key_rows)
        sf_key = (first_sf.stage, first_sf.order_id, first_sf.part_no)
        sf_key_rows = [r for r in rows if (r.stage, r.order_id, r.part_no) == sf_key]

        def case(name, mutated, expected, cfg=None):
            report = audit_schedule(snap, mutated, shifts, cfg or AuditConfig())
            self.assertFalse(report.passed, f"{name}: audit should fail\n{report.format_text()}")
            self.assertIn(expected, report.failed_names(), f"{name}: expected {expected}, got {report.failed_names()}")

        # 1-2: a whole work order disappears
        case("delete FG", [r for r in rows if (r.stage, r.order_id, r.part_no) != fg_key],
             "需求覆盖完整性（每条需求都必须有对应工单）")
        case("delete SF", [r for r in rows if (r.stage, r.order_id, r.part_no) != sf_key],
             "需求覆盖完整性（每条需求都必须有对应工单）")

        # 3-4: quantity does not match the net requirement
        idx = rows.index(fg_key_rows[0])
        case("reduce FG", rows[:idx] + [replace(rows[idx], quantity=rows[idx].quantity - fg_lot)] + rows[idx + 1:],
             "班次拆解守恒（各工单分班产量之和 = 净需求）")
        case("increase FG", rows[:idx] + [replace(rows[idx], quantity=rows[idx].quantity + fg_lot)] + rows[idx + 1:],
             "班次拆解守恒（各工单分班产量之和 = 净需求）")

        # 5-7: MPQ / integer violations
        case("non-integer", rows[:idx] + [replace(rows[idx], quantity=rows[idx].quantity + 0.5)] + rows[idx + 1:],
             "MPQ 与整数件（独立重算）")
        case("FG not MPQ multiple", rows[:idx] + [replace(rows[idx], quantity=rows[idx].quantity + 1)] + rows[idx + 1:],
             "MPQ 与整数件（独立重算）")
        sidx = rows.index(sf_key_rows[0])
        case("SF below MPQ", rows[:sidx] + [replace(rows[sidx], quantity=1.0)] + rows[sidx + 1:],
             "MPQ 与整数件（独立重算）")

        # 8-10: malformed rows
        case("unknown shift", rows[:idx] + [replace(rows[idx], shift_key="99/99 早班")] + rows[idx + 1:],
             "排程行有效性（班次存在、数量为正、产线归属正确）")
        other_line = next((r.line for r in rows if r.line != first_fg.line), first_fg.line)
        case("wrong line", rows[:idx] + [replace(rows[idx], line=other_line)] + rows[idx + 1:],
             "排程行有效性（班次存在、数量为正、产线归属正确）")
        case("negative qty", rows[:idx] + [replace(rows[idx], quantity=-1.0)] + rows[idx + 1:],
             "排程行有效性（班次存在、数量为正、产线归属正确）")

        # 11: overload one shift
        case("overload", rows + [replace(first_fg, task_id="OVERLOAD", quantity=fg_lot * 1000.0)],
             "单班物理容量（加工 + 换型 ≤ 班次工时）")

        # 12: ship after the due date
        late_rows = [r for r in rows if (r.stage, r.order_id, r.part_no) != fg_key]
        late_rows.append(replace(first_fg, shift_key=last_shift, quantity=fg_total))
        case("late FG", late_rows, "交付达成（按班次结束时间独立重算）")

        # 13: starve the line (semi-finished production removed)
        case("starve SF", rows[:sidx] + [replace(rows[sidx], quantity=1.0)] + rows[sidx + 1:],
             "物料齐套（前道产出先于后道消耗，独立仿真）")

        # 14: split a task into two blocks
        multi = None
        for key in {(r.stage, r.order_id, r.part_no) for r in rows}:
            key_rows = sorted((r for r in rows if (r.stage, r.order_id, r.part_no) == key),
                              key=lambda r: shifts.index(next(s for s in shifts if s.key == r.shift_key)))
            if len(key_rows) >= 3:
                multi = key_rows
                break
        if multi:
            moved = replace(multi[-1], shift_key=last_shift)
            gap_rows = [moved if r is multi[-1] else r for r in rows]
            case("split task", gap_rows, "工单连续性（默认仅统计，可配置硬上限）",
                 AuditConfig(max_blocks_per_task=1))

        # 15: open a shift with a token load (below 95%, not the tail)
        used = {(r.line, r.shift_key) for r in rows}
        line_last = {}
        for r in rows:
            line_last[r.line] = max(line_last.get(r.line, -1), ordered.index(r.shift_key))
        victim_shift = next(
            (k for i, k in enumerate(ordered)
             if i < line_last.get(first_fg.line, 0) and (first_fg.line, k) not in used),
            None,
        )
        if victim_shift:
            case("underfill shift", rows + [replace(first_fg, task_id="TOKEN", shift_key=victim_shift, quantity=fg_lot)],
                 "班次利用率（尾班豁免，独立重算）")

        # 16-20: duplicates, zero rows, unexpected orders, removed part
        case("duplicate row", rows + [rows[idx]], "班次拆解守恒（各工单分班产量之和 = 净需求）")
        case("zero row", rows[:idx] + [replace(rows[idx], quantity=0.0)] + rows[idx + 1:],
             "排程行有效性（班次存在、数量为正、产线归属正确）")
        case("unexpected order", rows[:idx] + [replace(rows[idx], order_id="ORD_UNKNOWN")] + rows[idx + 1:],
             "需求覆盖完整性（每条需求都必须有对应工单）")
        case("remove part", [r for r in rows if r.part_no != first_fg.part_no],
             "需求覆盖完整性（每条需求都必须有对应工单）")
        case("wrong stage", rows[:idx] + [replace(rows[idx], stage="SF")] + rows[idx + 1:],
             "排程行有效性（班次存在、数量为正、产线归属正确）")


if __name__ == "__main__":
    unittest.main()
