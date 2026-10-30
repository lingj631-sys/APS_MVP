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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import io
import openpyxl
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.audit import AuditConfig, PlanRow, ShiftInfo, audit_schedule_part_level
from src.balance_builder import build_validation_matrix
from src.data_loader import (
    CustomerOrder,
    ProductionInputSnapshot,
    RequirementPlan,
    build_requirement_plan,
    load_aps_database,
    save_snapshot_json,
)
from src.exporter import export_production_plan, export_validation_matrix_excel
from src.kernel.rule_mapping import describe_legacy_rule
from src.inventory_policy import load_inventory_policy, project_inventory
from src.labor import load_labor_data
from src.leadtime import load_operation_lead_times
from src.metric_labels import labels_for
from src.rule_catalog import load_catalog
from src.routing import assign_routing_lines, load_routing, solve_routing_assignment
from src.prechecker import precheck_capacity
from src.rule_sets import service as rule_set_service
from src.rule_sets import store as rule_set_store
from src.rule_sets import cli as rule_cli
from src.rule_sets import diagnose as rule_diagnose
from src.rule_sets import sensitivity as rule_sensitivity
from src.rules import REGISTRY, RuleCategory, RuleStatus, get_rules_for_site
from src.scheduler import APSScheduler, ScheduleResult, ScheduledTask
from src.shift_manager import (
    CalendarMode,
    DEFAULT_CHANGEOVER_MINUTES,
    evaluate_calendar_recommendation,
    get_topology,
)
from src.validator import validate_schedule, diagnose_empty_shifts

DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

def uploaded_source_path(site: str) -> str:
    return os.path.join(UPLOAD_DIR, f"current_{site}.xlsx")
DEFAULT_EXCEL = os.path.join(BASE_DIR, "dataSource.xlsx")
if not os.path.exists(DEFAULT_EXCEL):
    DEFAULT_EXCEL = os.path.join(BASE_DIR, "APS Database- Simulation draft.xlsx")
