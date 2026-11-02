"""C4-b: operation-level lead-time data (queue / inspection / move / transfer)."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class OperationLeadTime:
    site: str
    part_no: str
    stage: str
    operation_seq: int
    operation_name: str
    setup_min: float
    run_ct_seconds: float
    queue_min: float
    inspection_min: float
    transfer_batch: float
    move_min: float

    @property
    def non_processing_minutes(self) -> float:
        return self.setup_min + self.queue_min + self.inspection_min + self.move_min


def load_operation_lead_times(path: str | Path, site: Optional[str] = None) -> Dict[Tuple[str, str, int], OperationLeadTime]:
    out: Dict[Tuple[str, str, int], OperationLeadTime] = {}
    p = Path(path)
    if not p.exists():
        return out
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row_site = (row.get("Site") or "").strip()
            part = (row.get("Part No.") or "").strip()
            if not row_site or not part or (site and row_site != site):
                continue
            try:
                op = int(float(row.get("Operation Seq") or 0))
                setup = float(row.get("Setup Min") or 0.0)
                run_ct = float(row.get("Run CT Seconds") or 0.0)
                queue = float(row.get("Queue Min") or 0.0)
                inspection = float(row.get("Inspection Min") or 0.0)
                transfer = float(row.get("Transfer Batch Qty") or 0.0)
                move = float(row.get("Move Min") or 0.0)
            except ValueError:
                continue
            out[(row_site, part, op)] = OperationLeadTime(
                site=row_site,
                part_no=part,
                stage=(row.get("Stage") or "").strip(),
                operation_seq=op,
                operation_name=(row.get("Operation Name") or "").strip(),
                setup_min=setup,
                run_ct_seconds=run_ct,
                queue_min=queue,
                inspection_min=inspection,
                transfer_batch=transfer,
                move_min=move,
            )
    return out


def lead_shifts(
    entry: OperationLeadTime | None,
    *,
    lead_time_days: float,
    shift_hours: float,
    quantity: float = 0.0,
    trp: float = 1.0,
    whole_batch: bool = False,
) -> int:
    """Convert product lead days plus operation rest/prep into shifts.

    2026-10 语义：`setup_min` 承载『提前准备时间』（静置/等待），
    `transfer_batch` 是允许向下游转运的最小批量。首次转运必须等待
    整批完成并静置，因此前置时间 = 静置分钟 + 首批加工时间：
        rest + ceil(min(qty, batch) × CT / TRP)
    """
    hours = max(0.0, float(lead_time_days)) * 24.0
    if entry is not None:
        minutes = entry.setup_min + entry.queue_min + entry.inspection_min
        batch_pieces = 0.0
        if entry.transfer_batch > 0 and quantity > 0:
            transfers = math.ceil(quantity / entry.transfer_batch)
            minutes += entry.move_min * transfers
            batch_pieces = min(float(quantity), float(entry.transfer_batch))
        else:
            minutes += entry.move_min
            batch_pieces = max(0.0, float(quantity))
        if entry.run_ct_seconds > 0 and batch_pieces > 0:
            if whole_batch:
                batch_pieces = max(0.0, float(quantity))
            minutes += batch_pieces * float(entry.run_ct_seconds) / max(1e-9, float(trp)) / 60.0
        hours += minutes / 60.0
    if hours <= 0 or shift_hours <= 0:
        return 0
    return max(1, int(math.ceil(hours / shift_hours)))
