"""Unit tests for the domain layer (Site, Workshop, Line, ShiftSlot, Routing, Part)."""
import os
import sys
import unittest
from datetime import datetime

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(TESTS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.domain import (
    BOMRelation,
    Line,
    LineCapability,
    Part,
    PartType,
    ProcessOperation,
    Routing,
    ShiftSlot,
    Site,
    Workshop,
)


class TestDomainLayer(unittest.TestCase):
    def test_01_shift_slot_value_object(self):
        slot = ShiftSlot(
            slot_index=0,
            date_str="10/12",
            shift_name="早班",
            start_dt=datetime(2026, 10, 12, 7, 0),
            end_dt=datetime(2026, 10, 12, 15, 0),
            duration_hours=8.0,
            is_work_shift=True,
            weekday=0,
        )
        self.assertEqual(slot.key, "10/12 早班")
        self.assertEqual(slot.tuple_key, ("10/12", "早班"))
        self.assertEqual(slot.duration_seconds, 28800.0)
        self.assertEqual(str(slot), "10/12 早班")

    def test_02_site_workshop_line_topology(self):
        l1 = Line(line_code="PGLINKAGE", name="总装线", workshop_code="WE34_ASSY", site_code="WL", stage="FG", default_crew=6.0)
        l2 = Line(line_code="轴套6", name="冲压线", workshop_code="WE34_STAMP", site_code="WL", stage="SF", default_crew=2.0)
        ws1 = Workshop(workshop_code="WE34_ASSY", name="总装车间", site_code="WL", lines=[l1])
        ws2 = Workshop(workshop_code="WE34_STAMP", name="冲压车间", site_code="WL", lines=[l2])
        site = Site(site_code="WL", plant_name="温岭工厂", plant_alias="WE34", shifts_per_day=3, shift_hours=8.0, workshops=[ws1, ws2])

        self.assertEqual(len(site.total_lines), 2)
        self.assertEqual([line.line_code for line in site.total_lines], ["PGLINKAGE", "轴套6"])

    def test_03_routing_and_alternative_lines(self):
        cap_main = LineCapability(line_code="PGLINKAGE", priority=1, ct_seconds=30.0, trp=0.85)
        cap_alt = LineCapability(line_code="PGLINKAGE_2", priority=2, ct_seconds=35.0, trp=0.80)
        op = ProcessOperation(operation_id="OP20", stage="FG", sequence=20, capabilities=[cap_main, cap_alt])
        route = Routing(routing_id="ROUT_WBC9317C", part_no="WBC9317C", site_code="WL", operations=[op])

        self.assertEqual(op.primary_line, "PGLINKAGE")
        self.assertEqual(op.candidate_lines, ["PGLINKAGE", "PGLINKAGE_2"])
        self.assertEqual(len(route.operations), 1)

    def test_04_part_and_bom_relation(self):
        fg = Part(part_no="WBC9317C", description="雨刮总成", part_type=PartType.FINISHED_GOOD, site_code="WL", mpq=48.0)
        sf = Part(part_no="WBC9318", description="轴套", part_type=PartType.SEMI_FINISHED, site_code="WL", mpq=100.0)
        bom = BOMRelation(parent_part_no="WBC9317C", child_part_no="WBC9318", quantity_per_parent=1.0)

        self.assertEqual(fg.part_type, PartType.FINISHED_GOOD)
        self.assertEqual(sf.part_type, PartType.SEMI_FINISHED)
        self.assertEqual(bom.quantity_per_parent, 1.0)


if __name__ == "__main__":
    unittest.main()
