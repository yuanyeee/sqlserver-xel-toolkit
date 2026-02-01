from __future__ import annotations

import sqlite3


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in cols)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return r is not None


def migrate(conn: sqlite3.Connection) -> None:
    # Add files table if missing
    if not table_exists(conn, "files"):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, name TEXT NOT NULL, out_dir TEXT NOT NULL)"
        )

    # Add file_id column to reports if missing
    if table_exists(conn, "reports") and not column_exists(conn, "reports", "file_id"):
        conn.execute("ALTER TABLE reports ADD COLUMN file_id INTEGER")

    conn.commit()
