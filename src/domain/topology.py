"""Domain entities for manufacturing topology (Site -> Workshop -> Line)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Line:
    """Production line / Work center."""
    line_code: str
    name: str
    workshop_code: str
    site_code: str
    stage: str                          # "FG" or "SF"
    default_crew: float = 0.0           # Standard headcount per shift
    is_bottleneck: bool = False
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class Workshop:
    """Manufacturing workshop / department within a plant."""
    workshop_code: str
    name: str
    site_code: str
    lines: List[Line] = field(default_factory=list)


@dataclass
class Site:
    """Factory site / Manufacturing plant."""
    site_code: str                      # e.g., "WL", "SY38"
    plant_name: str                     # e.g., "温岭工厂", "沈阳工厂"
    plant_alias: str = ""               # e.g., "WE34"
    shifts_per_day: int = 3             # 3 shifts (8h) or 2 shifts (12h)
    shift_hours: float = 8.0
    workshops: List[Workshop] = field(default_factory=list)

    @property
    def total_lines(self) -> List[Line]:
        lines = []
        for ws in self.workshops:
            lines.extend(ws.lines)
        return lines
