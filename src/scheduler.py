"""
APS Scheduling Engine using Google OR-Tools CP-SAT.
Formulates the two-stage flow-shop scheduling problem:
Stage 1: Semi-finished bushing production on line '轴套6'
Stage 2: Finished linkage assembly production on line 'PGLINKAGE'
Enforces:
- Line resource non-overlap constraints
- Precedence: DS and PS bushings must finish before finished assembly begins
- Due-date tracking (earliest completion, target early-shift completion)
- Continuous flow / high utilization packing (no early idle)
- Multi-product sharing on lines
- MPQ integer multiple production: all quantities in all shifts are integer multiples of MPQ!
"""
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import math

from ortools.sat.python import cp_model

from src.data_loader import ProductionInputSnapshot, CustomerOrder, ProductParam
from src.shift_manager import ShiftBucket, generate_shift_calendar, allocate_mpq_to_shifts
from src.exporter import ScheduledTask

class APSScheduler:
    def __init__(
        self,
        snapshot: ProductionInputSnapshot,
        start_datetime: datetime = datetime(2026, 10, 12, 7, 0, 0),
        planning_days: int = 20
    ):
        self.snapshot = snapshot
        self.start_dt = start_datetime
        self.planning_days = planning_days
        self.shifts = generate_shift_calendar(self.start_dt, self.planning_days)
        
    def solve(self, time_limit_seconds: float = 30.0) -> List[ScheduledTask]:
        model = cp_model.CpModel()
        
        horizon_minutes = self.planning_days * 24 * 60
        
        fg_intervals = []
        sf_intervals = []
        fg_ends = []
        sf_ends = []
        latenesses = []
        
        scheduled_meta = []
        
        fg_line_name = "PGLINKAGE"
        sf_line_name = "轴套6"
        
        for idx, order in enumerate(self.snapshot.orders):
            fg_param = self.snapshot.fg_params.get(order.part_no)
            if not fg_param:
                continue
                
            # 1. Finished Goods: quantity is already rounded up to MPQ multiple
            # e.g. for WBC9317C: demand 960 -> scheduled_quantity = 1008 (14 batches of 72)
            fg_qty = order.scheduled_quantity
            fg_batches = order.batches
            fg_mpq = fg_param.mpq
            
            fg_seconds = (fg_qty * fg_param.ct_seconds) / fg_param.trp
            fg_dur_min = max(1, int(math.ceil(fg_seconds / 60.0)))
            
            fg_start = model.NewIntVar(0, horizon_minutes, f"fg_start_{idx}_{order.part_no}")
            fg_end = model.NewIntVar(0, horizon_minutes, f"fg_end_{idx}_{order.part_no}")
            fg_interval = model.NewIntervalVar(fg_start, fg_dur_min, fg_end, f"fg_interval_{idx}_{order.part_no}")
            fg_intervals.append(fg_interval)
            fg_ends.append(fg_end)
            
            # Due date in minutes from start_dt (Day D 15:00 end of 早班)
            due_delta_min = max(0, int((order.due_datetime - self.start_dt).total_seconds() / 60.0))
            
            lateness = model.NewIntVar(0, horizon_minutes, f"lateness_{idx}")
            model.Add(lateness >= fg_end - due_delta_min)
            latenesses.append(lateness)
            
            # 2. Semi-Finished Goods tasks via BOM on 轴套6
            sf_components = self.snapshot.bom.get(order.part_no, [])
            sf_task_records = []
            
            for sf_idx, (sf_pn, ratio) in enumerate(sf_components):
                sf_param = self.snapshot.sf_params.get(sf_pn)
                if not sf_param:
                    continue
                    
                # Semi-finished production must also round up to its own MPQ (e.g. 100)
                raw_sf_need = fg_qty * ratio
                sf_mpq = sf_param.mpq
                sf_batches = int(math.ceil(raw_sf_need / sf_mpq))
                sf_scheduled_qty = float(sf_batches * sf_mpq)
                
                sf_seconds = (sf_scheduled_qty * sf_param.ct_seconds) / sf_param.trp
                sf_dur_min = max(1, int(math.ceil(sf_seconds / 60.0)))
                
                sf_start = model.NewIntVar(0, horizon_minutes, f"sf_start_{idx}_{sf_idx}_{sf_pn}")
                sf_end = model.NewIntVar(0, horizon_minutes, f"sf_end_{idx}_{sf_idx}_{sf_pn}")
                sf_interval = model.NewIntervalVar(sf_start, sf_dur_min, sf_end, f"sf_interval_{idx}_{sf_idx}_{sf_pn}")
                sf_intervals.append(sf_interval)
                sf_ends.append(sf_end)
                
                # Precedence: Bushings must finish before Linkage assembly starts
                model.Add(sf_end <= fg_start)
                
                sf_task_records.append({
                    'line': sf_line_name,
                    'part_no': sf_pn,
                    'description': sf_param.description,
                    'ct_seconds': sf_param.ct_seconds,
                    'trp': sf_param.trp,
                    'order_id': order.order_id,
                    'quantity': sf_scheduled_qty,
                    'batches': sf_batches,
                    'mpq': sf_mpq,
                    'start_var': sf_start,
                    'end_var': sf_end,
                    'dur_min': sf_dur_min,
                    'dur_hours': sf_seconds / 3600.0
                })
                
            scheduled_meta.append({
                'order': order,
                'fg_line': fg_line_name,
                'fg_param': fg_param,
                'fg_qty': fg_qty,
                'fg_batches': fg_batches,
                'fg_mpq': fg_mpq,
                'fg_start': fg_start,
                'fg_end': fg_end,
                'fg_dur_min': fg_dur_min,
                'fg_dur_hours': fg_seconds / 3600.0,
                'due_min': due_delta_min,
                'lateness': lateness,
                'sf_tasks': sf_task_records
            })
            
        # Hard Constraints: Single machine No-Overlap
        model.AddNoOverlap(fg_intervals)
        model.AddNoOverlap(sf_intervals)
        
        # Objective:
        # Heavily penalize lateness (准时交付第一: delivery is #1 priority)
        # Minimize completion times (紧凑排产，消除早停，高利用率)
        model.Minimize(sum(latenesses) * 10000 + sum(fg_ends) + sum(sf_ends))
        
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_workers = 8
        solver.parameters.log_search_progress = False
        
        status = solver.Solve(model)
        
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"Solver failed to find a feasible solution! Status: {solver.StatusName(status)}")
            
        print(f"\n--- Solver Finished ---")
        print(f"Status: {solver.StatusName(status)}")
        print(f"Objective Value: {solver.ObjectiveValue()}")
        print(f"Wall Time: {solver.WallTime():.2f}s")
        
        # Extract results into ScheduledTask list
        results = []
        for item in scheduled_meta:
            # 1. Finished good task
            fg_s_min = solver.Value(item['fg_start'])
            fg_e_min = solver.Value(item['fg_end'])
            fg_s_dt = self.start_dt + timedelta(minutes=fg_s_min)
            fg_e_dt = self.start_dt + timedelta(minutes=fg_e_min)
            
            # Allocate integer MPQ batches across shifts
            fg_breakdown = allocate_mpq_to_shifts(
                task_start=fg_s_dt,
                task_end=fg_e_dt,
                total_batches=item['fg_batches'],
                mpq=item['fg_mpq'],
                shifts=self.shifts
            )
            
            results.append(ScheduledTask(
                line=item['fg_line'],
                part_no=item['order'].part_no,
                description=item['fg_param'].description,
                ct_seconds=item['fg_param'].ct_seconds,
                trp=item['fg_param'].trp,
                order_id=item['order'].order_id,
                quantity=item['fg_qty'],
                start_dt=fg_s_dt,
                end_dt=fg_e_dt,
                duration_hours=item['fg_dur_hours'],
                shift_breakdown=fg_breakdown
            ))
            
            # 2. Semi-finished tasks
            for sf in item['sf_tasks']:
                sf_s_min = solver.Value(sf['start_var'])
                sf_e_min = solver.Value(sf['end_var'])
                sf_s_dt = self.start_dt + timedelta(minutes=sf_s_min)
                sf_e_dt = self.start_dt + timedelta(minutes=sf_e_min)
                
                sf_breakdown = allocate_mpq_to_shifts(
                    task_start=sf_s_dt,
                    task_end=sf_e_dt,
                    total_batches=sf['batches'],
                    mpq=sf['mpq'],
                    shifts=self.shifts
                )
                
                results.append(ScheduledTask(
                    line=sf['line'],
                    part_no=sf['part_no'],
                    description=sf['description'],
                    ct_seconds=sf['ct_seconds'],
                    trp=sf['trp'],
                    order_id=sf['order_id'],
                    quantity=sf['quantity'],
                    start_dt=sf_s_dt,
                    end_dt=sf_e_dt,
                    duration_hours=sf['dur_hours'],
                    shift_breakdown=sf_breakdown
                ))
                
        return results
