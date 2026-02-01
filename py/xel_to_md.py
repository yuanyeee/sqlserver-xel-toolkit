#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from toolkit.xel_jsonl import iter_events
from toolkit.timeutil import in_range, parse_iso, to_jst


def _safe_name(name: str) -> str:
    s = os.path.basename(str(name))
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or "input"


def _fmt_value(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return repr(v)


def _write_md(path: str, kv: List[tuple[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in kv:
            f.write(f"# {k}\n\n")
            f.write("```\n")
            f.write(_fmt_value(v))
            f.write("\n```\n\n")


def main():
    ap = argparse.ArgumentParser(description="Convert XEL JSONL exports to per-event Markdown files")
    ap.add_argument("--jsonl", required=True, help="Input JSONL (from XelDump --export-jsonl)")
    ap.add_argument("--out", required=True, help="Output root folder")
    ap.add_argument("--key", required=True, help="Unique key (short hash) for this import batch")
    ap.add_argument("--event", required=True, help="Event name (e.g. blocked_process_report)")
    ap.add_argument("--source", required=True, help="Source XEL file name (for metadata)")
    ap.add_argument("--start", help='JST start time "YYYY-mm-dd HH:MM" (optional)')
    ap.add_argument("--end", help='JST end time "YYYY-mm-dd HH:MM" (optional)')
    ap.add_argument("--ranges-json", help="Optional ranges.json path (OR filter)")
    ap.add_argument("--limit", type=int, default=0, help="Max events (0=all)")

    args = ap.parse_args()

    out_root = os.path.expanduser(args.out)
    key = args.key
    event = args.event

    # Parse optional JST range (reuse toolkit.timeutil parser)
    from toolkit.timeutil import parse_jst_minute

    start_jst = parse_jst_minute(args.start) if args.start else None
    end_jst = parse_jst_minute(args.end) if args.end else None

    ranges = []
    ranges_path = args.ranges_json or os.environ.get("XEL_TOOLKIT_RANGES_JSON")
    if ranges_path:
        try:
            from toolkit.ranges import load_ranges_json

            ranges = load_ranges_json(ranges_path)
        except Exception:
            ranges = []

    count = 0
    for i, ev in enumerate(iter_events(args.jsonl, event_name=event), 1):
        dt = parse_iso(ev.timestamp or "")
        if dt is not None:
            if not in_range(dt, start_jst, end_jst):
                continue
            if ranges:
                from toolkit.ranges import in_any_range

                if not in_any_range(dt, ranges):
                    continue
            jst = to_jst(dt)
            date_part = jst.strftime("%Y%m%d")
            dt_display = jst.strftime("%Y-%m-%d %H:%M:%S%z")
        else:
            date_part = "unknown"
            dt_display = ev.timestamp or ""

        folder = os.path.join(out_root, date_part)
        filename = f"{date_part}_{key}_{event}_{i}.md"
        path = os.path.join(folder, filename)

        # Flatten fields/actions
        kv: List[tuple[str, Any]] = []
        kv.append(("source", args.source))
        kv.append(("event", ev.name))
        kv.append(("timestamp", dt_display))

        # Common actions we care about
        for k in ("client_app_name", "client_hostname", "database_name", "username", "session_id", "sql_text"):
            if k in ev.actions:
                kv.append((k, ev.actions.get(k)))

        # Common fields
        for k in ("duration", "cpu_time", "logical_reads", "physical_reads", "writes", "row_count", "lock_mode", "object_id", "index_id", "resource_owner_type"):
            if k in ev.fields:
                kv.append((k, ev.fields.get(k)))

        # Payload-heavy fields last
        if "blocked_process" in ev.fields:
            kv.append(("blocked_process", ev.fields.get("blocked_process")))
        if "xml_report" in ev.fields:
            kv.append(("xml_report", ev.fields.get("xml_report")))
        if "statement" in ev.fields:
            kv.append(("statement", ev.fields.get("statement")))
        if "batch_text" in ev.fields:
            kv.append(("batch_text", ev.fields.get("batch_text")))

        # Add remaining keys (avoid duplicates)
        seen = {k for k, _ in kv}
        for k, v in sorted(ev.actions.items()):
            if k not in seen:
                kv.append((f"action:{k}", v))
        for k, v in sorted(ev.fields.items()):
            if k not in seen:
                kv.append((k, v))

        _write_md(path, kv)
        count += 1
        if args.limit and count >= args.limit:
            break

    # index
    os.makedirs(out_root, exist_ok=True)
    with open(os.path.join(out_root, f"index_{key}_{event}.md"), "w", encoding="utf-8") as f:
        f.write(f"# XEL MD index\n\n")
        f.write(f"Event: {event}\n\n")
        f.write(f"Key: {key}\n\n")
        f.write(f"Generated at: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write(f"Count: {count}\n")


if __name__ == "__main__":
    main()
