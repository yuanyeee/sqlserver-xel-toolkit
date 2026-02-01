from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


@dataclass
class ParsedContext:
    raw: str
    key: str
    module: Optional[str] = None
    function: Optional[str] = None
    process: Optional[str] = None


def extract_comment_blocks(sql: str) -> List[str]:
    """Return all /* ... */ blocks (without the /* */) preserving inner text."""
    if not sql:
        return []
    blocks = []
    for m in _COMMENT_RE.finditer(sql):
        s = m.group(0)
        s = s[2:-2]  # strip /* */
        s = s.strip()
        if s:
            blocks.append(s)
    return blocks


def parse_outsystems_context(block: str) -> Optional[ParsedContext]:
    """Parse a comment block like:

      outsystemsのモジュール名.関数名.処理名

    We keep it tolerant: ignore leading/trailing junk, and split by '.' into up to 3 parts.
    """
    if not block:
        return None

    b = block.strip()

    # Find a likely token: allow japanese text around, but extract the dot-separated identifier chunk.
    # Examples:
    #   outsystems.Module.Func.Process
    #   outsystemsのモジュール名.関数名.処理名
    # We'll just take the whole block and split on '.'; if <2 parts it's not useful.
    parts = [p.strip() for p in b.split('.') if p.strip()]
    if len(parts) < 2:
        return None

    module = parts[0]
    function = parts[1] if len(parts) >= 2 else None
    process = parts[2] if len(parts) >= 3 else None

    key = ".".join([p for p in (module, function, process) if p])
    return ParsedContext(raw=b, key=key, module=module, function=function, process=process)


def pick_context(sql: str) -> Optional[ParsedContext]:
    """Pick best context from SQL text.

    Strategy:
    - extract all comment blocks
    - prefer ones that look like outsystems context (dot-separated)
    - otherwise return first comment as raw-only key
    """
    blocks = extract_comment_blocks(sql)
    if not blocks:
        return None

    for b in blocks:
        ctx = parse_outsystems_context(b)
        if ctx:
            return ctx

    # fallback: first comment
    raw = blocks[0]
    return ParsedContext(raw=raw, key=raw)
