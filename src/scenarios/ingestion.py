"""Ingestion service for automatic Excel sheet unbundling into scenario building blocks.

Splits a monolithic dataSource.xlsx into 7 modular building block tables:
1. Lines master & routing parameters
2. BOM packages & BOM items
3. Inventory packages & items
4. Demand batches & orders
5. Tooling molds
6. PM schedules
7. Setup matrix edges
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.data_loader import load_aps_database
from src.scenarios.repository import ScenarioRepository
from src.sequence import load_setup_matrix
from src.infrastructure.telemetry import trace


class IngestionService:
    """Service that unbundles factory Excel inputs into modular database tables."""

    def __init__(self, repo: Optional[ScenarioRepository] = None):
        self.repo = repo or ScenarioRepository()

    @trace(stage="INGESTION", logger_name="aps.engine")
    def ingest_excel(
        self,
        file_path: str,
        site: str = "WL",
        version_tag: str = "DEFAULT",
    ) -> Dict[str, str]:
        """Ingests a factory Excel workbook into the 7 building block tables.

        Executes within an application-level transaction with no foreign keys.
        Returns the dictionary of generated building block IDs.
        """
        snapshot = load_aps_database(file_path)

        # 1. Prepare Building Block Version IDs
        site_upper = site.upper()
        bom_package_id = f"BOM-{site_upper}-{version_tag}"
        demand_batch_id = f"ORD-{site_upper}-{version_tag}"
        inventory_batch_id = f"INV-{site_upper}-{version_tag}"
        routing_version = f"ROUTING-{site_upper}-{version_tag}"
        setup_matrix_id = f"SETUP-{site_upper}-DEFAULT"
        cal_pattern_id = f"{site_upper}_5_2"
        now_iso = datetime.now(timezone.utc).isoformat()

        # 2. Setup Matrix data sourcing
        setup_edges = []
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        csv_setup_path = os.path.join(base_dir, "data", "master", "Setup-Matrix.csv")
        raw_setup = load_setup_matrix(csv_setup_path)
        if raw_setup:
            for (edge_site, edge_line, from_m, to_m), edge_obj in raw_setup.items():
                if edge_site.upper() == site_upper:
                    setup_edges.append(
                        (
                            setup_matrix_id,
                            edge_site,
                            edge_line,
                            from_m,
                            to_m,
                            float(getattr(edge_obj, "minutes", 30.0)),
                            int(getattr(edge_obj, "technicians", 1)),
                        )
                    )

        with self.repo.transaction() as conn:
            # 3. Ensure Default Calendar Pattern exists for site
            conn.execute(
                """
                INSERT OR IGNORE INTO calendar_patterns (
                    pattern_id, site_code, name, workdays_per_week, shifts_per_day,
                    shift_hours, shift_start_times, shift_names, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cal_pattern_id,
                    site_upper,
                    f"{site_upper} 做五休二标准制",
                    5,
                    3,
                    8.0,
                    "07:00,15:00,23:00",
                    "早班,中班,晚班",
                    "系统自动初始化默认工作制",
                ),
            )

            # 4. Ingest Lines & Routing Parameters (FG + SFN)
            routing_rows = []
            lines_set = set()

            # FG parameters
            for part_no, param in snapshot.fg_params.items():
                line_name = param.line
                if line_name not in lines_set:
                    lines_set.add(line_name)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO lines (
                            line_code, site_code, workshop_code, stage,
                            default_calendar_id, max_util_rate, default_crew, is_bottleneck
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (line_name, site_upper, "WE34", "FG", cal_pattern_id, 1.05, 1, 0),
                    )
                routing_rows.append(
                    (
                        routing_version,
                        site_upper,
                        part_no,
                        line_name,
                        float(param.ct_seconds),
                        float(param.trp),
                        int(param.mpq),
                        int(param.moq),
                        float(getattr(param, "default_setup_min", 30.0) or 30.0),
                        1,
                        0,
                    )
                )

            # SFN parameters
            for part_no, param in snapshot.sf_params.items():
                line_name = param.line
                if line_name not in lines_set:
                    lines_set.add(line_name)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO lines (
                            line_code, site_code, workshop_code, stage,
                            default_calendar_id, max_util_rate, default_crew, is_bottleneck
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (line_name, site_upper, "冲压注塑车间", "SF", cal_pattern_id, 1.05, 1, 0),
                    )
                routing_rows.append(
                    (
                        routing_version,
                        site_upper,
                        part_no,
                        line_name,
                        float(param.ct_seconds),
                        float(param.trp),
                        int(param.mpq),
                        int(param.moq),
                        float(getattr(param, "default_setup_min", 30.0) or 30.0),
                        1,
                        0,
                    )
                )

            # Clean and bulk insert routing
            conn.execute("DELETE FROM routing_parameters WHERE version_id = ?", (routing_version,))
            if routing_rows:
                conn.executemany(
                    """
                    INSERT INTO routing_parameters (
                        version_id, site_code, part_no, line_code,
                        ct_seconds, trp, mpq, moq, default_setup_min, priority, is_locked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    routing_rows,
                )

            # 5. Ingest BOM Package and BOM Items
            conn.execute(
                """
                INSERT OR REPLACE INTO bom_packages (
                    package_id, name, site_code, version_no, is_locked, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bom_package_id, f"{site_upper} BOM 基线 ({version_tag})", site_upper, 1, 0, now_iso),
            )
            conn.execute("DELETE FROM bom_items WHERE package_id = ?", (bom_package_id,))
            bom_rows = []
            for parent, children in snapshot.bom.items():
                for child in children:
                    if isinstance(child, (tuple, list)):
                        child_no = child[0]
                        ratio = float(child[1]) if len(child) > 1 else 1.0
                    else:
                        child_no = str(child)
                        ratio = 1.0
                    bom_rows.append((bom_package_id, parent, child_no, ratio, 0.0))

            if bom_rows:
                conn.executemany(
                    """
                    INSERT INTO bom_items (
                        package_id, parent_part_no, child_part_no, usage_ratio, scrap_rate
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    bom_rows,
                )

            # 6. Ingest Inventory Package and Items
            conn.execute(
                """
                INSERT OR REPLACE INTO inventory_packages (
                    package_id, name, site_code, snapshot_time, is_locked, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (inventory_batch_id, f"{site_upper} 期初库存 ({version_tag})", site_upper, now_iso, 0, now_iso),
            )
            conn.execute("DELETE FROM inventory_items WHERE package_id = ?", (inventory_batch_id,))
            inv_rows = []
            for part_no, qty in snapshot.initial_stock.items():
                stage = "SF" if part_no in snapshot.sf_params else "FG"
                inv_rows.append((inventory_batch_id, part_no, stage, float(qty), 0.0))

            if inv_rows:
                conn.executemany(
                    """
                    INSERT INTO inventory_items (
                        package_id, part_no, stage, on_hand_qty, safety_stock
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    inv_rows,
                )

            # 7. Ingest Demand Batch and Orders
            conn.execute(
                """
                INSERT OR REPLACE INTO demand_batches (
                    batch_id, name, site_code, order_count, is_locked, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (demand_batch_id, f"{site_upper} 订单需求池 ({version_tag})", site_upper, len(snapshot.orders), 0, now_iso),
            )
            conn.execute("DELETE FROM demand_orders WHERE batch_id = ?", (demand_batch_id,))
            demand_rows = []
            for order in snapshot.orders:
                due_dt = order.due_datetime.isoformat() if order.due_datetime else str(order.due_date)
                demand_rows.append(
                    (
                        demand_batch_id,
                        order.order_id,
                        order.part_no,
                        getattr(order, "customer_code", "VALEO"),
                        "A",
                        due_dt,
                        float(order.demand_quantity),
                    )
                )

            if demand_rows:
                conn.executemany(
                    """
                    INSERT INTO demand_orders (
                        batch_id, order_id, part_no, customer_code, customer_tier, due_datetime, demand_quantity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    demand_rows,
                )

            # 8. Ingest Tooling Molds
            conn.execute("DELETE FROM tooling_molds WHERE site_code = ?", (site_upper,))
            molds_rows = []
            for mold in snapshot.tooling_molds:
                app_lines = ", ".join(mold.applicable_lines) if isinstance(mold.applicable_lines, list) else str(mold.applicable_lines)
                app_parts = ", ".join(mold.applicable_parts) if isinstance(mold.applicable_parts, list) else str(mold.applicable_parts)
                molds_rows.append(
                    (
                        site_upper,
                        mold.mold_id,
                        mold.mold_name,
                        app_lines,
                        app_parts,
                        int(mold.available_qty),
                        float(getattr(mold, "changeover_time_min", 45.0) or 45.0),
                    )
                )

            if molds_rows:
                conn.executemany(
                    """
                    INSERT INTO tooling_molds (
                        site_code, mold_id, mold_name, applicable_lines, applicable_parts, available_qty, changeover_minutes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    molds_rows,
                )

            # 9. Ingest PM Schedules
            conn.execute("DELETE FROM pm_schedules WHERE site_code = ?", (site_upper,))
            pm_rows = []
            for pm in snapshot.pm_schedules:
                start_str = pm.date_obj.isoformat() if hasattr(pm, "date_obj") and pm.date_obj else str(pm.start_date)
                end_str = str(getattr(pm, "end_date", start_str))
                pm_rows.append(
                    (
                        site_upper,
                        pm.pm_plan_no,
                        pm.line,
                        start_str,
                        end_str,
                        float(pm.downtime_hours),
                        str(getattr(pm, "reason", "") or ""),
                    )
                )

            if pm_rows:
                conn.executemany(
                    """
                    INSERT INTO pm_schedules (
                        site_code, pm_plan_no, line_code, start_dt, end_dt, downtime_hours, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    pm_rows,
                )

            # 10. Ingest Setup Matrix Edges
            conn.execute("DELETE FROM setup_edges WHERE matrix_id = ?", (setup_matrix_id,))
            if setup_edges:
                conn.executemany(
                    """
                    INSERT INTO setup_edges (
                        matrix_id, site_code, line_code, from_mold, to_mold, setup_minutes, technicians_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    setup_edges,
                )

        return {
            "bom_package_id": bom_package_id,
            "demand_batch_id": demand_batch_id,
            "inventory_batch_id": inventory_batch_id,
            "routing_version": routing_version,
            "setup_matrix_id": setup_matrix_id,
        }
