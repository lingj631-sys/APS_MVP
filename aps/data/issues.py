from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = ["Issue", "IssueLog", "Severity"]


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    GAP = "gap"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    entity: str | None
    field: str | None
    sheet: str | None
    row: int | None
    column: str | None
    message: str
    value: Any = None

    def location(self) -> str:
        if self.sheet is None:
            return "(无工作表)"
        parts = [self.sheet]
        if self.row is not None:
            parts.append(f"R{self.row}")
        if self.column is not None:
            parts.append(f"C[{self.column}]")
        return "!".join(parts[:1]) + ("!" + " ".join(parts[1:]) if len(parts) > 1 else "")


class IssueLog:
    def __init__(self, issues: Iterable[Issue] = ()) -> None:
        self._issues: list[Issue] = list(issues)

    def add(self, issue: Issue) -> None:
        self._issues.append(issue)

    def emit(self, severity: Severity, entity: str | None = None, field: str | None = None,
             sheet: str | None = None, row: int | None = None,
             column: str | None = None, message: str = "", value: Any = None) -> None:
        self._issues.append(Issue(severity, entity, field, sheet, row, column, message, value))

    def error(self, entity: str | None = None, field: str | None = None,
              sheet: str | None = None, row: int | None = None,
              column: str | None = None, message: str = "", value: Any = None) -> None:
        self.emit(Severity.ERROR, entity, field, sheet, row, column, message, value)

    def warning(self, entity: str | None = None, field: str | None = None,
                sheet: str | None = None, row: int | None = None,
                column: str | None = None, message: str = "", value: Any = None) -> None:
        self.emit(Severity.WARNING, entity, field, sheet, row, column, message, value)

    def gap(self, entity: str | None = None, field: str | None = None,
            sheet: str | None = None, row: int | None = None,
            column: str | None = None, message: str = "", value: Any = None) -> None:
        self.emit(Severity.GAP, entity, field, sheet, row, column, message, value)

    def extend(self, other: Iterable[Issue]) -> None:
        self._issues.extend(other)

    @property
    def issues(self) -> tuple[Issue, ...]:
        return tuple(self._issues)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self._issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self._issues if i.severity is Severity.WARNING)

    @property
    def gaps(self) -> tuple[Issue, ...]:
        return tuple(i for i in self._issues if i.severity is Severity.GAP)

    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self._issues)

    def __len__(self) -> int:
        return len(self._issues)
