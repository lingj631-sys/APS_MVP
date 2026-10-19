"""
Data loader for the APS MVP.

Reads master data, BOM, opening stock and customer demand from the simulation
workbook, then nets demand against stock to derive production tasks: finished
goods rounded up to whole MPQ batches, semi-finished only floored at the MPQ
start quantity.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import math
import openpyxl


@dataclass
class ProductParam:
    site: str
    line: str
    part_no: str
    description: str
    ct_seconds: float
    trp: float
    mpq: float
    output_per_shift: float
    hours_per_shift: float = 8.0
    shifts_per_day: int = 3
    target_stock: float = 0.0
    target_stock_days: float = 0.0
    lead_time_days: float = 0.0
    scrap_rate: float = 0.0


@dataclass
class CustomerOrder:
    order_id: str
    part_no: str
    description: str
    due_date: str
    due_datetime: datetime
    demand_quantity: float


@dataclass
class ToolingMold:
    site: str
    mold_id: str
    mold_name: str
    applicable_lines: List[str]
    applicable_parts: List[str]
    available_qty: int = 1
    changeover_time_min: float = 30.0
    max_life_strokes: int = 500000
    current_strokes: int = 0
    maintenance_cycle_strokes: int = 50000
    status: str = "可用 (Available)"


@dataclass
class PreventiveMaintenance:
    site: str
    pm_plan_no: str
    line: str
    equipment_name: str
    pm_type: str
    start_date: str
    start_shift: str
    end_date: str
    end_shift: str
    downtime_hours: float = 8.0
    capacity_rate: float = 0.0
    handling_strategy: str = "避让顺延 (Shift_Delay)"
    responsible_team: str = ""
    status: str = "已排定 (Planned)"


@dataclass
class MaterialArrival:
    site: str
    po_number: str
    supplier: str
    part_no: str
    description: str
    eta_date: str
    eta_shift: str
    quantity: float
    qc_release_hours: float = 2.0
    associated_part: str = ""
    status: str = "质检合格 (Released)"


@dataclass
class CustomerTier:
    site: str
    customer_code: str
    customer_name: str
    tier: str  # "STRATEGIC", "TIER_A", "TIER_B"
    priority_weight: int = 10
    otd_target_rate: float = 100.0
    delivery_window: str = "0天严格保供"
    subcontract_allowed: str = "否 (No)"
    associated_products: List[str] = field(default_factory=list)


@dataclass
class ProductionInputSnapshot:
    fg_params: Dict[str, ProductParam]
    sf_params: Dict[str, ProductParam]
    bom: Dict[str, List[Tuple[str, float]]]
    initial_stock: Dict[str, float]
    orders: List[CustomerOrder]
    planning_dates: List[str]
    tooling_molds: List[ToolingMold] = field(default_factory=list)
    pm_schedules: List[PreventiveMaintenance] = field(default_factory=list)
    material_arrivals: List[MaterialArrival] = field(default_factory=list)
    customer_tiers: List[CustomerTier] = field(default_factory=list)


@dataclass
class ProductionTask:
    task_id: str
    stage: str  # "FG" or "SF"
    line: str
    part_no: str
    description: str
    ct_seconds: float
    trp: float
    mpq: int  # FG: rounding multiple; SF: minimum start quantity
    lot_size: int  # units per scheduling lot: MPQ for FG, one piece for SF
    lots: int  # lot_size lots making up quantity
    quantity: float
    lot_millis: int  # one lot's machine time, scaled to whole milliseconds
    order_id: str
    bom_ratio: float = 1.0
    stock_covered: int = 0
    due_datetime: Optional[datetime] = None
    parent_task_id: Optional[str] = None
    mold_id: Optional[str] = None
    material_arrival_shift: int = 0
    customer_tier: str = "A"
    line_pm_slots: List[int] = field(default_factory=list)


@dataclass
class StockMovement:
    part_no: str
    stage: str
    opening: float
    produced: float
    consumed: float
    target_stock: float
    closing: float


@dataclass
class RequirementPlan:
    fg_tasks: List[ProductionTask]
    sf_tasks: List[ProductionTask]
    stock_movements: List[StockMovement]
    covered_by_stock: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def tasks(self) -> List[ProductionTask]:
        return self.fg_tasks + self.sf_tasks


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_aps_database(excel_path: str, site_filter: str = "WL") -> ProductionInputSnapshot:
    wb = openpyxl.load_workbook(excel_path, data_only=True)

    ct_map: Dict[str, float] = {}
    if "WL CT " in wb.sheetnames:
        ws_ct = wb["WL CT "]
        for r in range(2, ws_ct.max_row + 1):
            material = str(ws_ct.cell(r, 1).value or "").strip()
            ct_value = ws_ct.cell(r, 3).value
            if material and ct_value is not None:
                try:
                    ct_map[material] = float(ct_value)
                except ValueError:
                    pass

    fg_params = _load_params(wb["Parameters-FG"], site_filter, ct_map, default_ct=40.0, default_trp=0.71, default_mpq=1.0)
    sf_params = _load_params(wb["Parameters-SFN"], site_filter, ct_map, default_ct=25.0, default_trp=0.85, default_mpq=100.0, semi_finished=True)

    ws_bom = wb["BOM List"]
    bom: Dict[str, List[Tuple[str, float]]] = {}
    for r in range(2, ws_bom.max_row + 1):
        if str(ws_bom.cell(r, 1).value or "").strip() != site_filter:
            continue
        fg = str(ws_bom.cell(r, 2).value or "").strip()
        sf = str(ws_bom.cell(r, 4).value or "").strip()
        if fg and sf:
            bom.setdefault(fg, []).append((sf, _to_float(ws_bom.cell(r, 6).value, 1.0)))

    ws_stock = wb["Initial Stock"]
    initial_stock: Dict[str, float] = {}
    for r in range(2, ws_stock.max_row + 1):
        if str(ws_stock.cell(r, 1).value or "").strip() != site_filter:
            continue
        pn = str(ws_stock.cell(r, 3).value or "").strip()
        if pn:
            initial_stock[pn] = _to_float(ws_stock.cell(r, 5).value)

    ws_dem = wb["Customer Demand"]
    demand_dates = []
    for c in range(4, ws_dem.max_column + 1):
        v = ws_dem.cell(2, c).value
        if v is None:
            continue
        if isinstance(v, (int, float)) and v > 40000:
            dt_h = datetime(1899, 12, 30) + timedelta(days=int(v))
            demand_dates.append(f"{dt_h.month}/{dt_h.day}")
        elif isinstance(v, datetime):
            demand_dates.append(f"{v.month}/{v.day}")
        else:
            demand_dates.append(str(v).strip())
    orders: List[CustomerOrder] = []
    for r in range(3, ws_dem.max_row + 1):
        if str(ws_dem.cell(r, 1).value or "").strip() != site_filter:
            continue
        pn = str(ws_dem.cell(r, 2).value or "").strip()
        if not pn:
            continue
        desc = str(ws_dem.cell(r, 3).value or "").strip()
        for i, date_str in enumerate(demand_dates):
            qty = _to_float(ws_dem.cell(r, 4 + i).value)
            if qty <= 0:
                continue
            month, day = (int(p) for p in date_str.split("/"))
            orders.append(CustomerOrder(
                order_id=f"ORD_{site_filter}_{pn}_{month:02d}{day:02d}",
                part_no=pn,
                description=desc,
                due_date=date_str,
                due_datetime=datetime(2026, month, day, 15, 0),
                demand_quantity=qty,
            ))

    # Load Tooling-Mold
    tooling_molds: List[ToolingMold] = []
    if "Tooling-Mold" in wb.sheetnames:
        ws_tm = wb["Tooling-Mold"]
        for r in range(2, ws_tm.max_row + 1):
            site_val = str(ws_tm.cell(r, 1).value or "").strip()
            if site_val and site_val != site_filter:
                continue
            m_id = str(ws_tm.cell(r, 2).value or "").strip()
            if not m_id:
                continue
            lines = [l.strip() for l in str(ws_tm.cell(r, 4).value or "").split(",") if l.strip()]
            parts = [p.strip() for p in str(ws_tm.cell(r, 5).value or "").split(",") if p.strip()]
            tooling_molds.append(ToolingMold(
                site=site_val or site_filter,
                mold_id=m_id,
                mold_name=str(ws_tm.cell(r, 3).value or "").strip(),
                applicable_lines=lines,
                applicable_parts=parts,
                available_qty=int(_to_float(ws_tm.cell(r, 6).value, 1)),
                changeover_time_min=_to_float(ws_tm.cell(r, 7).value, 30.0),
                max_life_strokes=int(_to_float(ws_tm.cell(r, 8).value, 500000)),
                current_strokes=int(_to_float(ws_tm.cell(r, 9).value, 0)),
                maintenance_cycle_strokes=int(_to_float(ws_tm.cell(r, 10).value, 50000)),
                status=str(ws_tm.cell(r, 11).value or "可用 (Available)").strip(),
            ))

    # Load Preventive-Maintenance
    pm_schedules: List[PreventiveMaintenance] = []
    if "Preventive-Maintenance" in wb.sheetnames:
        ws_pm = wb["Preventive-Maintenance"]
        for r in range(2, ws_pm.max_row + 1):
            site_val = str(ws_pm.cell(r, 1).value or "").strip()
            if site_val and site_val != site_filter:
                continue
            plan_no = str(ws_pm.cell(r, 2).value or "").strip()
            if not plan_no:
                continue
            pm_schedules.append(PreventiveMaintenance(
                site=site_val or site_filter,
                pm_plan_no=plan_no,
                line=str(ws_pm.cell(r, 3).value or "").strip(),
                equipment_name=str(ws_pm.cell(r, 4).value or "").strip(),
                pm_type=str(ws_pm.cell(r, 5).value or "").strip(),
                start_date=str(ws_pm.cell(r, 6).value or "").strip()[:10],
                start_shift=str(ws_pm.cell(r, 7).value or "").strip(),
                end_date=str(ws_pm.cell(r, 8).value or "").strip()[:10],
                end_shift=str(ws_pm.cell(r, 9).value or "").strip(),
                downtime_hours=_to_float(ws_pm.cell(r, 10).value, 8.0),
                capacity_rate=_to_float(ws_pm.cell(r, 11).value, 0.0),
                handling_strategy=str(ws_pm.cell(r, 12).value or "避让顺延 (Shift_Delay)").strip(),
                responsible_team=str(ws_pm.cell(r, 13).value or "").strip(),
                status=str(ws_pm.cell(r, 14).value or "已排定 (Planned)").strip(),
            ))

    # Load Material-Arrival-ETA
    material_arrivals: List[MaterialArrival] = []
    if "Material-Arrival-ETA" in wb.sheetnames:
        ws_arr = wb["Material-Arrival-ETA"]
        for r in range(2, ws_arr.max_row + 1):
            site_val = str(ws_arr.cell(r, 1).value or "").strip()
            if site_val and site_val != site_filter:
                continue
            po_no = str(ws_arr.cell(r, 2).value or "").strip()
            if not po_no:
                continue
            material_arrivals.append(MaterialArrival(
                site=site_val or site_filter,
                po_number=po_no,
                supplier=str(ws_arr.cell(r, 3).value or "").strip(),
                part_no=str(ws_arr.cell(r, 4).value or "").strip(),
                description=str(ws_arr.cell(r, 5).value or "").strip(),
                eta_date=str(ws_arr.cell(r, 6).value or "").strip()[:10],
                eta_shift=str(ws_arr.cell(r, 7).value or "").strip(),
                quantity=_to_float(ws_arr.cell(r, 8).value, 0.0),
                qc_release_hours=_to_float(ws_arr.cell(r, 9).value, 2.0),
                associated_part=str(ws_arr.cell(r, 10).value or "").strip(),
                status=str(ws_arr.cell(r, 11).value or "质检合格 (Released)").strip(),
            ))

    # Load Customer-Priority
    customer_tiers: List[CustomerTier] = []
    if "Customer-Priority" in wb.sheetnames:
        ws_cust = wb["Customer-Priority"]
        for r in range(2, ws_cust.max_row + 1):
            site_val = str(ws_cust.cell(r, 1).value or "").strip()
            if site_val and site_val != site_filter:
                continue
            c_code = str(ws_cust.cell(r, 2).value or "").strip()
            if not c_code:
                continue
            prods = [p.strip() for p in str(ws_cust.cell(r, 9).value or "").split(",") if p.strip()]
            customer_tiers.append(CustomerTier(
                site=site_val or site_filter,
                customer_code=c_code,
                customer_name=str(ws_cust.cell(r, 3).value or "").strip(),
                tier=str(ws_cust.cell(r, 4).value or "TIER_A").strip(),
                priority_weight=int(_to_float(ws_cust.cell(r, 5).value, 3)),
                otd_target_rate=_to_float(ws_cust.cell(r, 6).value, 98.0),
                delivery_window=str(ws_cust.cell(r, 7).value or "0天严格保供").strip(),
                subcontract_allowed=str(ws_cust.cell(r, 8).value or "协商外协 (Negotiable)").strip(),
                associated_products=prods,
            ))

    return ProductionInputSnapshot(
        fg_params=fg_params,
        sf_params=sf_params,
        bom=bom,
        initial_stock=initial_stock,
        orders=orders,
        planning_dates=demand_dates,
        tooling_molds=tooling_molds,
        pm_schedules=pm_schedules,
        material_arrivals=material_arrivals,
        customer_tiers=customer_tiers,
    )


def _load_params(ws, site_filter: str, ct_map: Dict[str, float], *, default_ct: float,
                 default_trp: float, default_mpq: float, semi_finished: bool = False) -> Dict[str, ProductParam]:
    params: Dict[str, ProductParam] = {}
    hours_col = 11 if semi_finished else 9
    shifts_col = 12 if semi_finished else 10
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(r, 1).value or "").strip() != site_filter:
            continue
        pn = str(ws.cell(r, 4).value or "").strip()
        if not pn:
            continue
        ct = _to_float(ws.cell(r, 7).value) or ct_map.get(pn, default_ct)
        trp = _to_float(ws.cell(r, 3).value, default_trp) or default_trp
        mpq = _to_float(ws.cell(r, 6).value, default_mpq) or default_mpq
        hours = _to_float(ws.cell(r, hours_col).value, 8.0) or 8.0
        params[pn] = ProductParam(
            site=site_filter,
            line=str(ws.cell(r, 2).value or "").strip(),
            part_no=pn,
            description=str(ws.cell(r, 5).value or "").strip(),
            ct_seconds=ct,
            trp=trp,
            mpq=mpq,
            output_per_shift=hours * 3600.0 / ct * trp,
            hours_per_shift=hours,
            shifts_per_day=int(_to_float(ws.cell(r, shifts_col).value, 3.0) or 3),
            target_stock=0.0 if semi_finished else _to_float(ws.cell(r, 8).value),
            target_stock_days=_to_float(ws.cell(r, 8).value) if semi_finished else 0.0,
            lead_time_days=_to_float(ws.cell(r, 9).value) if semi_finished else 0.0,
            scrap_rate=_to_float(ws.cell(r, 10).value) if semi_finished else 0.0,
        )
    return params


TIME_SCALE = 1000


def lot_millis(lot_size: float, ct_seconds: float, trp: float) -> int:
    """Milliseconds for one production lot: CP-SAT only takes integer time."""
    return max(1, int(round(lot_size * ct_seconds / trp * TIME_SCALE)))


def build_requirement_plan(snapshot: ProductionInputSnapshot) -> RequirementPlan:
    """Net customer demand against opening and safety stock, then explode BOM.

    Finished goods are produced in whole MPQ batches, so the rounding surplus stays
    on hand and offsets the next order for the same part. Semi-finished output only
    has to clear the MPQ start quantity, so an order of 120 pieces costs 120 pieces
    instead of two batches of 100.
    """
    fg_tasks: List[ProductionTask] = []
    sf_tasks: List[ProductionTask] = []
    covered_by_stock: List[str] = []
    warnings: List[str] = []

    fg_avail = {pn: snapshot.initial_stock.get(pn, 0.0) for pn in snapshot.fg_params}
    sf_avail = {pn: snapshot.initial_stock.get(pn, 0.0) for pn in snapshot.sf_params}
    fg_produced: Dict[str, float] = {}
    sf_produced: Dict[str, float] = {}
    fg_consumed: Dict[str, float] = {}
    sf_consumed: Dict[str, float] = {}

    for pn, param in snapshot.sf_params.items():
        if param.target_stock_days > 0:
            warnings.append(
                f"{pn}: 半成品安全库存以天数({param.target_stock_days}天)给出，尚未折算为件数，本次按 0 处理"
            )

    # High-order master data lookup maps
    mold_map: Dict[str, str] = {}
    for tm in snapshot.tooling_molds:
        for p in tm.applicable_parts:
            mold_map[p] = tm.mold_id

    cust_tier_map: Dict[str, str] = {}
    for ct in snapshot.customer_tiers:
        tier_code = "STRATEGIC" if "STRATEGIC" in ct.tier.upper() else ("TIER_B" if "TIER_B" in ct.tier.upper() or "B级" in ct.tier else "TIER_A")
        for p in ct.associated_products:
            cust_tier_map[p] = tier_code

    shift_offset_map = {"早班": 0, "中班": 1, "晚班": 2}
    mat_arrival_map: Dict[str, int] = {}
    for ma in snapshot.material_arrivals:
        if ma.eta_date:
            try:
                d_idx = 0
                for idx, pdate in enumerate(snapshot.planning_dates):
                    if pdate in ma.eta_date or ma.eta_date.endswith(pdate.replace("/", "-")) or pdate.replace("/", "-") in ma.eta_date:
                        d_idx = idx
                        break
                s_offset = shift_offset_map.get(ma.eta_shift, 0)
                target_shift = d_idx * 3 + s_offset
                part_target = ma.associated_part or ma.part_no
                mat_arrival_map[part_target] = max(mat_arrival_map.get(part_target, 0), target_shift)
            except Exception:
                pass

    line_pm_slots: Dict[str, List[int]] = defaultdict(list)
    for pm in snapshot.pm_schedules:
        if pm.capacity_rate == 0 or "避让" in pm.handling_strategy or "切分" in pm.handling_strategy:
            try:
                d_start = 0
                for idx, pdate in enumerate(snapshot.planning_dates):
                    if pdate in pm.start_date or pm.start_date.endswith(pdate.replace("/", "-")) or pdate.replace("/", "-") in pm.start_date:
                        d_start = idx
                        break
                d_end = d_start
                for idx, pdate in enumerate(snapshot.planning_dates):
                    if pdate in pm.end_date or pm.end_date.endswith(pdate.replace("/", "-")) or pdate.replace("/", "-") in pm.end_date:
                        d_end = idx
                        break
                s_start = d_start * 3 + shift_offset_map.get(pm.start_shift, 0)
                s_end = d_end * 3 + shift_offset_map.get(pm.end_shift, 0)
                for s in range(s_start, s_end + 1):
                    if s not in line_pm_slots[pm.line]:
                        line_pm_slots[pm.line].append(s)
            except Exception:
                pass

    for order in sorted(snapshot.orders, key=lambda o: (o.due_datetime, o.part_no)):
        param = snapshot.fg_params.get(order.part_no)
        if param is None:
            warnings.append(f"{order.order_id}: 成品 {order.part_no} 无 Parameters-FG 主数据，跳过")
            continue

        usable = max(0.0, fg_avail[order.part_no] - param.target_stock)
        net_need = order.demand_quantity - usable
        fg_consumed[order.part_no] = fg_consumed.get(order.part_no, 0.0) + order.demand_quantity

        if net_need <= 0:
            fg_avail[order.part_no] -= order.demand_quantity
            covered_by_stock.append(
                f"{order.order_id}: 需求 {order.demand_quantity:.0f} 全部由库存覆盖"
                f"（期初 {snapshot.initial_stock.get(order.part_no, 0.0):.0f}），不排产"
            )
            continue

        batches = int(math.ceil(net_need / param.mpq))
        lot_size = int(round(param.mpq))
        qty = batches * lot_size
        fg_avail[order.part_no] += qty - order.demand_quantity
        fg_produced[order.part_no] = fg_produced.get(order.part_no, 0.0) + qty

        fg_task = ProductionTask(
            task_id=f"FG::{order.order_id}",
            stage="FG",
            line=param.line,
            part_no=order.part_no,
            description=param.description,
            ct_seconds=param.ct_seconds,
            trp=param.trp,
            mpq=lot_size,
            lot_size=lot_size,
            lots=batches,
            quantity=float(qty),
            lot_millis=lot_millis(lot_size, param.ct_seconds, param.trp),
            order_id=order.order_id,
            due_datetime=order.due_datetime,
            mold_id=mold_map.get(order.part_no),
            material_arrival_shift=mat_arrival_map.get(order.part_no, 0),
            customer_tier=cust_tier_map.get(order.part_no, "TIER_A"),
            line_pm_slots=line_pm_slots.get(param.line, []),
        )
        fg_tasks.append(fg_task)

        for sf_pn, ratio in snapshot.bom.get(order.part_no, []):
            sf_param = snapshot.sf_params.get(sf_pn)
            if sf_param is None:
                warnings.append(f"{fg_task.task_id}: BOM 半成品 {sf_pn} 无 Parameters-SFN 主数据，跳过齐套")
                continue

            gross_need = qty * ratio
            sf_usable = max(0.0, sf_avail[sf_pn] - sf_param.target_stock)
            sf_net = gross_need - sf_usable
            sf_consumed[sf_pn] = sf_consumed.get(sf_pn, 0.0) + gross_need

            if sf_net <= 0:
                sf_avail[sf_pn] -= gross_need
                continue

            min_lot = int(round(sf_param.mpq))
            sf_qty = max(min_lot, int(math.ceil(sf_net)))
            sf_avail[sf_pn] += sf_qty - gross_need
            sf_produced[sf_pn] = sf_produced.get(sf_pn, 0.0) + sf_qty

            sf_tasks.append(ProductionTask(
                task_id=f"SF::{order.order_id}::{sf_pn}",
                stage="SF",
                line=sf_param.line,
                part_no=sf_pn,
                description=sf_param.description,
                ct_seconds=sf_param.ct_seconds,
                trp=sf_param.trp,
                mpq=min_lot,
                lot_size=1,
                lots=sf_qty,
                quantity=float(sf_qty),
                lot_millis=lot_millis(1, sf_param.ct_seconds, sf_param.trp),
                order_id=order.order_id,
                bom_ratio=ratio,
                stock_covered=int(round(sf_usable)),
                due_datetime=order.due_datetime,
                parent_task_id=fg_task.task_id,
                mold_id=mold_map.get(sf_pn),
                material_arrival_shift=mat_arrival_map.get(sf_pn, 0),
                customer_tier=cust_tier_map.get(order.part_no, "TIER_A"),
                line_pm_slots=line_pm_slots.get(sf_param.line, []),
            ))

    movements = [
        StockMovement(
            part_no=pn,
            stage="FG",
            opening=snapshot.initial_stock.get(pn, 0.0),
            produced=fg_produced.get(pn, 0.0),
            consumed=fg_consumed.get(pn, 0.0),
            target_stock=snapshot.fg_params[pn].target_stock,
            closing=fg_avail[pn],
        )
        for pn in snapshot.fg_params
        if fg_produced.get(pn) or fg_consumed.get(pn) or snapshot.initial_stock.get(pn)
    ] + [
        StockMovement(
            part_no=pn,
            stage="SF",
            opening=snapshot.initial_stock.get(pn, 0.0),
            produced=sf_produced.get(pn, 0.0),
            consumed=sf_consumed.get(pn, 0.0),
            target_stock=snapshot.sf_params[pn].target_stock,
            closing=sf_avail[pn],
        )
        for pn in snapshot.sf_params
        if sf_produced.get(pn) or sf_consumed.get(pn) or snapshot.initial_stock.get(pn)
    ]

    return RequirementPlan(
        fg_tasks=fg_tasks,
        sf_tasks=sf_tasks,
        stock_movements=movements,
        covered_by_stock=covered_by_stock,
        warnings=warnings,
    )
