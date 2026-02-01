#!/usr/bin/env python3

import argparse
import os
from datetime import datetime

from toolkit.deadlock_report import generate_deadlock_report_from_jsonl
from toolkit.slowquery_report import generate_slowquery_reports_from_jsonl
from toolkit.blocking_report import generate_blocking_reports_from_jsonl


def main():
    ap = argparse.ArgumentParser(description="Generate reports from XEL JSONL exports")
    ap.add_argument("--blocking-jsonl", help="JSONL exported from blocked_process_report")
    ap.add_argument("--deadlock-jsonl", help="JSONL exported from xml_deadlock_report")
    ap.add_argument("--slowquery-jsonl", help="JSONL exported from rpc_completed/sql_batch_completed")
    ap.add_argument("--out", default="reports", help="Output directory")
    ap.add_argument("--source-xel", required=True, help="Source XEL filename (for report headers)")
    ap.add_argument("--prefix", required=True, help="Output filename prefix")
    ap.add_argument("--slow-threshold", type=float, default=3.0, help="Slow query threshold seconds (default: 3.0)")

    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    outputs = []

    if args.blocking_jsonl:
        out = generate_blocking_reports_from_jsonl(
            args.blocking_jsonl,
            out_dir=args.out,
            source_xel=args.source_xel,
            prefix=args.prefix,
        )
        outputs.extend(out.values())

    if args.deadlock_jsonl:
        out = generate_deadlock_report_from_jsonl(
            args.deadlock_jsonl,
            out_dir=args.out,
            source_xel=args.source_xel,
            prefix=args.prefix,
        )
        outputs.append(out)

    if args.slowquery_jsonl:
        out = generate_slowquery_reports_from_jsonl(
            args.slowquery_jsonl,
            out_dir=args.out,
            source_xel=args.source_xel,
            prefix=args.prefix,
            threshold_sec=args.slow_threshold,
        )
        outputs.extend(out.values())

    if outputs:
        print("Generated:")
        for p in outputs:
            print("-", p)


if __name__ == "__main__":
    main()
