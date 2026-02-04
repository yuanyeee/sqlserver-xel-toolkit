from __future__ import annotations

import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .xel_jsonl import iter_events
from .context import pick_context
from .timeutil import in_range, merge_range, parse_iso, range_tag


_ILLEGAL_EXCEL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _clean_excel_text(s: Any) -> Any:
    if s is None:
        return None
    try:
        text = str(s)
    except Exception:
        return s
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ILLEGAL_EXCEL_RE.sub("", text)


def _duration_us_to_s(us: Any) -> Optional[float]:
    try:
        if us is None:
            return None
        return float(us) / 1_000_000.0
    except Exception:
        return None


def _extract_table_from_sql(sql: str) -> Optional[str]:
    if not sql:
        return None
    # Very simple heuristic, good enough for top stats.
    s = re.sub(r"\s+", " ", sql.strip(), flags=re.M)
    m = re.search(r"\bFROM\s+([\[\]\w\.]+)", s, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bUPDATE\s+([\[\]\w\.]+)", s, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bINTO\s+([\[\]\w\.]+)", s, flags=re.I)
    if m:
        return m.group(1)
    return None


def _parse_blocked_process_xml(xml_text: str) -> Dict[str, Any]:
    """Parse SQL Server blocked process report XML.

    We try to extract:
      - blocked spid + waitresource + inputbuf
      - blocking spid + inputbuf

    XML shape can vary by version; we use robust searches.
    """
    root = ET.fromstring(xml_text)

    # Sometimes root is <blocked-process-report>.
    # Sometimes it's nested. Normalize to find the first blocked-process-report element.
    bpr = root
    if root.tag != "blocked-process-report":
        found = root.find(".//blocked-process-report")
        if found is not None:
            bpr = found

    out: Dict[str, Any] = {
        "blocked_spid": None,
        "blocking_spid": None,
        "waitresource": None,
        "blocked_inputbuf": None,
        "blocking_inputbuf": None,
    }

    blocked_proc = bpr.find(".//blocked-process/process")
    if blocked_proc is not None:
        out["blocked_spid"] = blocked_proc.attrib.get("spid")
        out["waitresource"] = blocked_proc.attrib.get("waitresource")
        ib = blocked_proc.find(".//inputbuf")
        if ib is not None and ib.text:
            out["blocked_inputbuf"] = ib.text.strip()

    blocking_proc = bpr.find(".//blocking-process/process")
    if blocking_proc is not None:
        out["blocking_spid"] = blocking_proc.attrib.get("spid")
        ib = blocking_proc.find(".//inputbuf")
        if ib is not None and ib.text:
            out["blocking_inputbuf"] = ib.text.strip()

    return out


def _try_enrich_object_names(df: pd.DataFrame, out_dir: str, prefix: str) -> pd.DataFrame:
    """Optionally enrich df with database/object/index names.

    If MSSQL_CONNSTR is not set or mapping fails, returns df unchanged.
    """
    conn = os.environ.get("MSSQL_CONNSTR")
    if not conn:
        return df

    try:
        # Collect unique keys
        keys = (
            df[["database_id", "object_id", "index_id"]]
            .dropna(subset=["database_id", "object_id"])
            .drop_duplicates()
            .to_dict(orient="records")
        )

        if not keys:
            return df

        tmp_dir = os.path.join(out_dir, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        keys_path = os.path.join(tmp_dir, f"{prefix}_object_keys.json")
        map_path = os.path.join(tmp_dir, f"{prefix}_object_map.json")

        with open(keys_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "database_id": int(k.get("database_id")),
                        "object_id": int(k.get("object_id")),
                        "index_id": (int(k["index_id"]) if k.get("index_id") not in (None, "", float("nan")) else None),
                    }
                    for k in keys
                    if k.get("database_id") and k.get("object_id")
                ],
                f,
            )

        # Run dotnet mapper (optional). It reads MSSQL_CONNSTR from env.
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        dotnet = "/usr/local/share/dotnet/dotnet"
        cmd = [
            dotnet,
            "run",
            "--project",
            os.path.join(repo_root, "src", "ObjectMapper"),
            "--",
            "--in",
            keys_path,
            "--out",
            map_path,
        ]

        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not os.path.exists(map_path):
            return df

        with open(map_path, "r", encoding="utf-8") as f:
            m = json.load(f) or {}

        if not m:
            return df

        def _mk_key(r):
            dbid = r.get("database_id")
            oid = r.get("object_id")
            iid = r.get("index_id")
            iid_s = "" if iid is None or (isinstance(iid, float) and pd.isna(iid)) else str(int(iid))
            return f"{int(dbid)}|{int(oid)}|{iid_s}"

        keys_series = df.apply(_mk_key, axis=1)
        df = df.copy()
        df["database_name_resolved"] = keys_series.map(lambda k: (m.get(k) or {}).get("database_name"))
        df["object_name_resolved"] = keys_series.map(lambda k: (m.get(k) or {}).get("object_name"))
        df["index_name_resolved"] = keys_series.map(lambda k: (m.get(k) or {}).get("index_name"))
        return df

    except Exception:
        return df


