#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import json
from datetime import datetime

from toolkit.deadlock_report import generate_deadlock_report_from_jsonl
from toolkit.slowquery_report import generate_slowquery_reports_from_jsonl
from toolkit.blocking_report import generate_blocking_reports_from_jsonl
from toolkit.timeutil import parse_jst_minute, parse_iso, to_jst
from toolkit.xel_jsonl import iter_events
from toolkit.timeutil import in_range


def _span_for_jsonl(jsonl_path: str, *, start_jst=None, end_jst=None, ranges=None):
    """Return (min_str, max_str) in JST minute format for events that pass filters."""
    dt_min = None
    dt_max = None
    ranges = ranges or []

    for ev in iter_events(jsonl_path, event_name=None):
        dt = parse_iso(ev.timestamp or "")
        if dt is None:
            continue

        if not in_range(dt, start_jst, end_jst):
            continue

        if ranges:
            from toolkit.ranges import in_any_range

            if not in_any_range(dt, ranges):
                continue

        jst = to_jst(dt)
        if dt_min is None or jst < dt_min:
            dt_min = jst
        if dt_max is None or jst > dt_max:
            dt_max = jst

    if dt_min is None or dt_max is None:
        return None, None

    return dt_min.strftime("%Y-%m-%d %H:%M"), dt_max.strftime("%Y-%m-%d %H:%M")


def _write_meta_for_outputs(outputs: list[str], *, type_: str, event_time_min: str | None, event_time_max: str | None, ranges_path: str | None, start: str | None, end: str | None):
    meta = {
        "type": type_,
        "event_time_min": event_time_min,
        "event_time_max": event_time_max,
        "ranges_json": ranges_path,
        "start": start,
        "end": end,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for p in outputs:
        try:
            mp = p + ".meta.json"
            with open(mp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="Generate reports from XEL JSONL exports")
    ap.add_argument("--blocking-jsonl", help="JSONL exported from blocked_process_report")
    ap.add_argument("--deadlock-jsonl", help="JSONL exported from xml_deadlock_report")
    ap.add_argument("--slowquery-jsonl", help="JSONL exported from rpc_completed/sql_batch_completed")
    ap.add_argument("--out", default="reports", help="Output directory")
    ap.add_argument("--source-xel", required=True, help="Source XEL filename (for report headers)")
    ap.add_argument("--prefix", required=True, help="Output filename prefix (base)")
    ap.add_argument("--start", help="JST start time (YYYY-mm-dd HH:MM)")
    ap.add_argument("--end", help="JST end time (YYYY-mm-dd HH:MM)")
    ap.add_argument("--ranges-json", help="Optional ranges.json path (OR filter)")
    ap.add_argument("--slow-threshold", type=float, default=3.0, help="Slow query threshold seconds (default: 3.0)")

    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    start_jst = parse_jst_minute(args.start) if getattr(args, 'start', None) else None
    end_jst = parse_jst_minute(args.end) if getattr(args, 'end', None) else None

    ranges = []
    ranges_path = getattr(args, 'ranges_json', None) or os.environ.get("XEL_TOOLKIT_RANGES_JSON")
    if ranges_path:
        from toolkit.ranges import load_ranges_json

        ranges = load_ranges_json(ranges_path)

    outputs = []

    if args.blocking_jsonl:
        out = generate_blocking_reports_from_jsonl(
            args.blocking_jsonl,
            out_dir=args.out,
            source_xel=args.source_xel,
            prefix_base=args.prefix,
            start_jst=start_jst,
            end_jst=end_jst,
            ranges=ranges,
        )
        block_outputs = list(out.values())
        outputs.extend(block_outputs)
        mn, mx = _span_for_jsonl(args.blocking_jsonl, start_jst=start_jst, end_jst=end_jst, ranges=ranges)
        _write_meta_for_outputs(block_outputs, type_="blocking", event_time_min=mn, event_time_max=mx, ranges_path=ranges_path, start=args.start, end=args.end)

    if args.deadlock_jsonl:
        out = generate_deadlock_report_from_jsonl(
            args.deadlock_jsonl,
            out_dir=args.out,
            source_xel=args.source_xel,
            prefix_base=args.prefix,
            start_jst=start_jst,
            end_jst=end_jst,
            ranges=ranges,
        )
        dead_outputs = list(out.values())
        outputs.extend(dead_outputs)
        mn, mx = _span_for_jsonl(args.deadlock_jsonl, start_jst=start_jst, end_jst=end_jst, ranges=ranges)
        _write_meta_for_outputs(dead_outputs, type_="deadlock", event_time_min=mn, event_time_max=mx, ranges_path=ranges_path, start=args.start, end=args.end)

    if args.slowquery_jsonl:
        out = generate_slowquery_reports_from_jsonl(
            args.slowquery_jsonl,
            out_dir=args.out,
            source_xel=args.source_xel,
            prefix_base=args.prefix,
            threshold_sec=args.slow_threshold,
            start_jst=start_jst,
            end_jst=end_jst,
            ranges=ranges,
        )
        slow_outputs = list(out.values())
        outputs.extend(slow_outputs)
        mn, mx = _span_for_jsonl(args.slowquery_jsonl, start_jst=start_jst, end_jst=end_jst, ranges=ranges)
        _write_meta_for_outputs(slow_outputs, type_="slowquery", event_time_min=mn, event_time_max=mx, ranges_path=ranges_path, start=args.start, end=args.end)

    if outputs:
        print("Generated:")
        for p in outputs:
            print("-", p)


if __name__ == "__main__":
    main()
