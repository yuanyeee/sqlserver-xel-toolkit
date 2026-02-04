"""Common utility functions for the toolkit modules."""
from __future__ import annotations

import os
import re
from typing import Any


_ILLEGAL_EXCEL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def clean_excel_text(s: Any) -> Any:
    """Remove characters that openpyxl rejects and normalize line endings."""
    if s is None:
        return None
    try:
        text = str(s)
    except Exception:
        return s
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _ILLEGAL_EXCEL_RE.sub("", text)


def safe_stem(path: str, default: str = "output") -> str:
    """Extract a filesystem-safe stem from path, sanitizing special characters."""
    base = os.path.basename(path)
    stem, _ = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or default
