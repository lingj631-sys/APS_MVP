"""Rebuild the Instructions sheet of the canonical shared template."""
import sys

import openpyxl

sys.path.insert(0, ".")

from src.instructions import build_counts, write_instructions
from src.master_schema import SHEET_BY_KEY, resolve_sheet

PATH = "APS_DataSource_Template.xlsx"
wb = openpyxl.load_workbook(PATH)
ws = resolve_sheet(wb, SHEET_BY_KEY["instructions"])
write_instructions(ws, build_counts(wb))
wb.save(PATH)
print("instructions rebuilt:", ws.max_row, "rows,", ws.max_column, "cols")
