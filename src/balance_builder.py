"""
Balance builder for APS MVP.

Generates the two-part Material and Line Balance matrices matching
'Production Plan Validation.xlsx':
1. Line Load Overview: Loading Hours, TRP, Loading Rate (%) per line and shift.
2. Material Balance (PSI): Customer Demand, Production Plan, Stock Balance, Coverage Days
   across all planning shifts for both Finished Goods (FG) and Semi-Finished Goods (SFIN).
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.data_loader import ProductionInputSnapshot, RequirementPlan
from src.scheduler import ScheduledTask, ScheduleResult
from src.shift_manager import SHIFT_HOURS, SHIFT_SECONDS, ShiftBucket, get_topology


@dataclass
class LineLoadRow:
    site: str
    line: str
    metric: str  # "Loading Hours", "TRP", "Loading Rate"
    shift_values: Dict[str, Any]  # key: "MM/DD 早班" -> value: float or str


@dataclass
class MaterialBalanceRow:
    site: str
    line: str
    part_no: str
    description: str
    initial_stock: float
    ct_seconds: float
    trp: float
    mpq: float
    total_demand: float
    avg_daily_demand: float
    # rows of data per shift:
    demand_by_shift: Dict[str, float]
    plan_by_shift: Dict[str, float]
    stock_by_shift: Dict[str, float]
    coverage_by_shift: Dict[str, float]


@dataclass
class ValidationMatrix:
    planning_dates: List[str]
    shift_keys: List[str]  # e.g. ["10/12 早班", "10/12 中班", "10/12 晚班", ...]
    shift_columns: List[Dict[str, str]]  # [{"date": "10/12", "shift": "早班", "key": "10/12 早班"}, ...]
    line_loads: List[LineLoadRow]
    fg_balance: List[MaterialBalanceRow]
    sf_balance: List[MaterialBalanceRow]


def build_validation_matrix(
    snapshot: ProductionInputSnapshot,
    plan: RequirementPlan,
    result: ScheduleResult,
    site: str = "WL",
) -> ValidationMatrix:
    topology = get_topology(site)
    shifts = result.shifts
    # Ordered shift list
    shift_columns = []
    shift_keys = []
    seen_dates = []
    for s in shifts:
        key = f"{s.date_str} {s.shift_name}"
        shift_keys.append(key)
        shift_columns.append({"date": s.date_str, "shift": s.shift_name, "key": key})
        if s.date_str not in seen_dates:
            seen_dates.append(s.date_str)

    # 1. Build FG Production and Demand by part and shift
    fg_plan_by_part_shift = defaultdict(lambda: defaultdict(float))
    for t in result.tasks:
        if t.stage == "FG":
            for (date_str, shift_name), qty in t.shift_breakdown.items():
                fg_plan_by_part_shift[t.part_no][f"{date_str} {shift_name}"] += qty

    fg_demand_by_part_shift = defaultdict(lambda: defaultdict(float))
    for order in snapshot.orders:
        # Due on morning shift of order due date
        key = f"{order.due_date} 早班"
        fg_demand_by_part_shift[order.part_no][key] += order.demand_quantity

    # 2. Build SFIN Demand from FG plan * BOM
    sf_demand_by_part_shift = defaultdict(lambda: defaultdict(float))
    for fg_pn, shift_dict in fg_plan_by_part_shift.items():
        children = snapshot.bom.get(fg_pn, [])
        for sf_pn, ratio in children:
            for s_key, qty in shift_dict.items():
                sf_demand_by_part_shift[sf_pn][s_key] += qty * ratio

    # 3. Build SFIN Production by part and shift
    sf_plan_by_part_shift = defaultdict(lambda: defaultdict(float))
    for t in result.tasks:
        if t.stage == "SF":
            for (date_str, shift_name), qty in t.shift_breakdown.items():
                sf_plan_by_part_shift[t.part_no][f"{date_str} {shift_name}"] += qty

    # 4. Line Load Overview
    # Group tasks by line
    lines = sorted({t.line for t in result.tasks})
    line_loads = []
    for line in lines:
        line_tasks = [t for t in result.tasks if t.line == line]
        trp = line_tasks[0].trp if line_tasks else 0.85
        
        hours_by_shift = {}
        rate_by_shift = {}
        trp_by_shift = {}
        
        for s_key in shift_keys:
            # pure manufacturing seconds = sum(qty * ct_seconds)
            pure_sec = 0.0
            for t in line_tasks:
                for (date_str, shift_name), qty in t.shift_breakdown.items():
                    if f"{date_str} {shift_name}" == s_key:
                        pure_sec += qty * t.ct_seconds
            hours = pure_sec / 3600.0
            # Loading rate = Loading Hours / (Shift_Hours * TRP)
            avail_hours = topology.shift_hours * trp
            rate = (hours / avail_hours * 100.0) if avail_hours > 0 else 0.0
            
            hours_by_shift[s_key] = round(hours, 2)
            trp_by_shift[s_key] = trp
            rate_by_shift[s_key] = round(rate, 1)

        line_loads.append(LineLoadRow(site=site, line=line, metric="Loading Hours", shift_values=hours_by_shift))
        line_loads.append(LineLoadRow(site=site, line=line, metric="TRP", shift_values=trp_by_shift))
        line_loads.append(LineLoadRow(site=site, line=line, metric="Loading Rate", shift_values=rate_by_shift))

    # Helper for Material Balance rows
    num_days = max(1, len(seen_dates))
    
    def build_balance_rows(param_dict, demand_dict, plan_dict):
        rows = []
        for pn, p in sorted(param_dict.items()):
            init_stock = snapshot.initial_stock.get(pn, 0.0)
            cur_demand_map = demand_dict.get(pn, {})
            cur_plan_map = plan_dict.get(pn, {})
            
            total_dem = sum(cur_demand_map.values())
            # Average daily demand over 7 days or total days
            avg_daily = total_dem / 7.0 if total_dem > 0 else 0.0

            d_row = {}
            p_row = {}
            s_row = {}
            c_row = {}

            running_stock = init_stock
            for s_key in shift_keys:
                d = cur_demand_map.get(s_key, 0.0)
                p_qty = cur_plan_map.get(s_key, 0.0)
                running_stock = running_stock + p_qty - d
                
                d_row[s_key] = round(d, 0) if d else 0
                p_row[s_key] = round(p_qty, 0) if p_qty else 0
                s_row[s_key] = round(running_stock, 0)
                
                if avg_daily > 0:
                    cov = round(running_stock / avg_daily, 1)
                    c_row[s_key] = cov
                else:
                    c_row[s_key] = 999.0

            rows.append(
                MaterialBalanceRow(
                    site=p.site,
                    line=p.line,
                    part_no=pn,
                    description=p.description,
                    initial_stock=init_stock,
                    ct_seconds=p.ct_seconds,
                    trp=p.trp,
                    mpq=p.mpq,
                    total_demand=total_dem,
                    avg_daily_demand=avg_daily,
                    demand_by_shift=d_row,
                    plan_by_shift=p_row,
                    stock_by_shift=s_row,
                    coverage_by_shift=c_row,
                )
            )
        return rows

    fg_balance = build_balance_rows(snapshot.fg_params, fg_demand_by_part_shift, fg_plan_by_part_shift)
    sf_balance = build_balance_rows(snapshot.sf_params, sf_demand_by_part_shift, sf_plan_by_part_shift)

    return ValidationMatrix(
        planning_dates=seen_dates,
        shift_keys=shift_keys,
        shift_columns=shift_columns,
        line_loads=line_loads,
        fg_balance=fg_balance,
        sf_balance=sf_balance,
    )
