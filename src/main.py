"""
Main pipeline for the APS MVP.

Reads 'APS Database- Simulation draft.xlsx', nets demand against stock, runs
pre-solver macro capacity precheck, solves the two-stage shift-slot schedule with
CP-SAT, writes 'Production plan-Done', then re-verifies the result against the
WE34 hard/soft constraints and outputs the 30-rule compliance matrix.
"""
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import build_requirement_plan, load_aps_database
from src.exporter import export_production_plan
from src.prechecker import format_precheck_report, precheck_capacity
from src.scheduler import APSScheduler
from src.shift_manager import (
    CalendarMode,
    MIN_SHIFT_UTILIZATION,
    SHIFT_SECONDS,
    decide_calendar_mode,
    generate_shift_calendar,
)
from src.validator import (
    format_report,
    format_rule_compliance_matrix,
    validate_schedule,
)

SITE = "WL"
START_DT = datetime(2026, 10, 12, 7, 0, 0)
SLACK_DAYS = 3
TIME_LIMIT_SECONDS = 90.0


def main():
    data_dir = os.path.join(BASE_DIR, "data")
    excel_path = os.path.join(BASE_DIR, "APS Database- Simulation draft.xlsx")
    backup_path = os.path.join(BASE_DIR, "APS Database- Simulation draft.backup.xlsx")
    if os.path.exists(excel_path) and not os.path.exists(backup_path):
        shutil.copyfile(excel_path, backup_path)
        print(f"Created backup at {backup_path}")

    source = data_dir if os.path.exists(data_dir) else excel_path
    print(f"Loading data from {source} for Site {SITE}...")
    snapshot = load_aps_database(source, site_filter=SITE)

    # 10-day standard horizon for deterministic scheduling
    horizon_days = 10
    horizon_end = START_DT + timedelta(days=horizon_days)
    snapshot.orders = [o for o in snapshot.orders if o.due_datetime <= horizon_end]
    plan = build_requirement_plan(snapshot)
    print(
        f"Loaded {len(snapshot.orders)} demand lines -> {len(plan.fg_tasks)} FG tasks "
        f"+ {len(plan.sf_tasks)} semi-finished tasks."
    )
    for note in plan.covered_by_stock:
        print(f"  [stock] {note}")
    for warning in plan.warnings:
        print(f"  [warn]  {warning}")

    planning_days = horizon_days

    # 1. Macro capacity precheck & 3-tier calendar decision (R-CL-01..03, R-EX-04, D-08)
    initial_calendar_shifts = generate_shift_calendar(
        START_DT, planning_days, calendar_mode=CalendarMode.SEVEN_ZERO
    )
    precheck = precheck_capacity(plan, initial_calendar_shifts)
    print(format_precheck_report(precheck))

    # Evaluate 3-tier calendar recommendation
    total_fg_hours = sum(t.lots * t.lot_millis / 1000.0 for t in plan.fg_tasks) / 3600.0
    full_7day_capacity = (planning_days * 3 * SHIFT_SECONDS) / 3600.0
    cal_mode = decide_calendar_mode(total_fg_hours, full_7day_capacity)
    print(
        f"\n[日历决策] 7日标准产能 {full_7day_capacity:.1f}h，需求工时 {total_fg_hours:.1f}h "
        f"(负荷比 {total_fg_hours / full_7day_capacity * 100:.1f}%) -> 推荐日历模式: {cal_mode.value}"
    )

    # In production, we run the selected mode (defaulting to SEVEN_ZERO for full coverage)
    active_mode = CalendarMode.SEVEN_ZERO

    print(f"\nInitializing OR-Tools CP-SAT shift-slot scheduler ({planning_days} days, mode={active_mode.value})...")
    scheduler = APSScheduler(
        plan,
        start_datetime=START_DT,
        planning_days=planning_days,
        calendar_mode=active_mode,
    )
    print(f"Solving mathematical optimization model (time limit = {TIME_LIMIT_SECONDS:.0f}s)...")
    result = scheduler.solve(time_limit_seconds=TIME_LIMIT_SECONDS)

    export_path = _export(result, excel_path)

    print_report(result, plan)
    checks = validate_schedule(plan, result)
    print(format_report(checks))
    print(format_rule_compliance_matrix(checks))


def _export(result, excel_path: str) -> str:
    try:
        export_production_plan(excel_path, excel_path, result.tasks, result.shifts)
        return excel_path
    except PermissionError:
        # The source workbook is open in Excel; save elsewhere instead of failing.
        fallback = excel_path.replace(".xlsx", ".scheduled.xlsx")
        print(f"  [warn]  源文件正被 Excel 占用，排产结果改写到副本：{fallback}")
        export_production_plan(excel_path, fallback, result.tasks, result.shifts)
        return fallback


