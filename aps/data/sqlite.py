from __future__ import annotations

import contextlib
import datetime as dt
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from aps.data.repository import EntityName, Row
from aps.schema.ddl import create_all_ddl
from aps.schema.registry import REGISTRY, EntitySpec, get_entity
from aps.schema.types import DataType

__all__ = ["SqliteRepository", "decode_value", "encode_value"]

_DATE_FORMAT = "%Y-%m-%d"
_TIME_FORMAT = "%H:%M:%S"
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def encode_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dt.datetime):
        return value.strftime(_DATETIME_FORMAT)
    if isinstance(value, dt.date):
        return value.strftime(_DATE_FORMAT)
    if isinstance(value, dt.time):
        return value.strftime(_TIME_FORMAT)
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    return value


def decode_value(raw: Any, dtype: DataType) -> Any:
    if raw is None:
        return None
    if dtype is DataType.BOOL:
        return bool(raw)
    if dtype is DataType.DATE:
        return dt.datetime.strptime(str(raw), _DATE_FORMAT).date()
    if dtype is DataType.TIME:
        return dt.datetime.strptime(str(raw), _TIME_FORMAT).time()
    if dtype is DataType.DATETIME:
        return dt.datetime.strptime(str(raw), _DATETIME_FORMAT)
    if dtype is DataType.MULTI_TEXT:
        return tuple(p for p in (s.strip() for s in str(raw).split(",")) if p)
    if dtype is DataType.INT:
        return int(raw)
    if dtype is DataType.REAL:
        return float(raw)
    return str(raw)


def _deletion_order() -> list[EntitySpec]:
    """Children first so FK constraints hold during site replacement."""
    return list(reversed(_topological_order()))


def _topological_order() -> list[EntitySpec]:
    pending = dict(REGISTRY)
    ordered: list[EntitySpec] = []
    placed: set[str] = set()
    while pending:
        progress = False
        for name in sorted(pending):
            spec = pending[name]
            deps = {
                f.fk.partition(".")[0] for f in spec.fields
                if f.fk and f.fk.partition(".")[0] != name
            }
            if deps <= placed:
                ordered.append(spec)
                placed.add(name)
                del pending[name]
                progress = True
        if not progress:
            raise RuntimeError(f"实体外键存在循环依赖: {sorted(pending)}")
    return ordered


class SqliteRepository:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = os.fspath(db_path)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def initialize_schema(self) -> None:
        with self.transaction():
            self._conn.executescript(create_all_ddl())
            self._conn.execute("PRAGMA foreign_keys = ON")

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _spec(self, entity: EntityName) -> EntitySpec:
        return get_entity(entity)

    def _columns(self, spec: EntitySpec) -> tuple[str, ...]:
        return tuple(f.name for f in spec.fields)

    def _decode_row(self, spec: EntitySpec, raw: sqlite3.Row) -> Row:
        keys = raw.keys()
        return {
            name: decode_value(raw[name], spec.field(name).dtype)
            for name in keys
        }

    def _prepare(self, spec: EntitySpec, row: Row) -> tuple[list[str], list[Any]]:
        names: list[str] = []
        values: list[Any] = []
        unknown = set(row) - set(self._columns(spec))
        if unknown:
            raise KeyError(f"{spec.name}: 未知字段 {sorted(unknown)}")
        for field in spec.fields:
            if field.name in row:
                value = row[field.name]
            elif field.nullable or field.provenance == "derived":
                value = None
            else:
                raise KeyError(f"{spec.name}.{field.name}: 缺少非空字段")
            if value is None and not field.nullable:
                raise KeyError(f"{spec.name}.{field.name}: 非空字段取值为 None")
            names.append(field.name)
            values.append(encode_value(value))
        return names, values

    def insert_many(self, entity: EntityName, rows: Sequence[Row]) -> int:
        spec = self._spec(entity)
        if not rows:
            return 0
        prepared = [self._prepare(spec, row) for row in rows]
        names = prepared[0][0]
        sql = (
            f"INSERT INTO {entity} ({', '.join(names)}) "
            f"VALUES ({', '.join('?' * len(names))})"
        )
        self._conn.executemany(sql, [values for _, values in prepared])
        return len(prepared)

    def _where(self, spec: EntitySpec, filters: Mapping[str, Any]) -> tuple[str, list[Any]]:
        unknown = set(filters) - set(self._columns(spec))
        if unknown:
            raise KeyError(f"{spec.name}: 未知过滤字段 {sorted(unknown)}")
        if not filters:
            return "", []
        clauses = [f"{name} = ?" for name in sorted(filters)]
        return " WHERE " + " AND ".join(clauses), [
            encode_value(filters[name]) for name in sorted(filters)
        ]

    def list_by(self, entity: EntityName, /, **filters: Any) -> list[Row]:
        spec = self._spec(entity)
        where, params = self._where(spec, filters)
        order = ", ".join(spec.pk)
        cursor = self._conn.execute(f"SELECT * FROM {entity}{where} ORDER BY {order}", params)
        return [self._decode_row(spec, raw) for raw in cursor.fetchall()]

    def get_by_pk(self, entity: EntityName, pk: tuple[Any, ...]) -> Row | None:
        spec = self._spec(entity)
        if len(pk) != len(spec.pk):
            raise ValueError(f"{entity} 主键需要 {len(spec.pk)} 个值，收到 {len(pk)} 个")
        filters = dict(zip(spec.pk, pk))
        rows = self.list_by(entity, **filters)
        return rows[0] if rows else None

    def delete_where(self, entity: EntityName, /, **filters: Any) -> int:
        spec = self._spec(entity)
        where, params = self._where(spec, filters)
        cursor = self._conn.execute(f"DELETE FROM {entity}{where}", params)
        return cursor.rowcount

    def count(self, entity: EntityName, /, **filters: Any) -> int:
        spec = self._spec(entity)
        where, params = self._where(spec, filters)
        cursor = self._conn.execute(f"SELECT COUNT(*) FROM {entity}{where}", params)
        return int(cursor.fetchone()[0])

    def replace_site(
        self, site_code: str, rows_by_entity: Mapping[str, Sequence[Row]]
    ) -> dict[str, int]:
        unknown = set(rows_by_entity) - set(REGISTRY)
        if unknown:
            raise KeyError(f"登记表无实体 {sorted(unknown)}")
        with self.transaction():
            for spec in _deletion_order():
                if spec.name == "site" or "site_code" not in self._columns(spec):
                    continue
                self.delete_where(spec.name, site_code=site_code)
            if self.get_by_pk("site", (site_code,)) is None:
                self.insert_many("site", [{"site_code": site_code}])
            counts: dict[str, int] = {}
            for spec in _topological_order():
                if spec.name == "site":
                    continue
                rows = rows_by_entity.get(spec.name)
                if rows:
                    counts[spec.name] = self.insert_many(spec.name, rows)
            return counts
