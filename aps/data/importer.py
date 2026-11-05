from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from aps.data.excel import read_workbook
from aps.data.repository import Row
from aps.data.sqlite import SqliteRepository
from aps.data.validate import ParsedTables, ValidationReport, validate

__all__ = ["ImportResult", "import_workbook", "rows_from_parsed"]


@dataclass(frozen=True)
class ImportResult:
    site: str
    db_path: str
    workbook_path: str
    report: ValidationReport
    counts: Mapping[str, int]
    wrote: bool

    @property
    def ok(self) -> bool:
        return self.report.ok

    @property
    def degraded_fields(self) -> frozenset[str]:
        return frozenset(
            f"{issue.entity}.{issue.field}"
            for issue in self.report.gaps
            if issue.entity and issue.field
        )

    def summary(self, limit: int = 40) -> str:
        counts = self.report.counts()
        head = f"站点 {self.site} · {os.path.basename(self.workbook_path)} → {self.db_path}"
        if not self.wrote:
            return (
                f"{head}\n导入中止：{counts['error']} 个 error，数据库未改动\n"
                + self.report.render(limit)
            )
        written = ", ".join(f"{name}={n}" for name, n in sorted(self.counts.items()) if n)
        return (
            f"{head}\n导入完成：{written or '（无数据行）'}\n"
            f"error={counts['error']}  warning={counts['warning']}  gap={counts['gap']}\n"
            + self.report.render(limit)
        )


def rows_from_parsed(parsed: ParsedTables) -> dict[str, list[Row]]:
    return {
        name: [candidate.as_row() for candidate in parsed[name]]
        for name in sorted(parsed)
    }


def import_workbook(workbook_path: str | os.PathLike[str],
                    db_path: str | os.PathLike[str], site: str) -> ImportResult:
    source = os.fspath(workbook_path)
    target = os.fspath(db_path)
    tables, structure_log = read_workbook(source, site)
    parsed, report = validate(tables, structure_log)
    if not report.ok:
        return ImportResult(site, target, source, report, {}, False)
    repo = SqliteRepository(target)
    try:
        repo.initialize_schema()
        counts = repo.replace_site(site, rows_from_parsed(parsed))
    finally:
        repo.close()
    return ImportResult(site, target, source, report, counts, True)
