"""C5-a campaign-family loader smoke tests."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.campaign import load_campaign_families


class TestCampaignFamilies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.families = load_campaign_families(
            os.path.join(BASE_DIR, "data", "master", "Campaign-Families.csv")
        )

    def test_parts_are_grouped(self):
        self.assertGreater(len(self.families), 0)
        self.assertEqual(self.families["WA14208"].campaign_group, "DX11")
        self.assertEqual(self.families["L755390S"].campaign_group, "B9")

    def test_site_filter(self):
        wl = load_campaign_families(
            os.path.join(BASE_DIR, "data", "master", "Campaign-Families.csv"), site="WL"
        )
        sy = load_campaign_families(
            os.path.join(BASE_DIR, "data", "master", "Campaign-Families.csv"), site="SY38"
        )
        self.assertTrue(all(v.site == "WL" for v in wl.values()))
        self.assertTrue(all(v.site == "SY38" for v in sy.values()))


if __name__ == "__main__":
    unittest.main()
