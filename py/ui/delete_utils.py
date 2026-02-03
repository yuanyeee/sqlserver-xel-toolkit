from __future__ import annotations

import os
from typing import Iterable, List

from send2trash import send2trash


def is_within(base: str, path: str) -> bool:
    base = os.path.realpath(base)
    path = os.path.realpath(path)
    try:
        common = os.path.commonpath([base, path])
    except Exception:
        return False
    return common == base


def _normalize_trash_path(p: str) -> str:
    # Make send2trash happier on Windows (avoid mixed slashes / long-path prefix)
    p = str(p)
    if os.name == "nt":
        p = p.replace("/", "\\")
        if p.startswith("\\\\?\\"):
            p = p[4:]
    return os.path.normpath(p)


def trash_paths(paths: Iterable[str]) -> List[str]:
    """Best-effort move to trash.

    Never raises if one path fails (e.g. OneDrive placeholder / already deleted).
    Returns list of successfully trashed paths.
    """
    trashed: List[str] = []
    for p in paths:
        if not p:
            continue
        p2 = _normalize_trash_path(p)
        if not os.path.exists(p2):
            continue
        try:
            send2trash(p2)
            trashed.append(p2)
        except Exception:
            # Keep UI responsive: skip failures.
            # (Caller may have already deleted/renamed the folder, or OneDrive may block it.)
            continue
    return trashed
