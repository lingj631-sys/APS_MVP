"""Versioned rule-set storage (SQLite).

Excel/UI are editing surfaces; the versioned store is the runtime source of truth.
A save with an unchanged payload is idempotent; a changed payload creates version+1.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "rule_sets.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    rule_set_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(site, rule_set_id, version)
);
CREATE INDEX IF NOT EXISTS idx_rule_sets_site_status ON rule_sets(site, status);
CREATE INDEX IF NOT EXISTS idx_rule_sets_site_version ON rule_sets(site, rule_set_id, version);
"""


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    with _connect(db_path) as conn:
        conn.commit()


def payload_hash(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_meta(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "site": row["site"],
        "rule_set_id": row["rule_set_id"],
        "version": row["version"],
        "status": row["status"],
        "payload_hash": row["payload_hash"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def _row_to_full(row: sqlite3.Row) -> Dict[str, Any]:
    data = _row_to_meta(row)
    data["payload"] = json.loads(row["payload"])
    return data


def list_versions(site: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM rule_sets WHERE site=? ORDER BY id DESC", (site,)
        ).fetchall()
        return [_row_to_meta(row) for row in rows]


def get_version(version_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM rule_sets WHERE id=?", (version_id,)).fetchone()
        return _row_to_full(row) if row else None


def get_active(site: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM rule_sets WHERE site=? AND status='active' ORDER BY id DESC LIMIT 1",
            (site,),
        ).fetchone()
        return _row_to_full(row) if row else None


def get_latest(
    site: str, rule_set_id: str, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM rule_sets WHERE site=? AND rule_set_id=? ORDER BY version DESC LIMIT 1",
            (site, rule_set_id),
        ).fetchone()
        return _row_to_full(row) if row else None


def _activate_tx(conn: sqlite3.Connection, version_id: int, site: str) -> None:
    conn.execute(
        "UPDATE rule_sets SET status='archived' WHERE site=? AND status='active'", (site,)
    )
    conn.execute("UPDATE rule_sets SET status='active' WHERE id=?", (version_id,))


def save_version(
    site: str,
    rule_set_id: str,
    payload: Dict[str, Any],
    note: str = "",
    activate: bool = False,
    db_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Return (version_record, created). Unchanged payload returns the latest row."""
    init_db(db_path)
    digest = payload_hash(payload)
    with _connect(db_path) as conn:
        latest = conn.execute(
            "SELECT * FROM rule_sets WHERE site=? AND rule_set_id=? ORDER BY version DESC LIMIT 1",
            (site, rule_set_id),
        ).fetchone()
        if latest is not None and latest["payload_hash"] == digest:
            if activate and latest["status"] != "active":
                _activate_tx(conn, latest["id"], site)
            return _row_to_full(latest), False

        version = (latest["version"] + 1) if latest is not None else 1
        cursor = conn.execute(
            "INSERT INTO rule_sets(site, rule_set_id, version, status, payload, payload_hash, note, created_at)"
            " VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)",
            (
                site,
                rule_set_id,
                version,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                digest,
                note,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        new_id = int(cursor.lastrowid)
        if activate:
            _activate_tx(conn, new_id, site)
        conn.commit()
        row = conn.execute("SELECT * FROM rule_sets WHERE id=?", (new_id,)).fetchone()
        return _row_to_full(row), True


def activate(version_id: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM rule_sets WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(f"rule set version {version_id} not found")
        _activate_tx(conn, version_id, row["site"])
        conn.commit()
        return _row_to_full(conn.execute("SELECT * FROM rule_sets WHERE id=?", (version_id,)).fetchone())
