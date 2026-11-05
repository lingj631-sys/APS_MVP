"""Tests for the rule-set trace (effective parameters the solver will use)."""
import json
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.kernel import trace
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope


def make_rule(**overrides):
    data = dict(
        rule_id="WL_CAP_FG",
        template_id="capacity_utilization",
        scope=Scope(stage="FG"),
        nature=Nature.SOFT,
        weight=2.0,
        group_id="G2_CAPACITY",
        priority=0,
        params={"min_util": 0.9},
    )
    data.update(overrides)
    return RuleInstance(**data)


class RuleTraceTest(unittest.TestCase):
    def test_01_effective_params_apply_schema_defaults(self):
        described = trace.describe_rule(make_rule())
        self.assertTrue(described["implemented"])
        self.assertEqual(described["params"]["min_util"], 0.9)
        self.assertEqual(described["params"]["max_util"], 1.0)
        self.assertTrue(described["params"]["tail_exempt"])

    def test_02_format_exposes_template_and_params(self):
        line = trace.format_rule_line(make_rule())
        self.assertIn("WL_CAP_FG", line)
        self.assertIn("template=capacity_utilization", line)
        self.assertIn("kernel", line)
        self.assertIn('"max_util": 1.0', line)

    def test_03_unimplemented_template_is_marked(self):
        described = trace.describe_rule(make_rule(template_id="routing", params={}))
        self.assertFalse(described["implemented"])
        self.assertIn("NO-COMPILER", trace.format_rule_line(make_rule(template_id="routing", params={})))

    def test_04_invalid_params_are_reported_not_raised(self):
        described = trace.describe_rule(make_rule(params={"min_util": 9.0}))
        self.assertIn("__error__", described["params"])
        self.assertIn("__error__", trace.format_rule_line(make_rule(params={"min_util": 9.0})))

    def test_05_rule_set_summary_keeps_ordered_groups(self):
        rule_set = RuleSet(
            rule_set_id="WL_PHASE1",
            site="WL",
            version=2,
            groups=[
                RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
            ],
            rules=[make_rule()],
        )
        summary = trace.describe_rule_set(rule_set)
        self.assertEqual(summary["rule_set_id"], "WL_PHASE1")
        self.assertEqual(summary["version"], 2)
        self.assertEqual([g["id"] for g in summary["groups"]], ["G0_PHYSICAL", "G2_CAPACITY"])
        self.assertEqual(json.loads(json.dumps(summary["rules"][0]["scope"])), {
            "site": "*", "line": "*", "stage": "FG", "part": "*"
        })


if __name__ == "__main__":
    unittest.main()
