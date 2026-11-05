from __future__ import annotations

import datetime as dt
import os
import shutil
import tempfile
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from aps.calendar.pattern import CalendarMode
from aps.data.importer import import_workbook
from aps.data.issues import IssueLog
from aps.data.sqlite import SqliteRepository
from aps.dataset import DataSet
from aps.export.excel import export_production_plan
from aps.model.context import StructureContext
from aps.model.plan_row import extract_line_shifts, extract_plan_rows
from aps.model.report import build_binding_reports
from aps.model.solver import solve_schedule
from aps.schema.registry import REGISTRY

__all__ = ["create_app"]

DEFAULT_DB = os.path.join("data", "aps.db")
DEFAULT_SITE = "WL"


class SolveRequest(BaseModel):
    site: str = DEFAULT_SITE
    start_date: str = "2026-11-03"
    days: int = 10
    timeout: float = 60.0
    calendar_mode: str = "CONTINUOUS"
    db_path: str = DEFAULT_DB


def create_app() -> FastAPI:
    app = FastAPI(
        title="APS 2.0 智能排产与决策中枢",
        version="2.0.0",
        description="基于 Google OR-Tools CP-SAT 的工业级高级排产系统",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"message": "APS 2.0 Web Server Running"}

    @app.get("/api/status")
    def get_status(db_path: str = DEFAULT_DB, site: str = DEFAULT_SITE):
        exists = os.path.exists(db_path)
        entity_counts = {}
        total_rows = 0
        if exists:
            repo = SqliteRepository(db_path)
            try:
                for name in sorted(REGISTRY):
                    count = repo.count(name)
                    entity_counts[name] = count
                    total_rows += count
            finally:
                repo.close()

        return {
            "status": "ready" if exists else "no_database",
            "db_path": db_path,
            "site": site,
            "total_rows": total_rows,
            "entity_counts": entity_counts,
        }

    @app.post("/api/import-file")
    async def import_file(
        file: UploadFile = File(...),
        site: str = Query(DEFAULT_SITE),
        db_path: str = Query(DEFAULT_DB),
    ):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        fd, temp_xlsx = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)

        try:
            with open(temp_xlsx, "wb") as f:
                shutil.copyfileobj(file.file, f)

            result = import_workbook(temp_xlsx, db_path, site)
            return {
                "ok": result.ok,
                "wrote": result.wrote,
                "site": result.site,
                "counts": dict(result.counts),
                "errors_count": len(result.report.errors),
                "warnings_count": len(result.report.warnings),
                "gaps_count": len(result.report.gaps),
                "degraded_fields": list(result.degraded_fields),
                "errors": [
                    {"entity": i.entity, "field": i.field, "sheet": i.sheet, "row": i.row, "message": i.message}
                    for i in result.report.errors[:50]
                ],
                "gaps": [
                    {"entity": i.entity, "field": i.field, "sheet": i.sheet, "row": i.row, "message": i.message}
                    for i in result.report.gaps[:50]
                ],
            }
        finally:
            if os.path.exists(temp_xlsx):
                os.unlink(temp_xlsx)

    @app.post("/api/import-default")
    def import_default(
        workbook_path: str = "APS_DataSource_Template - WL -V2.xlsx",
        site: str = DEFAULT_SITE,
        db_path: str = DEFAULT_DB,
    ):
        if not os.path.exists(workbook_path):
            raise HTTPException(status_code=404, detail=f"文件不存在: {workbook_path}")

        result = import_workbook(workbook_path, db_path, site)
        return {
            "ok": result.ok,
            "wrote": result.wrote,
            "site": result.site,
            "counts": dict(result.counts),
            "errors_count": len(result.report.errors),
            "warnings_count": len(result.report.warnings),
            "gaps_count": len(result.report.gaps),
            "degraded_fields": list(result.degraded_fields),
        }

    @app.get("/api/dataset")
    def get_dataset(
        site: str = DEFAULT_SITE,
        start_date: str = "2026-11-03",
        days: int = 10,
        calendar_mode: str = "CONTINUOUS",
        db_path: str = DEFAULT_DB,
    ):
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail="数据库未初始化，请先导入数据")

        try:
            start = dt.date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")

        repo = SqliteRepository(db_path)
        try:
            log = IssueLog()
            dataset = DataSet.load(repo, site, start, days, calendar_mode=calendar_mode, log=log)
        finally:
            repo.close()

        fg_tasks = [t for t in dataset.tasks if t.stage == "FG"]
        sf_tasks = [t for t in dataset.tasks if t.stage == "SF"]

        return {
            "site": site,
            "horizon": {
                "start": dataset.horizon.start_date.isoformat(),
                "end": dataset.horizon.end_date.isoformat(),
                "days": dataset.horizon.days,
                "mode": dataset.mode.value,
            },
            "stats": {
                "lines_count": len(dataset.lines),
                "slots_count": len(dataset.slots),
                "total_tasks": len(dataset.tasks),
                "fg_tasks": len(fg_tasks),
                "sf_tasks": len(sf_tasks),
                "routes_count": len(dataset.routes),
                "bom_count": len(dataset.bom),
            },
            "lines": [
                {
                    "line_code": l.line_code,
                    "shift_hours": l.shift_hours,
                    "shifts_per_day": l.shifts_per_day,
                    "trp": l.trp_efficiency,
                    "open_slots": len(dataset.open_slots(l.line_code)),
                }
                for l in dataset.lines
            ],
            "tasks_preview": [
                {
                    "pn": t.pn,
                    "stage": t.stage,
                    "net_requirement": t.net_requirement,
                    "due_date": t.due_date.isoformat(),
                    "mpq": t.mpq,
                    "description": t.description,
                    "parent_pn": t.parent_pn,
                }
                for t in dataset.tasks[:50]
            ],
        }

    @app.post("/api/solve")
    def run_solve(req: SolveRequest):
        if not os.path.exists(req.db_path):
            raise HTTPException(status_code=404, detail="数据库未就绪")

        try:
            start = dt.date.fromisoformat(req.start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="起始日期格式无效")

        repo = SqliteRepository(req.db_path)
        try:
            dataset = DataSet.load(repo, req.site, start, req.days, calendar_mode=req.calendar_mode)
        finally:
            repo.close()

        ctx = StructureContext.build(dataset)
        snapshot, summary = solve_schedule(ctx, time_limit_seconds=req.timeout)

        if snapshot is None:
            return {
                "ok": False,
                "status": summary.status,
                "wall_time": summary.wall_time_seconds,
                "diagnostics": summary.diagnostics,
            }

        plan_rows = extract_plan_rows(ctx, snapshot)
        shift_summaries = extract_line_shifts(ctx, snapshot)
        binding_reports = build_binding_reports(ctx)

        out_path = os.path.join("data", f"Production_Plan_{req.site}.xlsx")
        export_production_plan(
            plan_rows=plan_rows,
            shift_summaries=shift_summaries,
            solve_summary=summary,
            binding_reports=binding_reports,
            tasks=dataset.tasks,
            out_path=out_path,
        )

        return {
            "ok": True,
            "status": summary.status,
            "wall_time": summary.wall_time_seconds,
            "p1_lateness_shifts": summary.p1_lateness_shifts,
            "p2_gap_hours": round(summary.p2_utilization_gap_ms / 3600000.0, 2),
            "p3_changeover_blocks": summary.p3_changeover_blocks,
            "p4_wip_units": summary.p4_wip_units,
            "plan_rows_count": len(plan_rows),
            "export_file": os.path.basename(out_path),
            "plan_rows": [
                {
                    "site": r.site,
                    "line_code": r.line_code,
                    "date": r.date_str,
                    "shift": r.shift_name,
                    "slot_index": r.slot_index,
                    "task_id": r.task_id,
                    "pn": r.pn,
                    "stage": r.stage,
                    "quantity": r.quantity,
                    "start_min": r.start_min,
                    "end_min": r.end_min,
                    "setup_min": r.setup_min,
                    "is_tail": r.is_tail_shift,
                }
                for r in plan_rows[:500]
            ],
            "line_shifts": [
                {
                    "line_code": s.line_code,
                    "date": s.date_str,
                    "shift": s.shift_name,
                    "slot_index": s.slot_index,
                    "is_open": s.is_open,
                    "is_tail": s.is_tail,
                    "cap_hours": s.cap_hours,
                    "actual_hours": s.actual_hours,
                    "utilization_pct": s.utilization_pct,
                }
                for s in shift_summaries
            ],
            "binding_reports": [
                {
                    "rule_id": b.rule_id,
                    "status": b.status,
                    "constraints_count": b.constraints_count,
                    "penalty_terms_count": b.penalty_terms_count,
                    "provenance": b.provenance,
                }
                for b in binding_reports
            ],
        }

    @app.get("/api/download-plan")
    def download_plan(site: str = DEFAULT_SITE):
        plan_file = os.path.join("data", f"Production_Plan_{site}.xlsx")
        if not os.path.exists(plan_file):
            raise HTTPException(status_code=404, detail="排产文件尚未生成")
        return FileResponse(
            plan_file,
            filename=f"Production_Plan_{site}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return app
