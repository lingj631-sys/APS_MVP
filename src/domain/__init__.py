"""Core domain layer for APS MVP.

Provides first-class entities and value objects for manufacturing topology,
shift slots, process routing, line capabilities, parts, and BOM structures.
"""
from src.domain.product import BOMRelation, Part, PartType
from src.domain.routing import LineCapability, ProcessOperation, Routing
from src.domain.shift import ShiftSlot
from src.domain.topology import Line, Site, Workshop

__all__ = [
    "BOMRelation",
    "Line",
    "LineCapability",
    "Part",
    "PartType",
    "ProcessOperation",
    "Routing",
    "ShiftSlot",
    "Site",
    "Workshop",
]
