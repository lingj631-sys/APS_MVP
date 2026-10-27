"""Domain entities for manufacturing process routing and line selection.

Provides the foundational domain abstractions for Phase 2:
- Decouples products from a single hardcoded line string.
- Allows an operation to declare alternative lines with priority, efficiency factors,
  and changeover specifications.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LineCapability:
    """Capability of a specific line to produce an operation."""
    line_code: str
    priority: int = 1                    # 1 = primary line, 2 = secondary, etc.
    ct_seconds: float = 30.0             # Cycle time on this specific line
    trp: float = 0.85                    # OEE / TRP factor on this line
    setup_minutes: float = 30.0          # Changeover time on this line
    headcount_required: float = 1.0


@dataclass
class ProcessOperation:
    """A discrete manufacturing step in a product's routing."""
    operation_id: str                   # e.g., "OP10_STAMPING", "OP20_ASSEMBLY"
    stage: str                          # "FG" or "SF"
    sequence: int                       # 10, 20, 30...
    capabilities: List[LineCapability] = field(default_factory=list)

    @property
    def primary_line(self) -> Optional[str]:
        if not self.capabilities:
            return None
        sorted_caps = sorted(self.capabilities, key=lambda c: c.priority)
        return sorted_caps[0].line_code

    @property
    def candidate_lines(self) -> List[str]:
        return [c.line_code for c in self.capabilities]


@dataclass
class Routing:
    """Complete manufacturing route for a part."""
    routing_id: str
    part_no: str
    site_code: str
    version: str = "1.0"
    is_active: bool = True
    operations: List[ProcessOperation] = field(default_factory=list)
