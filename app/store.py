"""SQLite-backed request log. Standard library only, one file, no ORM.

The queue shown in the UI is a read of this table, so state survives a reload.
"""
import json
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = pathlib.Path(os.getenv("DB_PATH", "data/frontdesk.db"))
MOCKS = pathlib.Path(__file__).resolve().parent.parent / "data" / "mocks.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    seeded     INTEGER NOT NULL DEFAULT 0,
    triage     TEXT,
    source     TEXT,
    notes      TEXT,
    model      TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Cheap and idempotent, so a missing or recreated database file cannot turn every
    # read into a 500.
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    """Create the table and load the seed requests once."""
    with _conn() as conn:
        conn.executescript(SCHEMA)
        seeded = conn.execute("SELECT COUNT(*) FROM requests WHERE seeded = 1").fetchone()[0]
        if seeded:
            return
        for mock in json.loads(MOCKS.read_text()):
            conn.execute(
                "INSERT OR IGNORE INTO requests (id, text, seeded, created_at) VALUES (?, ?, 1, ?)",
                (mock["id"], mock["text"], now()),
            )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "seeded": bool(row["seeded"]),
        "triage": json.loads(row["triage"]) if row["triage"] else None,
        "source": row["source"],
        "notes": json.loads(row["notes"]) if row["notes"] else [],
        "model": row["model"],
        "latency_ms": row["latency_ms"] or 0,
        "created_at": row["created_at"],
    }


def list_requests() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM requests ORDER BY seeded DESC, created_at ASC, id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get(request_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    return _row_to_dict(row) if row else None


def add(text: str) -> str:
    request_id = uuid.uuid4().hex[:8]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO requests (id, text, seeded, created_at) VALUES (?, ?, 0, ?)",
            (request_id, text, now()),
        )
    return request_id


def save_triage(request_id: str, result: dict) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE requests SET triage = ?, source = ?, notes = ?, model = ?, latency_ms = ? WHERE id = ?",
            (
                json.dumps(result["triage"]),
                result["source"],
                json.dumps(result["notes"]),
                result["model"],
                result["latency_ms"],
                request_id,
            ),
        )


def update_draft(request_id: str, draft: str) -> dict | None:
    record = get(request_id)
    if not record or not record["triage"]:
        return None
    record["triage"]["draft_reply"] = draft
    with _conn() as conn:
        conn.execute("UPDATE requests SET triage = ? WHERE id = ?", (json.dumps(record["triage"]), request_id))
    return record


def reset() -> None:
    """Drop every request and reload the seed set."""
    with _conn() as conn:
        conn.execute("DROP TABLE IF EXISTS requests")
    init()
