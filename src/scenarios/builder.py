"""Modular context assembly pipeline for scenario-based scheduling.

Builds pure in-memory scheduling context (ProductionInputSnapshot, RequirementPlan, RuleSet)
from lightweight scenario manifests in under 25ms.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.data_loader import (
    CustomerOrder,
    ProductParam,
    ProductionInputSnapshot,
    RequirementPlan,
    PreventiveMaintenance,
    ToolingMold,
    build_requirement_plan,
)
from src.kernel.types import RuleSet
from src.rule_sets.service import active_rule_set, validate_payload
from src.scenarios.repository import ScenarioRepository
from src.infrastructure.telemetry import trace


class ScenarioBuilder:
    """Dynamic context assembler that transforms lightweight manifests into runnable models."""

    def __init__(self, repo: Optional[ScenarioRepository] = None):
        self.repo = repo or ScenarioRepository()

    @trace(stage="ASSEMBLY", logger_name="aps.engine")
    def assemble_context(
        self,
        scenario: Dict[str, Any],
    ) -> Tuple[ProductionInputSnapshot, RequirementPlan, RuleSet]:
        """Assembles a completely isolated scheduling context from a scenario manifest.

        Returns (snapshot, requirement_plan, rule_set) in under 25ms.
        """
        manifest = scenario.get("manifest", {})
        site = str(scenario.get("site", "WL")).upper()

        lines = manifest.get("lines", [])
        cal_pattern_id = manifest.get("calendar_pattern_id", f"{site}_5_2")
        routing_version = manifest.get("routing_version", "")
        bom_pkg_id = manifest.get("bom_package_id", "")
        demand_batch_id = manifest.get("demand_batch_id", "")
        inv_batch_id = manifest.get("inventory_batch_id", "")
        setup_matrix_id = manifest.get("setup_matrix_id", f"SETUP-{site}-DEFAULT")
        planning_days = int(manifest.get("planning_days", 10))

        start_dt_str = manifest.get("start_datetime", "2026-10-12T07:00:00")
        try:
            start_dt = datetime.fromisoformat(start_dt_str)
        except Exception:
            start_dt = datetime(2026, 10, 12, 7, 0)

        # 1. Generate planning date strings
        planning_dates = [
            (start_dt + timedelta(days=d)).strftime("%m/%d")
            for d in range(max(1, planning_days))
        ]

        # 2. Query lines & stage topology
        lines_data = self.repo.query_lines(lines, site=site)
        line_stages = {l["line_code"]: l.get("stage", "FG") for l in lines_data}

        # 3. Query routing and construct FG / SF params
        routing_data = self.repo.query_routing(routing_version, lines)
        fg_params: Dict[str, ProductParam] = {}
        sf_params: Dict[str, ProductParam] = {}

        for r in routing_data:
            part_no = r["part_no"]
            line_code = r["line_code"]
            stage = line_stages.get(line_code, "FG")
            ct_sec = float(r.get("ct_seconds", 30.0))
            trp = float(r.get("trp", 0.71))
            output_shift = (8.0 * 3600.0 * trp) / max(1.0, ct_sec)

            param = ProductParam(
                site=site,
                line=line_code,
                part_no=part_no,
                description=part_no,
                ct_seconds=ct_sec,
                trp=trp,
                mpq=float(r.get("mpq", 1.0)),
                output_per_shift=output_shift,
                hours_per_shift=8.0,
                shifts_per_day=3,
                target_stock=0.0,
                target_stock_days=0.0,
                lead_time_days=0.0,
                scrap_rate=0.0,
                headcount=float(r.get("default_crew", 1.0) or 1.0),
                moq=float(r.get("moq", 1.0)),
                max_stock=0.0,
            )
            if stage == "SF":
                sf_params[part_no] = param
            else:
                fg_params[part_no] = param

        # 4. Query BOM hierarchy
        bom_data = self.repo.query_bom(bom_pkg_id)
        bom_map: Dict[str, List[Tuple[str, float]]] = {}
        for parent, items in bom_data.items():
            bom_map[parent] = [
                (it["child_part_no"], float(it.get("usage_ratio", 1.0)))
                for it in items
            ]

        # 5. Query inventory & apply stock overrides
        stocks = self.repo.query_inventory(inv_batch_id)
        if "stock_overrides" in manifest and isinstance(manifest["stock_overrides"], dict):
            for p, val in manifest["stock_overrides"].items():
                stocks[p] = float(val)

        # 6. Query orders & handle SF-only slice safety stock derivation
        raw_orders_data = self.repo.query_demand_orders(demand_batch_id)
        raw_orders: List[CustomerOrder] = []
        for o in raw_orders_data:
            due_str = o.get("due_datetime", "")
            try:
                due_dt = datetime.fromisoformat(due_str)
            except Exception:
                due_dt = start_dt
            raw_orders.append(
                CustomerOrder(
                    order_id=o["order_id"],
                    part_no=o["part_no"],
                    description=o["part_no"],
                    due_date=due_dt.strftime("%m/%d"),
                    due_datetime=due_dt,
                    demand_quantity=float(o["demand_quantity"]),
                )
            )

        is_sf_only = len(fg_params) == 0 and len(sf_params) > 0
        horizon_end = start_dt + timedelta(days=planning_days)
        if is_sf_only:
            # Derive safety stock deficit replenishment orders for half-products
            orders = self._derive_safety_stock_orders(stocks, sf_params, start_dt, planning_dates, bom_map, raw_orders)
        else:
            orders = [
                o for o in raw_orders
                if (o.part_no in fg_params or o.part_no in sf_params)
                and o.due_datetime <= horizon_end
            ]

        # 7. Query molds, PM schedules
        molds_data = self.repo.query_molds(site, lines)
        tooling_molds: List[ToolingMold] = []
        for m in molds_data:
            app_lines = [l.strip() for l in m.get("applicable_lines", "").split(",") if l.strip()]
            app_parts = [p.strip() for p in m.get("applicable_parts", "").split(",") if p.strip()]
            tooling_molds.append(
                ToolingMold(
                    site=site,
                    mold_id=m["mold_id"],
                    mold_name=m["mold_name"],
                    applicable_lines=app_lines,
                    applicable_parts=app_parts,
                    available_qty=int(m.get("available_qty", 1)),
                    changeover_time_min=float(m.get("changeover_minutes", 45.0)),
                )
            )

        pm_data = self.repo.query_pm(site, lines, planning_days)
        pm_schedules: List[PreventiveMaintenance] = []
        for p in pm_data:
            try:
                s_dt = datetime.fromisoformat(p["start_dt"])
            except Exception:
                s_dt = start_dt
            pm_schedules.append(
                PreventiveMaintenance(
                    site=site,
                    pm_plan_no=p["pm_plan_no"],
                    line=p["line_code"],
                    equipment_name=p["line_code"],
                    pm_type="PM",
                    start_date=s_dt.strftime("%m/%d"),
                    start_shift="早班",
                    end_date=p["end_dt"],
                    end_shift="早班",
                    downtime_hours=float(p.get("downtime_hours", 0.0)),
                    capacity_rate=0.0,
                    handling_strategy="avoid",
                    responsible_team="Maintenance",
                    status="Scheduled",
                )
            )

        # 8. Construct pure in-memory snapshot and requirement plan
        snapshot = ProductionInputSnapshot(
            fg_params=fg_params,
            sf_params=sf_params,
            bom=bom_map,
            initial_stock=stocks,
            orders=orders,
            planning_dates=planning_dates,
            tooling_molds=tooling_molds,
            pm_schedules=pm_schedules,
            material_arrivals=[],
            customer_tiers=[],
        )
        plan = build_requirement_plan(snapshot)

        # 9. Resolve rule set with fine-tuning overrides
        rule_set = self._resolve_rule_set(
            manifest.get("rule_set_id"),
            site=site,
            overrides=manifest.get("rule_overrides"),
        )

        return snapshot, plan, rule_set

    def _derive_safety_stock_orders(
        self,
        stocks: Dict[str, float],
        sf_params: Dict[str, ProductParam],
        start_dt: datetime,
        planning_dates: List[str],
        bom_map: Dict[str, List[Dict[str, Any]]] = None,
        fg_orders: List[CustomerOrder] = None,
    ) -> List[CustomerOrder]:
        """B3 Fix: Dynamic Time-Phased MRP Explosion for SF components."""
        orders: List[CustomerOrder] = []
        bom_map = bom_map or {}
        fg_orders = fg_orders or []
        
        # 1. Bucket FG demand by day
        fg_demand_by_day = defaultdict(float)
        for order in fg_orders:
            due_day = order.due_datetime.strftime("%Y-%m-%d") if order.due_datetime else planning_dates[-1]
            fg_demand_by_day[(order.part_no, due_day)] += order.demand_quantity

        # 2. Explode BOM to calculate time-phased SF requirements
        sf_gross_requirements = defaultdict(float)
        for (fg_part, due_day), fg_qty in fg_demand_by_day.items():
            components = bom_map.get(fg_part, [])
            for comp in components:
                # bom_map stores tuples: (child_part_no, usage_ratio)
                if isinstance(comp, tuple) and len(comp) >= 2:
                    child_part = comp[0]
                    ratio = float(comp[1])
                elif isinstance(comp, dict):
                    child_part = comp.get("child_part_no")
                    ratio = float(comp.get("usage_ratio", 1.0))
                else:
                    continue
                sf_gross_requirements[(child_part, due_day)] += fg_qty * ratio

        # 3. Simulate inventory consumption over time
        current_stocks = dict(stocks)
        for date_str in planning_dates:
            target_dt = datetime.strptime(date_str, "%Y-%m-%d") if len(date_str) == 10 else start_dt
            for sf_part, param in sf_params.items():
                req_qty = sf_gross_requirements.get((sf_part, date_str), 0.0)
                if req_qty == 0.0:
                    continue
                
                # Consume stock
                current_stocks[sf_part] = current_stocks.get(sf_part, 0.0) - req_qty
                
                # If dropped below safety stock (or 0), generate a replenishment order
                safety = param.safety_stock if hasattr(param, "safety_stock") else param.mpq * 2.0
                if current_stocks[sf_part] < safety:
                    deficit = safety - current_stocks[sf_part]
                    replenish_qty = max(param.mpq, deficit)
                    current_stocks[sf_part] += replenish_qty
                    
                    orders.append(
                        CustomerOrder(
                            order_id=f"MRP_{sf_part}_{date_str}",
                            part_no=sf_part,
                            description=f"{sf_part} 动态 MRP 拉动补水需求",
                            due_date=date_str,
                            due_datetime=target_dt,
                            demand_quantity=replenish_qty,
                        )
                    )
                    
        # 4. Fallback for pure SF production without FG orders (e.g. SF-only slices without upstream)
        if not fg_orders:
            target_day = planning_dates[min(2, len(planning_dates) - 1)]
            due_dt = start_dt + timedelta(days=2)
            for part_no, param in sf_params.items():
                on_hand = stocks.get(part_no, 0.0)
                target_stock = max(param.mpq * 2.0, 50.0)
                deficit = max(0.0, target_stock - on_hand)
                qty = deficit if deficit > 0 else param.mpq
                orders.append(
                    CustomerOrder(
                        order_id=f"REPL_{part_no}",
                        part_no=part_no,
                        description=f"{part_no} 静态安全库存基线兜底",
                        due_date=target_day,
                        due_datetime=due_dt,
                        demand_quantity=qty,
                    )
                )

        return orders

    def _resolve_rule_set(
        self,
        rule_set_id: Optional[str],
        site: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> RuleSet:
        """Resolves RuleSet from JSON configuration or active database store."""
        rule_set: Optional[RuleSet] = None

        # 1. Try loading from config/rule_sets/{rule_set_id}.json
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if rule_set_id:
            for candidate in [
                f"{rule_set_id}.json",
                f"{site}_{rule_set_id}.json",
                f"{site}_Phase1.json",
            ]:
                p = os.path.join(base_dir, "config", "rule_sets", candidate)
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        parsed, _ = validate_payload(data)
                        if parsed:
                            rule_set = parsed
                            break
                    except Exception:
                        pass

        # 2. Fallback to active rule set in database
        if rule_set is None:
            active_res = active_rule_set(site)
            if active_res and active_res[0]:
                rule_set = active_res[0]

        # 3. Apply rule overrides if present
        if rule_set and overrides and isinstance(overrides, dict):
            for key, val in overrides.items():
                # E.g. "freeze.fence_shifts": 2
                if "." in key:
                    tpl, param_name = key.split(".", 1)
                    for r in rule_set.rules:
                        if r.template_id == tpl and isinstance(r.params, dict):
                            r.params[param_name] = val
                else:
                    for r in rule_set.rules:
                        if r.template_id == key and isinstance(val, dict):
                            r.params.update(val)

        return rule_set
