#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd


def _safe_name(s: str) -> str:
    s = str(s)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or "item"


def _guess_time_col(cols) -> Optional[str]:
    candidates = [
        "event_time",
        "timestamp",
        "StartTime",
        "start_time",
        "time",
        "Time",
        "INSTANT",
        "instant",
    ]
    for c in candidates:
        if c in cols:
            return c
    return None


def _parse_dt(x: Any) -> Optional[datetime]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        return pd.to_datetime(x, errors="coerce").to_pydatetime()
    except Exception:
        return None


def _write_md(path: str, title: str, row: Dict[str, Any]) -> None:
    lines = [f"# {title}", ""]
    for k, v in row.items():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        lines.append(f"## {k}")
        lines.append("")
        lines.append("```")
        lines.append(str(v))
        lines.append("```")
        lines.append("")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Convert CSV/Excel rows to per-row Markdown files")
    ap.add_argument("--in", dest="inp", required=True, help="Input .csv/.xlsx")
    ap.add_argument("--out", dest="out", required=True, help="Output folder (md files will be created inside)")
    ap.add_argument("--sheet", help="Excel sheet name (optional)")
    ap.add_argument("--limit", type=int, default=0, help="Limit rows (0=all)")
    args = ap.parse_args()

    inp = os.path.expanduser(args.inp)
    out = os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)

    if inp.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(inp, sheet_name=args.sheet or 0)
    else:
        df = pd.read_csv(inp)

    df = df.copy()
    time_col = _guess_time_col(df.columns)

    if args.limit and args.limit > 0:
        df = df.head(args.limit)

    for i, (_, r) in enumerate(df.iterrows(), 1):
        row = {c: (None if pd.isna(r.get(c)) else r.get(c)) for c in df.columns}

        date_part = "unknown"
        if time_col:
            dt = _parse_dt(row.get(time_col))
            if dt:
                date_part = dt.strftime("%Y%m%d")

        folder = os.path.join(out, date_part)
        filename = f"{date_part}_row_{i}.md"
        title = f"{os.path.basename(inp)} row {i}"
        _write_md(os.path.join(folder, filename), title, row)

    # lightweight index
    index_path = os.path.join(out, "index.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(f"# MD index for {os.path.basename(inp)}\n\n")
        f.write(f"Generated at: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write(f"Rows: {len(df)}\n")


if __name__ == "__main__":
    main()
