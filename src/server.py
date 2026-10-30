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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.audit import AuditConfig, PlanRow, ShiftInfo, audit_schedule_part_level
from src.data_loader import (
    CustomerOrder,
    ProductionInputSnapshot,
    RequirementPlan,
    build_requirement_plan,
    load_aps_database,
)
from src.exporter import export_production_plan, export_validation_matrix_excel
from src.kernel.rule_mapping import describe_legacy_rule
from src.inventory_policy import load_inventory_policy, project_inventory
from src.labor import load_labor_data
from src.campaign import load_campaign_families
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




app = FastAPI(title="APS MVP 智能高级排产系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from src.scenarios import scenarios_router
app.include_router(scenarios_router)


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
# Alias WE34 to WL




# In-memory storage for active session






# Initialize snapshot state




# ---------------- API Models ----------------




















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
    target_site = site or SITE
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
    target = site or SITE
    return {"site": target, "versions": rule_set_service.list_versions(target)}


@app.get("/api/rule-sets/active")
def get_active_rule_set(site: Optional[str] = None):
    target = site or SITE
    record = rule_set_store.get_active(target)
    warnings: List[str] = []
    if record:
        _, warnings = rule_set_service.validate_payload(record["payload"])
    return {"site": target, "active": record, "warnings": warnings}


@app.get("/api/rule-sets/template")
def download_rule_template(site: Optional[str] = None):
    target = site or SITE
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
@app.get("/scenarios", response_class=HTMLResponse)
def scenarios_page():
    scenarios_file = os.path.join(STATIC_DIR, "scenarios.html")
    if os.path.exists(scenarios_file):
        with open(scenarios_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Scenarios Workbench - Frontend Building...</h1>"


@app.get("/dashboard")
@app.get("/panorama")
def dashboard_redirect(scenario_id: Optional[str] = None):
    """Redirects legacy dashboard URLs directly into the modern unified scenario workbench."""
    target = f"/scenarios?scenario_id={scenario_id}" if scenario_id else "/scenarios"
    return RedirectResponse(url=target, status_code=307)


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

