"""
Shift Manager for APS MVP.
Implements Option B:
- 早班 (Early): 07:00 ~ 15:00 (8 hours)
- 中班 (Middle): 15:00 ~ 23:00 (8 hours)
- 晚班 (Night): 23:00 ~ 07:00 (+1 day, 8 hours)

Provides:
- generate_shift_calendar
- allocate_mpq_to_shifts: Distributes an exact integer number of MPQ batches across shifts.
  Ensures zero decimals and that every single shift quantity is an exact integer multiple of MPQ!
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import math

@dataclass
class ShiftBucket:
    date_str: str       # e.g. "10/12", "10/13"
    shift_name: str     # "早班", "中班", "晚班"
    start_dt: datetime
    end_dt: datetime

def generate_shift_calendar(start_date: datetime, num_days: int) -> List[ShiftBucket]:
    shifts = []
    for day_idx in range(num_days):
        cur_day = start_date + timedelta(days=day_idx)
        d_str = f"{cur_day.month}/{cur_day.day}"
        
        # 1. 早班: cur_day 07:00 ~ 15:00
        e_start = datetime(cur_day.year, cur_day.month, cur_day.day, 7, 0, 0)
        e_end = datetime(cur_day.year, cur_day.month, cur_day.day, 15, 0, 0)
        shifts.append(ShiftBucket(d_str, "早班", e_start, e_end))
        
        # 2. 中班: cur_day 15:00 ~ 23:00
        m_start = datetime(cur_day.year, cur_day.month, cur_day.day, 15, 0, 0)
        m_end = datetime(cur_day.year, cur_day.month, cur_day.day, 23, 0, 0)
        shifts.append(ShiftBucket(d_str, "中班", m_start, m_end))
        
        # 3. 晚班: cur_day 23:00 ~ next_day 07:00
        n_start = datetime(cur_day.year, cur_day.month, cur_day.day, 23, 0, 0)
        n_end = n_start + timedelta(hours=8)
        shifts.append(ShiftBucket(d_str, "晚班", n_start, n_end))
        
    return shifts

def allocate_mpq_to_shifts(
    task_start: datetime,
    task_end: datetime,
    total_batches: int,
    mpq: float,
    shifts: List[ShiftBucket]
) -> Dict[Tuple[str, str], int]:
    """
    Distributes total_batches (integers) across overlapping shifts based on proportional time overlap.
    Uses the Largest Remainder Method (Hare-Niemeyer) to ensure:
    1. sum(shift_batches) == total_batches exactly.
    2. Each shift gets an integer number of batches (>= 0).
    3. Shift quantity = shift_batches * mpq (an exact integer multiple of MPQ, NO DECIMALS).
    """
    total_seconds = (task_end - task_start).total_seconds()
    if total_seconds <= 0 or total_batches <= 0:
        return {}
        
    overlapping = []
    for s in shifts:
        o_start = max(task_start, s.start_dt)
        o_end = min(task_end, s.end_dt)
        if o_end > o_start:
            overlap_sec = (o_end - o_start).total_seconds()
            overlapping.append((s.date_str, s.shift_name, overlap_sec))
            
    if not overlapping:
        return {}
        
    raw_shares = []
    allocated_batches = []
    for d_str, s_name, o_sec in overlapping:
        exact_share = total_batches * (o_sec / total_seconds)
        base_share = int(math.floor(exact_share))
        remainder = exact_share - base_share
        raw_shares.append((remainder, len(allocated_batches)))
        allocated_batches.append(base_share)
        
    remaining_batches = total_batches - sum(allocated_batches)
    raw_shares.sort(key=lambda x: x[0], reverse=True)
    for i in range(remaining_batches):
        idx = raw_shares[i][1]
        allocated_batches[idx] += 1
        
    result = {}
    for i, (d_str, s_name, _) in enumerate(overlapping):
        b_count = allocated_batches[i]
        if b_count > 0:
            result[(d_str, s_name)] = int(b_count * int(mpq))
            
    return result