def _changeover_stats(tasks, shifts) -> dict:
    """How fragmented the plan is: production blocks and models sharing a shift."""
    index = {(s.date_str, s.shift_name): i for i, s in enumerate(shifts)}
    stats = {}
    for line in sorted({t.line for t in tasks}):
        line_tasks = [t for t in tasks if t.line == line]
        blocks = 0
        per_slot = defaultdict(int)
        for task in line_tasks:
            slots = sorted(index[key] for key in task.shift_breakdown)
            blocks += sum(1 for n, s in enumerate(slots) if n == 0 or s != slots[n - 1] + 1)
            for s in slots:
                per_slot[s] += 1
        stats[line] = (blocks, len(line_tasks), max(per_slot.values(), default=0))
    return stats


def print_report(result, plan) -> None:
    tasks = result.tasks
    fg_tasks = [t for t in tasks if t.stage == "FG"]
    sf_tasks = [t for t in tasks if t.stage == "SF"]

    loads = defaultdict(float)
    for task in tasks:
        for key, seconds in task.shift_seconds.items():
            loads[(task.line, key)] += seconds

    floor = MIN_SHIFT_UTILIZATION - 1e-9
    utilisation: dict = {}
    for line in sorted({t.line for t in tasks}):
        line_loads = {key[1]: seconds for key, seconds in loads.items() if key[0] == line}
        worked = [(s.date_str, s.shift_name) for s in result.shifts if (s.date_str, s.shift_name) in line_loads]
        closing = worked[-1] if worked else None
        below = [key for key in worked[:-1] if line_loads[key] / SHIFT_SECONDS < floor]
        utilisation[line] = (line_loads, worked, closing, below)

    gaps = sum(len(entry[3]) for entry in utilisation.values())
    if result.strict_utilization:
        status = "硬约束（全部满足）"
    else:
        status = "软约束（搜索后全部达标）" if not gaps else f"软约束（{gaps} 个班次未达 95%，见校验报告）"

    print("\n" + "=" * 70)
    print("                 APS MVP SCHEDULE KPI REPORT                 ")
    print("=" * 70)
    print(f"Total FG Tasks (PGLINKAGE): {len(fg_tasks)}")
    print(f"Total SF Tasks (轴套6):    {len(sf_tasks)}")
    print(f"Total Production Tasks:   {len(tasks)}")
    print(f"Utilization constraint:   {status}")

    min_start = min(t.start_dt for t in tasks)
    max_end = max(t.end_dt for t in tasks)
    span_hours = (max_end - min_start).total_seconds() / 3600.0
    print(f"\nSchedule Start: {min_start:%Y-%m-%d %H:%M}")
    print(f"Schedule End:   {max_end:%Y-%m-%d %H:%M}")
    print(f"Total Makespan: {span_hours:.1f} hours ({span_hours / 24:.1f} days)")

    print("\n" + "-" * 70)
    print("                    班次产能利用率                        ")
    print("-" * 70)
    for line, (line_loads, worked, closing, below) in utilisation.items():
        total_seconds = sum(line_loads.values())
        print(
            f"{line:<12} 开工班次 {len(worked):>3} | 投入 {total_seconds / 3600:>6.1f}h | "
            f"平均利用率 {total_seconds / (len(worked) * SHIFT_SECONDS) * 100:>5.1f}% | <95% 班次数 {len(below)}"
        )
        for key in below:
            hours = line_loads[key] / 3600.0
            print(f"             - {key[0]} {key[1]}: {hours:.2f}h ({hours / (SHIFT_SECONDS / 3600) * 100:.1f}%)")
        if closing and line_loads[closing] / SHIFT_SECONDS < floor:
            print(f"             - {closing[0]} {closing[1]}: 尾班收线，豁免 95% 要求")

    print("\n" + "-" * 70)
    print("                    换型集中度                            ")
    print("-" * 70)
    for line, (blocks, task_count, peak) in _changeover_stats(tasks, result.shifts).items():
        print(
            f"{line:<12} 型号生产块 {blocks:>3}（任务数 {task_count:>3}，一型号一块时为 {task_count}） | "
            f"单班并行型号峰值 {peak}"
        )

    late = [t for t in fg_tasks if t.end_dt > t.due_datetime]
    print("\n" + "-" * 70)
    print("                    交付表现                              ")
    print("-" * 70)
    for task in sorted(fg_tasks, key=lambda t: t.start_dt):
        delay = (task.end_dt - task.due_datetime).total_seconds() / 3600.0
        status = f"LATE (+{delay:.1f}h) [!]" if delay > 0 else "ON TIME [OK]"
        print(
            f"{task.order_id:<24} | Qty: {task.quantity:>6.0f} | Start: {task.start_dt:%m/%d %H:%M} | "
            f"End: {task.end_dt:%m/%d %H:%M} | Due: {task.due_datetime:%m/%d %H:%M} | {status}"
        )
    on_time = len(fg_tasks) - len(late)
    print("\n" + "-" * 70)
    print(f"On-Time Delivery Rate: {on_time / len(fg_tasks) * 100:.1f}% ({on_time}/{len(fg_tasks)})")
    print("=" * 70)


if __name__ == "__main__":
    main()