EXCEL_PATH = DEFAULT_EXCEL
# Each factory runs its own instance. FACTORY_SITE pins one instance to one site;
# DEFAULT_SITE is the fallback for a shared development instance.
FACTORY_SITE = os.getenv("FACTORY_SITE", "").strip().upper()
SITE = FACTORY_SITE or os.getenv("DEFAULT_SITE", "WL").strip().upper()
SINGLE_FACTORY = bool(FACTORY_SITE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app_state()
    yield


app = FastAPI(title="APS MVP 智能高级排产系统", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    """Healthcheck endpoint queried by Docker and Traefik edge gateway."""
    data_exists = os.path.exists(DATA_DIR) or os.path.exists(DEFAULT_EXCEL)
    return {
        "status": "healthy",
        "service": "aps-mvp",
        "version": "1.0.0",
        "default_site": SITE,
        "database_file_found": bool(data_exists),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/auth/me")
def get_current_user_identity(
    x_forwarded_user: Optional[str] = Header(None, alias="X-Forwarded-User"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    x_user_policies: Optional[str] = Header(None, alias="X-User-Policies"),
    x_user_scopes: Optional[str] = Header(None, alias="X-User-Scopes"),
):
    """Resolve user identity forwarded by Traefik and Valeo IAM Kerberos gateway."""
    return {
        "username": x_forwarded_user or os.getenv("DEFAULT_USER", "guest.user"),
        "user_id": x_user_id or "anonymous",
        "role": x_user_role or "USER",
        "policies": [p.strip() for p in x_user_policies.split(",") if p.strip()] if x_user_policies else ["aps:read", "aps:write"],
        "scopes": [s.strip() for s in x_user_scopes.split(",") if s.strip()] if x_user_scopes else [f"APS:CN01:{SITE}"],
    }

# Default and persistent settings per factory site
DEFAULT_SITE_SETTINGS: Dict[str, Dict[str, Any]] = {
    "WL": {
        "site": "WL",
        "plant_code": "WE34",
        "site_name": "WE34 (温岭工厂)",
        "factory_name": "温岭工厂",
        "horizon_days": 10,
        "start_date": "2026-10-12",
        "baseline_date": "2026-10-11",
        "calendar_mode": "SEVEN_ZERO",
        "sequence_setup": False,
        "setup_technicians_available": 0,
        "inventory_policy_enabled": False,
        "routing_assign_enabled": False,
        "leadtime_enabled": False,
        "lookahead_enabled": True,
        "lookahead_days": 30,
        "max_pull_forward_days": 10,
        "campaign_merge_enabled": True,
        "day_shift_overrides": {},
    },
    "SY38": {
        "site": "SY38",
        "plant_code": "SY38",
        "site_name": "SY38 (沈阳工厂)",
        "factory_name": "沈阳工厂",
        "horizon_days": 10,
        "start_date": "2026-10-12",
        "baseline_date": "2026-10-11",
        "calendar_mode": "FIVE_TWO",
        "sequence_setup": False,
        "setup_technicians_available": 0,
        "inventory_policy_enabled": False,
        "routing_assign_enabled": False,
        "leadtime_enabled": False,
        "lookahead_enabled": False,
        "lookahead_days": 30,
        "max_pull_forward_days": 10,
        "campaign_merge_enabled": False,
        "day_shift_overrides": {},
    },
}
# Alias WE34 to WL
DEFAULT_SITE_SETTINGS["WE34"] = DEFAULT_SITE_SETTINGS["WL"]

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
            pulled_o.due_datetime = horizon_end
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
    # A planner-uploaded workbook takes precedence over the checked-in CSV/Excel
    # fixture, so "upload -> smart solve" uses exactly what was uploaded.
    # Each site has its own uploaded file: WL and SY38 do not overwrite each other.
    uploaded = uploaded_source_path(site)
    if os.path.isfile(uploaded):
        return load_aps_database(uploaded, site_filter=site)
    source = DATA_DIR if os.path.exists(DATA_DIR) else EXCEL_PATH
    return load_aps_database(source, site_filter=site)


def generate_calendar_view(
    start_date_str: str,
    horizon_days: int,
    mode_str: str,
    overrides: Dict[str, Any],
    snap: Optional[ProductionInputSnapshot] = None,
    baseline_date_str: Optional[str] = None,
    site: str = "WL",
) -> Dict[str, Any]:
    topology = get_topology(site)
    try:
        y, m, d = [int(p) for p in start_date_str.split("-")]
        start_day = datetime(y, m, d)
    except Exception:
        start_day = datetime(2026, 10, 12)

    if baseline_date_str:
        try:
            by, bm, bd = [int(p) for p in baseline_date_str.split("-")]
            baseline_day = datetime(by, bm, bd)
        except Exception:
            baseline_day = start_day - timedelta(days=1)
    else:
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
            name: is_day_work and bool(shifts_ov.get(name, True))
            for name in topology.shift_names
        }
        open_shifts_count = sum(1 for v in shifts_status.values() if v)
        day_cap_hours = open_shifts_count * topology.shift_hours
        total_hours += day_cap_hours

        if open_shifts_count > 0:
            working_days_count += 1
            tag = "周末连产" if is_weekend else "正常工日"
            if open_shifts_count < topology.shifts_per_day:
                tag = f"部分开班 ({open_shifts_count}/{topology.shifts_per_day}班)"
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
        site=site,
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


# Initialize snapshot state
def init_app_state():
    try:
        session_state["snapshot"] = get_fresh_snapshot(SITE)
        session_state["plan"] = build_requirement_plan(session_state["snapshot"])
    except Exception as e:
        print(f"Error loading initial database: {e}")
    try:
        created = rule_set_service.ensure_seed()
        if created:
            print(f"[rules] seeded Phase 1 baseline for {', '.join(created)}")
    except Exception as e:
        print(f"[rules] seed skipped: {e}")


def startup_event():
    """Backward-compatible callable for test suites and offline harnesses."""
    init_app_state()


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
    baseline_date: Optional[str] = "2026-10-11"
    horizon_days: Optional[int] = 10
    calendar_mode: Optional[str] = "SEVEN_ZERO"
    day_shift_overrides: Optional[Dict[str, Any]] = None


class SiteSettingsPayload(BaseModel):
    site: str = "WL"
    horizon_days: Optional[int] = 10
    start_date: Optional[str] = "2026-10-12"
    baseline_date: Optional[str] = "2026-10-11"
    calendar_mode: Optional[str] = "SEVEN_ZERO"
    sequence_setup: Optional[bool] = False
    setup_technicians_available: Optional[int] = 0
    inventory_policy_enabled: Optional[bool] = False
    routing_assign_enabled: Optional[bool] = False
    leadtime_enabled: Optional[bool] = False
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
    baseline_date: Optional[str] = "2026-10-11"
    day_shift_overrides: Optional[Dict[str, Any]] = None
    allow_105_utilization_fg: bool = False
    allow_105_utilization_sf: bool = False
    rule_options: Optional[Dict[str, Any]] = None
    lookahead_enabled: Optional[bool] = None
    lookahead_days: Optional[int] = None
    max_pull_forward_days: Optional[int] = None
    use_rule_set: bool = True
    sequence_setup: bool = False
    setup_technicians_available: Optional[int] = None
    inventory_policy_enabled: Optional[bool] = None
    routing_assign_enabled: Optional[bool] = None
    leadtime_enabled: Optional[bool] = None


class RuleSetSavePayload(BaseModel):
    payload: Dict[str, Any]
    note: str = ""
    activate: bool = False


class RuleAnalysisPayload(BaseModel):
    version_id: Optional[int] = None
    site: str = "WL"
    budget: float = 15.0
    pct: float = 0.2


# ---------------- API Endpoints ----------------
@app.get("/api/sites")
def list_sites():
    return {
        "current": session_state["site"],
        "available": [
            {"code": "WL", "name": "WE34 (温岭工厂)", "description": "雨刮传动杆总成装配线 (PGLINKAGE) + 轴套冲压线 (轴套6)"},
            {"code": "SY38", "name": "SY38 (沈阳工厂)", "description": "注塑与涂装车间 (PU07/PU22 涂装线 + 1400T-6 注塑机)"},
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
    if payload.baseline_date is not None:
        cfg["baseline_date"] = payload.baseline_date
    if payload.calendar_mode is not None:
        cfg["calendar_mode"] = payload.calendar_mode
    if payload.sequence_setup is not None:
        cfg["sequence_setup"] = payload.sequence_setup
    if payload.setup_technicians_available is not None:
        cfg["setup_technicians_available"] = max(0, int(payload.setup_technicians_available))
    if payload.inventory_policy_enabled is not None:
        cfg["inventory_policy_enabled"] = payload.inventory_policy_enabled
    if payload.routing_assign_enabled is not None:
        cfg["routing_assign_enabled"] = payload.routing_assign_enabled
    if payload.leadtime_enabled is not None:
        cfg["leadtime_enabled"] = payload.leadtime_enabled
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
        session_state["calendar_config"]["baseline_date"] = cfg.get("baseline_date", "2026-10-11")
        session_state["calendar_config"]["calendar_mode"] = cfg["calendar_mode"]
        session_state["calendar_config"]["day_shift_overrides"] = cfg["day_shift_overrides"]

    return {"status": "success", "message": f"工厂 [{site}] 生产策略配置已更新", "settings": cfg}


@app.post("/api/sites/switch")
def switch_site(site: str = Body(..., embed=True)):
    site_key = "WL" if site in ["WL", "WE34"] else site
    if SINGLE_FACTORY and site_key != SITE:
        raise HTTPException(status_code=400, detail=f"该实例固定为 {SITE} 工厂，不能切换到 {site_key}")
    session_state["site"] = site_key
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


def _master_rows(sheet_name: str, site: str, limit: int = 500):
    """Read one planner-facing master-data sheet from the current per-site source."""
    source = uploaded_source_path(site) if os.path.isfile(uploaded_source_path(site)) else EXCEL_PATH
    if not os.path.isfile(source):
        return []
    wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            return []
        ws = wb[sheet_name]
        headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
        site_idx = next((i for i, h in enumerate(headers) if h.lower() == "site"), None)
        rows = []
        for values in ws.iter_rows(min_row=2, values_only=True):
            if site_idx is not None and str(values[site_idx] or "").strip() != site:
                continue
            item = {}
            for i, header in enumerate(headers):
                if not header:
                    continue
                value = values[i] if i < len(values) else None
                item[header] = value.isoformat(sep=" ") if hasattr(value, "isoformat") else value
            if any(v not in (None, "") for v in item.values()):
                rows.append(item)
            if len(rows) >= limit:
                break
        return rows
    finally:
        wb.close()


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
            "moq": p.moq,
            "lead_time_days": p.lead_time_days,
            "scrap_rate": p.scrap_rate,
            "max_stock": p.max_stock,
            "headcount": p.headcount,
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
            "moq": p.moq,
            "lead_time_days": p.lead_time_days,
            "scrap_rate": p.scrap_rate,
            "max_stock": p.max_stock,
            "headcount": p.headcount,
        }
        for p in snap.sf_params.values()
    ]

    # New master-data tables for UI tabs (current site, current uploaded source)
    routing_rows = _master_rows("Routing-Machines", session_state["site"])
    setup_matrix_rows = _master_rows("Setup-Matrix", session_state["site"])
    inventory_policy_rows = _master_rows("Inventory-Policy", session_state["site"])
    operation_leadtime_rows = _master_rows("Operation-LeadTime", session_state["site"])

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
        "routing": routing_rows,
        "setup_matrix": setup_matrix_rows,
        "inventory_policy": inventory_policy_rows,
        "operation_leadtime": operation_leadtime_rows,
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
    try:
        save_snapshot_json(snap, os.path.join(DATA_DIR, f"snapshot_{session_state['site']}.json"))
    except Exception:
        pass
    return {"status": "success", "message": "输入数据已更新"}



@app.get("/api/inputs/template")
def download_data_source_template():
    if not os.path.isfile(EXCEL_PATH):
        raise HTTPException(status_code=404, detail="dataSource.xlsx 模板不存在")
    return FileResponse(
        EXCEL_PATH,
        filename="APS_DataSource_Template.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _validate_template_schema(uploaded_path: str) -> int:
    """Reject uploads whose sheet set or row-1 schema differs from dataSource.xlsx."""
    if not os.path.isfile(EXCEL_PATH):
        raise ValueError("服务端缺少 dataSource.xlsx 标准模板")
    canonical = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    uploaded = openpyxl.load_workbook(uploaded_path, read_only=True, data_only=True)
    try:
        expected_sheets = list(canonical.sheetnames)
        actual_sheets = list(uploaded.sheetnames)
        missing = [name for name in expected_sheets if name not in actual_sheets]
        extra = [name for name in actual_sheets if name not in expected_sheets]
        if missing or extra:
            detail = []
            if missing:
                detail.append("缺少 sheet: " + ", ".join(missing))
            if extra:
                detail.append("多出 sheet: " + ", ".join(extra))
            raise ValueError("; ".join(detail))
        for sheet in expected_sheets:
            expected_ws = canonical[sheet]
            actual_ws = uploaded[sheet]
            expected = [str(expected_ws.cell(1, c).value or "").strip() for c in range(1, expected_ws.max_column + 1)]
            actual = [str(actual_ws.cell(1, c).value or "").strip() for c in range(1, actual_ws.max_column + 1)]
            if expected != actual:
                for idx, (a, b) in enumerate(zip(expected, actual), start=1):
                    if a != b:
                        raise ValueError(f"sheet [{sheet}] 表头第 {idx} 列不一致: 模板={a!r}, 上传={b!r}")
                raise ValueError(f"sheet [{sheet}] 表头列数不一致: 模板={len(expected)}, 上传={len(actual)}")
        return len(expected_sheets)
    finally:
        canonical.close()
        uploaded.close()


@app.post("/api/inputs/upload")
async def upload_data_source(file: UploadFile = File(...)):
    """Upload a planner-facing dataSource.xlsx and make it the active source.

    The workbook is validated by the same loader used by /api/solve, then kept as
    data/uploads/current.xlsx. The checked-in data is never overwritten.
    """
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="只接受 .xlsx 文件")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 25MB 限制")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    temp_path = os.path.join(UPLOAD_DIR, "upload.tmp.xlsx")
    with open(temp_path, "wb") as fh:
        fh.write(content)

    required_sheets = [
        "Parameters-FG", "Parameters-SFN", "BOM List", "Initial Stock",
        "Customer Demand", "Tooling-Mold", "Preventive-Maintenance",
        "Material-Arrival-ETA", "Customer-Priority",
    ]
    optional_sheets = [
        "Routing-Machines", "Setup-Matrix", "Operation-LeadTime",
        "Inventory-Policy", "Customer-Orders", "Trial-Windows", "Baseline-Schedule",
        "Calendar-Patterns", "Campaign-Families", "Setup-Technicians",
        "Crew-Pool", "Labor-Availability", "Line-Staffing",
    ]
    wb = None
    try:
        schema_sheet_count = _validate_template_schema(temp_path)
        wb = openpyxl.load_workbook(temp_path, read_only=True, data_only=True)
        sheetnames = set(wb.sheetnames)
        missing = [name for name in required_sheets if name not in sheetnames]
        if missing:
            raise ValueError("缺少必要 sheet: " + ", ".join(missing))
        site = session_state["site"]
        site_counts = {}
        for pn, ws in (("fg", wb["Parameters-FG"]), ("sf", wb["Parameters-SFN"])):
            count = 0
            for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
                if str(row[0] or "").strip() == site:
                    count += 1
            site_counts[pn] = count
        if not site_counts["fg"] and not site_counts["sf"]:
            raise ValueError(f"上传文件中没有 {site} 工厂的 Parameters-FG/SFN 数据")
        try:
            loaded = load_aps_database(temp_path, site_filter=site)
        except Exception as exc:
            raise ValueError(f"工作簿结构无法被 APS 解析: {exc}") from exc
    except Exception as exc:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    site = session_state["site"]
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.replace(temp_path, uploaded_source_path(site))
    session_state["snapshot"] = loaded
    session_state["plan"] = build_requirement_plan(loaded)
    session_state["result"] = None
    session_state["matrix"] = None
    session_state["checks"] = None
    session_state["compliance"] = None
    return {
        "status": "success",
        "filename": filename,
        "site": site,
        "site_counts": site_counts,
        "fg_params": len(loaded.fg_params),
        "sf_params": len(loaded.sf_params),
        "orders": len(loaded.orders),
        "sheets": sorted(sheetnames),
        "missing_optional_sheets": [name for name in optional_sheets if name not in sheetnames],
        "schema_sheet_count": schema_sheet_count,
        "message": f"已载入 {filename}，当前站点 {site} 数据已刷新",
    }


@app.delete("/api/inputs/orders/{order_id}")
def delete_order(order_id: str):
    snap = session_state["snapshot"]
    if snap is None:
        raise HTTPException(status_code=400, detail="Snapshot not loaded")
    snap.orders = [o for o in snap.orders if o.order_id != order_id]
    session_state["plan"] = build_requirement_plan(snap)
    try:
        save_snapshot_json(snap, os.path.join(DATA_DIR, f"snapshot_{session_state['site']}.json"))
    except Exception:
        pass
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
        baseline_date_str=cfg.get("baseline_date"),
        site=session_state["site"],
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
    if payload.baseline_date:
        cfg["baseline_date"] = payload.baseline_date
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
        baseline_date_str=cfg.get("baseline_date"),
        site=session_state["site"],
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
        site=session_state["site"],
    )


@app.get("/api/rule-catalog")
def get_rule_catalog():
    path = os.path.join(BASE_DIR, "config", "rule_catalog", "aps_32_rules.json")
    rules = load_catalog(path)
    return {
        "count": len(rules),
        "rules": [
            {
                "id": r.id,
                "class": r.rule_class,
                "category": r.category,
                "scope": r.scope,
                "name": r.name,
                "constraint": r.constraint,
                "weight": r.weight,
                "logic": r.logic,
                "legacy_ref": r.legacy_ref,
                "status": r.status,
                "phase": r.phase,
            }
            for r in rules
        ],
    }


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

    current_rule_set, _ = rule_set_service.active_rule_set(target_site)
    active_templates = {
        rule.template_id for rule in current_rule_set.rules if rule.enabled
    } if current_rule_set is not None else set()
    active_rule_ids: Dict[str, str] = {}
    if current_rule_set is not None:
        for rule in current_rule_set.rules:
            if rule.enabled:
                active_rule_ids.setdefault(rule.template_id, rule.rule_id)

    for r_id, r in sorted(site_rules.items(), key=lambda x: x[1].row_index):
        kernel_mapping = describe_legacy_rule(r_id, active_templates, active_rule_ids)
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
            "kernel_status": kernel_mapping["status"],
            "kernel_status_label": kernel_mapping["label"],
            "kernel_templates": kernel_mapping["templates"],
            "kernel_rule_ids": kernel_mapping["rule_ids"],
            "checked": True,  # Default all checked
            "load_limit_mode": "100",  # Default 95% ~ 100%
        })
    return {"site": target_site, "rules": rules_list}


