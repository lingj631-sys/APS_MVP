from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = ["EntityName", "Repository", "Row"]

EntityName = str
Row = dict[str, Any]


@runtime_checkable
class Repository(Protocol):
    def list_by(self, entity: EntityName, /, **filters: Any) -> list[Row]:
        ...

    def get_by_pk(self, entity: EntityName, pk: tuple[Any, ...]) -> Row | None:
        ...

    def insert_many(self, entity: EntityName, rows: Sequence[Row]) -> int:
        ...

    def delete_where(self, entity: EntityName, /, **filters: Any) -> int:
        ...

    def count(self, entity: EntityName, /, **filters: Any) -> int:
        ...
