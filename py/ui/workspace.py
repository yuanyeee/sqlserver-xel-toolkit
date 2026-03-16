from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  out_dir TEXT NOT NULL,
  slow_threshold_sec REAL NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS inputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  size_bytes INTEGER,
  mtime REAL,
  fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  out_dir TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
  type TEXT NOT NULL,
  title TEXT,
  md_path TEXT,
  xlsx_path TEXT,
  created_at TEXT NOT NULL,
  event_time_min TEXT,
  event_time_max TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(
  title,
  type,
  content,
  report_id UNINDEXED
);
"""


@dataclass
class RunRow:
    id: int
    started_at: str
    out_dir: str
    slow_threshold_sec: float
    note: Optional[str]


@dataclass
class FileRow:
    id: int
    run_id: int
    name: str
    out_dir: str


@dataclass
class ReportRow:
    id: int
    run_id: int
    file_id: Optional[int]
    type: str
    title: str
    md_path: Optional[str]
    xlsx_path: Optional[str]
    created_at: str
    event_time_min: Optional[str]
    event_time_max: Optional[str]


def connect_db(db_path: str) -> sqlite3.Connection:
    """Open workspace sqlite DB.

    Defensive: ensure schema exists even if caller forgot to run init_db() or
    the DB file was created without schema (common first-run failure).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Ensure schema (cheap check first)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        if row is None:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            # best-effort migration for existing workspaces
            try:
                from .migrate import migrate

                migrate(conn)
                conn.commit()
            except Exception:
                pass
    except Exception:
        # As a last resort, try to initialize schema.
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        except Exception:
            pass

    return conn


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = connect_db(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        # best-effort migration for existing workspaces
        from .migrate import migrate

        migrate(conn)
    finally:
        conn.close()


def compute_fingerprint(path: str, *, chunk_size: int = 4 * 1024 * 1024) -> str:
    """Fast fingerprint: size + sha256(head) + sha256(tail)."""
    st = os.stat(path)
    size = st.st_size

    h1 = hashlib.sha256()
    h2 = hashlib.sha256()

    with open(path, "rb") as f:
        head = f.read(chunk_size)
        h1.update(head)
        if size > chunk_size:
            try:
                f.seek(max(0, size - chunk_size))
                tail = f.read(chunk_size)
            except OSError:
                tail = b""
            h2.update(tail)

    return f"size={size};head={h1.hexdigest()};tail={h2.hexdigest()}"


def add_run(conn: sqlite3.Connection, *, started_at: str, out_dir: str, slow_threshold_sec: float, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO runs(started_at,out_dir,slow_threshold_sec,note) VALUES(?,?,?,?)",
        (started_at, out_dir, slow_threshold_sec, note or None),
    )
    conn.commit()
    return int(cur.lastrowid)


def replace_run_by_out_dir(conn: sqlite3.Connection, *, started_at: str, out_dir: str, slow_threshold_sec: float, note: str = "") -> int:
    """Ensure only one run row exists per out_dir.

    If an existing run uses the same out_dir, delete it (including FTS rows),
    then insert a new run.
    """
    row = conn.execute("SELECT id FROM runs WHERE out_dir=? ORDER BY id DESC LIMIT 1", (out_dir,)).fetchone()
    if row:
        run_id = int(row["id"]) if isinstance(row, sqlite3.Row) else int(row[0])
        # Clean FTS rows explicitly (no FK)
        rep_ids = conn.execute("SELECT id FROM reports WHERE run_id=?", (run_id,)).fetchall()
        for r in rep_ids:
            rid = int(r["id"]) if isinstance(r, sqlite3.Row) else int(r[0])
            conn.execute("DELETE FROM reports_fts WHERE report_id=?", (rid,))
        # Delete run (cascades to inputs/files/reports)
        conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
        conn.commit()

    return add_run(conn, started_at=started_at, out_dir=out_dir, slow_threshold_sec=slow_threshold_sec, note=note)


def add_input(conn: sqlite3.Connection, *, run_id: int, path: str) -> None:
    try:
        st = os.stat(path)
        fp = compute_fingerprint(path)
        conn.execute(
            "INSERT INTO inputs(run_id,path,size_bytes,mtime,fingerprint) VALUES(?,?,?,?,?)",
            (run_id, path, st.st_size, st.st_mtime, fp),
        )
    except FileNotFoundError:
        conn.execute(
            "INSERT INTO inputs(run_id,path,size_bytes,mtime,fingerprint) VALUES(?,?,?,?,?)",
            (run_id, path, None, None, None),
        )


def add_file(conn: sqlite3.Connection, *, run_id: int, name: str, out_dir: str) -> int:
    cur = conn.execute(
        "INSERT INTO files(run_id,name,out_dir) VALUES(?,?,?)",
        (run_id, name, out_dir),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_files(conn: sqlite3.Connection, run_id: int) -> List[FileRow]:
    rows = conn.execute(
        "SELECT id, run_id, name, out_dir FROM files WHERE run_id=? ORDER BY id DESC",
        (run_id,),
    ).fetchall()
    return [FileRow(int(r["id"]), int(r["run_id"]), r["name"], r["out_dir"]) for r in rows]


def add_report(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    file_id: Optional[int],
    type_: str,
    title: str,
    md_path: Optional[str],
    xlsx_path: Optional[str],
    created_at: str,
    event_time_min: Optional[str] = None,
    event_time_max: Optional[str] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO reports(run_id,file_id,type,title,md_path,xlsx_path,created_at,event_time_min,event_time_max) VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id, file_id, type_, title, md_path, xlsx_path, created_at, event_time_min, event_time_max),
    )
    report_id = int(cur.lastrowid)

    content = ""
    if md_path and os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = ""

    conn.execute(
        "INSERT INTO reports_fts(title,type,content,report_id) VALUES(?,?,?,?)",
        (title, type_, content, report_id),
    )
    return report_id


def list_runs(conn: sqlite3.Connection, limit: int = 200) -> List[RunRow]:
    rows = conn.execute(
        "SELECT id, started_at, out_dir, slow_threshold_sec, note FROM runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [RunRow(int(r["id"]), r["started_at"], r["out_dir"], float(r["slow_threshold_sec"]), r["note"]) for r in rows]


def delete_run_db(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    conn.commit()


def delete_report_db(conn: sqlite3.Connection, report_id: int) -> None:
    conn.execute("DELETE FROM reports_fts WHERE report_id=?", (report_id,))
    conn.execute("DELETE FROM reports WHERE id=?", (report_id,))
    conn.commit()


def delete_reports_by_file_db(conn: sqlite3.Connection, file_id: int, *, types: Optional[List[str]] = None) -> None:
    """Delete reports (and FTS rows) for a given file.

    If types is provided, only delete those report types.
    """
    if types:
        q = "SELECT id FROM reports WHERE file_id=? AND type IN (%s)" % (",".join(["?"] * len(types)))
        rows = conn.execute(q, (file_id, *types)).fetchall()
        for r in rows:
            rid = int(r[0])
            conn.execute("DELETE FROM reports_fts WHERE report_id=?", (rid,))
        qd = "DELETE FROM reports WHERE file_id=? AND type IN (%s)" % (",".join(["?"] * len(types)))
        conn.execute(qd, (file_id, *types))
    else:
        rows = conn.execute("SELECT id FROM reports WHERE file_id=?", (file_id,)).fetchall()
        for r in rows:
            rid = int(r[0])
            conn.execute("DELETE FROM reports_fts WHERE report_id=?", (rid,))
        conn.execute("DELETE FROM reports WHERE file_id=?", (file_id,))
    conn.commit()


def delete_file_db(conn: sqlite3.Connection, file_id: int) -> None:
    # Remove reports and fts entries associated with this file
    delete_reports_by_file_db(conn, file_id)
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()


def list_reports(conn: sqlite3.Connection, run_id: Optional[int] = None, file_id: Optional[int] = None) -> List[ReportRow]:
    if file_id is not None:
        rows = conn.execute(
            "SELECT id, run_id, file_id, type, COALESCE(title,'') AS title, md_path, xlsx_path, created_at, event_time_min, event_time_max FROM reports WHERE file_id=? ORDER BY id DESC",
            (file_id,),
        ).fetchall()
    elif run_id is None:
        rows = conn.execute(
            "SELECT id, run_id, file_id, type, COALESCE(title,'') AS title, md_path, xlsx_path, created_at, event_time_min, event_time_max FROM reports ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, run_id, file_id, type, COALESCE(title,'') AS title, md_path, xlsx_path, created_at, event_time_min, event_time_max FROM reports WHERE run_id=? ORDER BY id DESC",
            (run_id,),
        ).fetchall()
    return [
        ReportRow(
            int(r["id"]),
            int(r["run_id"]),
            (int(r["file_id"]) if r["file_id"] is not None else None),
            r["type"],
            r["title"],
            r["md_path"],
            r["xlsx_path"],
            r["created_at"],
            r["event_time_min"],
            r["event_time_max"],
        )
        for r in rows
    ]


def search_reports(conn: sqlite3.Connection, query: str, limit: int = 200) -> List[int]:
    rows = conn.execute(
        "SELECT report_id FROM reports_fts WHERE reports_fts MATCH ? LIMIT ?",
        (query, limit),
    ).fetchall()
    return [int(r[0]) for r in rows]


def get_report(conn: sqlite3.Connection, report_id: int) -> Optional[ReportRow]:
    r = conn.execute(
        "SELECT id, run_id, file_id, type, COALESCE(title,'') AS title, md_path, xlsx_path, created_at, event_time_min, event_time_max FROM reports WHERE id=?",
        (report_id,),
    ).fetchone()
    if not r:
        return None
    return ReportRow(
        int(r["id"]),
        int(r["run_id"]),
        (int(r["file_id"]) if r["file_id"] is not None else None),
        r["type"],
        r["title"],
        r["md_path"],
        r["xlsx_path"],
        r["created_at"],
        r["event_time_min"],
        r["event_time_max"],
    )