@app.get("/api/rule-sets")
def list_rule_sets(site: Optional[str] = None):
    target = site or session_state["site"]
    return {"site": target, "versions": rule_set_service.list_versions(target)}


@app.get("/api/rule-sets/active")
def get_active_rule_set(site: Optional[str] = None):
    target = site or session_state["site"]
    record = rule_set_store.get_active(target)
    warnings: List[str] = []
    if record:
        _, warnings = rule_set_service.validate_payload(record["payload"])
    return {"site": target, "active": record, "warnings": warnings}


@app.get("/api/rule-sets/template")
def download_rule_template(site: Optional[str] = None):
    target = site or session_state["site"]
    handle, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(handle)
    rule_set_service.write_template(path, site=target)
    return FileResponse(
        path,
        filename=f"{target}_rule_template.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/rule-sets/import")
async def import_rule_set(
    file: UploadFile = File(...),
    site: str = Form("WL"),
    note: str = Form(""),
    activate: bool = Form(False),
):
    suffix = os.path.splitext(file.filename or "rules.xlsx")[1] or ".xlsx"
    handle, path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    try:
        with open(path, "wb") as output:
            output.write(await file.read())
        return rule_set_service.import_excel(path, site=site, note=note, activate=activate)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@app.post("/api/rule-sets/save")
def save_rule_set(payload: RuleSetSavePayload):
    return rule_set_service.save_payload(
        payload.payload,
        note=payload.note,
        activate=payload.activate,
    )


@app.get("/api/rule-sets/diff")
def diff_rule_sets(from_id: int, to_id: int):
    before = rule_set_service.get_version(from_id)
    after = rule_set_service.get_version(to_id)
    if before is None or after is None:
        raise HTTPException(status_code=404, detail="rule set version not found")
    return rule_set_service.diff_payloads(before["payload"], after["payload"])