def generate_blocking_reports_from_jsonl(
    jsonl_path: str,
    *,
    out_dir: str,
    source_xel: str,
    prefix_base: str,
    start_jst=None,
    end_jst=None,
    ranges=None,
) -> Dict[str, str]:
    rows: List[Dict[str, Any]] = []
    r = (None, None)

    for ev in iter_events(jsonl_path, event_name="blocked_process_report"):
        dt = parse_iso(ev.timestamp or "")
        if dt is not None:
            if not in_range(dt, start_jst, end_jst):
                continue
            if ranges:
                from .ranges import in_any_range

                if not in_any_range(dt, ranges):
                    continue
            r = merge_range(r, dt)

        fields = ev.fields
        actions = ev.actions

        duration_sec = _duration_us_to_s(fields.get("duration"))

        parsed = {}
        blocked_xml = fields.get("blocked_process")
        if blocked_xml:
            try:
                parsed = _parse_blocked_process_xml(str(blocked_xml))
            except Exception:
                parsed = {}

        blocked_sql = parsed.get("blocked_inputbuf")
        blocking_sql = parsed.get("blocking_inputbuf")

        blocked_ctx = pick_context(str(blocked_sql or ""))
        blocking_ctx = pick_context(str(blocking_sql or ""))

        rows.append(
            {
                "timestamp": ev.timestamp,
                "database_id": fields.get("database_id") or actions.get("database_id"),
                "database_name": _clean_excel_text(fields.get("database_name") or actions.get("database_name")),
                "duration_us": fields.get("duration"),
                "duration_sec": duration_sec,
                "lock_mode": fields.get("lock_mode"),
                "resource_owner_type": fields.get("resource_owner_type"),
                "object_id": fields.get("object_id"),
                "index_id": fields.get("index_id"),
                "blocked_spid": parsed.get("blocked_spid") or actions.get("session_id"),
                "blocking_spid": parsed.get("blocking_spid"),
                "waitresource": parsed.get("waitresource"),
                "client_app_name": _clean_excel_text(actions.get("client_app_name")),
                "client_hostname": _clean_excel_text(actions.get("client_hostname")),
                "username": _clean_excel_text(actions.get("username") or actions.get("nt_username") or actions.get("session_nt_username")),
                "blocked_inputbuf": _clean_excel_text(blocked_sql),
                "blocking_inputbuf": _clean_excel_text(blocking_sql),
                "blocked_context_key": _clean_excel_text(blocked_ctx.key) if blocked_ctx else None,
                "blocking_context_key": _clean_excel_text(blocking_ctx.key) if blocking_ctx else None,
                "blocked_table_guess": _clean_excel_text(_extract_table_from_sql(blocked_sql or "")),
                "blocking_table_guess": _clean_excel_text(_extract_table_from_sql(blocking_sql or "")),
            }
        )

    df = pd.DataFrame(rows)

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lo, hi = r
    tag = range_tag(lo, hi)
    prefix = f"{prefix_base}_{tag}"

    # Optional SQL Server mapping uses prefix for temp filenames; prefix_base is sufficient and avoids ordering bugs.
    df = _try_enrich_object_names(df, out_dir=out_dir, prefix=prefix_base)
    xlsx_path = os.path.join(out_dir, f"{prefix}_blocking.xlsx")
    legacy_xlsx_path = os.path.join(out_dir, f"{prefix}_blocking_legacy.xlsx")
    md_path = os.path.join(out_dir, f"{prefix}_blocking_report.md")

    if df.empty:
        md = [
            "# Blocking Report (from XEL)",
            "",
            f"- Created: {created}",
            f"- Source XEL: `{source_xel}`",
            "",
            "No blocked_process_report events found.",
        ]
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        return {"md": md_path}

    df_sorted = df.sort_values(["duration_sec"], ascending=False, na_position="last")

    by_blocked = df.groupby("blocked_spid")["duration_sec"].agg(["count", "mean", "max"]).sort_values("count", ascending=False)
    by_blocking = df.groupby("blocking_spid")["duration_sec"].agg(["count", "mean", "max"]).sort_values("count", ascending=False)

    by_blocked_table = df["blocked_table_guess"].value_counts().head(50)
    by_blocking_table = df["blocking_table_guess"].value_counts().head(50)

    by_blocked_ctx = df["blocked_context_key"].value_counts().head(50)
    by_blocking_ctx = df["blocking_context_key"].value_counts().head(50)

    by_db = df["database_name"].value_counts().head(50)
    by_lock = df["lock_mode"].value_counts().head(50)

    # 1) Legacy workbook (keep previous Toolkit format)
    with pd.ExcelWriter(legacy_xlsx_path, engine="openpyxl") as w:
        df_sorted.to_excel(w, sheet_name="Events", index=False)
        df_sorted.head(100).to_excel(w, sheet_name="Top100_Duration", index=False)
        by_blocked.reset_index().to_excel(w, sheet_name="ByBlockedSpid", index=False)
        by_blocking.reset_index().to_excel(w, sheet_name="ByBlockingSpid", index=False)
        by_db.reset_index(name="count").to_excel(w, sheet_name="ByDatabase", index=False)
        by_lock.reset_index(name="count").to_excel(w, sheet_name="ByLockMode", index=False)
        by_blocked_table.reset_index(name="count").to_excel(w, sheet_name="Blocked_Table_Guess", index=False)
        by_blocking_table.reset_index(name="count").to_excel(w, sheet_name="Blocking_Table_Guess", index=False)
        by_blocked_ctx.reset_index(name="count").to_excel(w, sheet_name="Blocked_Context", index=False)
        by_blocking_ctx.reset_index(name="count").to_excel(w, sheet_name="Blocking_Context", index=False)

    # 2) IntegratedTool-style aggregated workbook (primary)
    try:
        import sys

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        it_dir = os.path.join(repo_root, "integratedtool")
        if os.path.isdir(it_dir) and it_dir not in sys.path:
            sys.path.insert(0, it_dir)

        from aggregation_processor import AggregationProcessor  # type: ignore

        ap = AggregationProcessor()
        df_it = df.copy()
        df_it["event_time"] = pd.to_datetime(df_it.get("timestamp"), errors="coerce")
        df_it["duration"] = pd.to_numeric(df_it.get("duration_us"), errors="coerce")
        df_it["victim_sql"] = df_it.get("blocked_inputbuf")
        df_it["blocking_sql"] = df_it.get("blocking_inputbuf")
        df_it["victim_spid"] = df_it.get("blocked_spid")
        df_it["blocking_spid"] = df_it.get("blocking_spid")
        df_it["victim_clientapp"] = df_it.get("client_app_name")

        ap._process_blocking_df(df_it, out_dir, source_files=None, forced_prefix=prefix)
    except Exception:
        # If IntegratedTool export fails, keep legacy only.
        xlsx_path = legacy_xlsx_path

    md: List[str] = []
    md.append("# Blocking Report (from XEL)")
    md.append("")
    md.append(f"- Created: {created}")
    md.append(f"- Source XEL: `{source_xel}`")
    md.append(f"- Events: {len(df)} (blocked_process_report)")
    md.append(f"- Output (aggregated): `{os.path.basename(xlsx_path)}`")
    md.append(f"- Output (legacy): `{os.path.basename(legacy_xlsx_path)}`")
    md.append("")

    # Quick highlights
    top_duration = df_sorted["duration_sec"].dropna().head(1)
    if len(top_duration) == 1:
        md.append(f"- Max duration: **{float(top_duration.iloc[0]):.3f}s**")
    if len(by_blocking) > 0:
        top_blocker = by_blocking.reset_index().iloc[0]
        md.append(f"- Top blocking SPID: **{top_blocker['blocking_spid']}** (count={int(top_blocker['count'])})")
    md.append("")

    def md_table(title: str, header: str, rows_: List[str]):
        md.append(f"## {title}")
        md.append("")
        md.append(header)
        md.append("---|---:")
        md.extend(rows_)
        md.append("")

    md_table(
        "Blocked context top 15",
        "context | count",
        [f"{k} | {v}" for k, v in df["blocked_context_key"].value_counts().head(15).items() if str(k).strip() and k != "None"],
    )

    md_table(
        "Blocked table (guess) top 15",
        "table | count",
        [f"{k} | {v}" for k, v in df["blocked_table_guess"].value_counts().head(15).items() if str(k).strip() and k != "None"],
    )

    md_table(
        "Blocking SPID top 15",
        "spid | count",
        [f"{k} | {v}" for k, v in df["blocking_spid"].value_counts().head(15).items() if str(k).strip() and k != "None"],
    )

    md.append("## Top 10 events (by duration)")
    md.append("")
    md.append("duration_sec | db | blocked_spid | blocking_spid | lock_mode | waitresource")
    md.append("---:|---|---:|---:|---|---")
    for _, r in df_sorted.head(10).iterrows():
        waitres = str(r.get('waitresource', '') or '').replace("\n", " ")[:80]
        md.append(
            f"{(r.get('duration_sec') or 0):.3f} | {r.get('database_name','')} | {r.get('blocked_spid','')} | {r.get('blocking_spid','')} | {r.get('lock_mode','')} | {waitres}"
        )
    md.append("")

    out_path = md_path
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    return {"md": md_path, "xlsx": xlsx_path}
