"""Domain value object for shift time slots.

Eliminates Primitive Obsession by encapsulating (date_str, shift_name),
start/end timestamps, duration, and global slot indexing into a single
immutable, hashable Value Object.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Tuple


@dataclass(frozen=True)
class ShiftSlot:
    """Atomic time-slot entity in the APS scheduling calendar."""
    slot_index: int
    date_str: str          # e.g., "10/12"
    shift_name: str        # e.g., "早班", "中班", "晚班"
    start_dt: datetime
    end_dt: datetime
    duration_hours: float
    is_work_shift: bool = True
    weekday: int = 0       # 0=Monday, 6=Sunday

    @property
    def key(self) -> str:
        """Formatted display key e.g. '10/12 早班'."""
        return f"{self.date_str} {self.shift_name}"

    @property
    def tuple_key(self) -> Tuple[str, str]:
        """Tuple representation for dictionary keys."""
        return (self.date_str, self.shift_name)

    @property
    def duration_seconds(self) -> float:
        return self.duration_hours * 3600.0

    def __str__(self) -> str:
        return self.key
