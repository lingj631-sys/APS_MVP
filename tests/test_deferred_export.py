"""BS-06 deferred orders are exported to a separate Excel sheet."""
import os
import tempfile
import unittest

import openpyxl

from src.exporter import append_deferred_orders


class TestDeferredExport(unittest.TestCase):
    def test_deferred_sheet_is_appended(self):
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            wb = openpyxl.Workbook()
            wb.active.title = "计划"
            wb.active["A1"] = "main"
            wb.save(path)

            append_deferred_orders(
                path,
                [
                    {
                        "order_id": "ORD-1",
                        "batch_id": "CB-1",
                        "qty": 120,
                        "target_order_id": "ORD-2",
                        "reason": "保留量满足 MOQ",
                    }
                ],
            )

            out = openpyxl.load_workbook(path)
            self.assertIn("本期顺延订单", out.sheetnames)
            ws = out["本期顺延订单"]
            self.assertEqual(ws["A2"].value, "订单号")
            self.assertEqual(ws["A3"].value, "ORD-1")
            self.assertEqual(ws["C3"].value, 120)
            self.assertEqual(ws["E3"].value, "保留量满足 MOQ")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
