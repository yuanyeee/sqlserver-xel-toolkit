#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from toolkit.xel_jsonl import iter_events
from toolkit.timeutil import in_range, parse_iso, to_jst


def _safe_name(name: str) -> str:
    s = os.path.basename(str(name))
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or "input"


def _fmt_value(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return repr(v)


def _write_md(path: str, kv: List[tuple[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in kv:
            f.write(f"# {k}\n\n")
            f.write("```\n")
            f.write(_fmt_value(v))
            f.write("\n```\n\n")


def _write_blocking_md(path: str, *, source: str, timestamp: str, actions: dict, fields: dict) -> None:
    """Write IntegratedTool-friendly blocking markdown.

    Keep legacy '# key' codeblock sections at the bottom for backward compatibility.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    xml = str(fields.get('blocked_process') or '')

    def rx(pat: str) -> str:
        m = re.search(pat, xml, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ''

    victim_sql = rx(r'<blocked-process>.*?<inputbuf>\s*(.+?)\s*</inputbuf>')
    blocker_sql = rx(r'<blocking-process>.*?<inputbuf>\s*(.+?)\s*</inputbuf>')
    victim_spid = rx(r"<blocked-process[^>]*spid=['\"](\d+)['\"]")
    blocker_spid = rx(r"<blocking-process[^>]*spid=['\"](\d+)['\"]")

    out: List[str] = []
    def w(s: str = ""):
        out.append(s)

    w('# Blocking (from XEL)')
    w('')
    w(f'**Timestamp:** {timestamp}')
    w(f'**Source:** {source}')
    w(f'**Event:** blocked_process_report')
    w('')

    w('## Context')
    w('')
    for k, label in [
        ('database_name','Database'),
        ('client_app_name','Client App'),
        ('client_hostname','Client Host'),
        ('username','Username'),
        ('session_id','Session ID'),
    ]:
        v = actions.get(k) or fields.get(k)
        if v:
            w(f'**{label}:** {v}')
    w('')

    w('## Metrics')
    w('')
    for k, label in [
        ('duration','Duration'),
        ('cpu_time','CPU Time'),
        ('logical_reads','Logical Reads'),
        ('physical_reads','Physical Reads'),
        ('writes','Writes'),
        ('row_count','Row Count'),
    ]:
        v = fields.get(k)
        if v is not None and v != '':
            w(f'**{label}:** {v}')
    w('')

    w('## Victim (blocked)')
    w('')
    if victim_spid:
        w(f'**SPID:** {victim_spid}')
    if victim_sql:
        w('```sql')
        w(victim_sql)
        w('```')
    w('')

    w('## Blocker (blocking)')
    w('')
    if blocker_spid:
        w(f'**SPID:** {blocker_spid}')
    if blocker_sql:
        w('```sql')
        w(blocker_sql)
        w('```')
    w('')

    if xml:
        w('## XML')
        w('')
        w('```xml')
        w(xml.strip())
        w('```')
        w('')

    # Legacy KV blocks (used by older IntegratedTool parsers)
    w('---')
    w('')
    w('# timestamp')
    w('')
    w('```')
    w(timestamp)
    w('```')
    w('')
    if 'duration' in fields:
        w('# duration')
        w('')
        w('```')
        w(str(fields.get('duration') or ''))
        w('```')
        w('')
    if actions.get('database_name'):
        w('# database_name')
        w('')
        w('```')
        w(str(actions.get('database_name') or ''))
        w('```')
        w('')
    if xml:
        w('# blocked_process')
        w('')
        w('```')
        w(xml.strip())
        w('```')
        w('')

    with open(path, 'w', encoding='utf-8') as f:
        f.write("\n".join(out))


def _write_slowquery_md(path: str, *, source: str, timestamp: str, event_name: str, actions: dict, fields: dict) -> None:
    """Write IntegratedTool-friendly slowquery markdown.

    Keep legacy '# key' codeblock sections at the bottom for backward compatibility.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    sql = actions.get('sql_text') or fields.get('statement') or fields.get('batch_text') or ''

    out: List[str] = []
    def w(s: str = ""):
        out.append(s)

    w('# SlowQuery (from XEL)')
    w('')
    w(f'**Timestamp:** {timestamp}')
    w(f'**Source:** {source}')
    w(f'**Event:** {event_name}')
    w('')

    w('## Context')
    w('')
    for k, label in [
        ('database_name','Database'),
        ('client_app_name','Client App'),
        ('client_hostname','Client Host'),
        ('username','Username'),
        ('session_id','Session ID'),
    ]:
        v = actions.get(k) or fields.get(k)
        if v:
            w(f'**{label}:** {v}')
    w('')

    w('## Metrics')
    w('')
    for k, label in [
        ('duration','Duration'),
        ('cpu_time','CPU Time'),
        ('logical_reads','Logical Reads'),
        ('physical_reads','Physical Reads'),
        ('writes','Writes'),
        ('row_count','Row Count'),
    ]:
        v = fields.get(k)
        if v is not None and v != '':
            w(f'**{label}:** {v}')
    w('')

    if sql:
        w('## SQL')
        w('')
        w('```sql')
        w(str(sql).strip())
        w('```')
        w('')

    # Legacy KV blocks
    w('---')
    w('')
    w('# timestamp')
    w('')
    w('```')
    w(timestamp)
    w('```')
    w('')
    if actions.get('database_name'):
        w('# database_name')
        w('')
        w('```')
        w(str(actions.get('database_name') or ''))
        w('```')
        w('')
    if fields.get('duration') is not None:
        w('# duration')
        w('')
        w('```')
        w(str(fields.get('duration') or ''))
        w('```')
        w('')
    if actions.get('sql_text'):
        w('# sql_text')
        w('')
        w('```')
        w(str(actions.get('sql_text') or '').strip())
        w('```')
        w('')
    if fields.get('statement') is not None:
        w('# statement')
        w('')
        w('```')
        w(str(fields.get('statement') or '').strip())
        w('```')
        w('')
    if fields.get('batch_text') is not None:
        w('# batch_text')
        w('')
        w('```')
        w(str(fields.get('batch_text') or '').strip())
        w('```')
        w('')

    with open(path, 'w', encoding='utf-8') as f:
        f.write("\n".join(out))


def _write_deadlock_md(path: str, *, source: str, timestamp: str, xml_report: str) -> None:
    """Write IntegratedTool-compatible deadlock markdown from xml_report."""
    import xml.etree.ElementTree as ET

    os.makedirs(os.path.dirname(path), exist_ok=True)

    victim_pid = None
    victim_spid = None
    processes = []
    resources = []

    try:
        root = ET.fromstring(xml_report)

        vp = root.find("./victim-list/victimProcess")
        if vp is not None:
            victim_pid = vp.attrib.get("id")

        pid_to_spid = {}
        for p in root.findall("./process-list/process"):
            attrib = dict(p.attrib)
            pid = attrib.get("id")
            spid = attrib.get("spid")
            if pid and spid:
                pid_to_spid[pid] = spid

            inputbuf_el = p.find("./inputbuf")
            if inputbuf_el is not None and inputbuf_el.text:
                attrib["inputbuf"] = inputbuf_el.text.strip()

            processes.append(attrib)

        if victim_pid:
            victim_spid = pid_to_spid.get(victim_pid)

        for res in root.findall("./resource-list/*"):
            r = dict(res.attrib)
            r["_type"] = res.tag
            resources.append(r)
    except Exception:
        # fall back to minimal
        pass

    def w(line: str = ""):
        out.append(line)

    out: List[str] = []
    w("# Deadlock (from XEL)")
    w("")
    w(f"**Timestamp:** {timestamp}")
    w(f"**Source:** {source}")
    if victim_pid or victim_spid:
        w(f"**Victim process:** {victim_pid or victim_spid}")
    w("")

    # Processes section (IntegratedTool parser expects these headings)
    if processes:
        w("## Processes")
        w("")
        for i, p in enumerate(processes, 1):
            w(f"### Process {i}")
            w("")
            if p.get("id"):
                w(f"**Process ID:** {p.get('id')}")
            if p.get("spid"):
                w(f"**SPID:** {p.get('spid')}")
            if p.get("status"):
                w(f"**Status:** {p.get('status')}")
            if p.get("isolationlevel"):
                w(f"**Isolation Level:** {p.get('isolationlevel')}")
            if p.get("waitresource"):
                w(f"**Wait Resource:** {p.get('waitresource')}")
            if p.get("logused"):
                w(f"**Log Used:** {p.get('logused')}")
            if p.get("clientapp"):
                w(f"**Client Application:** {p.get('clientapp')}")
            if p.get("loginname"):
                w(f"**Login Name:** {p.get('loginname')}")
            if p.get("hostname"):
                w(f"**Hostname:** {p.get('hostname')}")
            if p.get("transactionname"):
                w(f"**Transaction Name:** {p.get('transactionname')}")
            if p.get("ownerid"):
                w(f"**Owner ID:** {p.get('ownerid')}")
            w("")
            if p.get("inputbuf"):
                w("```sql")
                w(str(p.get("inputbuf")).strip())
                w("```")
                w("")

    # Resources section
    if resources:
        w("## Resources")
        w("")
        for i, r in enumerate(resources, 1):
            w(f"### Resource {i}")
            w("")
            if r.get("objectname"):
                w(f"**Object Name:** {r.get('objectname')}")
            if r.get("indexname"):
                w(f"**Index Name:** {r.get('indexname')}")
            if r.get("dbname"):
                w(f"**Database Name:** {r.get('dbname')}")
            if r.get("dbid"):
                w(f"**Database Name:** dbid={r.get('dbid')}")
            if r.get("_type"):
                w(f"**Type:** {r.get('_type')}")
            if r.get("mode"):
                w(f"**Mode:** {r.get('mode')}")
            w("")

    # Raw XML
    w("## XML")
    w("")
    w("```xml")
    w(xml_report.strip())
    w("```")
    w("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


# ======================================================================
# Event type mapping
# ======================================================================

_EVENT_TYPE_MAP: Dict[str, str] = {
    "blocked_process_report": "Blocking",
    "rpc_completed": "SlowQuery",
    "sql_batch_completed": "SlowQuery",
    "xml_deadlock_report": "DeadLock",
}


def _dedup_key(*, timestamp: str, event_name: str, actions: dict, fields: dict) -> str:
    """Generate a deduplication hash from key event fields.

    Returns a 16-char hex string.  Two events with the same dedup key
    are considered identical and the later one is skipped.
    """
    parts = [
        timestamp,
        event_name,
        str(fields.get("duration", "")),
        str(actions.get("database_name", "") or fields.get("database_name", "")),
        str(actions.get("session_id", "") or fields.get("session_id", "")),
    ]
    # For slowquery: include a hash of the SQL text
    sql = actions.get("sql_text") or fields.get("statement") or fields.get("batch_text") or ""
    if sql:
        parts.append(hashlib.sha256(str(sql).encode("utf-8", errors="ignore")).hexdigest()[:16])
    # For blocking: include blocked_process hash
    bp = fields.get("blocked_process", "")
    if bp:
        parts.append(hashlib.sha256(str(bp).encode("utf-8", errors="ignore")).hexdigest()[:16])
    # For deadlock: include xml_report hash
    xr = fields.get("xml_report", "")
    if xr:
        parts.append(hashlib.sha256(str(xr).encode("utf-8", errors="ignore")).hexdigest()[:16])

    combined = "|".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def _existing_dedup_keys(folder: str) -> set:
    """Collect dedup keys already present in the target folder.

    File naming convention: ``{EventType}_{HHMMSS}_{dedupKey}.md``
    """
    keys: set = set()
    if not os.path.isdir(folder):
        return keys
    for fn in os.listdir(folder):
        if fn.endswith(".md"):
            # Extract dedup key (last 16 hex chars before .md)
            stem = fn[:-3]  # remove .md
            parts = stem.rsplit("_", 1)
            if len(parts) == 2 and len(parts[1]) == 16:
                keys.add(parts[1])
    return keys


def main():
    ap = argparse.ArgumentParser(description="Convert XEL JSONL exports to per-event Markdown files")
    ap.add_argument("--jsonl", required=True, help="Input JSONL (from XelDump --export-jsonl)")
    ap.add_argument("--out", required=True, help="Output root folder (inputMD root)")
    ap.add_argument("--key", required=True, help="Unique key (short hash) for this import batch")
    ap.add_argument("--event", required=True, help="Event name (e.g. blocked_process_report)")
    ap.add_argument("--event-type", dest="event_type", default="",
                    help="Event type folder name (Blocking/SlowQuery/DeadLock). Auto-detected from --event if omitted.")
    ap.add_argument("--source", required=True, help="Source XEL file name (for metadata)")
    ap.add_argument("--start", help='JST start time "YYYY-mm-dd HH:MM" (optional)')
    ap.add_argument("--end", help='JST end time "YYYY-mm-dd HH:MM" (optional)')
    ap.add_argument("--ranges-json", help="Optional ranges.json path (OR filter)")
    ap.add_argument("--limit", type=int, default=0, help="Max events (0=all)")

    args = ap.parse_args()

    out_root = os.path.expanduser(args.out)
    key = args.key
    event = args.event
    event_type = args.event_type or _EVENT_TYPE_MAP.get(event, "Other")

    # Parse optional JST range (reuse toolkit.timeutil parser)
    from toolkit.timeutil import parse_jst_minute

    start_jst = parse_jst_minute(args.start) if args.start else None
    end_jst = parse_jst_minute(args.end) if args.end else None

    ranges = []
    ranges_path = args.ranges_json or os.environ.get("XEL_TOOLKIT_RANGES_JSON")
    if ranges_path:
        try:
            from toolkit.ranges import load_ranges_json

            ranges = load_ranges_json(ranges_path)
        except Exception:
            ranges = []

    # Pre-load existing dedup keys per date folder to skip duplicates
    dedup_cache: Dict[str, set] = {}  # date_part -> set of dedup keys

    count = 0
    skipped = 0
    for i, ev in enumerate(iter_events(args.jsonl, event_name=event), 1):
        dt = parse_iso(ev.timestamp or "")
        if dt is not None:
            if not in_range(dt, start_jst, end_jst):
                continue
            if ranges:
                from toolkit.ranges import in_any_range

                if not in_any_range(dt, ranges):
                    continue
            jst = to_jst(dt)
            date_part = jst.strftime("%Y%m%d")
            time_part = jst.strftime("%H%M%S")
            dt_display = jst.strftime("%Y-%m-%d %H:%M:%S%z")
        else:
            date_part = "unknown"
            time_part = "000000"
            dt_display = ev.timestamp or ""

        # Compute dedup key
        dk = _dedup_key(
            timestamp=dt_display,
            event_name=event,
            actions=ev.actions,
            fields=ev.fields,
        )

        # Check for duplicates
        if date_part not in dedup_cache:
            folder = os.path.join(out_root, event_type, date_part)
            dedup_cache[date_part] = _existing_dedup_keys(folder)

        if dk in dedup_cache[date_part]:
            skipped += 1
            continue
        dedup_cache[date_part].add(dk)

        # Build path: {out}/{EventType}/{YYYYMMDD}/{EventType}_{HHMMSS}_{dedup}.md
        folder = os.path.join(out_root, event_type, date_part)
        filename = f"{event_type}_{time_part}_{dk}.md"
        path = os.path.join(folder, filename)

        # Special case: deadlock report -> emit IntegratedTool-compatible markdown
        if event == "xml_deadlock_report" and "xml_report" in ev.fields and ev.fields.get("xml_report"):
            _write_deadlock_md(path, source=args.source, timestamp=dt_display, xml_report=str(ev.fields.get("xml_report")))
            count += 1
            if args.limit and count >= args.limit:
                break
            continue

        # Special case: blocking -> emit IntegratedTool-friendly markdown
        if event == "blocked_process_report" and (ev.fields.get("blocked_process") or "blocked_process" in ev.fields):
            _write_blocking_md(path, source=args.source, timestamp=dt_display, actions=ev.actions, fields=ev.fields)
            count += 1
            if args.limit and count >= args.limit:
                break
            continue

        # Special case: slowquery events -> emit IntegratedTool-friendly markdown
        if event in ("rpc_completed", "sql_batch_completed"):
            _write_slowquery_md(path, source=args.source, timestamp=dt_display, event_name=event, actions=ev.actions, fields=ev.fields)
            count += 1
            if args.limit and count >= args.limit:
                break
            continue

        # Flatten fields/actions
        kv: List[tuple[str, Any]] = []
        kv.append(("source", args.source))
        kv.append(("event", ev.name))
        kv.append(("timestamp", dt_display))

        # Common actions we care about
        for k in ("client_app_name", "client_hostname", "database_name", "username", "session_id", "sql_text"):
            if k in ev.actions:
                kv.append((k, ev.actions.get(k)))

        # Common fields
        for k in ("duration", "cpu_time", "logical_reads", "physical_reads", "writes", "row_count", "lock_mode", "object_id", "index_id", "resource_owner_type"):
            if k in ev.fields:
                kv.append((k, ev.fields.get(k)))

        # Payload-heavy fields last
        if "blocked_process" in ev.fields:
            kv.append(("blocked_process", ev.fields.get("blocked_process")))
        if "xml_report" in ev.fields:
            kv.append(("xml_report", ev.fields.get("xml_report")))
        if "statement" in ev.fields:
            kv.append(("statement", ev.fields.get("statement")))
        if "batch_text" in ev.fields:
            kv.append(("batch_text", ev.fields.get("batch_text")))

        # Add remaining keys (avoid duplicates)
        seen = {k for k, _ in kv}
        for k, v in sorted(ev.actions.items()):
            if k not in seen:
                kv.append((f"action:{k}", v))
        for k, v in sorted(ev.fields.items()):
            if k not in seen:
                kv.append((k, v))

        _write_md(path, kv)
        count += 1
        if args.limit and count >= args.limit:
            break

    print(f"[xel_to_md] {event_type}: {count} files written, {skipped} duplicates skipped")


if __name__ == "__main__":
    main()
