from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .xel_jsonl import iter_events
from .context import pick_context
from .timeutil import in_range, merge_range, parse_iso, range_tag
from .utils import clean_excel_text


def _duration_us_to_s(us: Any) -> Optional[float]:
    try:
        if us is None:
            return None
        return float(us) / 1_000_000.0
    except Exception:
        return None


def generate_slowquery_reports_from_jsonl(
    jsonl_path: str,
    *,
    out_dir: str,
    source_xel: str,
    prefix_base: str,
    threshold_sec: float = 3.0,
    start_jst=None,
    end_jst=None,
    ranges=None,
) -> Dict[str, str]:
    rows: List[Dict[str, Any]] = []
    r = (None, None)

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

        duration_us = fields.get("duration")
        duration_sec = _duration_us_to_s(duration_us)
        if duration_sec is None or duration_sec < threshold_sec:
            continue

        if dt is not None:
            r = merge_range(r, dt)

        sql_text = actions.get("sql_text") or fields.get("statement") or fields.get("batch_text")
        sql_text = clean_excel_text(sql_text)

        ctx = pick_context(str(sql_text or ""))

        rows.append(
            {
                "event": ev.name,
                "timestamp": ev.timestamp,
                "duration_us": duration_us,
                "duration_sec": duration_sec,
                "cpu_time": fields.get("cpu_time"),
                "logical_reads": fields.get("logical_reads"),
                "physical_reads": fields.get("physical_reads"),
                "writes": fields.get("writes"),
                "row_count": fields.get("row_count"),
                "database_name": clean_excel_text(actions.get("database_name")),
                "username": clean_excel_text(actions.get("username")),
                "client_app_name": clean_excel_text(actions.get("client_app_name")),
                "client_hostname": clean_excel_text(actions.get("client_hostname")),
                "session_id": actions.get("session_id"),
                "object_name": clean_excel_text(fields.get("object_name")),
                "context_raw": clean_excel_text(ctx.raw) if ctx else None,
                "context_key": clean_excel_text(ctx.key) if ctx else None,
                "context_module": clean_excel_text(ctx.module) if ctx else None,
                "context_function": clean_excel_text(ctx.function) if ctx else None,
                "context_process": clean_excel_text(ctx.process) if ctx else None,
                "sql_text": sql_text,
            }
        )

    df = pd.DataFrame(rows)

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lo, hi = r
    tag = range_tag(lo, hi)
    prefix = f"{prefix_base}_{tag}"
    xlsx_path = os.path.join(out_dir, f"{prefix}_slowquery.xlsx")
    md_path = os.path.join(out_dir, f"{prefix}_slowquery_report.md")

    if df.empty:
        md = [
            "# Slow Query Report (from XEL)",
            "",
            f"- Created: {created}",
            f"- Source XEL: `{source_xel}`",
            f"- Threshold: >= {threshold_sec:.1f}s",
            "",
            "No events matched the threshold.",
        ]
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        return {"md": md_path}

    # Top by duration
    df_sorted = df.sort_values("duration_sec", ascending=False)

    # Basic aggregations
    by_db = df.groupby("database_name")["duration_sec"].agg(["count", "mean", "max"]).sort_values("count", ascending=False)
    by_app = df.groupby("client_app_name")["duration_sec"].agg(["count", "mean", "max"]).sort_values("count", ascending=False)
    by_ctx = (
        df.dropna(subset=["context_key"])
        .groupby("context_key")["duration_sec"]
        .agg(["count", "mean", "max"])
        .sort_values("count", ascending=False)
    )

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        df_sorted.to_excel(w, sheet_name="Events", index=False)
        df_sorted.head(50).to_excel(w, sheet_name="Top50_Duration", index=False)
        by_db.reset_index().to_excel(w, sheet_name="ByDatabase", index=False)
        by_app.reset_index().to_excel(w, sheet_name="ByApp", index=False)
        if not by_ctx.empty:
            by_ctx.reset_index().to_excel(w, sheet_name="ByContext", index=False)

    # Markdown summary
    md: List[str] = []
    md.append("# Slow Query Report (from XEL)")
    md.append("")
    md.append(f"- Created: {created}")
    md.append(f"- Source XEL: `{source_xel}`")
    md.append(f"- Threshold: >= {threshold_sec:.1f}s")
    md.append(f"- Matched events: {len(df)}")
    md.append(f"- Output: `{os.path.basename(xlsx_path)}`")
    md.append("")

    top = df_sorted.head(10)
    md.append("## Top 10 (by duration)")
    md.append("")
    md.append("duration_sec | database | app | user | session_id | object | sql")
    md.append("---:|---|---|---|---:|---|---")
    for _, r in top.iterrows():
        sql = str(r.get("sql_text") or "").replace("\n", " ").strip()
        if len(sql) > 120:
            sql = sql[:117] + "..."
        md.append(
            f"{r['duration_sec']:.3f} | {r.get('database_name','')} | {r.get('client_app_name','')} | {r.get('username','')} | {r.get('session_id','')} | {r.get('object_name','')} | {sql}"
        )
    md.append("")

    if not by_ctx.empty:
        md.append("## By context (top 15 by count)")
        md.append("")
        md.append("context | count | mean_sec | max_sec")
        md.append("---|---:|---:|---:")
        for _, r in by_ctx.reset_index().head(15).iterrows():
            md.append(f"{r['context_key']} | {int(r['count'])} | {r['mean']:.3f} | {r['max']:.3f}")
        md.append("")

    md.append("## By database (top 10 by count)")
    md.append("")
    md.append("database | count | mean_sec | max_sec")
    md.append("---|---:|---:|---:")
    for _, r in by_db.reset_index().head(10).iterrows():
        md.append(f"{r['database_name']} | {int(r['count'])} | {r['mean']:.3f} | {r['max']:.3f}")
    md.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    return {"md": md_path, "xlsx": xlsx_path}
