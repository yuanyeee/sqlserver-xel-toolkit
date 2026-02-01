from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None


@dataclass
class TimeRange:
    start: datetime
    end: datetime


def parse_jst_minute(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if JST:
                return dt.replace(tzinfo=JST)
            return dt
        except Exception:
            continue
    return None


def load_ranges_json(path: str) -> List[TimeRange]:
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f) or {}
    except Exception:
        return []

    items = obj.get("ranges", []) if isinstance(obj, dict) else []
    out: List[TimeRange] = []
    if not isinstance(items, list):
        return []
    for it in items:
        if not isinstance(it, dict):
            continue
        s = parse_jst_minute(it.get("start"))
        e = parse_jst_minute(it.get("end"))
        if s and e and s < e:
            out.append(TimeRange(start=s, end=e))
    out.sort(key=lambda r: r.start)
    return out


def in_any_range(dt: datetime, ranges: List[TimeRange]) -> bool:
    if not ranges:
        return True
    if JST:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        else:
            dt = dt.astimezone(JST)
    for r in ranges:
        if r.start <= dt <= r.end:
            return True
    return False
