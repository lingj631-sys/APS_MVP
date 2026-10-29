"""32-rule catalog and per-site override smoke tests."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.rule_catalog import SiteRuleOverride, effective_for_site, load_catalog


class TestRuleCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(os.path.join(BASE_DIR, "config", "rule_catalog", "aps_32_rules.json"))

    def test_catalog_has_32_unique_rules(self):
        self.assertEqual(len(self.catalog), 32)
        self.assertEqual(len({r.id for r in self.catalog}), 32)

    def test_classes_are_hc_sc_bs(self):
        self.assertEqual({r.rule_class for r in self.catalog}, {"HC", "SC", "BS"})

    def test_site_override_does_not_mutate_global(self):
        overrides = {
            "HC-15": SiteRuleOverride(enabled=False),
            "SC-02": SiteRuleOverride(weight="0.7", params={"min_util": 0.9}),
        }
        effective = effective_for_site(self.catalog, overrides)
        self.assertEqual(len(effective), 31)
        sc02 = next(item for item in effective if item["id"] == "SC-02")
        self.assertEqual(sc02["weight"], "0.7")
        self.assertEqual(sc02["params"]["min_util"], 0.9)
        self.assertTrue(next(item for item in self.catalog if item.id == "HC-15"))


if __name__ == "__main__":
    unittest.main()
