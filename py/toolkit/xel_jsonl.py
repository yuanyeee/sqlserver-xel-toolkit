import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, Optional


@dataclass
class XelEvent:
    name: str
    timestamp: Optional[str]
    fields: Dict[str, Any]
    actions: Dict[str, Any]


def iter_events(jsonl_path: str, *, event_name: Optional[str] = None) -> Iterator[XelEvent]:
    """Iterate XEL events exported by XelDump (--export-jsonl).

    Each line is JSON with keys: name, timestamp, fields, actions.
    """
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            name = obj.get("name")
            if not name:
                continue
            if event_name and name.lower() != event_name.lower():
                continue
            yield XelEvent(
                name=name,
                timestamp=obj.get("timestamp"),
                fields=obj.get("fields") or {},
                actions=obj.get("actions") or {},
            )
