"""ACT-L3: labor master data (availability, line staffing, crew pools).

Files are optional; when absent the labor operator is inert and the planner keeps
the existing behavior. The loader validates missing/negative/over-assigned values.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

AVAILABILITY_FILE = "Labor-Availability.csv"
STAFFING_FILE = "Line-Staffing.csv"
POOL_FILE = "Crew-Pool.csv"

AVAILABILITY_HEADERS = ["site", "date", "shift", "available_headcount"]
STAFFING_HEADERS = ["site", "line", "part_no", "crew_required", "changeover_extra", "skill"]
POOL_HEADERS = ["site", "pool_id", "line"]


@dataclass
class LaborData:
    availability: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    staffing: Dict[Tuple[str, str, str], float] = field(default_factory=dict)
    pools: Dict[Tuple[str, str], str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def available(self, site: str, date_str: str, shift_name: str) -> Optional[int]:
        return self.availability.get((site, date_str, shift_name))

    def crew_required(self, site: str, line: str, part_no: str) -> Optional[float]:
        return self.staffing.get((site, line, part_no)) or self.staffing.get((site, line, "*"))

    def pool(self, site: str, line: str) -> str:
        return self.pools.get((site, line)) or f"__line__:{line}"


def _read_rows(path: Path, headers: List[str], errors: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [header for header in headers if header not in (reader.fieldnames or [])]
        if missing:
            errors.append(f"{path.name}: 缺少列 {missing}")
            return []
        return [dict(row) for row in reader]


def load_labor_data(data_dir: str | Path, site: Optional[str] = None) -> LaborData:
    directory = Path(data_dir)
    data = LaborData()
    availability_rows = _read_rows(directory / AVAILABILITY_FILE, AVAILABILITY_HEADERS, data.errors)
    staffing_rows = _read_rows(directory / STAFFING_FILE, STAFFING_HEADERS, data.errors)
    pool_rows = _read_rows(directory / POOL_FILE, POOL_HEADERS, data.errors)

    for row in availability_rows:
        row_site = (row.get("site") or "").strip()
        if site and row_site != site:
            continue
        date_str = (row.get("date") or "").strip()
        shift = (row.get("shift") or "").strip()
        try:
            headcount = int(float(row.get("available_headcount") or 0))
        except ValueError:
            data.errors.append(f"{AVAILABILITY_FILE}: {row_site}/{date_str}/{shift} 可用人数不是数字")
            continue
        if not row_site or not date_str or not shift:
            data.errors.append(f"{AVAILABILITY_FILE}: 缺少 site/date/shift")
            continue
        if headcount < 0:
            data.errors.append(f"{AVAILABILITY_FILE}: {row_site}/{date_str}/{shift} 可用人数为负")
            continue
        key = (row_site, date_str, shift)
        if key in data.availability:
            data.errors.append(f"{AVAILABILITY_FILE}: {row_site}/{date_str}/{shift} 重复")
            continue
        data.availability[key] = headcount

    for row in staffing_rows:
        row_site = (row.get("site") or "").strip()
        if site and row_site != site:
            continue
        line = (row.get("line") or "").strip()
        part = (row.get("part_no") or "*").strip() or "*"
        try:
            crew = float(row.get("crew_required") or 0)
        except ValueError:
            data.errors.append(f"{STAFFING_FILE}: {row_site}/{line}/{part} 人数不是数字")
            continue
        if not row_site or not line:
            data.errors.append(f"{STAFFING_FILE}: 缺少 site/line")
            continue
        if crew <= 0:
            data.errors.append(f"{STAFFING_FILE}: {row_site}/{line}/{part} 人数必须为正")
            continue
        key = (row_site, line, part)
        if key in data.staffing:
            data.errors.append(f"{STAFFING_FILE}: {row_site}/{line}/{part} 重复")
            continue
        data.staffing[key] = crew

    for row in pool_rows:
        row_site = (row.get("site") or "").strip()
        if site and row_site != site:
            continue
        line = (row.get("line") or "").strip()
        pool_id = (row.get("pool_id") or "").strip()
        if not row_site or not line or not pool_id:
            data.errors.append(f"{POOL_FILE}: 缺少 site/pool_id/line")
            continue
        key = (row_site, line)
        if key in data.pools and data.pools[key] != pool_id:
            data.errors.append(f"{POOL_FILE}: {row_site}/{line} 同时属于 {data.pools[key]} 和 {pool_id}")
            continue
        data.pools[key] = pool_id

    if not availability_rows and not staffing_rows:
        data.warnings.append("未找到人力主数据（Labor-Availability / Line-Staffing），labor_capacity 将跳过")
    return data


def write_templates(directory: str | Path) -> List[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    examples = {
        AVAILABILITY_FILE: (AVAILABILITY_HEADERS, [["WL", "2026-10-12", "早班", 20]]),
        STAFFING_FILE: (STAFFING_HEADERS, [["WL", "PGLINKAGE", "*", 3, 0, ""]]),
        POOL_FILE: (POOL_HEADERS, [["WL", "POOL-A", "PGLINKAGE"]]),
    }
    for name, (headers, rows) in examples.items():
        path = directory / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        written.append(path)
    return written
