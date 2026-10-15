"""
Main pipeline execution script for APS MVP.
Reads 'APS Database- Simulation draft.xlsx', solves two-stage scheduling using OR-Tools CP-SAT,
and exports the result to 'Production plan-Done' sheet.
"""
import os
import shutil
import sys

# Ensure d:\Codes\APS_MVP is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from datetime import datetime
from src.data_loader import load_aps_database
from src.scheduler import APSScheduler
from src.exporter import export_production_plan

def main():
    excel_path = os.path.join(BASE_DIR, "APS Database- Simulation draft.xlsx")
    backup_path = os.path.join(BASE_DIR, "APS Database- Simulation draft.backup.xlsx")
    
    # 1. Backup original file if backup doesn't exist
    if not os.path.exists(backup_path):
        shutil.copyfile(excel_path, backup_path)
        print(f"Created backup at {backup_path}")
        
    print(f"Loading data from {excel_path} for Site WL...")
    snapshot = load_aps_database(excel_path, site_filter="WL")
    print(f"Loaded {len(snapshot.fg_params)} FG params, {len(snapshot.sf_params)} SF params, {len(snapshot.orders)} orders.")
    
    # Planning anchor: 10/12 07:00 (Option B: early shift of 10/12)
    start_dt = datetime(2026, 10, 12, 7, 0, 0)
    
    # Expand planning dates to include 10/12 through 10/24
    planning_dates = ["10/12"] + [d for d in snapshot.planning_dates if d != "10/12"]
    # Also add remaining dates up to 10/26 if needed
    for extra_day in range(23, 27):
        extra_str = f"10/{extra_day}"
        if extra_str not in planning_dates:
            planning_dates.append(extra_str)
            
    print("\nInitializing OR-Tools CP-SAT Scheduler...")
    scheduler = APSScheduler(snapshot=snapshot, start_datetime=start_dt, planning_days=20)
    
    print("Solving mathematical optimization model (time limit = 15s)...")
    tasks = scheduler.solve(time_limit_seconds=15.0)
    
    # 2. Export results to Production plan-Done
    print("\nExporting schedule to Excel...")
    export_production_plan(
        template_excel_path=excel_path,
        output_excel_path=excel_path,
        scheduled_tasks=tasks,
        planning_dates=planning_dates
    )
    
    # 3. Print KPI Summary
    print("\n" + "="*70)
    print("                 APS MVP SCHEDULE KPI REPORT                 ")
    print("="*70)
    fg_tasks = [t for t in tasks if t.line == 'PGLINKAGE']
    sf_tasks = [t for t in tasks if t.line == '轴套6']
    
    print(f"Total FG Tasks (PGLINKAGE): {len(fg_tasks)}")
    print(f"Total SF Tasks (轴套6):    {len(sf_tasks)}")
    print(f"Total Production Tasks:   {len(tasks)}")
    
    # Makespan
    max_end = max(t.end_dt for t in tasks)
    min_start = min(t.start_dt for t in tasks)
    span_hours = (max_end - min_start).total_seconds() / 3600.0
    print(f"Schedule Start: {min_start.strftime('%Y-%m-%d %H:%M')}")
    print(f"Schedule End:   {max_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"Total Makespan: {span_hours:.1f} hours ({span_hours/24:.1f} days)")
    
    # Line Working Hours
    fg_hours = sum(t.duration_hours for t in fg_tasks)
    sf_hours = sum(t.duration_hours for t in sf_tasks)
    print(f"\nPGLINKAGE Total Working Time: {fg_hours:.1f} hours (Capacity Utilization: {fg_hours/span_hours*100:.1f}%)")
    print(f"轴套6      Total Working Time: {sf_hours:.1f} hours (Capacity Utilization: {sf_hours/span_hours*100:.1f}%)")
    
    # Check Lateness against due dates
    lateness_count = 0
    print("\n" + "-"*70)
    print("                    DELIVERY PERFORMANCE CHECKLIST                    ")
    print("-"*70)
    order_map = {o.order_id: o for o in snapshot.orders}
    # Sort fg_tasks by start_dt
    for t in sorted(fg_tasks, key=lambda x: x.start_dt):
        ord_obj = order_map.get(t.order_id)
        if ord_obj:
            is_late = t.end_dt > ord_obj.due_datetime
            delay_h = (t.end_dt - ord_obj.due_datetime).total_seconds() / 3600.0 if is_late else 0.0
            status = f"LATE (+{delay_h:.1f}h) [!]" if is_late else "ON TIME [OK]"
            if is_late: lateness_count += 1
            print(f"{t.order_id:<24} | Qty: {t.quantity:<6.0f} | Start: {t.start_dt.strftime('%m/%d %H:%M')} | End: {t.end_dt.strftime('%m/%d %H:%M')} | Due: {ord_obj.due_datetime.strftime('%m/%d %H:%M')} | {status}")
            
    print("\n" + "-"*70)
    print(f"On-Time Delivery Rate: {(len(fg_tasks) - lateness_count) / len(fg_tasks) * 100:.1f}% ({len(fg_tasks) - lateness_count}/{len(fg_tasks)})")
    print("="*70)

if __name__ == '__main__':
    main()
