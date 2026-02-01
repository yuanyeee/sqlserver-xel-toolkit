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


def trash_paths(paths: Iterable[str]) -> List[str]:
    trashed = []
    for p in paths:
        if not p:
            continue
        if os.path.exists(p):
            send2trash(p)
            trashed.append(p)
    return trashed
