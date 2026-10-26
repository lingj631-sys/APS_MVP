"""Independent audit of a production plan.

This package deliberately does **not** import ``src.scheduler``: it re-derives the
requirement plan from master data and re-computes every duration from CT/TRP.  A
plan that only passes because it reuses the solver's own numbers has not been
audited.

Public API::

    from src.audit import AuditConfig, PlanRow, ShiftInfo, audit_schedule

    report = audit_schedule(snapshot, rows, shifts, AuditConfig(...))
    print(report.format_text())
"""
from src.audit.engine import (
    AuditCheck,
    AuditConfig,
    AuditReport,
    PlanRow,
    ShiftInfo,
    audit_schedule,
    audit_schedule_part_level,
)

__all__ = [
    "AuditCheck",
    "AuditConfig",
    "AuditReport",
    "PlanRow",
    "ShiftInfo",
    "audit_schedule",
    "audit_schedule_part_level",
]
