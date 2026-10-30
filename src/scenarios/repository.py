"""Database-neutral repository layer for scenario-based modular planning.

Follows the No-Foreign-Keys architecture specification:
- Pure ANSI SQL DDL compatible with SQLite (3.37+) and PostgreSQL (10+).
- Physical foreign keys disabled; application-level transactional cascade.
- High-density B-Tree indices on all logical relations.
- Safe JSON serialization for manifests, KPIs, checks, and breakdowns.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


from src.infrastructure.telemetry import wrap_traced_connection


SCHEMA_DDL = """
-- 1. Scenarios master table
CREATE TABLE IF NOT EXISTS scenarios (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    site VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    manifest TEXT NOT NULL,
    kpis TEXT,
    validation_checks TEXT,
    audit_summary TEXT,
    solve_duration_ms INTEGER DEFAULT 0,
    created_by VARCHAR(64) DEFAULT 'system',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scenarios_site_status ON scenarios(site, status);

-- 2. Solved scenario tasks table
CREATE TABLE IF NOT EXISTS scenario_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    line VARCHAR(32) NOT NULL,
    stage VARCHAR(16) NOT NULL,
    part_no VARCHAR(64) NOT NULL,
    quantity REAL NOT NULL,
    start_dt TEXT NOT NULL,
    end_dt TEXT NOT NULL,
    duration_hours REAL NOT NULL,
    setup_minutes REAL DEFAULT 0.0,
    shift_breakdown TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scenario_tasks_sid ON scenario_tasks(scenario_id);
CREATE INDEX IF NOT EXISTS idx_scenario_tasks_line_time ON scenario_tasks(line, start_dt, end_dt);

-- 3. Calendar patterns table
CREATE TABLE IF NOT EXISTS calendar_patterns (
    pattern_id VARCHAR(32) PRIMARY KEY,
    site_code VARCHAR(32) NOT NULL,
    name VARCHAR(64) NOT NULL,
    workdays_per_week INTEGER NOT NULL,
    shifts_per_day INTEGER NOT NULL,
    shift_hours REAL NOT NULL,
    shift_start_times VARCHAR(128) NOT NULL,
    shift_names VARCHAR(64) NOT NULL,
    note VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_calendar_patterns_site ON calendar_patterns(site_code);

-- 4. Lines master table
CREATE TABLE IF NOT EXISTS lines (
    line_code VARCHAR(32) PRIMARY KEY,
    site_code VARCHAR(32) NOT NULL,
    workshop_code VARCHAR(32) NOT NULL,
    stage VARCHAR(16) NOT NULL,
    default_calendar_id VARCHAR(32) NOT NULL,
    max_util_rate REAL DEFAULT 1.05,
    default_crew INTEGER DEFAULT 1,
    is_bottleneck INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lines_site_stage ON lines(site_code, stage);
CREATE INDEX IF NOT EXISTS idx_lines_calendar ON lines(default_calendar_id);

-- 5. Routing parameters table
CREATE TABLE IF NOT EXISTS routing_parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id VARCHAR(64) NOT NULL,
    site_code VARCHAR(32) NOT NULL,
    part_no VARCHAR(64) NOT NULL,
    line_code VARCHAR(32) NOT NULL,
    ct_seconds REAL NOT NULL,
    trp REAL NOT NULL DEFAULT 0.71,
    mpq INTEGER NOT NULL DEFAULT 1,
    moq INTEGER NOT NULL DEFAULT 1,
    default_setup_min REAL DEFAULT 30.0,
    priority INTEGER DEFAULT 1,
    is_locked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_routing_lookup ON routing_parameters(version_id, line_code, part_no);
CREATE INDEX IF NOT EXISTS idx_routing_part ON routing_parameters(part_no);

-- 6. BOM packages master table
CREATE TABLE IF NOT EXISTS bom_packages (
    package_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    site_code VARCHAR(32) NOT NULL,
    version_no INTEGER NOT NULL,
    is_locked INTEGER DEFAULT 0,
    created_by VARCHAR(64) DEFAULT 'system',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bom_packages_site ON bom_packages(site_code, is_locked);

-- 7. BOM items table
CREATE TABLE IF NOT EXISTS bom_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id VARCHAR(64) NOT NULL,
    parent_part_no VARCHAR(64) NOT NULL,
    child_part_no VARCHAR(64) NOT NULL,
    usage_ratio REAL NOT NULL DEFAULT 1.0,
    scrap_rate REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_bom_items_pkg ON bom_items(package_id, parent_part_no);
CREATE INDEX IF NOT EXISTS idx_bom_items_child ON bom_items(package_id, child_part_no);

-- 8. Inventory packages master table
CREATE TABLE IF NOT EXISTS inventory_packages (
    package_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    site_code VARCHAR(32) NOT NULL,
    snapshot_time TEXT NOT NULL,
    is_locked INTEGER DEFAULT 0,
    created_by VARCHAR(64) DEFAULT 'system',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_inv_packages_site ON inventory_packages(site_code);

-- 9. Inventory items table
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id VARCHAR(64) NOT NULL,
    part_no VARCHAR(64) NOT NULL,
    stage VARCHAR(16) NOT NULL,
    on_hand_qty REAL NOT NULL DEFAULT 0.0,
    safety_stock REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_inv_items_pkg ON inventory_items(package_id, part_no);

-- 10. Demand batches master table
CREATE TABLE IF NOT EXISTS demand_batches (
    batch_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    site_code VARCHAR(32) NOT NULL,
    order_count INTEGER DEFAULT 0,
    is_locked INTEGER DEFAULT 0,
    created_by VARCHAR(64) DEFAULT 'system',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_demand_batches_site ON demand_batches(site_code);

-- 11. Demand orders table
CREATE TABLE IF NOT EXISTS demand_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    part_no VARCHAR(64) NOT NULL,
    customer_code VARCHAR(64) NOT NULL,
    customer_tier VARCHAR(16) DEFAULT 'A',
    due_datetime TEXT NOT NULL,
    demand_quantity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demand_orders_batch ON demand_orders(batch_id, part_no);
CREATE INDEX IF NOT EXISTS idx_demand_orders_due ON demand_orders(due_datetime);

-- 12. Tooling molds table
CREATE TABLE IF NOT EXISTS tooling_molds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_code VARCHAR(32) NOT NULL,
    mold_id VARCHAR(64) NOT NULL,
    mold_name VARCHAR(128) NOT NULL,
    applicable_lines VARCHAR(128) NOT NULL,
    applicable_parts TEXT NOT NULL,
    available_qty INTEGER DEFAULT 1,
    changeover_minutes REAL DEFAULT 45.0
);
CREATE INDEX IF NOT EXISTS idx_tooling_molds_site ON tooling_molds(site_code, mold_id);

-- 13. PM schedules table
CREATE TABLE IF NOT EXISTS pm_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_code VARCHAR(32) NOT NULL,
    pm_plan_no VARCHAR(64) NOT NULL,
    line_code VARCHAR(32) NOT NULL,
    start_dt TEXT NOT NULL,
    end_dt TEXT NOT NULL,
    downtime_hours REAL NOT NULL,
    reason VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_pm_schedules_lookup ON pm_schedules(site_code, line_code, start_dt);

-- 14. Setup matrix edges table
CREATE TABLE IF NOT EXISTS setup_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    matrix_id VARCHAR(64) NOT NULL,
    site_code VARCHAR(32) NOT NULL,
    line_code VARCHAR(32) NOT NULL,
    from_mold VARCHAR(64) NOT NULL,
    to_mold VARCHAR(64) NOT NULL,
    setup_minutes REAL NOT NULL,
    technicians_required INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_setup_lookup ON setup_edges(matrix_id, line_code, from_mold, to_mold);
"""


class ScenarioRepository:
    """Database-neutral storage engine for scenarios and building blocks."""

    def __init__(self, db_url: Optional[str] = None):
        if not db_url:
            db_url = os.getenv("DATABASE_URL", "sqlite:///data/scenarios.sqlite3")
        self.db_url = db_url
        self._is_memory = ":memory:" in db_url
        self._memory_conn: Optional[sqlite3.Connection] = None

        if self._is_memory:
            raw_mem = sqlite3.connect(":memory:")
            raw_mem.row_factory = sqlite3.Row
            self._memory_conn = wrap_traced_connection(raw_mem, logger_name="aps.db")
        else:
            # Normalize sqlite path and create directory if needed
            if db_url.startswith("sqlite:///"):
                self.file_path = db_url[len("sqlite:///"):]
            else:
                self.file_path = db_url
            os.makedirs(os.path.dirname(os.path.abspath(self.file_path)), exist_ok=True)

    def _connect(self) -> Any:
        if self._is_memory and self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self.file_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return wrap_traced_connection(conn, logger_name="aps.db")

    @contextmanager
    def get_connection(self):
        """Context manager for read queries."""
        conn = self._connect()
        try:
            yield conn
        finally:
            if not self._is_memory:
                conn.close()

    @contextmanager
    def transaction(self):
        """Context manager providing application-level transactional boundary."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if not self._is_memory:
                conn.close()

    def init_db(self) -> None:
        """Executes neutral DDL statements idempotently."""
        with self.transaction() as conn:
            conn.executescript(SCHEMA_DDL)

    def get_table_names(self) -> List[str]:
        """Returns the list of tables currently in the database."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            return [row["name"] for row in rows]

    # -------------------------------------------------------------------------
    # Scenario Master Operations
    # -------------------------------------------------------------------------

    def create_scenario(
        self,
        scenario_id: str,
        name: str,
        site: str,
        manifest: Dict[str, Any],
        status: str = "DRAFT",
        created_by: str = "system",
    ) -> str:
        """Inserts a new lightweight scenario row."""
        manifest_json = json.dumps(manifest, ensure_ascii=False)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scenarios (id, name, site, status, manifest, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (scenario_id, name, site, status, manifest_json, created_by, now_iso, now_iso),
            )
        return scenario_id

    def get_scenario(self, scenario_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a scenario by its primary key, deserializing JSON blobs."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["manifest"] = json.loads(res["manifest"]) if res["manifest"] else {}
            res["kpis"] = json.loads(res["kpis"]) if res["kpis"] else None
            res["validation_checks"] = json.loads(res["validation_checks"]) if res["validation_checks"] else None
            res["audit_summary"] = json.loads(res["audit_summary"]) if res["audit_summary"] else None
            return res

    def list_scenarios(
        self,
        site: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lists scenarios with optional filtering by site and status."""
        query = "SELECT * FROM scenarios WHERE 1=1"
        params: List[Any] = []
        if site:
            query += " AND site = ?"
            params.append(site)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["manifest"] = json.loads(item["manifest"]) if item["manifest"] else {}
                item["kpis"] = json.loads(item["kpis"]) if item["kpis"] else None
                item["validation_checks"] = json.loads(item["validation_checks"]) if item["validation_checks"] else None
                item["audit_summary"] = json.loads(item["audit_summary"]) if item["audit_summary"] else None
                results.append(item)
            return results

    def update_scenario(
        self,
        scenario_id: str,
        name: Optional[str] = None,
        status: Optional[str] = None,
        manifest: Optional[Dict[str, Any]] = None,
        kpis: Optional[Dict[str, Any]] = None,
        validation_checks: Optional[Dict[str, Any]] = None,
        audit_summary: Optional[Dict[str, Any]] = None,
        solve_duration_ms: Optional[int] = None,
    ) -> bool:
        """Updates scenario metadata, results, and checks."""
        updates: List[str] = []
        params: List[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if manifest is not None:
            updates.append("manifest = ?")
            params.append(json.dumps(manifest, ensure_ascii=False))
        if kpis is not None:
            updates.append("kpis = ?")
            params.append(json.dumps(kpis, ensure_ascii=False))
        if validation_checks is not None:
            updates.append("validation_checks = ?")
            params.append(json.dumps(validation_checks, ensure_ascii=False))
        if audit_summary is not None:
            updates.append("audit_summary = ?")
            params.append(json.dumps(audit_summary, ensure_ascii=False))
        if solve_duration_ms is not None:
            updates.append("solve_duration_ms = ?")
            params.append(solve_duration_ms)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(scenario_id)

        sql = f"UPDATE scenarios SET {', '.join(updates)} WHERE id = ?"
        with self.transaction() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0

    def delete_scenario(self, scenario_id: str) -> bool:
        """Deletes scenario and its child tasks within an application-level transaction."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM scenario_tasks WHERE scenario_id = ?", (scenario_id,))
            cur = conn.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
            return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Scenario Task Details Operations
    # -------------------------------------------------------------------------

    def save_scenario_tasks(self, scenario_id: str, tasks: List[Dict[str, Any]]) -> int:
        """Persists the full Gantt schedule tasks for a solved scenario."""
        rows_to_insert = []
        for t in tasks:
            shift_json = json.dumps(t.get("shift_breakdown", {}), ensure_ascii=False)
            rows_to_insert.append(
                (
                    scenario_id,
                    str(t.get("task_id", "")),
                    str(t.get("line", "")),
                    str(t.get("stage", "FG")),
                    str(t.get("part_no", "")),
                    float(t.get("quantity", 0.0)),
                    str(t.get("start_dt", "")),
                    str(t.get("end_dt", "")),
                    float(t.get("duration_hours", 0.0)),
                    float(t.get("setup_minutes", 0.0)),
                    shift_json,
                )
            )

        with self.transaction() as conn:
            conn.execute("DELETE FROM scenario_tasks WHERE scenario_id = ?", (scenario_id,))
            if rows_to_insert:
                conn.executemany(
                    """
                    INSERT INTO scenario_tasks (
                        scenario_id, task_id, line, stage, part_no, quantity,
                        start_dt, end_dt, duration_hours, setup_minutes, shift_breakdown
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )
        return len(rows_to_insert)

    def get_scenario_tasks(self, scenario_id: str) -> List[Dict[str, Any]]:
        """Retrieves all tasks for a scenario sorted by line and start time."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scenario_tasks WHERE scenario_id = ? ORDER BY line, start_dt",
                (scenario_id,),
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["shift_breakdown"] = json.loads(d["shift_breakdown"]) if d["shift_breakdown"] else {}
                results.append(d)
            return results

    # -------------------------------------------------------------------------
    # Manual Building Block Management (Asset Hub)
    # -------------------------------------------------------------------------

    def save_line(self, line: Dict[str, Any]) -> None:
        """Upserts a line record."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO lines (
                    line_code, site_code, workshop_code, stage,
                    default_calendar_id, max_util_rate, default_crew, is_bottleneck
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line["line_code"],
                    line.get("site_code", "WL").upper(),
                    line.get("workshop_code", "WE34"),
                    line.get("stage", "FG"),
                    line.get("default_calendar_id", "WL_5_2"),
                    float(line.get("max_util_rate", 1.05)),
                    int(line.get("default_crew", 1)),
                    int(line.get("is_bottleneck", 0)),
                ),
            )

    def delete_line(self, line_code: str) -> bool:
        """Deletes a line record."""
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM lines WHERE line_code = ?", (line_code,))
            return cur.rowcount > 0

    def save_bom_package(self, pkg: Dict[str, Any], items: List[Dict[str, Any]], created_by: str = "system") -> str:
        """Saves a custom BOM package and its component items."""
        now_iso = datetime.now(timezone.utc).isoformat()
        package_id = pkg["package_id"]
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bom_packages (
                    package_id, name, site_code, version_no, is_locked, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package_id,
                    pkg["name"],
                    pkg.get("site_code", "WL").upper(),
                    int(pkg.get("version_no", 1)),
                    0,
                    created_by,
                    now_iso,
                ),
            )
            conn.execute("DELETE FROM bom_items WHERE package_id = ?", (package_id,))
            item_rows = [
                (
                    package_id,
                    it["parent_part_no"],
                    it["child_part_no"],
                    float(it.get("usage_ratio", 1.0)),
                    float(it.get("scrap_rate", 0.0)),
                )
                for it in items
            ]
            if item_rows:
                conn.executemany(
                    "INSERT INTO bom_items (package_id, parent_part_no, child_part_no, usage_ratio, scrap_rate) VALUES (?, ?, ?, ?, ?)",
                    item_rows,
                )
        return package_id

    def get_bom_package_items(self, package_id: str) -> List[Dict[str, Any]]:
        """Queries all items in a BOM package."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM bom_items WHERE package_id = ? ORDER BY parent_part_no", (package_id,)).fetchall()
            return [dict(r) for r in rows]

    def delete_bom_package(self, package_id: str) -> bool:
        """Deletes a BOM package and its items."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM bom_items WHERE package_id = ?", (package_id,))
            cur = conn.execute("DELETE FROM bom_packages WHERE package_id = ?", (package_id,))
            return cur.rowcount > 0

    def save_calendar_pattern(self, cal: Dict[str, Any]) -> str:
        """Upserts a calendar pattern record."""
        pattern_id = cal["pattern_id"]
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO calendar_patterns (
                    pattern_id, site_code, name, workdays_per_week, shifts_per_day,
                    shift_hours, shift_start_times, shift_names, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern_id,
                    cal.get("site_code", "WL").upper(),
                    cal["name"],
                    int(cal["workdays_per_week"]),
                    int(cal["shifts_per_day"]),
                    float(cal.get("shift_hours", 8.0)),
                    cal.get("shift_start_times", "07:00,15:00,23:00"),
                    cal.get("shift_names", "早班,中班,晚班"),
                    cal.get("note", ""),
                ),
            )
        return pattern_id

    def delete_calendar_pattern(self, pattern_id: str) -> bool:
        """Deletes a calendar pattern record."""
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM calendar_patterns WHERE pattern_id = ?", (pattern_id,))
            return cur.rowcount > 0

    def save_inventory_package(self, pkg: Dict[str, Any], items: List[Dict[str, Any]], created_by: str = "system") -> str:
        """Saves a custom inventory package and its items."""
        now_iso = datetime.now(timezone.utc).isoformat()
        package_id = pkg["package_id"]
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO inventory_packages (
                    package_id, name, site_code, snapshot_time, is_locked, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package_id,
                    pkg["name"],
                    pkg.get("site_code", "WL").upper(),
                    now_iso,
                    0,
                    created_by,
                    now_iso,
                ),
            )
            conn.execute("DELETE FROM inventory_items WHERE package_id = ?", (package_id,))
            item_rows = [
                (
                    package_id,
                    it["part_no"],
                    it.get("stage", "FG"),
                    float(it.get("on_hand_qty", 0.0)),
                    float(it.get("safety_stock", 0.0)),
                )
                for it in items
            ]
            if item_rows:
                conn.executemany(
                    "INSERT INTO inventory_items (package_id, part_no, stage, on_hand_qty, safety_stock) VALUES (?, ?, ?, ?, ?)",
                    item_rows,
                )
        return package_id

    def get_inventory_package_items(self, package_id: str) -> List[Dict[str, Any]]:
        """Queries all items in an inventory package."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM inventory_items WHERE package_id = ?", (package_id,)).fetchall()
            return [dict(r) for r in rows]

    def delete_inventory_package(self, package_id: str) -> bool:
        """Deletes an inventory package and its items."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM inventory_items WHERE package_id = ?", (package_id,))
            cur = conn.execute("DELETE FROM inventory_packages WHERE package_id = ?", (package_id,))
            return cur.rowcount > 0

    def get_available_building_blocks(self, site: str = "WL") -> Dict[str, Any]:
        """Queries all distinct building block assets available for a given site."""
        site_upper = site.upper()
        with self.get_connection() as conn:
            cals = [dict(r) for r in conn.execute(
                "SELECT pattern_id, name, workdays_per_week, shifts_per_day, shift_hours FROM calendar_patterns WHERE site_code = ?",
                (site_upper,),
            ).fetchall()]
            lines = [dict(r) for r in conn.execute(
                "SELECT line_code, stage, workshop_code, default_calendar_id, max_util_rate FROM lines WHERE site_code = ? ORDER BY stage DESC, line_code",
                (site_upper,),
            ).fetchall()]
            routings = [r[0] for r in conn.execute(
                "SELECT DISTINCT version_id FROM routing_parameters WHERE site_code = ? ORDER BY version_id DESC",
                (site_upper,),
            ).fetchall()]
            boms = [dict(r) for r in conn.execute(
                "SELECT package_id, name, version_no, is_locked FROM bom_packages WHERE site_code = ? ORDER BY created_at DESC",
                (site_upper,),
            ).fetchall()]
            invs = [dict(r) for r in conn.execute(
                "SELECT package_id, name, snapshot_time, is_locked FROM inventory_packages WHERE site_code = ? ORDER BY created_at DESC",
                (site_upper,),
            ).fetchall()]
            demands = [dict(r) for r in conn.execute(
                "SELECT batch_id, name, order_count, is_locked FROM demand_batches WHERE site_code = ? ORDER BY created_at DESC",
                (site_upper,),
            ).fetchall()]
            setups = [r[0] for r in conn.execute(
                "SELECT DISTINCT matrix_id FROM setup_edges WHERE site_code = ?",
                (site_upper,),
            ).fetchall()]

        return {
            "calendar_patterns": cals,
            "lines": lines,
            "routing_versions": routings or [f"ROUTING-{site_upper}-2026Q4"],
            "bom_packages": boms,
            "inventory_packages": invs,
            "demand_batches": demands,
            "setup_matrices": setups or [f"SETUP-{site_upper}-DEFAULT"],
            "rule_sets": [f"{site_upper}_Phase1", f"{site_upper}_2026Q4"],
        }

    # -------------------------------------------------------------------------
    # Building Block Query Methods (Used by ScenarioBuilder)
    # -------------------------------------------------------------------------

    def query_calendar(self, pattern_id: str) -> Optional[Dict[str, Any]]:
        """Queries calendar pattern by pattern_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM calendar_patterns WHERE pattern_id = ?",
                (pattern_id,),
            ).fetchone()
            return dict(row) if row else None

    def query_lines(self, line_codes: List[str], site: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries lines matching line_codes and optional site filter."""
        if not line_codes:
            return []
        placeholders = ",".join(["?"] * len(line_codes))
        sql = f"SELECT * FROM lines WHERE line_code IN ({placeholders})"
        params: List[Any] = list(line_codes)
        if site:
            sql += " AND site_code = ?"
            params.append(site)
        sql += " ORDER BY line_code"

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_routing(self, version_id: str, line_codes: List[str]) -> List[Dict[str, Any]]:
        """Queries routing parameters by version and lines."""
        if not line_codes:
            return []
        placeholders = ",".join(["?"] * len(line_codes))
        sql = f"SELECT * FROM routing_parameters WHERE version_id = ? AND line_code IN ({placeholders}) ORDER BY part_no"
        params: List[Any] = [version_id] + list(line_codes)

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_bom(self, package_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Queries BOM items grouped by parent_part_no."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM bom_items WHERE package_id = ? ORDER BY parent_part_no, child_part_no",
                (package_id,),
            ).fetchall()
            bom_map: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                d = dict(r)
                parent = d["parent_part_no"]
                if parent not in bom_map:
                    bom_map[parent] = []
                bom_map[parent].append(d)
            return bom_map

    def query_demand_orders(self, batch_id: str) -> List[Dict[str, Any]]:
        """Queries demand orders in a batch ordered by due_datetime."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM demand_orders WHERE batch_id = ? ORDER BY due_datetime, order_id",
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def query_inventory(self, package_id: str) -> Dict[str, float]:
        """Queries inventory on-hand quantities mapped by part_no."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT part_no, on_hand_qty FROM inventory_items WHERE package_id = ?",
                (package_id,),
            ).fetchall()
            return {r["part_no"]: float(r["on_hand_qty"]) for r in rows}

    def query_molds(self, site: str, line_codes: List[str]) -> List[Dict[str, Any]]:
        """Queries tooling molds applicable to the specified lines."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tooling_molds WHERE site_code = ?",
                (site,),
            ).fetchall()
            results = []
            line_set = set(line_codes)
            for r in rows:
                d = dict(r)
                app_lines = [l.strip() for l in d.get("applicable_lines", "").split(",") if l.strip()]
                if not app_lines or any(l in line_set for l in app_lines):
                    results.append(d)
            return results

    def query_pm(self, site: str, line_codes: List[str], planning_days: int = 10) -> List[Dict[str, Any]]:
        """Queries PM maintenance schedules for the specified site and lines."""
        if not line_codes:
            return []
        placeholders = ",".join(["?"] * len(line_codes))
        sql = f"SELECT * FROM pm_schedules WHERE site_code = ? AND line_code IN ({placeholders}) ORDER BY start_dt"
        params: List[Any] = [site] + list(line_codes)
        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_setup_matrix(self, matrix_id: Optional[str], line_codes: List[str]) -> List[Dict[str, Any]]:
        """Queries setup matrix transition edges for the lines."""
        if not line_codes:
            return []
        placeholders = ",".join(["?"] * len(line_codes))
        if matrix_id:
            sql = f"SELECT * FROM setup_edges WHERE matrix_id = ? AND line_code IN ({placeholders})"
            params = [matrix_id] + list(line_codes)
        else:
            sql = f"SELECT * FROM setup_edges WHERE line_code IN ({placeholders})"
            params = list(line_codes)

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Safety Guards: Data Drift Lock Defense
    # -------------------------------------------------------------------------

    def lock_building_blocks(self, manifest: Dict[str, Any]) -> None:
        """Sets is_locked = 1 on all building block assets referenced by this scenario."""
        bom_pkg = manifest.get("bom_package_id")
        routing_ver = manifest.get("routing_version")
        demand_batch = manifest.get("demand_batch_id")
        inv_batch = manifest.get("inventory_batch_id")

        with self.transaction() as conn:
            if bom_pkg:
                conn.execute("UPDATE bom_packages SET is_locked = 1 WHERE package_id = ?", (bom_pkg,))
            if routing_ver:
                conn.execute("UPDATE routing_parameters SET is_locked = 1 WHERE version_id = ?", (routing_ver,))
            if demand_batch:
                conn.execute("UPDATE demand_batches SET is_locked = 1 WHERE batch_id = ?", (demand_batch,))
            if inv_batch:
                conn.execute("UPDATE inventory_packages SET is_locked = 1 WHERE package_id = ?", (inv_batch,))