@app.post("/api/rule-sets/{version_id}/activate")
def activate_rule_set(version_id: int):
    try:
        return rule_set_service.activate(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/rule-sets/{version_id}/export")
def export_rule_set(version_id: int):
    record = rule_set_service.get_version(version_id)
    if record is None:
        raise HTTPException(status_code=404, detail="rule set version not found")
    from src.kernel.types import RuleSet
    from src.rule_sets import excel as rule_excel

    rule_set = RuleSet.from_dict(record["payload"])
    handle, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(handle)
    rule_excel.export_rule_set(rule_set, path)
    return FileResponse(
        path,
        filename=f"{rule_set.site}_{rule_set.rule_set_id}_v{rule_set.version}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _analysis_payload(version_id: Optional[int], site: str) -> Dict[str, Any]:
    record = (
        rule_set_service.get_version(version_id)
        if version_id
        else rule_set_store.get_active(site)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="rule set version not found")
    return record["payload"]


@app.post("/api/rule-sets/sensitivity")
def rule_sensitivity_endpoint(payload: RuleAnalysisPayload):
    target_site = payload.site.upper()
    data = _analysis_payload(payload.version_id, target_site)
    solve_fn = rule_cli.make_solve_fn(target_site, max(5.0, payload.budget), 10)
    return rule_sensitivity.run_sensitivity(data, solve_fn, pct=payload.pct)


@app.post("/api/rule-sets/diagnose")
def rule_diagnose_endpoint(payload: RuleAnalysisPayload):
    target_site = payload.site.upper()
    data = _analysis_payload(payload.version_id, target_site)
    solve_fn = rule_cli.make_solve_fn(target_site, max(5.0, payload.budget), 10)
    return rule_diagnose.diagnose(data, solve_fn)


@app.get("/api/rule-sets/templates")
def list_rule_templates():
    from src.kernel.registry import TEMPLATES

    return [
        {
            "template_id": spec.template_id,
            "description": spec.description,
            "default_nature": spec.default_nature,
            "default_group": spec.default_group,
            "params": {param.name: param.default for param in spec.params},
        }
        for spec in TEMPLATES.values()
    ]


@app.get("/api/rule-sets/{version_id}")
def get_rule_set_version(version_id: int):
    record = rule_set_service.get_version(version_id)
    if record is None:
        raise HTTPException(status_code=404, detail="rule set version not found")
    return record


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
    if payload.baseline_date:
        cfg["baseline_date"] = payload.baseline_date
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

    # 105% utilization threshold option for R-FG-03, R-SF-01, R-WL-26, R-SY-08
    allow_105_fg = payload.allow_105_utilization_fg
    allow_105_sf = payload.allow_105_utilization_sf
    if payload.rule_options:
        for rk in ("R-FG-03", "R-WL-26", "R-SY-08"):
            if payload.rule_options.get(rk, {}).get("load_limit_mode") == "105":
                allow_105_fg = True
        for rk in ("R-SF-01", "R-WL-26", "R-SY-08"):
            if payload.rule_options.get(rk, {}).get("load_limit_mode") == "105":
                allow_105_sf = True
    enforce_on_time_delivery = ("R-FG-01" in enabled or "R-WL-20" in enabled or "R-SY-01" in enabled or "R-SY-09" in enabled)
    # R-WL-11 is the WL rule for "merge same-model runs, keep the line to a few
    # models per shift", so it also switches the ≤3-models-per-shift limit on.
    enforce_concurrency_limit = ("R-FG-13" in enabled or "R-SY-05" in enabled or "R-WL-11" in enabled)
    enforce_controlled_window = ("R-WL-14" in enabled)
    enforce_mpq_batch = ("R-FG-02" in enabled or "R-SF-09" in enabled or "R-WL-09" in enabled or "R-WL-15" in enabled)
    enforce_material_balance = ("R-SF-03" in enabled or "R-WL-23" in enabled or "R-SY-02" in enabled or "R-SY-04" in enabled)
    enforce_sequential_pulling = ("R-SF-02" in enabled or "R-FG-05" in enabled or "R-WL-12" in enabled)
    enforce_freeze_window = ("R-SF-04" in enabled or "R-FG-04" in enabled or "R-WL-10" in enabled)
    enforce_tooling_exclusivity = ("R-TL-01" in enabled or "R-WL-01" in enabled or "R-SY-03" in enabled)
    enforce_material_arrival = ("R-MT-01" in enabled or "R-WL-21" in enabled)
    enforce_pm_maintenance = ("R-PM-01" in enabled or "R-WL-27" in enabled)
    enforce_customer_priority = ("R-CR-01" in enabled or "R-WL-24" in enabled)
    enforce_weekend_penalty = ("R-SF-07" in enabled or "R-FG-09" in enabled or "R-WL-06" in enabled)

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

    kernel_rule_set = None
    kernel_templates = set()
    if payload.use_rule_set:
        kernel_rule_set, rule_warnings = rule_set_service.active_rule_set(payload.site.upper())
        for warning in rule_warnings:
            print(f"  [rules] {warning}")
        if kernel_rule_set is not None:
            from src.kernel.registry import IMPLEMENTED_TEMPLATES

            kernel_templates = {
                rule.template_id
                for rule in kernel_rule_set.rules
                if rule.enabled and rule.template_id in IMPLEMENTED_TEMPLATES
            }
            print(
                f"[rules] using {kernel_rule_set.rule_set_id} v{kernel_rule_set.version} "
                f"({len(kernel_rule_set.rules)} rules, kernel templates: {sorted(kernel_templates)})"
            )

    labor_data = load_labor_data(DATA_DIR, site=payload.site.upper())
    for warning in labor_data.warnings:
        print(f"  [labor] {warning}")
    for error in labor_data.errors:
        print(f"  [labor] {error}")

    routing_enabled = (
        payload.routing_assign_enabled
        if payload.routing_assign_enabled is not None
        else site_settings_store.get(payload.site.upper(), {}).get("routing_assign_enabled", False)
    )
    routing_decisions = []
    if routing_enabled:
        routing_path = os.path.join(DATA_DIR, "master", "Routing-Machines.csv")
        routing_table = load_routing(routing_path)
        topology = get_topology(payload.site.upper())
        line_names = {getattr(t, "line", "") for t in plan.tasks}
        for (_site, _part, _op), options in routing_table.items():
            if _site == payload.site.upper():
                line_names.update(o.candidate_line for o in options if o.candidate_line)
        capacity_hours = {
            line: float(horizon_days * topology.shifts_per_day * topology.shift_hours)
            for line in line_names
        }
        routing_decisions = solve_routing_assignment(
            plan.tasks, routing_table, site=payload.site.upper(),
            capacity_hours_by_line=capacity_hours, time_limit_seconds=5.0,
        )
        if not routing_decisions:
            routing_decisions = assign_routing_lines(
                plan.tasks, routing_table, site=payload.site.upper(),
                capacity_hours_by_line=capacity_hours, max_ratio=0.95,
            )
    inventory_enabled = (
        payload.inventory_policy_enabled
        if payload.inventory_policy_enabled is not None
        else site_settings_store.get(payload.site.upper(), {}).get("inventory_policy_enabled", False)
    )
    inventory_policies = (
        load_inventory_policy(os.path.join(DATA_DIR, "master", "Inventory-Policy.csv"), site=payload.site.upper())
        if inventory_enabled else {}
    )
    leadtime_enabled = (
        payload.leadtime_enabled
        if payload.leadtime_enabled is not None
        else site_settings_store.get(payload.site.upper(), {}).get("leadtime_enabled", False)
    )
    operation_lead_times = (
        load_operation_lead_times(os.path.join(DATA_DIR, "master", "Operation-LeadTime.csv"), site=payload.site.upper())
        if leadtime_enabled else {}
    )
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
        price_changeovers=("R-FG-06" in enabled or "R-FG-13" in enabled or "R-WL-11" in enabled or "R-WL-14" in enabled or "R-SY-05" in enabled),
        enforce_sequential_pulling=enforce_sequential_pulling,
        enforce_freeze_window=enforce_freeze_window,
        enforce_tooling_exclusivity=enforce_tooling_exclusivity,
        enforce_material_arrival=enforce_material_arrival,
        enforce_pm_maintenance=enforce_pm_maintenance,
        pm_shifts=pm_shifts_map,
        enforce_customer_priority=enforce_customer_priority,
        enforce_weekend_penalty=enforce_weekend_penalty,
        allow_105_utilization_fg=allow_105_fg,
        allow_105_utilization_sf=allow_105_sf,
        rule_set=kernel_rule_set,
        kernel_templates=kernel_templates,
        labor_data=labor_data,
        sequence_setup=bool(payload.sequence_setup or site_settings_store.get(payload.site.upper(), {}).get("sequence_setup", False)),
        setup_technicians_available=(
            payload.setup_technicians_available
            if payload.setup_technicians_available is not None
            else site_settings_store.get(payload.site.upper(), {}).get("setup_technicians_available", 0)
        ),
        inventory_policies=inventory_policies,
        operation_lead_times=operation_lead_times,
        enforce_lead_time=leadtime_enabled,
    )

    # Solve with capacity overload diagnosis
    solve_budget = float(payload.time_limit_seconds)
    if kernel_templates:
        # The compiled path carries extra variables/constraints; keep the UI wait
        # bounded but give the hard tier enough time to find the compliant packing.
        solve_budget = max(solve_budget, 90.0)
    try:
        result = scheduler.solve(time_limit_seconds=solve_budget)
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
    checks = validate_schedule(
        plan,
        result,
        site=payload.site,
        allow_105_fg=allow_105_fg or getattr(result, "allow_105_fg", False),
        allow_105_sf=allow_105_sf or getattr(result, "allow_105_sf", False),
    )
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
    shift_lookup = {(shift.date_str, shift.shift_name): shift for shift in scheduler.shifts}

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

        # One block per shift: this is what makes intermittent production visible.
        segments = []
        for (date_str, shift_name), qty in t.shift_breakdown.items():
            shift = shift_lookup.get((date_str, shift_name))
            if shift is None or qty <= 0:
                continue
            seg_left = max(0.0, round((shift.start_dt - horizon_start).total_seconds() / total_horizon_sec * 100.0, 3))
            seg_width = max(0.1, round((shift.end_dt - shift.start_dt).total_seconds() / total_horizon_sec * 100.0, 3))
            seg_seconds = t.shift_seconds.get((date_str, shift_name), 0.0)
            segments.append({
                "key": f"{date_str} {shift_name}",
                "date": date_str,
                "shift": shift_name,
                "qty": int(qty),
                "duration_hours": round(seg_seconds / 3600.0, 2),
                "start_dt": shift.start_dt.strftime("%m/%d %H:%M"),
                "end_dt": shift.end_dt.strftime("%m/%d %H:%M"),
                "start_timestamp": int(shift.start_dt.timestamp()),
                "end_timestamp": int(shift.end_dt.timestamp()),
                "left_percent": seg_left,
                "width_percent": seg_width,
            })
        segments.sort(key=lambda segment: segment["left_percent"])
        snake_points = " ".join(
            f"{segment['left_percent'] + segment['width_percent'] / 2:.3f},50"
            for segment in segments
        )

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
            "mold_id": getattr(t, "mold_id", None),
            "start_dt": t.start_dt.strftime("%m/%d %H:%M"),
            "end_dt": t.end_dt.strftime("%m/%d %H:%M"),
            "due_dt": t.due_datetime.strftime("%m/%d %H:%M") if t.due_datetime else "-",
            "start_timestamp": int(t.start_dt.timestamp()),
            "end_timestamp": int(t.end_dt.timestamp()),
            "due_timestamp": int(t.due_datetime.timestamp()) if t.due_datetime else None,
            "duration_hours": round(t.duration_hours, 1),
            "crew_required": getattr(t, "crew_required", 0.0),
            "slack_hours": slack_h,
            "is_late": (t.end_dt > t.due_datetime) if t.due_datetime else False,
            "left_percent": left_pct,
            "width_percent": dur_pct,
            "due_percent": due_pct,
            "shift_breakdown": {f"{k[0]} {k[1]}": v for k, v in t.shift_breakdown.items()},
            "shift_segments": segments,
            "snake_points": snake_points,
            "bom_children": bom_children,
            "bom_parents": bom_parents,
        })

    # Suggested production order and changeover flags per line.
    by_line: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in task_schedule_list:
        by_line[item["line"]].append(item)
    for line_items in by_line.values():
        line_items.sort(key=lambda item: item["start_timestamp"])
        previous = None
        for index, item in enumerate(line_items, start=1):
            item["sequence"] = index
            changed = previous is not None and (
                previous["part_no"] != item["part_no"]
                or previous.get("mold_id") != item.get("mold_id")
            )
            item["changeover"] = bool(changed)
            item["changeover_from"] = previous["part_no"] if changed else ""
            previous = item

    # Sequence small production blocks per line (for Gantt dots and table rows).
    segments_by_line: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
    for item in task_schedule_list:
        for segment in item.get("shift_segments", []):
            segments_by_line[item["line"]].append((item, segment))
    for line_segments in segments_by_line.values():
        line_segments.sort(key=lambda pair: (
            pair[1]["start_timestamp"], pair[0]["start_timestamp"], pair[0]["task_id"]
        ))
        for sequence, (item, segment) in enumerate(line_segments, start=1):
            segment["sequence"] = sequence
        previous_item = None
        previous_segment = None
        for sequence, (item, segment) in enumerate(line_segments, start=1):
            changed = previous_item is not None and (
                previous_item["part_no"] != item["part_no"]
                or previous_item.get("mold_id") != item.get("mold_id")
            )
            segment["changeover"] = bool(changed)
            segment["changeover_from"] = previous_item["part_no"] if changed else ""
            segment["same_shift_as_previous"] = bool(
                previous_segment is not None
                and previous_segment["start_timestamp"] == segment["start_timestamp"]
            )
            previous_item = item
            previous_segment = segment
    for item in task_schedule_list:
        for index, segment in enumerate(item.get("shift_segments", []), start=1):
            segment["task_sequence"] = index
            segment["is_first"] = index == 1

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

    # Compliance Matrix with Rich Data Drill-down
    check_status = {}
    check_by_rule = {}
    for c in checks:
        for r_id in c.rule_ids:
            check_status[r_id] = check_status.get(r_id, True) and c.passed
            check_by_rule[r_id] = c

    compliance_rows = []
    for r_id, rule in sorted(site_rules.items(), key=lambda x: x[1].row_index):
        is_checked = r_id in enabled
        if not is_checked:
            eff_status = "SKIPPED (未勾选)"
        elif r_id in check_status:
            eff_status = "PASS" if check_status[r_id] else "FAIL"
        else:
            eff_status = rule.status.value

        matched_check = check_by_rule.get(r_id)

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
            "metrics": getattr(matched_check, "metrics", {}) if matched_check else {},
            "metric_labels": labels_for(getattr(matched_check, "metrics", {}) if matched_check else {}),
            "calculation_details": getattr(matched_check, "calculation_details", []) if matched_check else [],
            "table_data": getattr(matched_check, "table_data", None) if matched_check else None,
            "violations": getattr(matched_check, "violations", []) if matched_check else [],
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

    # Validation Checks with Full Calculation Data
    checks_data = [
        {
            "name": c.name,
            "passed": c.passed,
            "rule_ids": c.rule_ids,
            "violations": c.violations,
            "total_violations": len(c.violations),
            "metrics": getattr(c, "metrics", {}),
            "metric_labels": labels_for(getattr(c, "metrics", {})),
            "calculation_details": getattr(c, "calculation_details", []),
            "table_data": getattr(c, "table_data", None),
        }
        for c in checks
    ]

    # Shift Idle Diagnostic Engine (Why shifts are empty)
    empty_shift_reasons = diagnose_empty_shifts(result.tasks, scheduler.shifts, plan)

    # B4-b-a: inventory policy projection is an audit surface, not a hard model
    # constraint yet. Keep it optional and side-effect free.
    inventory_policies = load_inventory_policy(
        os.path.join(DATA_DIR, "master", "Inventory-Policy.csv"), site=payload.site.upper()
    )
    inventory_projection = project_inventory(
        result.tasks, solve_snap, scheduler.shifts, inventory_policies, site=payload.site.upper()
    )
    inventory_data = {
        "metrics": inventory_projection.metrics,
        "violations": inventory_projection.violations[:50],
        "points": [
            {
                "part_no": p.part_no,
                "shift_key": p.shift_key,
                "day": p.day,
                "production": p.production,
                "demand": p.demand,
                "inventory": p.inventory,
                "period": p.policy.period_type if p.policy else None,
                "violation": p.violation,
            }
            for p in inventory_projection.points
        ],
    }

    return {
        "status": "success",
        "solve_time": session_state["last_solve_time"],
        "kpi": {
            "status_name": result.status_name,
            "capacity_note": getattr(result, "capacity_note", ""),
            "allow_105_fg": getattr(result, "allow_105_fg", allow_105_fg),
            "allow_105_sf": getattr(result, "allow_105_sf", allow_105_sf),
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
        "sequence_plan": result.sequence_plan or [],
        "routing_decisions": routing_decisions,
        "checks": checks_data,
        "compliance_matrix": compliance_rows,
        "inventory_projection": inventory_data,
        "empty_shift_reasons": empty_shift_reasons,
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


@app.post("/api/audit_manual_schedule")
async def audit_manual_schedule(
    file: UploadFile = File(...),
    site: str = Form("WL"),
):
    """Perform independent compliance audit on a user-uploaded manual schedule Excel file."""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法打开上传的 Excel 文件: {e}")

    site_upper = (site or "WL").upper()
    snap = session_state.get("snapshot") or get_fresh_snapshot(site_upper)
    settings = get_site_settings(site_upper)
    horizon_days = settings.get("horizon_days", 10)
    start_date_str = settings.get("start_date", "2026-10-12")
    cal_mode_str = settings.get("calendar_mode", "SEVEN_ZERO")
    y, m, d = [int(p) for p in start_date_str.split("-")]
    start_dt = datetime(y, m, d)

    try:
        cal_mode = CalendarMode(cal_mode_str)
    except Exception:
        cal_mode = CalendarMode.SEVEN_ZERO

    plan = session_state.get("plan") or build_requirement_plan(snap)
    scheduler = APSScheduler(
        plan=plan,
        start_datetime=start_dt,
        planning_days=horizon_days,
        calendar_mode=cal_mode,
        site=site_upper,
    )
    shifts = scheduler.shifts
    index_of = {}
    shift_by_index = {}
    for s in shifts:
        index_of[(s.date_str, s.shift_name)] = s.index
        index_of[f"{s.date_str} {s.shift_name}"] = s.index
        shift_by_index[s.index] = s

    # Detect worksheet
    ws = None
    for name in ["生产排程与物料平衡总表 (PSI)", "Production plan-Done"]:
        if name in wb.sheetnames:
            ws = wb[name]
            break
    if ws is None:
        ws = wb.active

    tasks_raw = []
    if "生产排程" in ws.title or "PSI" in ws.title:
        shift_cols = {}
        last_date = ""
        for col in range(5, ws.max_column + 1):
            d_val = ws.cell(1, col).value
            s_val = ws.cell(2, col).value
            if d_val:
                last_date = d_val if isinstance(d_val, str) else f"{d_val.month}/{d_val.day}"
            if s_val:
                s_name = str(s_val).strip()
                if s_name == "早":
                    s_name = "早班"
                elif s_name == "中":
                    s_name = "中班"
                elif s_name == "晚":
                    s_name = "晚班"
                shift_cols[col] = f"{last_date} {s_name}"
        for r in range(3, ws.max_row + 1):
            metric = str(ws.cell(r, 3).value or "").strip()
            if "Production plan" in metric or "排产计划" in metric:
                # 准确解析料号与产线：
                # 在导出的 PSI 矩阵中，第 r-1 行（Customer demand）的第 1 列是料号 PN；
                # 第 r 行（Production plan）的第 1 列是产线 Line · Site，第 2 列是 MPQ/TRP 参数规格。
                raw_c1 = str(ws.cell(r, 1).value or "").strip()
                raw_pn = str(ws.cell(r - 1, 1).value or "").strip()
                pn = raw_pn if raw_pn and not raw_pn.startswith("Site") and not raw_pn.startswith("料号") and not raw_pn.startswith("站点") else raw_c1
                line = raw_c1.split("·")[0].strip() if "·" in raw_c1 else raw_c1

                # 优先根据主数据校准产线名称，防止取到 MPQ/TRP 或规格说明
                param = snap.fg_params.get(pn) or snap.sf_params.get(pn)
                if param:
                    line = param.line

                breakdown = {}
                for c, sk in shift_cols.items():
                    v = ws.cell(r, c).value
                    if isinstance(v, (int, float)) and v > 0:
                        breakdown[sk] = float(v)
                if breakdown and pn:
                    tasks_raw.append({"part_no": pn, "line": line, "breakdown": breakdown})
    else:
        shift_cols = {}
        last_date = ""
        for col in range(10, ws.max_column + 1):
            d_val = ws.cell(1, col).value
            s_val = ws.cell(2, col).value
            if d_val:
                if isinstance(d_val, datetime):
                    last_date = f"{d_val.month}/{d_val.day}"
                else:
                    last_date = str(d_val).strip()
            if s_val:
                s_name = str(s_val).strip()
                if s_name == "早":
                    s_name = "早班"
                elif s_name == "中":
                    s_name = "中班"
                elif s_name == "晚":
                    s_name = "晚班"
                shift_cols[col] = f"{last_date} {s_name}"
        for r in range(3, ws.max_row + 1):
            line = str(ws.cell(r, 1).value or "").strip()
            pn = str(ws.cell(r, 3).value or "").strip()
            if not pn:
                continue
            breakdown = {}
            for c, sk in shift_cols.items():
                v = ws.cell(r, c).value
                if isinstance(v, (int, float)) and v > 0:
                    breakdown[sk] = float(v)
            if breakdown:
                tasks_raw.append({"part_no": pn, "line": line, "breakdown": breakdown})

    if not tasks_raw:
        raise HTTPException(
            status_code=400,
            detail="未能从上传的 Excel 文件中解析出排产计划数据，请确认文件包含【生产排程与物料平衡总表 (PSI)】或【Production plan-Done】工作表且包含分班排产数值。"
        )

    # Reconstruct ScheduledTask objects
    manual_tasks: List[ScheduledTask] = []
    task_idx = 1
    assigned_task_ids: set = set()
    for item in tasks_raw:
        pn = item["part_no"]
        line = item["line"]
        breakdown = item["breakdown"]

        is_fg = pn in snap.fg_params
        param = snap.fg_params.get(pn) if is_fg else snap.sf_params.get(pn)
        stage = "FG" if is_fg else "SF"
        default_line = "PGLINKAGE" if is_fg else "轴套6"
        task_line = param.line if param else (line if (line and not line.startswith("MPQ") and line != "None") else default_line)
        ct = param.ct_seconds if param else (40.0 if is_fg else 25.0)
        trp = param.trp if param else (0.71 if is_fg else 0.85)
        mpq = int(param.mpq) if param else 1
        desc = param.description if param else pn

        # Map breakdown keys to (date_str, shift_name)
        mapped_breakdown = {}
        for sk, qty in breakdown.items():
            if isinstance(sk, tuple):
                mapped_breakdown[sk] = int(round(qty))
            else:
                parts = str(sk).split()
                tuple_key = (parts[0], parts[1]) if len(parts) >= 2 else (str(sk), "早班")
                mapped_breakdown[tuple_key] = int(round(qty))

        matching = sorted([t for t in plan.tasks if t.part_no == pn], key=lambda t: t.due_datetime or datetime.max)
        if not matching or len(matching) == 1:
            matched_t = matching[0] if matching else None
            tot_qty = sum(mapped_breakdown.values())
            active_indices = [index_of[k] for k in mapped_breakdown if k in index_of]
            if not active_indices:
                continue
            min_s, max_s = min(active_indices), max(active_indices)
            shift_secs = {k: v * (ct / trp) for k, v in mapped_breakdown.items()}
            manual_tasks.append(ScheduledTask(
                task_id=matched_t.task_id if matched_t else f"MANUAL_{stage}_{pn}_{task_idx:02d}",
                stage=stage,
                line=task_line,
                part_no=pn,
                description=desc,
                quantity=float(tot_qty),
                ct_seconds=ct,
                trp=trp,
                mpq=mpq,
                lot_size=mpq if is_fg else 1,
                lots=int(tot_qty // mpq) if is_fg else int(tot_qty),
                order_id=matched_t.order_id if matched_t else f"ORD_MANUAL_{pn}",
                due_datetime=matched_t.due_datetime if matched_t else (shift_by_index[min_s].start_dt + timedelta(days=7)),
                start_dt=shift_by_index[min_s].start_dt,
                end_dt=shift_by_index[max_s].end_dt,
                duration_hours=sum(shift_secs.values()) / 3600.0,
                shift_breakdown=mapped_breakdown,
                shift_seconds=shift_secs,
            ))
            task_idx += 1
        else:
            # Multiple orders for same part_no: FIFO chronological distribution
            rem_needs = {t.task_id: t.quantity for t in matching}
            task_breakdowns = defaultdict(dict)
            sorted_shifts = sorted(mapped_breakdown.keys(), key=lambda k: index_of.get(k, 0))
            for sk in sorted_shifts:
                avail = mapped_breakdown[sk]
                for t in matching:
                    if avail <= 0:
                        break
                    rem = rem_needs[t.task_id]
                    if rem <= 0:
                        continue
                    take = min(avail, rem)
                    task_breakdowns[t.task_id][sk] = take
                    rem_needs[t.task_id] -= take
                    avail -= take
            if avail > 0:
                last_t = matching[-1]
                last_sk = sorted_shifts[-1]
                task_breakdowns[last_t.task_id][last_sk] = task_breakdowns[last_t.task_id].get(last_sk, 0) + avail

            for t in matching:
                tb = task_breakdowns.get(t.task_id, {})
                tot_qty = sum(tb.values())
                if tot_qty <= 0:
                    continue
                active_indices = [index_of[k] for k in tb if k in index_of]
                if not active_indices:
                    continue
                min_s, max_s = min(active_indices), max(active_indices)
                shift_secs = {k: v * (ct / trp) for k, v in tb.items()}
                manual_tasks.append(ScheduledTask(
                    task_id=t.task_id,
                    stage=stage,
                    line=task_line,
                    part_no=pn,
                    description=desc,
                    quantity=float(tot_qty),
                    ct_seconds=ct,
                    trp=trp,
                    mpq=mpq,
                    lot_size=mpq if is_fg else 1,
                    lots=int(tot_qty // mpq) if is_fg else int(tot_qty),
                    order_id=t.order_id,
                    due_datetime=t.due_datetime,
                    start_dt=shift_by_index[min_s].start_dt,
                    end_dt=shift_by_index[max_s].end_dt,
                    duration_hours=sum(shift_secs.values()) / 3600.0,
                    shift_breakdown=tb,
                    shift_seconds=shift_secs,
                ))

    manual_result = ScheduleResult(
        tasks=manual_tasks,
        shifts=shifts,
        strict_utilization=True,
        status_name="MANUAL_AUDIT",
        objective_value=0.0,
        wall_time_seconds=0.02,
    )

    baseline_result = session_state.get("result")
    # Identify frozen window tasks if baseline exists
    if baseline_result and getattr(baseline_result, "tasks", None):
        baseline_task_map = {t.task_id: t for t in baseline_result.tasks}
        for mt in manual_tasks:
            bt = baseline_task_map.get(mt.task_id)
            if bt:
                bt_min_s = min([index_of[k] for k in bt.shift_breakdown if k in index_of], default=999)
                if bt_min_s < 3:  # First 3 shifts = 24h freeze fence
                    setattr(mt, "is_locked", True)

    allow_105_fg = settings.get("allow_105_utilization_fg", True)
    allow_105_sf = settings.get("allow_105_utilization_sf", True)
    checks = validate_schedule(
        plan,
        manual_result,
        site=site_upper,
        allow_105_fg=allow_105_fg,
        allow_105_sf=allow_105_sf,
        baseline_result=baseline_result,
    )

    # Second, independent part-level audit: no solver objects, own netting/BOM/CT-TRP.
    # The uploaded PSI sheet is part-level, so the audit aggregates the requirement
    # plan by part instead of by order.
    audit_rows = []
    for item in tasks_raw:
        pn = item["part_no"]
        param = snap.fg_params.get(pn) or snap.sf_params.get(pn)
        stage = "FG" if pn in snap.fg_params else "SF"
        line = param.line if param else item["line"]
        for shift_key, qty in item["breakdown"].items():
            audit_rows.append(
                PlanRow(
                    task_id=f"MANUAL::{pn}",
                    stage=stage,
                    line=line,
                    part_no=pn,
                    order_id="",
                    shift_key=str(shift_key),
                    quantity=float(qty),
                )
            )
    shift_infos = [
        ShiftInfo(
            key=f"{s.date_str} {s.shift_name}",
            index=s.index,
            date_str=s.date_str,
            shift_name=s.shift_name,
            start_dt=s.start_dt,
            end_dt=s.end_dt,
            duration_hours=s.duration_hours,
        )
        for s in shifts
    ]
    independent_audit = audit_schedule_part_level(
        snap,
        audit_rows,
        shift_infos,
        AuditConfig(
            setup_minutes=DEFAULT_CHANGEOVER_MINUTES,
            allow_105=allow_105_fg or allow_105_sf,
            delivery_tolerance_minutes=24.0 if (allow_105_fg or allow_105_sf) else 0.0,
        ),
    )
    empty_shift_reasons = diagnose_empty_shifts(manual_tasks, shifts, plan)
    matrix = build_validation_matrix(snap, plan, manual_result, site=site_upper)

    # Compliance matrix
    site_rules = get_rules_for_site(site_upper)
    check_status = {}
    check_by_rule = {}
    for c in checks:
        for r_id in c.rule_ids:
            check_status[r_id] = check_status.get(r_id, True) and c.passed
            check_by_rule[r_id] = c

    compliance_rows = []
    for r_id, rule in sorted(site_rules.items(), key=lambda x: x[1].row_index):
        if r_id in check_status:
            eff_status = "PASS" if check_status[r_id] else "FAIL"
        else:
            eff_status = rule.status.value

        matched_check = check_by_rule.get(r_id)
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
            "is_checked": True,
            "metrics": getattr(matched_check, "metrics", {}) if matched_check else {},
            "metric_labels": labels_for(getattr(matched_check, "metrics", {}) if matched_check else {}),
            "calculation_details": getattr(matched_check, "calculation_details", []) if matched_check else [],
            "table_data": getattr(matched_check, "table_data", None) if matched_check else None,
            "violations": getattr(matched_check, "violations", []) if matched_check else [],
        })

    checks_data = [
        {
            "name": c.name,
            "passed": c.passed,
            "rule_ids": c.rule_ids,
            "violations": c.violations,
            "total_violations": len(c.violations),
            "metrics": getattr(c, "metrics", {}),
            "metric_labels": labels_for(getattr(c, "metrics", {})),
            "calculation_details": getattr(c, "calculation_details", []),
            "table_data": getattr(c, "table_data", None),
        }
        for c in checks
    ]

    # Line Load Output
    line_loads_data = [
        {"site": row.site, "line": row.line, "metric": row.metric, "shift_values": row.shift_values}
        for row in matrix.line_loads
    ]

    # FG & SF Balance
    fg_balance_data = [
        {
            "site": row.site, "line": row.line, "part_no": row.part_no, "description": row.description,
            "initial_stock": row.initial_stock, "ct_seconds": row.ct_seconds, "trp": row.trp, "mpq": row.mpq,
            "total_demand": row.total_demand, "avg_daily_demand": round(row.avg_daily_demand, 1),
            "demand": row.demand_by_shift, "plan": row.plan_by_shift, "stock": row.stock_by_shift, "coverage": row.coverage_by_shift,
        }
        for row in matrix.fg_balance
    ]
    sf_balance_data = [
        {
            "site": row.site, "line": row.line, "part_no": row.part_no, "description": row.description,
            "initial_stock": row.initial_stock, "ct_seconds": row.ct_seconds, "trp": row.trp, "mpq": row.mpq,
            "total_demand": row.total_demand, "avg_daily_demand": round(row.avg_daily_demand, 1),
            "demand": row.demand_by_shift, "plan": row.plan_by_shift, "stock": row.stock_by_shift, "coverage": row.coverage_by_shift,
        }
        for row in matrix.sf_balance
    ]

    # KPIs
    fg_tasks = [t for t in manual_tasks if t.stage == "FG"]
    sf_tasks = [t for t in manual_tasks if t.stage == "SF"]
    late_tasks = [t for t in fg_tasks if t.end_dt > t.due_datetime]
    on_time_rate = ((len(fg_tasks) - len(late_tasks)) / len(fg_tasks) * 100.0) if fg_tasks else 100.0

    min_start = min((t.start_dt for t in manual_tasks), default=start_dt)
    max_end = max((t.end_dt for t in manual_tasks), default=start_dt + timedelta(days=horizon_days))
    makespan_hours = (max_end - min_start).total_seconds() / 3600.0

    line_utilizations = {}
    for ll in matrix.line_loads:
        if ll.metric == "Loading Rate":
            rates = [v for v in ll.shift_values.values() if isinstance(v, (int, float)) and v > 0]
            line_utilizations[ll.line] = round((sum(rates) / len(rates)) if rates else 0.0, 1)

    total_lines_cnt = len(line_utilizations)
    qualified_lines_cnt = len([r for r in line_utilizations.values() if r >= 95.0])
    avg_line_util = round(sum(line_utilizations.values()) / max(1, total_lines_cnt), 1) if total_lines_cnt else 0.0

    horizon_start = shifts[0].start_dt if shifts else min_start
    horizon_end = shifts[-1].end_dt if shifts else max_end
    total_horizon_sec = max(1.0, (horizon_end - horizon_start).total_seconds())

    task_schedule_list = []
    for t in sorted(manual_tasks, key=lambda x: (x.stage, x.start_dt)):
        slack_h = round((t.due_datetime - t.end_dt).total_seconds() / 3600.0, 1) if t.due_datetime else 999.0
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
            "duration_hours": round(t.duration_hours, 1),
            "crew_required": getattr(t, "crew_required", 0.0),
            "slack_hours": slack_h,
            "is_late": (t.end_dt > t.due_datetime) if t.due_datetime else False,
            "left_percent": left_pct,
            "width_percent": dur_pct,
            "due_percent": due_pct,
            "shift_breakdown": {f"{k}": v for k, v in t.shift_breakdown.items()},
            "bom_children": [],
            "bom_parents": [],
        })

    return {
        "status": "success",
        "is_manual": True,
        "manual_filename": file.filename,
        "audit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kpi": {
            "status_name": "MANUAL_AUDITED",
            "total_fg_tasks": len(fg_tasks),
            "total_sf_tasks": len(sf_tasks),
            "total_tasks": len(manual_tasks),
            "on_time_delivery_rate": round(on_time_rate, 1),
            "late_task_count": len(late_tasks),
            "makespan_hours": round(makespan_hours, 1),
            "wall_time_seconds": 0.02,
            "line_utilizations": line_utilizations,
            "avg_line_utilization": avg_line_util,
            "total_lines_count": total_lines_cnt,
            "qualified_lines_count": qualified_lines_cnt,
            "unqualified_lines_count": total_lines_cnt - qualified_lines_cnt,
            "unqualified_lines": [l for l, r in line_utilizations.items() if r < 95.0],
            "schedule_start": min_start.strftime("%Y-%m-%d %H:%M"),
            "schedule_end": max_end.strftime("%Y-%m-%d %H:%M"),
        },
        "gantt_horizon": {
            "start_dt": horizon_start.strftime("%Y-%m-%d %H:%M"),
            "end_dt": horizon_end.strftime("%Y-%m-%d %H:%M"),
            "total_seconds": int(total_horizon_sec),
            "total_hours": round(total_horizon_sec / 3600.0, 1),
            "shift_count": len(shifts),
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
            for s in shifts
        ],
        "shift_columns": matrix.shift_columns,
        "line_loads": line_loads_data,
        "fg_balance": fg_balance_data,
        "sf_balance": sf_balance_data,
        "tasks": task_schedule_list,
        "checks": checks_data,
        "compliance_matrix": compliance_rows,
        "independent_audit": {
            "passed": independent_audit.passed,
            "summary": f"{len(independent_audit.checks) - len(independent_audit.failed_names())}/{len(independent_audit.checks)} 通过",
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "violations": check.violations[:20],
                    "metrics": check.metrics,
                    "metric_labels": labels_for(check.metrics),
                }
                for check in independent_audit.checks
            ],
        },
        "empty_shift_reasons": empty_shift_reasons,
    }


# Mount static directory for single-page web app
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/rule-sets", response_class=HTMLResponse)
def rule_sets_page():
    rule_sets_file = os.path.join(STATIC_DIR, "rule_sets.html")
    if os.path.exists(rule_sets_file):
        with open(rule_sets_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Rule Set Editor - Frontend Building...</h1>"


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>APS MVP Web Interface - Frontend Building...</h1>"


@app.get("/video", response_class=HTMLResponse)
def video_page():
    video_file = os.path.join(STATIC_DIR, "storyboard.html")
    if os.path.exists(video_file):
        with open(video_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>APS Storyboard Video Not Found</h1>"


@app.get("/favicon.ico")
def favicon():
    fav_file = os.path.join(STATIC_DIR, "valeo_logo.png")
    if os.path.exists(fav_file):
        return FileResponse(fav_file, media_type="image/png")
    return HTMLResponse("", status_code=404)

