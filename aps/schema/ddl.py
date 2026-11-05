from __future__ import annotations

from aps.schema.registry import REGISTRY, EntitySpec
from aps.schema.types import DataType

__all__ = ["SQL_TYPE", "create_all_ddl", "create_ddl", "create_indexes_ddl"]

SQL_TYPE: dict[DataType, str] = {
    DataType.TEXT: "TEXT",
    DataType.INT: "INTEGER",
    DataType.REAL: "REAL",
    DataType.BOOL: "INTEGER",
    DataType.DATE: "TEXT",
    DataType.TIME: "TEXT",
    DataType.DATETIME: "TEXT",
    DataType.MULTI_TEXT: "TEXT",
}


def _fk_clause(fk: str) -> str:
    target_entity, _, target_field = fk.partition(".")
    target = REGISTRY[target_entity]
    if not target_field:
        target_field = target.pk[0]
    return f" REFERENCES {target_entity} ({target_field})"


def create_ddl(spec: EntitySpec) -> str:
    lines: list[str] = []
    for field in spec.fields:
        column = f"    {field.name} {SQL_TYPE[field.dtype]}"
        if not field.nullable:
            column += " NOT NULL"
        if field.fk:
            column += _fk_clause(field.fk)
        lines.append(column)
    lines.append(f"    PRIMARY KEY ({', '.join(spec.pk)})")
    return f"CREATE TABLE IF NOT EXISTS {spec.name} (\n" + ",\n".join(lines) + "\n);"


def _referenced_targets() -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    for spec in REGISTRY.values():
        for field in spec.fields:
            if field.fk:
                t_ent, _, t_fld = field.fk.partition(".")
                if not t_fld:
                    t_fld = REGISTRY[t_ent].pk[0]
                if REGISTRY[t_ent].pk != (t_fld,):
                    targets.add((t_ent, t_fld))
    return targets


def create_indexes_ddl(spec: EntitySpec) -> str:
    pk = set(spec.pk)
    seen: set[str] = set()
    out: list[str] = []

    # Target unique indexes required by SQLite for foreign keys referencing non-sole-PK columns
    targets = _referenced_targets()
    for field in spec.fields:
        if (spec.name, field.name) in targets and field.name not in seen:
            seen.add(field.name)
            out.append(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{spec.name}_{field.name} "
                f"ON {spec.name} ({field.name});"
            )

    # FK indexes on child tables
    for field in spec.fields:
        if not field.fk or field.name in pk or field.name in seen:
            continue
        seen.add(field.name)
        out.append(
            f"CREATE INDEX IF NOT EXISTS ix_{spec.name}_{field.name} "
            f"ON {spec.name} ({field.name});"
        )
    return "\n".join(out)


def _creation_order() -> list[EntitySpec]:
    pending = dict(REGISTRY)
    ordered: list[EntitySpec] = []
    placed: set[str] = set()
    while pending:
        progress = False
        for name in sorted(pending):
            spec = pending[name]
            deps = {
                field.fk.partition(".")[0]
                for field in spec.fields
                if field.fk and field.fk.partition(".")[0] != name
            }
            if deps <= placed:
                ordered.append(spec)
                placed.add(name)
                del pending[name]
                progress = True
        if not progress:
            raise RuntimeError(f"实体外键存在循环依赖: {sorted(pending)}")
    return ordered


def create_all_ddl() -> str:
    parts: list[str] = ["PRAGMA foreign_keys = ON;"]
    for spec in _creation_order():
        parts.append(create_ddl(spec))
    for spec in _creation_order():
        indexes = create_indexes_ddl(spec)
        if indexes:
            parts.append(indexes)
    return "\n\n".join(parts) + "\n"
