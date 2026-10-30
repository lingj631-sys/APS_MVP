"""FastAPI RESTful router for Scenario Platform endpoints (/api/scenarios)."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from src.scenarios.service import ScenarioService
from src.scenarios.ingestion import IngestionService

router = APIRouter(prefix="/api/scenarios", tags=["Scenarios"])
_service = ScenarioService()


class CreateScenarioPayload(BaseModel):
    id: Optional[str] = None
    name: str
    site: str
    manifest: Optional[Dict[str, Any]] = None
    clone_from_id: Optional[str] = None


class UpdateScenarioPayload(BaseModel):
    name: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None


class SolveScenarioPayload(BaseModel):
    time_limit_seconds: Optional[float] = 10.0


class CompareScenariosPayload(BaseModel):
    scenario_ids: List[str] = Field(default_factory=list)


# ---------------- Building Block Payload Models ----------------

class SaveLinePayload(BaseModel):
    line_code: str
    site_code: str = "WL"
    workshop_code: str = "WE34"
    stage: str = "FG"
    default_calendar_id: str = "WL_5_2"
    max_util_rate: float = 1.05
    default_crew: int = 1
    is_bottleneck: int = 0


class BOMItemPayload(BaseModel):
    parent_part_no: str
    child_part_no: str
    usage_ratio: float = 1.0
    scrap_rate: float = 0.0


class SaveBOMPackagePayload(BaseModel):
    package_id: str
    name: str
    site_code: str = "WL"
    version_no: int = 1
    items: List[BOMItemPayload] = Field(default_factory=list)


class SaveCalendarPayload(BaseModel):
    pattern_id: str
    site_code: str = "WL"
    name: str
    workdays_per_week: int
    shifts_per_day: int
    shift_hours: float = 8.0
    shift_start_times: str = "07:00,15:00,23:00"
    shift_names: str = "早班,中班,晚班"
    note: str = ""


class InventoryItemPayload(BaseModel):
    part_no: str
    stage: str = "FG"
    on_hand_qty: float = 0.0
    safety_stock: float = 0.0


class SaveInventoryPackagePayload(BaseModel):
    package_id: str
    name: str
    site_code: str = "WL"
    items: List[InventoryItemPayload] = Field(default_factory=list)


# ---------------- Scenarios Endpoints ----------------


@router.post("/upload-excel", status_code=200)
async def upload_excel(
    file: UploadFile = File(...),
    site: str = Form("WL"),
    version_tag: Optional[str] = Form(None),
):
    """Uploads a factory Excel workbook and automatically unbundles it into the 7 building block tables."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are supported.")

    tag = version_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    site_upper = site.upper()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        ingestion = IngestionService(_service.repo)
        ingested = ingestion.ingest_excel(tmp_path, site=site_upper, version_tag=tag)
        return {
            "status": "success",
            "site": site_upper,
            "version_tag": tag,
            "filename": file.filename,
            "ingested": ingested,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to ingest Excel: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.get("", response_model=List[Dict[str, Any]])
def list_scenarios(
    site: Optional[str] = Query(None, description="Filter by site code (WL, SY38)"),
    status: Optional[str] = Query(None, description="Filter by status (DRAFT, SOLVED, RELEASED)"),
):
    """List scenarios with optional site and status filters."""
    return _service.list_scenarios(site=site, status=status)


@router.get("/building-blocks")
def get_building_blocks(
    site: str = Query("WL", description="Site code (WL, SY38)"),
):
    """List all available modular building blocks for scenario assembly."""
    return _service.repo.get_available_building_blocks(site=site)


@router.post("", status_code=201)
def create_scenario(
    payload: CreateScenarioPayload,
    x_forwarded_user: Optional[str] = Header(None, alias="X-Forwarded-User"),
):
    """Create a new scenario or clone an existing one."""
    created_by = x_forwarded_user or "system"
    return _service.create_scenario(
        name=payload.name,
        site=payload.site,
        manifest=payload.manifest,
        scenario_id=payload.id,
        clone_from_id=payload.clone_from_id,
        created_by=created_by,
    )


@router.get("/{scenario_id}")
def get_scenario(scenario_id: str):
    """Retrieve single scenario by ID."""
    scn = _service.get_scenario(scenario_id)
    if not scn:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scn


@router.put("/{scenario_id}")
def update_scenario(scenario_id: str, payload: UpdateScenarioPayload):
    """Update scenario metadata or manifest."""
    try:
        return _service.update_scenario(
            scenario_id=scenario_id,
            name=payload.name,
            manifest=payload.manifest,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Scenario not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: str):
    """Delete scenario and child tasks."""
    success = _service.delete_scenario(scenario_id)
    if not success:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return {"status": "success", "id": scenario_id}


@router.post("/{scenario_id}/solve")
def solve_scenario(
    scenario_id: str,
    payload: Optional[SolveScenarioPayload] = None,
):
    """Assemble context, solve with CP-SAT and persist KPIs + 17-item checks."""
    time_limit = payload.time_limit_seconds if payload else 10.0
    try:
        return _service.solve_scenario(scenario_id=scenario_id, time_limit_seconds=time_limit or 10.0)
    except KeyError:
        raise HTTPException(status_code=404, detail="Scenario not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{scenario_id}/tasks")
def get_scenario_tasks(scenario_id: str):
    """Get schedule task list for solved scenario."""
    return _service.get_scenario_tasks(scenario_id)


@router.get("/{scenario_id}/panorama")
def get_scenario_panorama(scenario_id: str):
    """Reconstructs the full scheduling context and returns panorama matrices for dashboard."""
    try:
        return _service.get_scenario_panorama(scenario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Scenario not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{scenario_id}/release")
def release_scenario(scenario_id: str):
    """Officially lock and release a solved scenario."""
    try:
        return _service.release_scenario(scenario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Scenario not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/compare")
def compare_scenarios(payload: CompareScenariosPayload):
    """Compare multiple scenarios on KPIs, utilization, setup time, and checks."""
    return _service.compare_scenarios(payload.scenario_ids)


# ---------------- Building Block Asset Hub Routes ----------------

@router.post("/blocks/lines", status_code=201)
def save_line(payload: SaveLinePayload):
    """Upsert a production line into lines asset table."""
    _service.repo.save_line(payload.model_dump())
    return {"status": "success", "line_code": payload.line_code}


@router.delete("/blocks/lines/{line_code}")
def delete_line(line_code: str):
    """Delete a production line from lines asset table."""
    success = _service.repo.delete_line(line_code)
    if not success:
        raise HTTPException(status_code=404, detail="Line not found")
    return {"status": "success", "line_code": line_code}


@router.post("/blocks/bom-packages", status_code=201)
def save_bom_package(
    payload: SaveBOMPackagePayload,
    x_forwarded_user: Optional[str] = Header(None, alias="X-Forwarded-User"),
):
    """Create or overwrite a custom BOM package with child items."""
    pkg_data = {
        "package_id": payload.package_id,
        "name": payload.name,
        "site_code": payload.site_code,
        "version_no": payload.version_no,
    }
    items_data = [item.model_dump() for item in payload.items]
    pkg_id = _service.repo.save_bom_package(pkg_data, items_data, created_by=x_forwarded_user or "system")
    return {"status": "success", "package_id": pkg_id}


@router.get("/blocks/bom-packages/{package_id}")
def get_bom_package(package_id: str):
    """Get items in a BOM package."""
    items = _service.repo.get_bom_package_items(package_id)
    return {"package_id": package_id, "items": items}


@router.delete("/blocks/bom-packages/{package_id}")
def delete_bom_package(package_id: str):
    """Delete a custom BOM package and its items."""
    success = _service.repo.delete_bom_package(package_id)
    if not success:
        raise HTTPException(status_code=404, detail="BOM package not found")
    return {"status": "success", "package_id": package_id}


@router.post("/blocks/calendar-patterns", status_code=201)
def save_calendar_pattern(payload: SaveCalendarPayload):
    """Upsert a work calendar pattern."""
    pat_id = _service.repo.save_calendar_pattern(payload.model_dump())
    return {"status": "success", "pattern_id": pat_id}


@router.delete("/blocks/calendar-patterns/{pattern_id}")
def delete_calendar_pattern(pattern_id: str):
    """Delete a calendar pattern."""
    success = _service.repo.delete_calendar_pattern(pattern_id)
    if not success:
        raise HTTPException(status_code=404, detail="Calendar pattern not found")
    return {"status": "success", "pattern_id": pattern_id}


@router.post("/blocks/inventory-packages", status_code=201)
def save_inventory_package(
    payload: SaveInventoryPackagePayload,
    x_forwarded_user: Optional[str] = Header(None, alias="X-Forwarded-User"),
):
    """Create or overwrite a custom inventory package with items."""
    pkg_data = {
        "package_id": payload.package_id,
        "name": payload.name,
        "site_code": payload.site_code,
    }
    items_data = [item.model_dump() for item in payload.items]
    pkg_id = _service.repo.save_inventory_package(pkg_data, items_data, created_by=x_forwarded_user or "system")
    return {"status": "success", "package_id": pkg_id}


@router.get("/blocks/inventory-packages/{package_id}")
def get_inventory_package(package_id: str):
    """Get items in an inventory package."""
    items = _service.repo.get_inventory_package_items(package_id)
    return {"package_id": package_id, "items": items}


@router.delete("/blocks/inventory-packages/{package_id}")
def delete_inventory_package(package_id: str):
    """Delete an inventory package and its items."""
    success = _service.repo.delete_inventory_package(package_id)
    if not success:
        raise HTTPException(status_code=404, detail="Inventory package not found")
    return {"status": "success", "package_id": package_id}
