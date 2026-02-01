from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None


def parse_iso(ts: str) -> Optional[datetime]:
    """Parse ISO timestamps from XELite JSON.

    Note: some timestamps include 7+ fractional second digits (e.g. ...7454758+00:00),
    which Python's datetime.fromisoformat (py3.9) cannot parse.
    We truncate to microseconds.
    """
    if not ts:
        return None

    s = ts.strip()

    # Normalize trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    # Truncate fractional seconds to 6 digits if needed.
    # 2026-01-05T05:22:22.7454758+00:00 -> 2026-01-05T05:22:22.745475+00:00
    import re

    m = re.match(r"^(.*?)(\.\d+)([+-]\d\d:\d\d)$", s)
    if m:
        head, frac, tz = m.group(1), m.group(2), m.group(3)
        digits = frac[1:]
        if len(digits) > 6:
            digits = digits[:6]
        s = f"{head}.{digits}{tz}"

    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def to_jst(dt: datetime) -> datetime:
    if JST is None:
        return dt
    if dt.tzinfo is None:
        # assume UTC if naive
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(JST)
    return dt.astimezone(JST)


def fmt_jst_minute(dt: datetime) -> str:
    # JSTYYYYmmdd_HHMM
    d = to_jst(dt)
    return d.strftime("JST%Y%m%d_%H%M")


def parse_jst_minute(s: str) -> Optional[datetime]:
    """Parse 'YYYY-mm-dd HH:MM' or 'YYYY/mm/dd HH:MM' as JST."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if JST is None:
                return dt
            return dt.replace(tzinfo=JST)
        except Exception:
            continue
    return None


def in_range(dt: datetime, start: Optional[datetime], end: Optional[datetime]) -> bool:
    d = to_jst(dt)
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def merge_range(r: Tuple[Optional[datetime], Optional[datetime]], dt: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
    lo, hi = r
    d = to_jst(dt)
    if lo is None or d < lo:
        lo = d
    if hi is None or d > hi:
        hi = d
    return lo, hi


def range_tag(lo: Optional[datetime], hi: Optional[datetime]) -> str:
    if not lo or not hi:
        return "JSTunknown"
    return f"{fmt_jst_minute(lo)}-{fmt_jst_minute(hi)}"
