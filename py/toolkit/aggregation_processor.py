"""aggregation_processor.py

Native implementation of IntegratedTool's AggregationProcessor.

Provides the same analysis angles as IntegratedTool:
  - Deadlock: objects, hostnames, logins, isolation levels, processes
  - SlowQuery: events, top-N, by-database, by-app, by-context, table-guess, fingerprint
  - Blocking: events, by-spid, by-db, by-lock, table-guess, context, fingerprint

This module is self-contained and has no dependency on the integratedtool submodule.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# SQL fingerprinting
# ---------------------------------------------------------------------------

_STR_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'")
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_SPACE_RE = re.compile(r"\s+")
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"--[^\n]*")
# IN (...) collapse
_IN_LIST_RE = re.compile(r"\bIN\s*\([^()]*\)", re.IGNORECASE)
# VALUES (...) collapse
_VALUES_RE = re.compile(r"\bVALUES\s*\([^()]*\)", re.IGNORECASE)


def fingerprint_sql(sql: str, *, max_len: int = 200) -> str:
    """Normalize SQL to a canonical fingerprint string.

    Steps:
    1. Strip comments
    2. Collapse string literals → ?
    3. Collapse numbers → ?
    4. Collapse IN-lists → IN (?)
    5. Collapse VALUES rows → VALUES (?)
    6. Normalize whitespace
    7. Uppercase
    """
    if not sql:
        return ""
    s = str(sql)
    s = _COMMENT_BLOCK_RE.sub(" ", s)
    s = _COMMENT_LINE_RE.sub(" ", s)
    s = _STR_LITERAL_RE.sub("?", s)
    s = _IN_LIST_RE.sub("IN (?)", s)
    s = _VALUES_RE.sub("VALUES (?)", s)
    s = _NUM_RE.sub("?", s)
    s = _SPACE_RE.sub(" ", s).strip().upper()
    if max_len and len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def extract_tables(sql: str) -> List[str]:
    """Extract likely table names from SQL text."""
    if not sql:
        return []
    s = str(sql)
    s = _COMMENT_BLOCK_RE.sub(" ", s)
    s = _COMMENT_LINE_RE.sub(" ", s)
    tables: List[str] = []
    for pat in [
        r"\bFROM\s+([\[\]`\w\.]+)",
        r"\bJOIN\s+([\[\]`\w\.]+)",
        r"\bUPDATE\s+([\[\]`\w\.]+)",
        r"\bINTO\s+([\[\]`\w\.]+)",
        r"\bMERGE\s+(?:INTO\s+)?([\[\]`\w\.]+)",
    ]:
        for m in re.finditer(pat, s, re.IGNORECASE):
            t = m.group(1).strip("[]` ")
            if t and not t.upper().startswith("SELECT"):
                tables.append(t)
    return tables


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ILLEGAL_EXCEL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _clean(s: Any) -> Any:
    if s is None:
        return None
    try:
        text = str(s)
    except Exception:
        return s
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ILLEGAL_EXCEL_RE.sub("", text)


def _to_naive(s: pd.Series) -> pd.Series:
    """Convert tz-aware datetime series to tz-naive (UTC→strip tz)."""
    try:
        if hasattr(s, "dt") and hasattr(s.dt, "tz") and s.dt.tz is not None:
            return s.dt.tz_localize(None)
    except Exception:
        pass
    return s


def _write_xlsx(path: str, sheets: Dict[str, pd.DataFrame]) -> None:
    """Write a multi-sheet Excel workbook."""
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, df in sheets.items():
            # Truncate sheet name to 31 chars (Excel limit)
            sname = name[:31]
            # Strip tz-aware datetimes
            df2 = df.copy()
            for col in df2.columns:
                if pd.api.types.is_datetime64_any_dtype(df2[col]):
                    df2[col] = _to_naive(df2[col])
                elif df2[col].dtype == object:
                    df2[col] = df2[col].apply(_clean)
            df2.to_excel(w, sheet_name=sname, index=False)


# ---------------------------------------------------------------------------
# AggregationProcessor
# ---------------------------------------------------------------------------


class AggregationProcessor:
    """Native equivalent of IntegratedTool's AggregationProcessor.

    Produces aggregated Excel workbooks for Deadlock / SlowQuery / Blocking.
    """

    # ------------------------------------------------------------------
    # Deadlock
    # ------------------------------------------------------------------

    def _process_deadlock_df(
        self,
        df: pd.DataFrame,
        out_dir: str,
        *,
        source_files=None,
        forced_prefix: str = "",
    ) -> str:
        """Generate aggregated Deadlock Excel from a DataFrame.

        Expected column: ``parsed_data`` — a dict per row with keys:
          - timestamp (str ISO)
          - processes (list of process attrib dicts)
          - resources (list of resource attrib dicts)
          - victim_process (dict of victim process attribs)

        Returns path to the generated xlsx.
        """
        os.makedirs(out_dir, exist_ok=True)
        prefix = forced_prefix or f"deadlock_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_path = os.path.join(out_dir, f"{prefix}_deadlock.xlsx")

        rows: List[Dict[str, Any]] = []
        obj_counter: Dict[str, int] = {}
        host_counter: Dict[str, int] = {}
        login_counter: Dict[str, int] = {}
        iso_counter: Dict[str, int] = {}
        proc_rows: List[Dict[str, Any]] = []

        for _, row in df.iterrows():
            d = row.get("parsed_data") or {}
            if not isinstance(d, dict):
                continue

            ts = d.get("timestamp", "")
            victim = d.get("victim_process") or {}
            processes = d.get("processes") or []
            resources = d.get("resources") or []

            objects = sorted(set(
                r.get("objectname", "") for r in resources if r.get("objectname")
            ))
            for o in objects:
                obj_counter[o] = obj_counter.get(o, 0) + 1

            victim_spid = victim.get("spid", "")
            victim_host = victim.get("hostname", "")
            victim_login = victim.get("loginname", "")
            victim_iso = victim.get("isolationlevel", "")
            victim_sql = victim.get("inputbuf", "")
            victim_tran = victim.get("transactionname", "")
            victim_logused = victim.get("logused", "")

            offender_spids = ",".join(
                p.get("spid", "") for p in processes
                if p.get("spid") and p.get("spid") != str(victim_spid)
            )

            rows.append({
                "timestamp": ts,
                "victim_spid": victim_spid,
                "victim_hostname": victim_host,
                "victim_login": victim_login,
                "victim_isolationlevel": victim_iso,
                "victim_transactionname": victim_tran,
                "victim_logused": victim_logused,
                "offender_spids": offender_spids,
                "objects": ";".join(objects),
                "victim_sql": victim_sql,
                "process_count": len(processes),
            })

            for p in processes:
                host = p.get("hostname", "")
                login = p.get("loginname", "")
                iso = p.get("isolationlevel", "")
                if host:
                    host_counter[host] = host_counter.get(host, 0) + 1
                if login:
                    login_counter[login] = login_counter.get(login, 0) + 1
                if iso:
                    iso_counter[iso] = iso_counter.get(iso, 0) + 1

                proc_rows.append({
                    "event_timestamp": ts,
                    "spid": p.get("spid", ""),
                    "hostname": host,
                    "loginname": login,
                    "isolationlevel": iso,
                    "status": p.get("status", ""),
                    "waitresource": p.get("waitresource", ""),
                    "transactionname": p.get("transactionname", ""),
                    "logused": p.get("logused", ""),
                    "clientapp": p.get("clientapp", ""),
                    "inputbuf": p.get("inputbuf", ""),
                    "is_victim": "YES" if str(p.get("spid", "")) == str(victim_spid) else "",
                })

        df_events = pd.DataFrame(rows)
        df_objs = pd.DataFrame(
            [{"object": k, "count": v} for k, v in sorted(obj_counter.items(), key=lambda x: -x[1])]
        )
        df_hosts = pd.DataFrame(
            [{"hostname": k, "count": v} for k, v in sorted(host_counter.items(), key=lambda x: -x[1])]
        )
        df_logins = pd.DataFrame(
            [{"loginname": k, "count": v} for k, v in sorted(login_counter.items(), key=lambda x: -x[1])]
        )
        df_iso = pd.DataFrame(
            [{"isolationlevel": k, "count": v} for k, v in sorted(iso_counter.items(), key=lambda x: -x[1])]
        )
        df_procs = pd.DataFrame(proc_rows)

        # Fingerprint of victim SQL
        fp_rows: List[Dict[str, Any]] = []
        if not df_events.empty and "victim_sql" in df_events.columns:
            df_events["fingerprint"] = df_events["victim_sql"].apply(fingerprint_sql)
            fp_groups = (
                df_events[df_events["fingerprint"].notna() & (df_events["fingerprint"] != "")]
                .groupby("fingerprint")
                .agg(count=("fingerprint", "size"))
                .sort_values("count", ascending=False)
                .reset_index()
            )
            fp_rows = fp_groups.to_dict("records")
        df_fingerprint = pd.DataFrame(fp_rows)

        sheets: Dict[str, pd.DataFrame] = {"Events": df_events}
        if not df_objs.empty:
            sheets["Objects"] = df_objs
        if not df_hosts.empty:
            sheets["Hostnames"] = df_hosts
        if not df_logins.empty:
            sheets["Logins"] = df_logins
        if not df_iso.empty:
            sheets["IsolationLevels"] = df_iso
        if not df_procs.empty:
            sheets["Processes"] = df_procs
        if not df_fingerprint.empty:
            sheets["VictimSQL_Fingerprint"] = df_fingerprint

        _write_xlsx(out_path, sheets)
        return out_path

    # ------------------------------------------------------------------
    # SlowQuery
    # ------------------------------------------------------------------

    def _process_slowquery_df(
        self,
        df: pd.DataFrame,
        out_dir: str,
        *,
        source_files=None,
        forced_prefix: str = "",
    ) -> str:
        """Generate aggregated SlowQuery Excel from a DataFrame.

        Expected columns (from generate_slowquery_reports_from_jsonl):
          StartTime, Duration (us), database_name, client_app_name,
          client_hostname, username, session_id, object_name,
          context_key, sql_text, duration_sec, ...
        """
        os.makedirs(out_dir, exist_ok=True)
        prefix = forced_prefix or f"slowquery_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_path = os.path.join(out_dir, f"{prefix}_slowquery.xlsx")

        if df.empty:
            _write_xlsx(out_path, {"Events": pd.DataFrame()})
            return out_path

        # Resolve column names flexibly
        def _col(candidates: List[str]) -> Optional[str]:
            for c in candidates:
                if c in df.columns:
                    return c
            return None

        ts_col = _col(["StartTime", "timestamp"])
        dur_col = _col(["duration_sec"])
        dur_us_col = _col(["Duration", "duration_us"])
        sql_col = _col(["sql_text"])
        db_col = _col(["database_name"])
        app_col = _col(["client_app_name"])
        ctx_col = _col(["context_key"])
        user_col = _col(["username"])
        obj_col = _col(["object_name"])
        host_col = _col(["client_hostname"])
        session_col = _col(["session_id"])

        df_work = df.copy()

        # Ensure duration_sec
        if dur_col and dur_col in df_work.columns:
            df_work["_dur_sec"] = pd.to_numeric(df_work[dur_col], errors="coerce")
        elif dur_us_col and dur_us_col in df_work.columns:
            df_work["_dur_sec"] = pd.to_numeric(df_work[dur_us_col], errors="coerce") / 1_000_000
        else:
            df_work["_dur_sec"] = None

        df_sorted = df_work.sort_values("_dur_sec", ascending=False, na_position="last")

        def _agg(group_col: Optional[str]) -> pd.DataFrame:
            if not group_col or group_col not in df_work.columns:
                return pd.DataFrame()
            g = (
                df_work.dropna(subset=[group_col])
                .groupby(group_col)["_dur_sec"]
                .agg(count="count", mean_sec="mean", max_sec="max", sum_sec="sum")
                .sort_values("count", ascending=False)
                .reset_index()
            )
            g.columns = [group_col, "count", "mean_sec", "max_sec", "sum_sec"]
            return g

        by_db = _agg(db_col)
        by_app = _agg(app_col)
        by_ctx = _agg(ctx_col)
        by_user = _agg(user_col)
        by_obj = _agg(obj_col)
        by_host = _agg(host_col)

        # Table guess
        if sql_col and sql_col in df_work.columns:
            def _first_table(sql: Any) -> str:
                tables = extract_tables(str(sql or ""))
                return tables[0] if tables else ""

            df_work["_table_guess"] = df_work[sql_col].apply(_first_table)
            by_table = (
                df_work[df_work["_table_guess"] != ""]["_table_guess"]
                .value_counts()
                .reset_index()
            )
            by_table.columns = ["table_guess", "count"]
        else:
            df_work["_table_guess"] = ""
            by_table = pd.DataFrame()

        # SQL Fingerprint
        if sql_col and sql_col in df_work.columns:
            df_work["_fingerprint"] = df_work[sql_col].apply(fingerprint_sql)
            by_fp = (
                df_work[df_work["_fingerprint"] != ""]
                .groupby("_fingerprint")["_dur_sec"]
                .agg(count="count", mean_sec="mean", max_sec="max", total_sec="sum")
                .sort_values("count", ascending=False)
                .reset_index()
            )
            by_fp.columns = ["fingerprint", "count", "mean_sec", "max_sec", "total_sec"]
        else:
            by_fp = pd.DataFrame()

        sheets: Dict[str, pd.DataFrame] = {
            "Events": df_sorted.drop(columns=["_dur_sec", "_table_guess", "_fingerprint"], errors="ignore"),
            "Top50_Duration": df_sorted.head(50).drop(columns=["_dur_sec", "_table_guess", "_fingerprint"], errors="ignore"),
        }
        if not by_db.empty:
            sheets["ByDatabase"] = by_db
        if not by_app.empty:
            sheets["ByApp"] = by_app
        if not by_ctx.empty:
            sheets["ByContext"] = by_ctx
        if not by_user.empty:
            sheets["ByUser"] = by_user
        if not by_obj.empty:
            sheets["ByObject"] = by_obj
        if not by_host.empty:
            sheets["ByHostname"] = by_host
        if not by_table.empty:
            sheets["Table_Guess"] = by_table
        if not by_fp.empty:
            sheets["SQL_Fingerprint"] = by_fp

        _write_xlsx(out_path, sheets)
        return out_path

    # ------------------------------------------------------------------
    # Blocking
    # ------------------------------------------------------------------

    def _process_blocking_df(
        self,
        df: pd.DataFrame,
        out_dir: str,
        *,
        source_files=None,
        forced_prefix: str = "",
    ) -> str:
        """Generate aggregated Blocking Excel from a DataFrame.

        Expected columns (from generate_blocking_reports_from_jsonl + extras):
          timestamp, database_name, duration_us, duration_sec, lock_mode,
          blocked_spid, blocking_spid, waitresource, client_app_name,
          client_hostname, username, blocked_inputbuf, blocking_inputbuf,
          blocked_context_key, blocking_context_key,
          blocked_table_guess, blocking_table_guess,
          event_time (optional), duration (optional),
          victim_sql (optional alias for blocked_inputbuf), ...
        """
        os.makedirs(out_dir, exist_ok=True)
        prefix = forced_prefix or f"blocking_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_path = os.path.join(out_dir, f"{prefix}_blocking.xlsx")

        if df.empty:
            _write_xlsx(out_path, {"Events": pd.DataFrame()})
            return out_path

        def _col(candidates: List[str]) -> Optional[str]:
            for c in candidates:
                if c in df.columns:
                    return c
            return None

        dur_col = _col(["duration_sec"])
        blocked_sql_col = _col(["blocked_inputbuf", "victim_sql"])
        blocking_sql_col = _col(["blocking_inputbuf", "blocking_sql"])
        blocked_spid_col = _col(["blocked_spid", "victim_spid"])
        blocking_spid_col = _col(["blocking_spid"])
        blocked_ctx_col = _col(["blocked_context_key"])
        blocking_ctx_col = _col(["blocking_context_key"])
        blocked_tbl_col = _col(["blocked_table_guess"])
        blocking_tbl_col = _col(["blocking_table_guess"])
        db_col = _col(["database_name"])
        app_col = _col(["client_app_name"])
        lock_col = _col(["lock_mode"])

        df_work = df.copy()

        if dur_col:
            df_work["_dur_sec"] = pd.to_numeric(df_work[dur_col], errors="coerce")
        else:
            df_work["_dur_sec"] = None

        df_sorted = df_work.sort_values("_dur_sec", ascending=False, na_position="last")

        def _spid_agg(col: Optional[str], label: str) -> pd.DataFrame:
            if not col or col not in df_work.columns:
                return pd.DataFrame()
            g = (
                df_work.dropna(subset=[col])
                .groupby(col)["_dur_sec"]
                .agg(count="count", mean_sec="mean", max_sec="max")
                .sort_values("count", ascending=False)
                .reset_index()
            )
            g.columns = [label, "count", "mean_sec", "max_sec"]
            return g

        by_blocked = _spid_agg(blocked_spid_col, "blocked_spid")
        by_blocking = _spid_agg(blocking_spid_col, "blocking_spid")

        def _val_counts(col: Optional[str], label: str) -> pd.DataFrame:
            if not col or col not in df_work.columns:
                return pd.DataFrame()
            vc = df_work[col].value_counts().reset_index()
            vc.columns = [label, "count"]
            return vc

        by_db = _val_counts(db_col, "database_name")
        by_lock = _val_counts(lock_col, "lock_mode")
        by_blocked_ctx = _val_counts(blocked_ctx_col, "blocked_context_key")
        by_blocking_ctx = _val_counts(blocking_ctx_col, "blocking_context_key")

        # Table guess (compute or use existing column)
        def _add_table_guess(sql_col: Optional[str], existing_col: Optional[str], new_col: str) -> None:
            if existing_col and existing_col in df_work.columns:
                df_work[new_col] = df_work[existing_col]
            elif sql_col and sql_col in df_work.columns:
                df_work[new_col] = df_work[sql_col].apply(
                    lambda s: (extract_tables(str(s or "")) or [""])[0]
                )
            else:
                df_work[new_col] = ""

        _add_table_guess(blocked_sql_col, blocked_tbl_col, "_blocked_tbl")
        _add_table_guess(blocking_sql_col, blocking_tbl_col, "_blocking_tbl")

        by_blocked_tbl = df_work[df_work["_blocked_tbl"] != ""]["_blocked_tbl"].value_counts().reset_index()
        if not by_blocked_tbl.empty:
            by_blocked_tbl.columns = ["blocked_table", "count"]
        by_blocking_tbl = df_work[df_work["_blocking_tbl"] != ""]["_blocking_tbl"].value_counts().reset_index()
        if not by_blocking_tbl.empty:
            by_blocking_tbl.columns = ["blocking_table", "count"]

        # SQL fingerprints
        def _fingerprint_agg(sql_col: Optional[str], label: str) -> pd.DataFrame:
            if not sql_col or sql_col not in df_work.columns:
                return pd.DataFrame()
            df_work["_fp"] = df_work[sql_col].apply(fingerprint_sql)
            fp_g = (
                df_work[df_work["_fp"] != ""]
                .groupby("_fp")["_dur_sec"]
                .agg(count="count", mean_sec="mean", max_sec="max")
                .sort_values("count", ascending=False)
                .reset_index()
            )
            fp_g.columns = [label, "count", "mean_sec", "max_sec"]
            df_work.drop(columns=["_fp"], inplace=True, errors="ignore")
            return fp_g

        by_blocked_fp = _fingerprint_agg(blocked_sql_col, "blocked_sql_fingerprint")
        by_blocking_fp = _fingerprint_agg(blocking_sql_col, "blocking_sql_fingerprint")

        drop_cols = ["_dur_sec", "_blocked_tbl", "_blocking_tbl"]
        events_df = df_sorted.drop(columns=drop_cols, errors="ignore")

        sheets: Dict[str, pd.DataFrame] = {
            "Events": events_df,
            "Top100_Duration": events_df.head(100),
        }
        if not by_blocked.empty:
            sheets["ByBlockedSpid"] = by_blocked
        if not by_blocking.empty:
            sheets["ByBlockingSpid"] = by_blocking
        if not by_db.empty:
            sheets["ByDatabase"] = by_db
        if not by_lock.empty:
            sheets["ByLockMode"] = by_lock
        if not by_blocked_tbl.empty:
            sheets["Blocked_Table_Guess"] = by_blocked_tbl
        if not by_blocking_tbl.empty:
            sheets["Blocking_Table_Guess"] = by_blocking_tbl
        if not by_blocked_ctx.empty:
            sheets["Blocked_Context"] = by_blocked_ctx
        if not by_blocking_ctx.empty:
            sheets["Blocking_Context"] = by_blocking_ctx
        if not by_blocked_fp.empty:
            sheets["Blocked_SQL_Fingerprint"] = by_blocked_fp
        if not by_blocking_fp.empty:
            sheets["Blocking_SQL_Fingerprint"] = by_blocking_fp

        _write_xlsx(out_path, sheets)
        return out_path

    # ------------------------------------------------------------------
    # Aggregation across multiple inputMD folders
    # ------------------------------------------------------------------

    def aggregate_from_jsonl_files(
        self,
        *,
        deadlock_jsonl_files: List[str],
        blocking_jsonl_files: List[str],
        slowquery_jsonl_files: List[str],
        out_dir: str,
        prefix: str = "",
        start_jst=None,
        end_jst=None,
        ranges=None,
        slow_threshold_sec: float = 3.0,
    ) -> Dict[str, str]:
        """Aggregate multiple JSONL files into combined analysis reports.

        Returns dict of {type: xlsx_path}.
        """
        from .xel_jsonl import iter_events
        from .timeutil import in_range, parse_iso, to_jst
        from .context import pick_context
        from .deadlock_report import _parse_deadlock_xml
        from .blocking_report import _parse_blocked_process_xml, _extract_table_from_sql

        os.makedirs(out_dir, exist_ok=True)
        if not prefix:
            prefix = f"agg_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        out: Dict[str, str] = {}

        # ---- Deadlock ----
        if deadlock_jsonl_files:
            from .deadlock_report import _parse_deadlock_xml

            parsed_list = []
            for jsonl_path in deadlock_jsonl_files:
                if not os.path.exists(jsonl_path):
                    continue
                for ev in iter_events(jsonl_path, event_name="xml_deadlock_report"):
                    xml_report = ev.fields.get("xml_report")
                    if not xml_report:
                        continue
                    dt = parse_iso(ev.timestamp or "")
                    if dt is not None:
                        if not in_range(dt, start_jst, end_jst):
                            continue
                        if ranges:
                            from .ranges import in_any_range
                            if not in_any_range(dt, ranges):
                                continue
                    try:
                        item = _parse_deadlock_xml(str(xml_report))
                        item.timestamp = ev.timestamp
                    except Exception:
                        continue

                    victim_proc = None
                    if item.victim_spid:
                        for p in item.processes:
                            if str(p.get("spid") or "") == str(item.victim_spid):
                                victim_proc = p
                                break
                    parsed_list.append({
                        "timestamp": item.timestamp or None,
                        "processes": item.processes,
                        "resources": [],
                        "victim_process": victim_proc or {},
                    })

            if parsed_list:
                df_d = pd.DataFrame({"parsed_data": parsed_list})
                p = self._process_deadlock_df(df_d, out_dir, forced_prefix=prefix)
                out["deadlock"] = p

        # ---- Blocking ----
        if blocking_jsonl_files:
            rows = []
            for jsonl_path in blocking_jsonl_files:
                if not os.path.exists(jsonl_path):
                    continue
                for ev in iter_events(jsonl_path, event_name="blocked_process_report"):
                    dt = parse_iso(ev.timestamp or "")
                    if dt is not None:
                        if not in_range(dt, start_jst, end_jst):
                            continue
                        if ranges:
                            from .ranges import in_any_range
                            if not in_any_range(dt, ranges):
                                continue
                    fields = ev.fields
                    actions = ev.actions
                    try:
                        dur_us = fields.get("duration")
                        dur_sec = float(dur_us) / 1_000_000.0 if dur_us is not None else None
                    except Exception:
                        dur_sec = None
                    parsed = {}
                    if fields.get("blocked_process"):
                        try:
                            parsed = _parse_blocked_process_xml(str(fields["blocked_process"]))
                        except Exception:
                            pass
                    blocked_sql = parsed.get("blocked_inputbuf")
                    blocking_sql = parsed.get("blocking_inputbuf")
                    blocked_ctx = pick_context(str(blocked_sql or ""))
                    blocking_ctx = pick_context(str(blocking_sql or ""))
                    rows.append({
                        "timestamp": ev.timestamp,
                        "database_name": actions.get("database_name") or fields.get("database_name"),
                        "duration_us": fields.get("duration"),
                        "duration_sec": dur_sec,
                        "lock_mode": fields.get("lock_mode"),
                        "blocked_spid": parsed.get("blocked_spid") or actions.get("session_id"),
                        "blocking_spid": parsed.get("blocking_spid"),
                        "waitresource": parsed.get("waitresource"),
                        "client_app_name": actions.get("client_app_name"),
                        "client_hostname": actions.get("client_hostname"),
                        "username": actions.get("username"),
                        "blocked_inputbuf": blocked_sql,
                        "blocking_inputbuf": blocking_sql,
                        "blocked_context_key": blocked_ctx.key if blocked_ctx else None,
                        "blocking_context_key": blocking_ctx.key if blocking_ctx else None,
                        "blocked_table_guess": _extract_table_from_sql(blocked_sql or ""),
                        "blocking_table_guess": _extract_table_from_sql(blocking_sql or ""),
                    })
            if rows:
                df_b = pd.DataFrame(rows)
                p = self._process_blocking_df(df_b, out_dir, forced_prefix=prefix)
                out["blocking"] = p

        # ---- SlowQuery ----
        if slowquery_jsonl_files:
            rows = []
            for jsonl_path in slowquery_jsonl_files:
                if not os.path.exists(jsonl_path):
                    continue
                for ev in iter_events(jsonl_path):
                    if ev.name.lower() not in ("rpc_completed", "sql_batch_completed"):
                        continue
                    dt = parse_iso(ev.timestamp or "")
                    if dt is not None:
                        if not in_range(dt, start_jst, end_jst):
                            continue
                        if ranges:
                            from .ranges import in_any_range
                            if not in_any_range(dt, ranges):
                                continue
                    fields = ev.fields
                    actions = ev.actions
                    try:
                        dur_us = fields.get("duration")
                        dur_sec = float(dur_us) / 1_000_000.0 if dur_us is not None else None
                    except Exception:
                        dur_sec = None
                    if dur_sec is None or dur_sec < slow_threshold_sec:
                        continue
                    sql = actions.get("sql_text") or fields.get("statement") or fields.get("batch_text")
                    ctx = pick_context(str(sql or ""))
                    rows.append({
                        "event": ev.name,
                        "timestamp": ev.timestamp,
                        "duration_us": dur_us,
                        "duration_sec": dur_sec,
                        "cpu_time": fields.get("cpu_time"),
                        "logical_reads": fields.get("logical_reads"),
                        "physical_reads": fields.get("physical_reads"),
                        "writes": fields.get("writes"),
                        "row_count": fields.get("row_count"),
                        "database_name": actions.get("database_name"),
                        "username": actions.get("username"),
                        "client_app_name": actions.get("client_app_name"),
                        "client_hostname": actions.get("client_hostname"),
                        "session_id": actions.get("session_id"),
                        "object_name": fields.get("object_name"),
                        "context_key": ctx.key if ctx else None,
                        "context_module": ctx.module if ctx else None,
                        "context_function": ctx.function if ctx else None,
                        "sql_text": sql,
                    })
            if rows:
                df_s = pd.DataFrame(rows)
                p = self._process_slowquery_df(df_s, out_dir, forced_prefix=prefix)
                out["slowquery"] = p

        return out
