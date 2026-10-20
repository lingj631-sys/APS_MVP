"""
FastAPI Backend Server for APS MVP.

Provides REST APIs for:
1. Input Master Data & Customer Demand (view and edit).
2. Rule Registry & factory-based constraint selection.
3. Solver execution (OR-Tools CP-SAT) with configurable rule toggles.
4. Output schedule & PSI Material Balance Matrix (referencing Production Plan Validation.xlsx).
5. Excel export of production plans.
"""
import copy
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.balance_builder import build_validation_matrix
from src.data_loader import (
    CustomerOrder,
    ProductionInputSnapshot,
    RequirementPlan,
    build_requirement_plan,
    load_aps_database,
)
from src.exporter import export_production_plan, export_validation_matrix_excel
from src.prechecker import precheck_capacity
from src.rules import REGISTRY, RuleCategory, RuleStatus, get_rules_for_site
from src.scheduler import APSScheduler, ScheduleResult
from src.shift_manager import CalendarMode, evaluate_calendar_recommendation
from src.validator import validate_schedule

EXCEL_PATH = os.path.join(BASE_DIR, "APS Database- Simulation draft.xlsx")
SITE = "WL"

app = FastAPI(title="APS MVP 智能高级排产系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Default and persistent settings per factory site
DEFAULT_SITE_SETTINGS: Dict[str, Dict[str, Any]] = {
    "WL": {
        "site": "WL",
        "site_name": "WL 工厂 (WE34 车间 - 雨刮/轴套)",
        "horizon_days": 10,
        "start_date": "2026-10-12",
        "baseline_date": "2026-10-11",
        "calendar_mode": "SEVEN_ZERO",
        "lookahead_enabled": True,
        "lookahead_days": 30,
        "max_pull_forward_days": 10,
        "campaign_merge_enabled": True,
        "day_shift_overrides": {},
    },
    "SY38": {
        "site": "SY38",
        "site_name": "SY38 工厂 (沈阳产线)",
        "horizon_days": 10,
        "start_date": "2026-10-12",
        "baseline_date": "2026-10-11",
        "calendar_mode": "FIVE_TWO",
        "lookahead_enabled": False,
        "lookahead_days": 30,
        "max_pull_forward_days": 10,
        "campaign_merge_enabled": False,
        "day_shift_overrides": {},
    },
}

site_settings_store: Dict[str, Dict[str, Any]] = copy.deepcopy(DEFAULT_SITE_SETTINGS)

def apply_demand_lookahead(
    snapshot: ProductionInputSnapshot,
    start_dt: datetime,
    horizon_days: int = 10,
    lookahead_days: int = 30,
    max_pull_forward_days: int = 10,
    max_line_utilization: float = 0.98,
    shifts: Optional[List[Any]] = None,
) -> Tuple[ProductionInputSnapshot, Dict[str, Any]]:
    """
    30-Day Demand Lookahead & Campaign Batching Engine:
    1. Scope immediate orders within horizon_days.
    2. Scope candidate future orders up to min(lookahead_days, horizon_days + max_pull_forward_days).
    3. Identify active parts in immediate horizon (avoiding introducing new tool setups/changeovers).
    4. For active parts, evaluate line capacity headroom and pull forward future orders
       chronologically to build economic batches and pre-build inventory for month-end peak.
    """
    horizon_end = start_dt + timedelta(days=horizon_days)
    lookahead_end = start_dt + timedelta(days=lookahead_days)
    max_pull_end = min(lookahead_end, horizon_end + timedelta(days=max_pull_forward_days))

    # Calculate line capacity headroom (in hours)
    line_avail_hours: Dict[str, float] = defaultdict(float)
    if shifts:
        for s in shifts:
            if getattr(s, "is_work_shift", True):
                for p in snapshot.fg_params.values():
                    line_avail_hours[p.line] += 8.0
    else:
        for p in snapshot.fg_params.values():
            line_avail_hours[p.line] = float(horizon_days * 24.0)

    # Separate orders
    immediate_orders = [copy.deepcopy(o) for o in snapshot.orders if o.due_datetime <= horizon_end]
    future_orders = [copy.deepcopy(o) for o in snapshot.orders if horizon_end < o.due_datetime <= max_pull_end]

    # Calculate load of immediate orders
    line_used_hours: Dict[str, float] = defaultdict(float)
    for o in immediate_orders:
        param = snapshot.fg_params.get(o.part_no)
        if param and param.ct_seconds > 0 and param.trp > 0:
            hrs = (o.demand_quantity * param.ct_seconds) / (3600.0 * param.trp)
            line_used_hours[param.line] += hrs

    active_parts = set(o.part_no for o in immediate_orders)
    candidates = [o for o in future_orders if o.part_no in active_parts]
    candidates.sort(key=lambda x: (x.due_datetime, x.part_no))

    pulled_orders = []
    pulled_details = []
    merged_by_part: Dict[str, float] = defaultdict(float)

    for o in candidates:
        param = snapshot.fg_params.get(o.part_no)
        if not param:
            continue
        req_hrs = (o.demand_quantity * param.ct_seconds) / (3600.0 * param.trp) if (param.ct_seconds > 0 and param.trp > 0) else 0.0
        line = param.line
        cap_limit = line_avail_hours[line] * max_line_utilization

        if line_used_hours[line] + req_hrs <= cap_limit or (line_used_hours[line] == 0):
            line_used_hours[line] += req_hrs
            pulled_o = copy.deepcopy(o)
            pulled_o.order_id = f"PULL_{o.order_id}"
            # Adjust due datetime to the end of the horizon window
            pulled_o.due_datetime = horizon_end - timedelta(hours=1)
            pulled_o.due_date = (horizon_end - timedelta(days=1)).strftime("%m/%d")
            pulled_orders.append(pulled_o)
            merged_by_part[o.part_no] += o.demand_quantity
            pulled_details.append({
                "original_order_id": o.order_id,
                "pulled_order_id": pulled_o.order_id,
                "part_no": o.part_no,
                "description": o.description,
                "quantity": o.demand_quantity,
                "original_due_date": o.due_date,
                "target_due_date": pulled_o.due_date,
                "required_hours": round(req_hrs, 1),
            })

    scoped_snapshot = copy.deepcopy(snapshot)
    scoped_snapshot.orders = immediate_orders + pulled_orders

    summary = {
        "is_enabled": True,
        "horizon_days": horizon_days,
        "lookahead_days": lookahead_days,
        "max_pull_forward_days": max_pull_forward_days,
        "immediate_orders_count": len(immediate_orders),
        "pulled_orders_count": len(pulled_orders),
        "pulled_quantity_total": sum(o.demand_quantity for o in pulled_orders),
        "active_parts_count": len(active_parts),
        "pulled_parts_count": len(merged_by_part),
        "pulled_details": pulled_details,
        "pulled_by_part": dict(merged_by_part),
        "line_utilization_after_pull": {
            l: round(line_used_hours[l] / max(1.0, line_avail_hours[l]) * 100, 1)
            for l in line_avail_hours
        },
        "changeovers_saved_estimate": len(pulled_orders),
    }

    return scoped_snapshot, summary

# In-memory storage for active session
session_state = {
    "site": SITE,
    "snapshot": None,
    "plan": None,
    "result": None,
    "matrix": None,
    "checks": None,
    "last_solve_time": None,
    "calendar_config": {
        "baseline_date": "2026-10-11",
        "start_date": "2026-10-12",
        "horizon_days": 10,
        "calendar_mode": "SEVEN_ZERO",
        "day_shift_overrides": {},
    },
}


def get_fresh_snapshot(site: str = "WL") -> ProductionInputSnapshot:
    return load_aps_database(EXCEL_PATH, site_filter=site)


def generate_calendar_view(
    start_date_str: str,
    horizon_days: int,
    mode_str: str,
    overrides: Dict[str, Any],
    snap: Optional[ProductionInputSnapshot] = None,
) -> Dict[str, Any]:
    try:
        y, m, d = [int(p) for p in start_date_str.split("-")]
        start_day = datetime(y, m, d)
    except Exception:
        start_day = datetime(2026, 10, 12)

    baseline_day = start_day - timedelta(days=1)

    if mode_str == "FIVE_TWO":
        mode_working_weekdays = {0, 1, 2, 3, 4}
    elif mode_str == "FOUR_THREE":
        mode_working_weekdays = {0, 1, 2, 3}
    else:
        mode_working_weekdays = {0, 1, 2, 3, 4, 5, 6}

    days_list = []
    total_hours = 0.0
    working_days_count = 0
    rest_days_count = 0
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    for i in range(horizon_days):
        day = start_day + timedelta(days=i)
        date_str = day.strftime("%m/%d")
        full_date = day.strftime("%Y-%m-%d")
        w = day.weekday()
        is_weekend = w in (5, 6)

        day_ov = overrides.get(date_str) or overrides.get(full_date)
        if day_ov is not None:
            is_day_work = bool(day_ov.get("is_work", day_ov.get("is_workday", True)))
            shifts_ov = day_ov.get("shifts", {})
        else:
            is_day_work = w in mode_working_weekdays
            shifts_ov = {}

        shifts_status = {
            "早班": is_day_work and bool(shifts_ov.get("早班", True)),
            "中班": is_day_work and bool(shifts_ov.get("中班", True)),
            "晚班": is_day_work and bool(shifts_ov.get("晚班", True)),
        }
        open_shifts_count = sum(1 for v in shifts_status.values() if v)
        day_cap_hours = open_shifts_count * 8.0
        total_hours += day_cap_hours

        if open_shifts_count > 0:
            working_days_count += 1
            tag = "周末连产" if is_weekend else "正常工日"
            if open_shifts_count < 3:
                tag = f"部分开班 ({open_shifts_count}/3班)"
        else:
            rest_days_count += 1
            tag = "周末休假" if is_weekend else "停工检修"

        days_list.append({
            "index": i,
            "day_offset": f"D+{i+1}",
            "date_str": date_str,
            "full_date": full_date,
            "weekday": w,
            "weekday_name": weekday_names[w],
            "is_weekend": is_weekend,
            "is_work": (open_shifts_count > 0),
            "shifts": shifts_status,
            "open_shifts_count": open_shifts_count,
            "capacity_hours": day_cap_hours,
            "tag": tag,
        })

    horizon_end = start_day + timedelta(days=horizon_days)
    orders_in_scope = 0
    orders_out_of_scope = 0
    if snap and snap.orders:
        for o in snap.orders:
            if o.due_datetime <= horizon_end:
                orders_in_scope += 1
            else:
                orders_out_of_scope += 1

    scoped_plan = None
    if snap and snap.orders:
        scoped_snap = copy.deepcopy(snap)
        scoped_snap.orders = [o for o in scoped_snap.orders if o.due_datetime <= horizon_end]
        scoped_plan = build_requirement_plan(scoped_snap)

    ai_rec = evaluate_calendar_recommendation(
        plan=scoped_plan,
        start_date=start_day,
        horizon_days=horizon_days,
        current_calendar_mode=mode_str,
    )

    return {
        "baseline_date": baseline_day.strftime("%Y-%m-%d"),
        "baseline_display": f"{baseline_day.strftime('%m/%d')} (D+0 冻结基准)",
        "start_date": start_day.strftime("%Y-%m-%d"),
        "start_display": f"{start_day.strftime('%m/%d')} (D+1 排产起始)",
        "horizon_days": horizon_days,
        "horizon_options": [5, 10, 15],
        "calendar_mode": mode_str,
        "day_shift_overrides": overrides,
        "days": days_list,
        "total_days": horizon_days,
        "working_days": working_days_count,
        "rest_days": rest_days_count,
        "total_capacity_hours": total_hours,
        "orders_in_scope_count": orders_in_scope,
        "orders_out_of_scope_count": orders_out_of_scope,
        "total_orders_count": orders_in_scope + orders_out_of_scope,
        "ai_recommendation": ai_rec,
    }


# Initialize snapshot on startup
@app.on_event("startup")
def startup_event():
    try:
        session_state["snapshot"] = get_fresh_snapshot(SITE)
        session_state["plan"] = build_requirement_plan(session_state["snapshot"])
    except Exception as e:
        print(f"Error loading initial database: {e}")


# ---------------- API Models ----------------
class UpdateDemandItem(BaseModel):
    order_id: str
    part_no: str
    due_date: str
    demand_quantity: float


class UpdateStockItem(BaseModel):
    part_no: str
    quantity: float


class UpdateToolingMoldItem(BaseModel):
    mold_id: str
    status: Optional[str] = None
    available_qty: Optional[int] = None
    current_strokes: Optional[int] = None
    changeover_time_min: Optional[float] = None


class UpdatePMItem(BaseModel):
    pm_plan_no: str
    status: Optional[str] = None
    handling_strategy: Optional[str] = None
    start_shift: Optional[str] = None
    end_shift: Optional[str] = None


class UpdateMaterialArrivalItem(BaseModel):
    po_number: str
    eta_date: Optional[str] = None
    eta_shift: Optional[str] = None
    quantity: Optional[float] = None
    status: Optional[str] = None


class UpdateCustomerTierItem(BaseModel):
    customer_code: str
    tier: Optional[str] = None
    priority_weight: Optional[int] = None
    delivery_window: Optional[str] = None
    subcontract_allowed: Optional[str] = None


class UpdateInputsPayload(BaseModel):
    orders: Optional[List[UpdateDemandItem]] = None
    initial_stock: Optional[List[UpdateStockItem]] = None
    tooling_molds: Optional[List[UpdateToolingMoldItem]] = None
    pm_schedules: Optional[List[UpdatePMItem]] = None
    material_arrivals: Optional[List[UpdateMaterialArrivalItem]] = None
    customer_tiers: Optional[List[UpdateCustomerTierItem]] = None


class CalendarUpdatePayload(BaseModel):
    start_date: Optional[str] = "2026-10-12"
    horizon_days: Optional[int] = 10
    calendar_mode: Optional[str] = "SEVEN_ZERO"
    day_shift_overrides: Optional[Dict[str, Any]] = None


class SiteSettingsPayload(BaseModel):
    site: str = "WL"
    horizon_days: Optional[int] = 10
    start_date: Optional[str] = "2026-10-12"
    calendar_mode: Optional[str] = "SEVEN_ZERO"
    lookahead_enabled: Optional[bool] = True
    lookahead_days: Optional[int] = 30
    max_pull_forward_days: Optional[int] = 10
    campaign_merge_enabled: Optional[bool] = True
    day_shift_overrides: Optional[Dict[str, Any]] = None


class SolvePayload(BaseModel):
    site: str = "WL"
    calendar_mode: Optional[str] = "SEVEN_ZERO"
    enabled_rules: List[str] = []
    time_limit_seconds: float = 30.0
    horizon_days: Optional[int] = 10
    start_date: Optional[str] = "2026-10-12"
    day_shift_overrides: Optional[Dict[str, Any]] = None
    allow_105_utilization_fg: bool = False
    allow_105_utilization_sf: bool = False
    rule_options: Optional[Dict[str, Any]] = None
    lookahead_enabled: Optional[bool] = None
    lookahead_days: Optional[int] = None
    max_pull_forward_days: Optional[int] = None


# ---------------- API Endpoints ----------------
@app.get("/api/sites")
def list_sites():
    return {
        "current": session_state["site"],
        "available": [
            {"code": "WL", "name": "WL 工厂 (WE34 车间 - 雨刮/轴套)", "description": "雨刮传动杆总成装配线 (PGLINKAGE) + 轴套冲压线 (轴套6)"},
            {"code": "SY38", "name": "SY38 工厂 (沈阳产线 - 涂装/注塑)", "description": "注塑与涂装车间 (PU07/PU22 涂装线 + 1400T-6 注塑机)"},
        ],
    }


@app.get("/api/site-settings")
def get_site_settings(site: str = "WL"):
    if site not in site_settings_store:
        site_settings_store[site] = copy.deepcopy(DEFAULT_SITE_SETTINGS.get("WL", {}))
        site_settings_store[site]["site"] = site
    return site_settings_store[site]


@app.post("/api/site-settings")
def update_site_settings(payload: SiteSettingsPayload):
    site = payload.site
    if site not in site_settings_store:
        site_settings_store[site] = copy.deepcopy(DEFAULT_SITE_SETTINGS.get("WL", {}))
        site_settings_store[site]["site"] = site
    cfg = site_settings_store[site]
    if payload.horizon_days is not None:
        cfg["horizon_days"] = payload.horizon_days
    if payload.start_date is not None:
        cfg["start_date"] = payload.start_date
    if payload.calendar_mode is not None:
        cfg["calendar_mode"] = payload.calendar_mode
    if payload.lookahead_enabled is not None:
        cfg["lookahead_enabled"] = payload.lookahead_enabled
    if payload.lookahead_days is not None:
        cfg["lookahead_days"] = payload.lookahead_days
    if payload.max_pull_forward_days is not None:
        cfg["max_pull_forward_days"] = payload.max_pull_forward_days
    if payload.campaign_merge_enabled is not None:
        cfg["campaign_merge_enabled"] = payload.campaign_merge_enabled
    if payload.day_shift_overrides is not None:
        cfg["day_shift_overrides"] = payload.day_shift_overrides

    # If this is active site, sync session_state calendar_config
    if site == session_state["site"]:
        session_state["calendar_config"]["horizon_days"] = cfg["horizon_days"]
        session_state["calendar_config"]["start_date"] = cfg["start_date"]
        session_state["calendar_config"]["calendar_mode"] = cfg["calendar_mode"]
        session_state["calendar_config"]["day_shift_overrides"] = cfg["day_shift_overrides"]

    return {"status": "success", "message": f"工厂 [{site}] 生产策略配置已更新", "settings": cfg}


@app.post("/api/sites/switch")
def switch_site(site: str = Body(..., embed=True)):
    session_state["site"] = site
    session_state["snapshot"] = get_fresh_snapshot(site)
    session_state["plan"] = build_requirement_plan(session_state["snapshot"])
    session_state["result"] = None
    session_state["matrix"] = None
    session_state["checks"] = None
    if site not in site_settings_store:
        site_settings_store[site] = copy.deepcopy(DEFAULT_SITE_SETTINGS.get("WL", {}))
        site_settings_store[site]["site"] = site
    cfg = site_settings_store[site]
    session_state["calendar_config"] = {
        "baseline_date": "2026-10-11",
        "start_date": cfg.get("start_date", "2026-10-12"),
        "horizon_days": cfg.get("horizon_days", 10),
        "calendar_mode": cfg.get("calendar_mode", "SEVEN_ZERO"),
        "day_shift_overrides": cfg.get("day_shift_overrides", {}),
    }
    return {
        "status": "success",
        "message": f"已成功切换至工厂站点 [{site}]",
        "site": site,
        "settings": cfg,
    }


@app.get("/api/inputs")
def get_inputs(site: Optional[str] = None):
    if site and site != session_state["site"]:
        session_state["site"] = site
        session_state["snapshot"] = get_fresh_snapshot(site)
        session_state["plan"] = build_requirement_plan(session_state["snapshot"])
        session_state["result"] = None
        session_state["matrix"] = None
        session_state["checks"] = None
        if site in site_settings_store:
            cfg = site_settings_store[site]
            session_state["calendar_config"] = {
                "baseline_date": "2026-10-11",
                "start_date": cfg.get("start_date", "2026-10-12"),
                "horizon_days": cfg.get("horizon_days", 10),
                "calendar_mode": cfg.get("calendar_mode", "SEVEN_ZERO"),
                "day_shift_overrides": cfg.get("day_shift_overrides", {}),
            }

    snap = session_state["snapshot"]
    if snap is None:
        snap = get_fresh_snapshot(session_state["site"])
        session_state["snapshot"] = snap

    # Format orders
    orders_list = [
        {
            "order_id": o.order_id,
            "part_no": o.part_no,
            "description": o.description,
            "due_date": o.due_date,
            "due_datetime": o.due_datetime.strftime("%Y-%m-%d %H:%M"),
            "demand_quantity": o.demand_quantity,
        }
        for o in snap.orders
    ]

    # Format initial stock
    stock_list = [
        {"part_no": pn, "quantity": qty}
        for pn, qty in sorted(snap.initial_stock.items())
    ]

    # Format FG params
    fg_params_list = [
        {
            "part_no": p.part_no,
            "line": p.line,
            "description": p.description,
            "ct_seconds": p.ct_seconds,
            "trp": p.trp,
            "mpq": p.mpq,
            "hours_per_shift": p.hours_per_shift,
            "shifts_per_day": p.shifts_per_day,
            "output_per_shift": round(p.output_per_shift, 1),
            "target_stock": p.target_stock,
        }
        for p in snap.fg_params.values()
    ]

    # Format SF params
    sf_params_list = [
        {
            "part_no": p.part_no,
            "line": p.line,
            "description": p.description,
            "ct_seconds": p.ct_seconds,
            "trp": p.trp,
            "mpq": p.mpq,
            "hours_per_shift": p.hours_per_shift,
            "shifts_per_day": p.shifts_per_day,
            "output_per_shift": round(p.output_per_shift, 1),
            "target_stock_days": p.target_stock_days,
        }
        for p in snap.sf_params.values()
    ]

    # Format BOM
    bom_list = []
    for fg_pn, children in snap.bom.items():
        for sf_pn, ratio in children:
            bom_list.append({
                "fg_part_no": fg_pn,
                "sf_part_no": sf_pn,
                "ratio": ratio,
            })

    # Master Data: Shift & Calendar specs (Adapted to Site)
    current_site = session_state["site"]
    is_sy = (current_site == "SY38")
    if is_sy:
        calendar_info = {
            "shift_hours": 12.0,
            "shifts_per_day": 2,
            "shift_seconds": 43200,
            "min_utilization": 0.95,
            "min_shift_seconds": 41040,
            "shifts": [
                {
                    "name": "早班",
                    "time_range": "08:00 ~ 20:00",
                    "duration_hours": 12.0,
                    "is_critical": True,
                    "description": "沈阳白班（含日内紧急加急订单插入及完工截单出货）",
                },
                {
                    "name": "晚班",
                    "time_range": "20:00 ~ 08:00 (+1)",
                    "duration_hours": 12.0,
                    "is_critical": False,
                    "description": "沈阳夜班（注塑与喷涂连产，为次日装配及发运储备安全缓冲库存）",
                },
            ],
            "modes": [
                {
                    "code": "SEVEN_ZERO",
                    "name": "连续生产 (7天×2班)",
                    "work_days": "周一至周日全开工 (14班/周，168小时)",
                    "condition": "周总负荷 > 5/7 极限产能",
                    "desc": "高负荷时释放注塑与喷涂极限产能，保障宝马/奥迪主机厂准时交付",
                },
                {
                    "code": "FIVE_TWO",
                    "name": "正常均衡 (5天×2班)",
                    "work_days": "周一至周五开工，周六日双休 (10班/周，120小时)",
                    "condition": "4/7 < 周总负荷 ≤ 5/7 极限产能",
                    "desc": "标准法定工作制，工时平准化排程，周末设备停机维护",
                },
                {
                    "code": "FOUR_THREE",
                    "name": "极度空闲 (4天×2班)",
                    "work_days": "周一至周四开工，周五六日休假 (8班/周，96小时)",
                    "condition": "周总负荷 ≤ 4/7 极限产能",
                    "desc": "需求低谷时压缩开机天数，降本节能",
                },
            ],
        }
    else:
        calendar_info = {
            "shift_hours": 8.0,
            "shifts_per_day": 3,
            "shift_seconds": 28800,
            "min_utilization": 0.95,
            "min_shift_seconds": 27360,
            "shifts": [
                {
                    "name": "早班",
                    "time_range": "07:00 ~ 15:00",
                    "duration_hours": 8.0,
                    "is_critical": True,
                    "description": "当日客户交付截单基准线（早班下班 15:00 截止发货入库）",
                },
                {
                    "name": "中班",
                    "time_range": "15:00 ~ 23:00",
                    "duration_hours": 8.0,
                    "is_critical": False,
                    "description": "标准连续装配与冲压班次，为后道及次日产出提供缓冲",
                },
                {
                    "name": "晚班",
                    "time_range": "23:00 ~ 07:00 (+1)",
                    "duration_hours": 8.0,
                    "is_critical": False,
                    "description": "夜间连产班次，为次日早班交付储备成品",
                },
            ],
            "modes": [
                {
                    "code": "SEVEN_ZERO",
                    "name": "连续生产 (7天×3班)",
                    "work_days": "周一至周日全开工 (21班/周，168小时)",
                    "condition": "周总负荷 > 5/7 极限产能",
                    "desc": "订单高企时打破常规周末休息，全量释放产线极限理论产能保障准时交付",
                },
                {
                    "code": "FIVE_TWO",
                    "name": "正常均衡 (5天×3班)",
                    "work_days": "周一至周五开工，周六日双休 (15班/周，120小时)",
                    "condition": "4/7 < 周总负荷 ≤ 5/7 极限产能",
                    "desc": "标准法定工作制，工时平准化排程，周末产线正常停工保养",
                },
                {
                    "code": "FOUR_THREE",
                    "name": "极度空闲 (4天×3班)",
                    "work_days": "周一至周四开工，周五六日休假 (12班/周，96小时)",
                    "condition": "周总负荷 ≤ 4/7 极限产能",
                    "desc": "需求低谷时集中压缩开机天数，周四预估库存覆盖周末提货，降本节能",
                },
            ],
        }

    # Master Data: Tooling Molds
    tooling_molds_list = [
        {
            "site": tm.site,
            "mold_id": tm.mold_id,
            "mold_name": tm.mold_name,
            "applicable_lines": tm.applicable_lines,
            "applicable_parts": tm.applicable_parts,
            "available_qty": tm.available_qty,
            "changeover_time_min": tm.changeover_time_min,
            "max_life_strokes": tm.max_life_strokes,
            "current_strokes": tm.current_strokes,
            "maintenance_cycle_strokes": tm.maintenance_cycle_strokes,
            "status": tm.status,
        }
        for tm in snap.tooling_molds
    ]

    # Master Data: PM Schedules
    pm_schedules_list = [
        {
            "site": pm.site,
            "pm_plan_no": pm.pm_plan_no,
            "line": pm.line,
            "equipment_name": pm.equipment_name,
            "pm_type": pm.pm_type,
            "start_date": pm.start_date,
            "start_shift": pm.start_shift,
            "end_date": pm.end_date,
            "end_shift": pm.end_shift,
            "downtime_hours": pm.downtime_hours,
            "capacity_rate": pm.capacity_rate,
            "handling_strategy": pm.handling_strategy,
            "responsible_team": pm.responsible_team,
            "status": pm.status,
        }
        for pm in snap.pm_schedules
    ]

    # Master Data: Material Arrivals
    material_arrivals_list = [
        {
            "site": ma.site,
            "po_number": ma.po_number,
            "supplier": ma.supplier,
            "part_no": ma.part_no,
            "description": ma.description,
            "eta_date": ma.eta_date,
            "eta_shift": ma.eta_shift,
            "quantity": ma.quantity,
            "qc_release_hours": ma.qc_release_hours,
            "associated_part": ma.associated_part,
            "status": ma.status,
        }
        for ma in snap.material_arrivals
    ]

    # Master Data: Customer Tiers
    customer_tiers_list = [
        {
            "site": ct.site,
            "customer_code": ct.customer_code,
            "customer_name": ct.customer_name,
            "tier": ct.tier,
            "priority_weight": ct.priority_weight,
            "otd_target_rate": ct.otd_target_rate,
            "delivery_window": ct.delivery_window,
            "subcontract_allowed": ct.subcontract_allowed,
            "associated_products": ct.associated_products,
        }
        for ct in snap.customer_tiers
    ]

    return {
        "site": session_state["site"],
        "planning_dates": snap.planning_dates,
        "orders": orders_list,
        "initial_stock": stock_list,
        "fg_params": fg_params_list,
        "sf_params": sf_params_list,
        "bom": bom_list,
        "calendar_info": calendar_info,
        "tooling_molds": tooling_molds_list,
        "pm_schedules": pm_schedules_list,
        "material_arrivals": material_arrivals_list,
        "customer_tiers": customer_tiers_list,
    }


@app.post("/api/inputs/update")
def update_inputs(payload: UpdateInputsPayload):
    snap = session_state["snapshot"]
    if snap is None:
        snap = get_fresh_snapshot(session_state["site"])
        session_state["snapshot"] = snap

    if payload.orders is not None:
        order_map = {o.order_id: o for o in snap.orders}
        for item in payload.orders:
            if item.order_id in order_map:
                o = order_map[item.order_id]
                o.demand_quantity = float(item.demand_quantity)
                if item.due_date and item.due_date != o.due_date:
                    o.due_date = item.due_date
                    try:
                        month, day = [int(p) for p in item.due_date.split("/")]
                        o.due_datetime = datetime(2026, month, day, 15, 0)
                    except Exception:
                        pass
            elif item.part_no and item.demand_quantity > 0:
                # Add new order
                try:
                    month, day = [int(p) for p in item.due_date.split("/")]
                    dt = datetime(2026, month, day, 15, 0)
                except Exception:
                    dt = datetime(2026, 10, 20, 15, 0)
                desc = snap.fg_params.get(item.part_no)
                snap.orders.append(CustomerOrder(
                    order_id=item.order_id or f"ORD_USER_{len(snap.orders)+1}",
                    part_no=item.part_no,
                    description=desc.description if desc else item.part_no,
                    due_date=item.due_date or "10/20",
                    due_datetime=dt,
                    demand_quantity=float(item.demand_quantity),
                ))

    if payload.initial_stock is not None:
        for item in payload.initial_stock:
            snap.initial_stock[item.part_no] = float(item.quantity)

    if payload.tooling_molds is not None:
        tm_map = {m.mold_id: m for m in snap.tooling_molds}
        for item in payload.tooling_molds:
            if item.mold_id in tm_map:
                m = tm_map[item.mold_id]
                if item.status is not None:
                    m.status = item.status
                if item.available_qty is not None:
                    m.available_qty = item.available_qty
                if item.current_strokes is not None:
                    m.current_strokes = item.current_strokes
                if item.changeover_time_min is not None:
                    m.changeover_time_min = item.changeover_time_min

    if payload.pm_schedules is not None:
        pm_map = {p.pm_plan_no: p for p in snap.pm_schedules}
        for item in payload.pm_schedules:
            if item.pm_plan_no in pm_map:
                p = pm_map[item.pm_plan_no]
                if item.status is not None:
                    p.status = item.status
                if item.handling_strategy is not None:
                    p.handling_strategy = item.handling_strategy
                if item.start_shift is not None:
                    p.start_shift = item.start_shift
                if item.end_shift is not None:
                    p.end_shift = item.end_shift

    if payload.material_arrivals is not None:
        ma_map = {a.po_number: a for a in snap.material_arrivals}
        for item in payload.material_arrivals:
            if item.po_number in ma_map:
                a = ma_map[item.po_number]
                if item.eta_date is not None:
                    a.eta_date = item.eta_date
                if item.eta_shift is not None:
                    a.eta_shift = item.eta_shift
                if item.quantity is not None:
                    a.quantity = item.quantity
                if item.status is not None:
                    a.status = item.status

    if payload.customer_tiers is not None:
        ct_map = {c.customer_code: c for c in snap.customer_tiers}
        for item in payload.customer_tiers:
            if item.customer_code in ct_map:
                c = ct_map[item.customer_code]
                if item.tier is not None:
                    c.tier = item.tier
                if item.priority_weight is not None:
                    c.priority_weight = item.priority_weight
                if item.delivery_window is not None:
                    c.delivery_window = item.delivery_window
                if item.subcontract_allowed is not None:
                    c.subcontract_allowed = item.subcontract_allowed

    # Rebuild plan
    session_state["plan"] = build_requirement_plan(snap)
    return {"status": "success", "message": "输入数据已更新"}


@app.delete("/api/inputs/orders/{order_id}")
def delete_order(order_id: str):
    snap = session_state["snapshot"]
    if snap is None:
        raise HTTPException(status_code=400, detail="Snapshot not loaded")
    snap.orders = [o for o in snap.orders if o.order_id != order_id]
    session_state["plan"] = build_requirement_plan(snap)
    return {"status": "success", "message": f"订单 {order_id} 已删除"}


@app.post("/api/inputs/reset")
def reset_inputs():
    session_state["snapshot"] = get_fresh_snapshot(session_state["site"])
    session_state["plan"] = build_requirement_plan(session_state["snapshot"])
    return {"status": "success", "message": "已重置为 Excel 原始数据"}


@app.get("/api/calendar")
def get_calendar():
    snap = session_state["snapshot"]
    if snap is None:
        snap = get_fresh_snapshot(session_state["site"])
        session_state["snapshot"] = snap

    cfg = session_state.get("calendar_config", {})
    return generate_calendar_view(
        start_date_str=cfg.get("start_date", "2026-10-12"),
        horizon_days=cfg.get("horizon_days", 10),
        mode_str=cfg.get("calendar_mode", "SEVEN_ZERO"),
        overrides=cfg.get("day_shift_overrides", {}),
        snap=snap,
    )


@app.post("/api/calendar/update")
def update_calendar(payload: CalendarUpdatePayload):
    snap = session_state["snapshot"]
    if snap is None:
        snap = get_fresh_snapshot(session_state["site"])
        session_state["snapshot"] = snap

    cfg = session_state.setdefault("calendar_config", {})
    if payload.start_date:
        cfg["start_date"] = payload.start_date
    if payload.horizon_days and payload.horizon_days > 0:
        cfg["horizon_days"] = payload.horizon_days
    if payload.calendar_mode:
        cfg["calendar_mode"] = payload.calendar_mode
    if payload.day_shift_overrides is not None:
        cfg["day_shift_overrides"] = payload.day_shift_overrides

    view = generate_calendar_view(
        start_date_str=cfg["start_date"],
        horizon_days=cfg["horizon_days"],
        mode_str=cfg["calendar_mode"],
        overrides=cfg["day_shift_overrides"],
        snap=snap,
    )
    return {"status": "success", "message": "工厂日历配置已更新", "calendar": view}


@app.get("/api/calendar/recommendation")
def get_calendar_recommendation(horizon_days: int = 10, calendar_mode: str = "SEVEN_ZERO"):
    snap = session_state["snapshot"]
    if snap is None:
        snap = get_fresh_snapshot(session_state["site"])
        session_state["snapshot"] = snap

    cfg = session_state.get("calendar_config", {})
    start_date_str = cfg.get("start_date", "2026-10-12")
    try:
        y, m, d = [int(p) for p in start_date_str.split("-")]
        start_day = datetime(y, m, d, 7, 0, 0)
    except Exception:
        start_day = datetime(2026, 10, 12, 7, 0, 0)

    horizon_end = start_day + timedelta(days=horizon_days)
    scoped_snap = copy.deepcopy(snap)
    scoped_snap.orders = [o for o in scoped_snap.orders if o.due_datetime <= horizon_end]
    scoped_plan = build_requirement_plan(scoped_snap)

    return evaluate_calendar_recommendation(
        plan=scoped_plan,
        start_date=start_day,
        horizon_days=horizon_days,
        current_calendar_mode=calendar_mode,
    )


@app.get("/api/rules")
def get_rules(site: Optional[str] = None):
    target_site = site or session_state["site"]
    site_rules = get_rules_for_site(target_site)
    rules_list = []
    engine_switches = {
        # WL (WE34 27 Rules)
        "R-WL-01": "enforce_tooling_exclusivity",
        "R-WL-02": "enforce_peak_smoothing",
        "R-WL-03": "enforce_demand_lookahead",
        "R-WL-04": "enforce_segmented_calendar",
        "R-WL-05": "enforce_delay_suggestions",
        "R-WL-06": "calendar_mode",
        "R-WL-07": "calendar_mode",
        "R-WL-08": "calendar_mode",
        "R-WL-09": "enforce_mpq_batch",
        "R-WL-10": "enforce_freeze_window",
        "R-WL-11": "enforce_continuous_merge",
        "R-WL-12": "enforce_sequential_pulling",
        "R-WL-13": "enforce_exact_demand",
        "R-WL-14": "enforce_controlled_window",
        "R-WL-15": "enforce_mpq_batch",
        "R-WL-20": "enforce_on_time_delivery",
        "R-WL-21": "enforce_material_arrival",
        "R-WL-22": "enforce_safety_stock",
        "R-WL-23": "enforce_material_balance",
        "R-WL-24": "enforce_lookahead_limits",
        "R-WL-25": "calendar_mode",
        "R-WL-26": "enforce_utilization_95",
        "R-WL-27": "enforce_pm_maintenance",
        # SY38 (9 Rules)
        "R-SY-01": "enforce_rush_order_day_shift",
        "R-SY-02": "enforce_anti_starvation_overstock",
        "R-SY-03": "enforce_lr_co_mold_sync",
        "R-SY-04": "enforce_sf_lead_time_12h",
        "R-SY-05": "enforce_max_2_molds_per_shift",
        "R-SY-06": "enforce_fg_stock_limit_5d",
        "R-SY-07": "enforce_sf_stock_limit_penalty",
        "R-SY-08": "enforce_utilization_95",
        "R-SY-09": "enforce_fg_1day_ship_buffer",
        # Legacy IDs
        "R-SF-01": "enforce_utilization_95",
        "R-SF-02": "enforce_sequential_pulling",
        "R-SF-03": "enforce_material_balance",
        "R-SF-04": "enforce_freeze_window",
        "R-SF-05": "enforce_exact_demand",
        "R-SF-07": "enforce_weekend_penalty",
        "R-SF-09": "enforce_mpq_batch",
        "R-FG-01": "enforce_on_time_delivery",
        "R-FG-02": "enforce_mpq_batch",
        "R-FG-03": "enforce_utilization_95",
        "R-FG-04": "enforce_freeze_window",
        "R-FG-05": "enforce_sequential_pulling",
        "R-FG-06": "enforce_continuous_merge",
        "R-FG-07": "enforce_exact_demand",
        "R-FG-09": "enforce_weekend_penalty",
        "R-FG-13": "enforce_concurrency_limit",
        "R-CL-01": "calendar_mode",
        "R-CL-02": "calendar_mode",
        "R-CL-03": "calendar_mode",
        "R-BK-01": "enforce_backward_scheduling",
        "R-EX-04": "enforce_delay_suggestions",
        "R-TL-01": "enforce_tooling_exclusivity",
        "R-MT-01": "enforce_material_arrival",
        "R-PM-01": "enforce_pm_maintenance",
        "R-CR-01": "enforce_customer_priority",
    }

    for r_id, r in sorted(site_rules.items(), key=lambda x: x[1].row_index):
        rules_list.append({
            "rule_id": r.rule_id,
            "row_index": r.row_index,
            "stage": r.stage,
            "name": r.name,
            "category": r.category.value,
            "nature": r.nature.value,
            "weight": r.weight,
            "status": r.status.value,
            "landing_layer": r.landing_layer,
            "validation_check": r.validation_check,
            "decision_id": r.decision_id,
            "description": r.description,
            "ai_logic": r.ai_logic,
            "is_engine_switch": r_id in engine_switches,
            "switch_key": engine_switches.get(r_id),
            "checked": True,  # Default all checked
            "load_limit_mode": "100",  # Default 95% ~ 100%
        })
    return {"site": target_site, "rules": rules_list}


@app.post("/api/solve")
def run_solver(payload: SolvePayload):
    snap = session_state["snapshot"]
    if snap is None or session_state.get("site") != payload.site:
        snap = get_fresh_snapshot(payload.site)
        session_state["snapshot"] = snap
        session_state["site"] = payload.site

    cfg = session_state.setdefault("calendar_config", {})
    horizon_days = payload.horizon_days if (payload.horizon_days and payload.horizon_days > 0) else cfg.get("horizon_days", 10)
    start_date_str = payload.start_date or cfg.get("start_date", "2026-10-12")
    day_shift_overrides = payload.day_shift_overrides if payload.day_shift_overrides is not None else cfg.get("day_shift_overrides", {})
    cal_mode_str = payload.calendar_mode or cfg.get("calendar_mode", "SEVEN_ZERO")

    # Update session calendar config
    cfg["horizon_days"] = horizon_days
    cfg["start_date"] = start_date_str
    cfg["calendar_mode"] = cal_mode_str
    cfg["day_shift_overrides"] = day_shift_overrides

    try:
        y, m, d = [int(p) for p in start_date_str.split("-")]
        start_dt = datetime(y, m, d, 7, 0, 0)
    except Exception:
        start_dt = datetime(2026, 10, 12, 7, 0, 0)



    # Determine lookahead configuration for this site
    # Map checked rule IDs to scheduler options (Site-Scoped)
    site_rules = get_rules_for_site(payload.site)
    enabled = set(payload.enabled_rules or [r_id for r_id in site_rules])
    site_cfg = site_settings_store.get(payload.site, DEFAULT_SITE_SETTINGS.get("WL", {}))
    lookahead_on = payload.lookahead_enabled if payload.lookahead_enabled is not None else site_cfg.get("lookahead_enabled", True)
    if "R-WL-03" in site_rules and "R-WL-03" not in enabled:
        lookahead_on = False
    lookahead_days = payload.lookahead_days or site_cfg.get("lookahead_days", 30)
    max_pull_days = payload.max_pull_forward_days or site_cfg.get("max_pull_forward_days", 10)

    # Scoped orders for the chosen planning horizon (with or without 30-day lookahead)
    if lookahead_on:
        solve_snap, lookahead_summary = apply_demand_lookahead(
            snapshot=snap,
            start_dt=start_dt,
            horizon_days=horizon_days,
            lookahead_days=lookahead_days,
            max_pull_forward_days=max_pull_days,
            max_line_utilization=0.98,
        )
    else:
        horizon_end = start_dt + timedelta(days=horizon_days)
        solve_snap = copy.deepcopy(snap)
        solve_snap.orders = [o for o in solve_snap.orders if o.due_datetime <= horizon_end]
        lookahead_summary = {
            "is_enabled": False,
            "horizon_days": horizon_days,
            "lookahead_days": lookahead_days,
            "immediate_orders_count": len(solve_snap.orders),
            "pulled_orders_count": 0,
            "pulled_quantity_total": 0,
            "active_parts_count": len(set(o.part_no for o in solve_snap.orders)),
            "pulled_parts_count": 0,
            "pulled_details": [],
            "pulled_by_part": {},
            "changeovers_saved_estimate": 0,
        }

    plan = build_requirement_plan(solve_snap)
    session_state["plan"] = plan

    # Map checked rule IDs to scheduler options (Site-Scoped)
    enforce_utilization_95 = (
        "R-FG-03" in enabled or "R-SF-01" in enabled or "R-WL-26" in enabled or "R-SY-08" in enabled
    )

    # 105% utilization threshold option for R-FG-03 and R-SF-01
    allow_105_fg = payload.allow_105_utilization_fg
    allow_105_sf = payload.allow_105_utilization_sf
    if payload.rule_options:
        if payload.rule_options.get("R-FG-03", {}).get("load_limit_mode") == "105":
            allow_105_fg = True
        if payload.rule_options.get("R-SF-01", {}).get("load_limit_mode") == "105":
            allow_105_sf = True
    enforce_on_time_delivery = ("R-FG-01" in enabled or "R-WL-20" in enabled or "R-SY-01" in enabled or "R-SY-09" in enabled)
    enforce_concurrency_limit = ("R-FG-13" in enabled or "R-SY-05" in enabled)
    enforce_mpq_batch = ("R-FG-02" in enabled or "R-SF-09" in enabled or "R-WL-09" in enabled or "R-WL-15" in enabled)
    enforce_material_balance = ("R-SF-03" in enabled or "R-WL-23" in enabled or "R-SY-02" in enabled or "R-SY-04" in enabled)
    enforce_sequential_pulling = ("R-SF-02" in enabled or "R-FG-05" in enabled or "R-WL-12" in enabled)
    enforce_freeze_window = ("R-SF-04" in enabled or "R-FG-04" in enabled or "R-WL-10" in enabled)
    enforce_tooling_exclusivity = ("R-TL-01" in enabled or "R-WL-01" in enabled or "R-SY-03" in enabled)
    enforce_material_arrival = ("R-MT-01" in enabled or "R-WL-21" in enabled)
    enforce_pm_maintenance = ("R-PM-01" in enabled or "R-WL-27" in enabled)
    enforce_customer_priority = ("R-CR-01" in enabled or "R-WL-24" in enabled)
    enforce_weekend_penalty = ("R-SF-07" in enabled or "R-FG-09" in enabled or "R-WL-06" in enabled)
    enforce_exact_demand = ("R-SF-05" in enabled or "R-FG-07" in enabled or "R-WL-13" in enabled)

    try:
        cal_mode = CalendarMode(cal_mode_str)
    except Exception:
        cal_mode = CalendarMode.SEVEN_ZERO

    pm_shifts_map = defaultdict(list)
    for t in plan.tasks:
        if t.line_pm_slots:
            for s in t.line_pm_slots:
                if s not in pm_shifts_map[t.line]:
                    pm_shifts_map[t.line].append(s)

    scheduler = APSScheduler(
        plan=plan,
        start_datetime=start_dt,
        planning_days=horizon_days,
        calendar_mode=cal_mode,
        day_shift_overrides=day_shift_overrides,
        enforce_utilization_95=enforce_utilization_95,
        enforce_on_time_delivery=enforce_on_time_delivery,
        enforce_concurrency_limit=enforce_concurrency_limit,
        enforce_mpq_batch=enforce_mpq_batch,
        enforce_material_balance=enforce_material_balance,
        price_changeovers=("R-FG-06" in enabled or "R-FG-13" in enabled),
        enforce_sequential_pulling=enforce_sequential_pulling,
        enforce_freeze_window=enforce_freeze_window,
        enforce_tooling_exclusivity=enforce_tooling_exclusivity,
        enforce_material_arrival=enforce_material_arrival,
        enforce_pm_maintenance=enforce_pm_maintenance,
        pm_shifts=pm_shifts_map,
        enforce_customer_priority=enforce_customer_priority,
        enforce_weekend_penalty=enforce_weekend_penalty,
        enforce_exact_demand=enforce_exact_demand,
        allow_105_utilization_fg=allow_105_fg,
        allow_105_utilization_sf=allow_105_sf,
    )

    # Solve with capacity overload diagnosis
    try:
        result = scheduler.solve(time_limit_seconds=float(payload.time_limit_seconds))
    except RuntimeError as e:
        precheck = precheck_capacity(plan, scheduler.shifts)
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "CAPACITY_OVERLOAD_EXCEPTION",
                "title": f"排产不可行：当前【{cal_mode.value}】工作制下产能严重超载",
                "mode": cal_mode.value,
                "horizon_days": horizon_days,
                "total_shifts": len(scheduler.shifts),
                "total_hours": len(scheduler.shifts) * 8.0,
                "message": (
                    f"在当前【{cal_mode.value}】工作制下（10天内共 {len(scheduler.shifts)} 个有效工作班次 / {len(scheduler.shifts)*8} 小时），"
                    f"产线订单负荷超出了理论极限工时，无法在交期前排下所有工单。"
                ),
                "bottlenecks": precheck.bottlenecks,
                "line_loads": [
                    {
                        "line": l,
                        "req_hours": round(s.required_hours, 1),
                        "avail_hours": round(s.available_hours, 1),
                        "utilization_rate": round(s.utilization_rate * 100, 1),
                        "is_overloaded": s.is_overloaded,
                    }
                    for l, s in precheck.line_loads.items()
                ],
                "suggestions": [
                    "根据排产规则 R-CL-01..03：当前 10 天订单负荷较重（>5/7 极限产能），请切换为【7×3 连续生产】模式（全量 240 小时产能）；",
                    "若车间必须执行【做五休二】，建议将计划期切换至【15天扩展计划】，以提供充裕的平准与备料周期；",
                    "或进入工厂日历工作台，为周末手动点亮 2 个开工班次以弥补产能缺口。"
                ]
            }
        )
    session_state["result"] = result
    session_state["last_solve_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build Validation Matrix
    matrix = build_validation_matrix(solve_snap, plan, result, site=payload.site)
    session_state["matrix"] = matrix

    # Run Independent Validation
    checks = validate_schedule(plan, result, allow_105_fg=allow_105_fg, allow_105_sf=allow_105_sf)
    session_state["checks"] = checks

    # Calculate KPIs
    fg_tasks = [t for t in result.tasks if t.stage == "FG"]
    sf_tasks = [t for t in result.tasks if t.stage == "SF"]
    late_tasks = [t for t in fg_tasks if t.end_dt > t.due_datetime]
    on_time_rate = ((len(fg_tasks) - len(late_tasks)) / len(fg_tasks) * 100.0) if fg_tasks else 100.0

    if result.tasks:
        min_start = min(t.start_dt for t in result.tasks)
        max_end = max(t.end_dt for t in result.tasks)
        makespan_hours = (max_end - min_start).total_seconds() / 3600.0
    else:
        min_start = scheduler.shifts[0].start_dt if scheduler.shifts else start_dt
        max_end = scheduler.shifts[-1].end_dt if scheduler.shifts else (start_dt + timedelta(days=horizon_days))
        makespan_hours = 0.0

    # Line Average Utilization
    line_utilizations = {}
    for ll in matrix.line_loads:
        if ll.metric == "Loading Rate":
            rates = [v for v in ll.shift_values.values() if isinstance(v, (int, float)) and v > 0]
            avg_r = (sum(rates) / len(rates)) if rates else 0.0
            line_utilizations[ll.line] = round(avg_r, 1)

    total_lines_cnt = len(line_utilizations)
    qualified_lines_cnt = len([r for r in line_utilizations.values() if r >= 95.0])
    unqualified_lines = [l for l, r in line_utilizations.items() if r < 95.0]
    avg_line_util = round(sum(line_utilizations.values()) / max(1, total_lines_cnt), 1) if total_lines_cnt else 0.0

    # Overall Timeline Horizon
    horizon_start = scheduler.shifts[0].start_dt if scheduler.shifts else min_start
    horizon_end = scheduler.shifts[-1].end_dt if scheduler.shifts else max_end
    total_horizon_sec = max(1.0, (horizon_end - horizon_start).total_seconds())

    # Task Schedule for Gantt/Table
    task_schedule_list = []
    for t in sorted(result.tasks, key=lambda x: (x.stage, x.start_dt)):
        slack_h = round((t.due_datetime - t.end_dt).total_seconds() / 3600.0, 1) if t.due_datetime else 999.0
        
        # BOM Linkages
        bom_children = []
        if t.stage == "FG":
            bom_children = [
                {
                    "part_no": sf_pn,
                    "ratio": ratio,
                    "description": snap.sf_params.get(sf_pn).description if sf_pn in snap.sf_params else sf_pn,
                }
                for sf_pn, ratio in snap.bom.get(t.part_no, [])
            ]
        bom_parents = []
        if t.stage == "SF":
            bom_parents = [
                fg_pn for fg_pn, ch in snap.bom.items() if any(c[0] == t.part_no for c in ch)
            ]

        # Calculate exact visual positioning percentages for Gantt timeline
        left_pct = max(0.0, round((t.start_dt - horizon_start).total_seconds() / total_horizon_sec * 100.0, 3))
        dur_pct = max(0.8, round((t.end_dt - t.start_dt).total_seconds() / total_horizon_sec * 100.0, 3))
        due_pct = round((t.due_datetime - horizon_start).total_seconds() / total_horizon_sec * 100.0, 3) if t.due_datetime else None

        task_schedule_list.append({
            "task_id": t.task_id,
            "stage": t.stage,
            "line": t.line,
            "part_no": t.part_no,
            "description": t.description,
            "quantity": int(t.quantity),
            "order_id": t.order_id,
            "ct_seconds": t.ct_seconds,
            "trp": t.trp,
            "mpq": t.mpq,
            "lot_size": t.lot_size,
            "lots": t.lots,
            "start_dt": t.start_dt.strftime("%m/%d %H:%M"),
            "end_dt": t.end_dt.strftime("%m/%d %H:%M"),
            "due_dt": t.due_datetime.strftime("%m/%d %H:%M") if t.due_datetime else "-",
            "start_timestamp": int(t.start_dt.timestamp()),
            "end_timestamp": int(t.end_dt.timestamp()),
            "due_timestamp": int(t.due_datetime.timestamp()) if t.due_datetime else None,
            "duration_hours": round(t.duration_hours, 1),
            "slack_hours": slack_h,
            "is_late": (t.end_dt > t.due_datetime) if t.due_datetime else False,
            "left_percent": left_pct,
            "width_percent": dur_pct,
            "due_percent": due_pct,
            "shift_breakdown": {f"{k[0]} {k[1]}": v for k, v in t.shift_breakdown.items()},
            "bom_children": bom_children,
            "bom_parents": bom_parents,
        })

    # Precheck Report
    precheck = precheck_capacity(plan, scheduler.shifts)
    precheck_summary = {
        "is_feasible": precheck.is_feasible,
        "status": precheck.status,
        "bottlenecks": precheck.bottlenecks,
        "delay_suggestions": [
            {
                "order_id": s.order_id,
                "part_no": s.part_no,
                "original_due": s.original_due.strftime("%m/%d %H:%M"),
                "suggested_due": s.suggested_due.strftime("%m/%d %H:%M"),
                "excess_hours": round(s.excess_hours, 1),
                "reason": s.reason,
            }
            for s in precheck.delay_suggestions
        ],
    }

    # Compliance Matrix
    check_status = {}
    for c in checks:
        for r_id in c.rule_ids:
            check_status[r_id] = check_status.get(r_id, True) and c.passed

    compliance_rows = []
    for r_id, rule in sorted(site_rules.items(), key=lambda x: x[1].row_index):
        is_checked = r_id in enabled
        if not is_checked:
            eff_status = "SKIPPED (未勾选)"
        elif r_id in check_status:
            eff_status = "PASS" if check_status[r_id] else "FAIL"
        else:
            eff_status = rule.status.value

        compliance_rows.append({
            "rule_id": rule.rule_id,
            "row_index": rule.row_index,
            "stage": rule.stage,
            "category": rule.category.value,
            "nature": rule.nature.value,
            "name": rule.name,
            "status": eff_status,
            "weight": rule.weight,
            "note": rule.validation_check or rule.landing_layer,
            "ai_logic": rule.ai_logic,
            "is_checked": is_checked,
        })

    # Line Load Output
    line_loads_data = [
        {
            "site": row.site,
            "line": row.line,
            "metric": row.metric,
            "shift_values": row.shift_values,
        }
        for row in matrix.line_loads
    ]

    # FG Balance Output
    fg_balance_data = [
        {
            "site": row.site,
            "line": row.line,
            "part_no": row.part_no,
            "description": row.description,
            "initial_stock": row.initial_stock,
            "ct_seconds": row.ct_seconds,
            "trp": row.trp,
            "mpq": row.mpq,
            "total_demand": row.total_demand,
            "avg_daily_demand": round(row.avg_daily_demand, 1),
            "demand": row.demand_by_shift,
            "plan": row.plan_by_shift,
            "stock": row.stock_by_shift,
            "coverage": row.coverage_by_shift,
        }
        for row in matrix.fg_balance
    ]

    # SF Balance Output
    sf_balance_data = [
        {
            "site": row.site,
            "line": row.line,
            "part_no": row.part_no,
            "description": row.description,
            "initial_stock": row.initial_stock,
            "ct_seconds": row.ct_seconds,
            "trp": row.trp,
            "mpq": row.mpq,
            "total_demand": row.total_demand,
            "avg_daily_demand": round(row.avg_daily_demand, 1),
            "demand": row.demand_by_shift,
            "plan": row.plan_by_shift,
            "stock": row.stock_by_shift,
            "coverage": row.coverage_by_shift,
        }
        for row in matrix.sf_balance
    ]

    # Validation Checks
    checks_data = [
        {
            "name": c.name,
            "passed": c.passed,
            "rule_ids": c.rule_ids,
            "violations": c.violations[:10],
            "total_violations": len(c.violations),
        }
        for c in checks
    ]

    return {
        "status": "success",
        "solve_time": session_state["last_solve_time"],
        "kpi": {
            "status_name": result.status_name,
            "total_fg_tasks": len(fg_tasks),
            "total_sf_tasks": len(sf_tasks),
            "total_tasks": len(result.tasks),
            "on_time_delivery_rate": round(on_time_rate, 1),
            "late_task_count": len(late_tasks),
            "makespan_hours": round(makespan_hours, 1),
            "wall_time_seconds": round(result.wall_time_seconds, 2),
            "line_utilizations": line_utilizations,
            "avg_line_utilization": avg_line_util,
            "total_lines_count": total_lines_cnt,
            "qualified_lines_count": qualified_lines_cnt,
            "unqualified_lines_count": len(unqualified_lines),
            "unqualified_lines": unqualified_lines,
            "schedule_start": min_start.strftime("%Y-%m-%d %H:%M"),
            "schedule_end": max_end.strftime("%Y-%m-%d %H:%M"),
        },
        "precheck": precheck_summary,
        "gantt_horizon": {
            "start_dt": horizon_start.strftime("%Y-%m-%d %H:%M"),
            "end_dt": horizon_end.strftime("%Y-%m-%d %H:%M"),
            "total_seconds": int(total_horizon_sec),
            "total_hours": round(total_horizon_sec / 3600.0, 1),
            "shift_count": len(scheduler.shifts),
        },
        "shifts_info": [
            {
                "index": s.index,
                "date": s.date_str,
                "shift": s.shift_name,
                "key": f"{s.date_str} {s.shift_name}",
                "start_dt": s.start_dt.strftime("%m/%d %H:%M"),
                "end_dt": s.end_dt.strftime("%m/%d %H:%M"),
                "is_work": s.is_work_shift,
                "weekday": s.weekday,
                "left_percent": max(0.0, round((s.start_dt - horizon_start).total_seconds() / total_horizon_sec * 100.0, 3)),
                "width_percent": max(0.1, round((s.end_dt - s.start_dt).total_seconds() / total_horizon_sec * 100.0, 3)),
            }
            for s in scheduler.shifts
        ],
        "shift_columns": matrix.shift_columns,
        "line_loads": line_loads_data,
        "fg_balance": fg_balance_data,
        "sf_balance": sf_balance_data,
        "tasks": task_schedule_list,
        "checks": checks_data,
        "compliance_matrix": compliance_rows,
        "lookahead_summary": lookahead_summary,
        "calendar_summary": {
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "horizon_days": horizon_days,
            "orders_in_scope": len(solve_snap.orders),
            "orders_total": len(snap.orders),
            "work_shifts_count": len([s for s in scheduler.shifts if s.is_work_shift]),
            "total_shifts_count": len(scheduler.shifts),
        },
    }


@app.get("/api/export")
def export_plan_excel():
    matrix = session_state.get("matrix")
    result = session_state.get("result")
    snap = session_state.get("snapshot")
    plan = session_state.get("plan")

    if matrix is None:
        if result is not None and snap is not None and plan is not None:
            matrix = build_validation_matrix(snap, plan, result, site=session_state["site"])
            session_state["matrix"] = matrix
        else:
            raise HTTPException(status_code=400, detail="请先点击【执行智能排产】生成排产计划")

    temp_dir = tempfile.mkdtemp()
    target_excel = os.path.join(temp_dir, f"APS_Production_Plan_{session_state['site']}.xlsx")
    export_validation_matrix_excel(matrix, target_excel, site=session_state["site"])

    return FileResponse(
        target_excel,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"APS_Production_Plan_{session_state['site']}.xlsx",
    )


# Mount static directory for single-page web app
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>APS MVP Web Interface - Frontend Building...</h1>"
