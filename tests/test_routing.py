"""C2-a: routing table loader / candidate lookup."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.routing import (
    RoutingOption,
    assign_routing_lines,
    available_hours_by_line,
    capability_violations,
    candidates_for,
    load_routing,
    primary_line,
    solve_routing_assignment,
)


class TestRoutingCandidates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routing = load_routing(os.path.join(BASE_DIR, "data", "master", "Routing-Machines.csv"))

    def test_all_parts_have_a_route(self):
        self.assertGreater(len(self.routing), 0)
        options = candidates_for(self.routing, "WL", "WBC9317C")
        self.assertTrue(options)
        self.assertTrue(any(item.certified for item in options))

    def test_sy38_has_an_alternate_line(self):
        options = candidates_for(self.routing, "SY38", "L755390S", 20)
        lines = {item.candidate_line for item in options}
        self.assertIn("PU07", lines)
        self.assertIn("PU22", lines)

    def test_primary_line(self):
        self.assertEqual(primary_line(self.routing, "WL", "WBC9317C", 20), "PGLINKAGE")

    def test_assignment_moves_overloaded_primary_to_certified_alternate(self):
        class Task:
            task_id = "T1"
            part_no = "P1"
            stage = "FG"
            line = "PU07"
            quantity = 100.0
            ct_seconds = 72.0
            trp = 1.0

        routing = {
            ("SY38", "P1", 20): [
                RoutingOption("SY38", "P1", "FG", 20, "总装", "PU07", "PU07", "PU07-M1", True, 72, 1.0, 30, 1),
                RoutingOption("SY38", "P1", "FG", 20, "总装", "PU07", "PU22", "PU22-M1", True, 80, 1.0, 30, 2),
            ]
        }
        task = Task()
        decisions = assign_routing_lines(
            [task], routing, site="SY38", capacity_hours_by_line={"PU07": 0.5, "PU22": 10.0}
        )
        self.assertEqual(task.line, "PU22")
        self.assertEqual(decisions[0]["to"], "PU22")


    def test_cpsat_assignment_moves_overloaded_primary(self):
        class Task:
            task_id = "T1"
            part_no = "P1"
            stage = "FG"
            line = "PU07"
            quantity = 100.0
            ct_seconds = 72.0
            trp = 1.0

        routing = {
            ("SY38", "P1", 20): [
                RoutingOption("SY38", "P1", "FG", 20, "总装", "PU07", "PU07", "PU07-M1", True, 72, 1.0, 30, 1),
                RoutingOption("SY38", "P1", "FG", 20, "总装", "PU07", "PU22", "PU22-M1", True, 80, 1.0, 30, 2),
            ]
        }
        task = Task()
        decisions = solve_routing_assignment(
            [task], routing, site="SY38", capacity_hours_by_line={"PU07": 1.0, "PU22": 10.0}
        )
        self.assertEqual(task.line, "PU22")
        self.assertEqual(decisions[0]["to"], "PU22")


    def test_capability_violation_detects_uncertified_line(self):
        class Task:
            task_id = "T1"
            part_no = "P1"
            stage = "FG"
            line = "BAD"

        routing = {
            ("SY38", "P1", 20): [
                RoutingOption("SY38", "P1", "FG", 20, "总装", "PU07", "PU07", "PU07-M1", True, 72, 1.0, 30, 1),
            ]
        }
        bad = capability_violations([Task()], routing, site="SY38")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["line"], "BAD")

    def test_available_hours_uses_work_shifts_and_subtracts_pm(self):
        class Shift:
            def __init__(self, work):
                self.is_work_shift = work

        class PM:
            line = "PU07"
            downtime_hours = 8.0

        shifts = [Shift(True)] * 5 + [Shift(False)] * 2
        hours = available_hours_by_line(
            shifts, shift_hours=12.0, lines=["PU07", "PU22"], pm_schedules=[PM()]
        )
        self.assertEqual(hours["PU22"], 60.0)
        self.assertEqual(hours["PU07"], 52.0)



if __name__ == "__main__":
    unittest.main()
