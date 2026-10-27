"""Domain entities for parts, multi-level BOMs, and inventory policies."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class PartType(str, Enum):
    FINISHED_GOOD = "FG"
    SEMI_FINISHED = "SF"
    RAW_MATERIAL = "RAW"


@dataclass
class BOMRelation:
    """Directed link between parent and child parts."""
    parent_part_no: str
    child_part_no: str
    quantity_per_parent: float = 1.0     # e.g., 1.0 or 0.5 (supports fractional BOMs)
    scrap_rate: float = 0.0


@dataclass
class Part:
    """Master part entity."""
    part_no: str
    description: str
    part_type: PartType
    site_code: str
    mpq: float = 1.0
    target_stock_days: float = 0.0
    safety_stock_units: float = 0.0
    current_inventory: float = 0.0
    unit: str = "piece"
