"""
Data Loader for APS MVP Simulation.
Reads parameters, BOM, initial stock, and customer demand from Excel.
Enforces MPQ batch rounding for scheduled production quantities.
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import openpyxl
import math
from datetime import datetime

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
    lead_time_days: float = 0.0

@dataclass
class CustomerOrder:
    order_id: str
    part_no: str
    description: str
    due_date: str  # e.g. "10/13"
    due_datetime: datetime
    demand_quantity: float      # raw customer demand
    scheduled_quantity: float   # rounded up to multiple of MPQ
    batches: int                # scheduled_quantity / MPQ
    surplus_inventory: float    # scheduled_quantity - demand_quantity

@dataclass
class ProductionInputSnapshot:
    fg_params: Dict[str, ProductParam]
    sf_params: Dict[str, ProductParam]
    bom: Dict[str, List[Tuple[str, float]]]
    initial_stock: Dict[str, float]
    orders: List[CustomerOrder]
    planning_dates: List[str]

def load_aps_database(excel_path: str, site_filter: str = "WL") -> ProductionInputSnapshot:
    # Use data_only=False first to read structure, but data_only=True to read values
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    
    # 0. Load CT master from 'WL CT ' sheet
    ct_map = {}
    if 'WL CT ' in wb.sheetnames:
        ws_ct = wb['WL CT ']
        for r in range(2, ws_ct.max_row + 1):
            m = ws_ct.cell(r, 1).value
            c_val = ws_ct.cell(r, 3).value
            if m is not None and c_val is not None:
                try:
                    ct_map[str(m).strip()] = float(c_val)
                except ValueError:
                    pass

    # 1. Load Parameters-FG
    ws_fg = wb['Parameters-FG']
    fg_params = {}
    for r in range(2, ws_fg.max_row + 1):
        site = str(ws_fg.cell(r, 1).value or '').strip()
        if site == site_filter:
            pn = str(ws_fg.cell(r, 4).value or '').strip()
            if not pn: continue
            
            # CT fallback to ct_map if cell value is None or formula
            cell_ct = ws_fg.cell(r, 7).value
            try:
                ct = float(cell_ct) if cell_ct is not None else ct_map.get(pn, 40.0)
            except (ValueError, TypeError):
                ct = ct_map.get(pn, 40.0)
                
            trp = float(ws_fg.cell(r, 3).value or 0.71)
            mpq = float(ws_fg.cell(r, 6).value or 1.0)
            hours_shift = float(ws_fg.cell(r, 9).value or 8.0)
            shifts_day = int(ws_fg.cell(r, 10).value or 3)
            out_shift = (hours_shift * 3600.0 / ct) * trp
            
            fg_params[pn] = ProductParam(
                site=site,
                line=str(ws_fg.cell(r, 2).value or '').strip(),
                part_no=pn,
                description=str(ws_fg.cell(r, 5).value or '').strip(),
                ct_seconds=ct,
                trp=trp,
                mpq=mpq,
                output_per_shift=out_shift,
                hours_per_shift=hours_shift,
                shifts_per_day=shifts_day,
                target_stock=float(ws_fg.cell(r, 8).value or 0.0)
            )

    # 2. Load Parameters-SFN
    ws_sf = wb['Parameters-SFN']
    sf_params = {}
    for r in range(2, ws_sf.max_row + 1):
        site = str(ws_sf.cell(r, 1).value or '').strip()
        if site == site_filter:
            pn = str(ws_sf.cell(r, 4).value or '').strip()
            if not pn: continue
            
            cell_ct = ws_sf.cell(r, 7).value
            try:
                ct = float(cell_ct) if cell_ct is not None else ct_map.get(pn, 25.0)
            except (ValueError, TypeError):
                ct = ct_map.get(pn, 25.0)
                
            trp = float(ws_sf.cell(r, 3).value or 0.85)
            mpq = float(ws_sf.cell(r, 6).value or 100.0)
            hours_shift = float(ws_sf.cell(r, 11).value or 8.0)
            shifts_day = int(ws_sf.cell(r, 12).value or 3)
            out_shift = (hours_shift * 3600.0 / ct) * trp
            
            sf_params[pn] = ProductParam(
                site=site,
                line=str(ws_sf.cell(r, 2).value or '').strip(),
                part_no=pn,
                description=str(ws_sf.cell(r, 5).value or '').strip(),
                ct_seconds=ct,
                trp=trp,
                mpq=mpq,
                output_per_shift=out_shift,
                hours_per_shift=hours_shift,
                shifts_per_day=shifts_day,
                target_stock=float(ws_sf.cell(r, 8).value or 0.0),
                lead_time_days=float(ws_sf.cell(r, 9).value or 0.0)
            )

    # 3. Load BOM List
    ws_bom = wb['BOM List']
    bom = {}
    for r in range(2, ws_bom.max_row + 1):
        site = str(ws_bom.cell(r, 1).value or '').strip()
        if site == site_filter:
            fg = str(ws_bom.cell(r, 2).value or '').strip()
            sf = str(ws_bom.cell(r, 4).value or '').strip()
            ratio = float(ws_bom.cell(r, 6).value or 1.0)
            if fg and sf:
                bom.setdefault(fg, []).append((sf, ratio))

    # 4. Load Initial Stock
    ws_stock = wb['Initial Stock']
    initial_stock = {}
    for r in range(2, ws_stock.max_row + 1):
        site = str(ws_stock.cell(r, 1).value or '').strip()
        if site == site_filter:
            pn = str(ws_stock.cell(r, 3).value or '').strip()
            qty = float(ws_stock.cell(r, 5).value or 0.0)
            if pn:
                initial_stock[pn] = qty

    # 5. Load Customer Demand and enforce MPQ integer multiple
    ws_dem = wb['Customer Demand']
    demand_dates = [str(ws_dem.cell(2, c).value or '').strip() for c in range(4, ws_dem.max_column + 1) if ws_dem.cell(2, c).value is not None]
    orders = []
    for r in range(3, ws_dem.max_row + 1):
        site = str(ws_dem.cell(r, 1).value or '').strip()
        if site == site_filter:
            pn = str(ws_dem.cell(r, 2).value or '').strip()
            desc = str(ws_dem.cell(r, 3).value or '').strip()
            if not pn: continue
            
            fg_p = fg_params.get(pn)
            mpq = fg_p.mpq if fg_p else 1.0
            
            for i, d_str in enumerate(demand_dates):
                v = ws_dem.cell(r, 4 + i).value
                if v and float(v) > 0:
                    dem_qty = float(v)
                    # Enforce MPQ integer multiple
                    batches = int(math.ceil(dem_qty / mpq))
                    sched_qty = float(batches * mpq)
                    surplus = sched_qty - dem_qty
                    
                    parts = d_str.split('/')
                    m, d = int(parts[0]), int(parts[1])
                    due_dt = datetime(2026, m, d, 15, 0)
                    ord_id = f"ORD_{site}_{pn}_{m:02d}{d:02d}"
                    orders.append(CustomerOrder(
                        order_id=ord_id,
                        part_no=pn,
                        description=desc,
                        due_date=d_str,
                        due_datetime=due_dt,
                        demand_quantity=dem_qty,
                        scheduled_quantity=sched_qty,
                        batches=batches,
                        surplus_inventory=surplus
                    ))

    return ProductionInputSnapshot(
        fg_params=fg_params,
        sf_params=sf_params,
        bom=bom,
        initial_stock=initial_stock,
        orders=orders,
        planning_dates=demand_dates
    )
