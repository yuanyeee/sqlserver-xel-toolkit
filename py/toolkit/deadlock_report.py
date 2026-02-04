from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .xel_jsonl import iter_events
from .timeutil import in_range, merge_range, parse_iso, range_tag, to_jst
from .utils import clean_excel_text


@dataclass
class DeadlockItem:
    timestamp: Optional[str]
    victim_spid: Optional[str]
    processes: List[Dict[str, str]]
    objects: List[str]


def _parse_deadlock_xml(xml_text: str) -> DeadlockItem:
    # SQL Server deadlock XML is not namespaced typically.
    root = ET.fromstring(xml_text)

    # victimProcess id points to process id; extract spid from that process.
    victim_pid = None
    vp = root.find("./victim-list/victimProcess")
    if vp is not None:
        victim_pid = vp.attrib.get("id")

    processes: List[Dict[str, str]] = []
    pid_to_spid: Dict[str, str] = {}

    for p in root.findall("./process-list/process"):
        attrib = dict(p.attrib)
        pid = attrib.get("id")
        spid = attrib.get("spid")
        if pid and spid:
            pid_to_spid[pid] = spid

        # Pull inputbuf text if present
        inputbuf_el = p.find("./inputbuf")
        if inputbuf_el is not None and inputbuf_el.text:
            attrib["inputbuf"] = inputbuf_el.text.strip()

        processes.append(attrib)

    victim_spid = pid_to_spid.get(victim_pid) if victim_pid else None

    # Objects/tables involved: pull objectname from resource-list (keylock/objectlock/pagelock etc)
    objects: List[str] = []
    for res in root.findall("./resource-list/*"):
        obj = res.attrib.get("objectname")
        if obj:
            objects.append(obj)

    return DeadlockItem(
        timestamp=None,
        victim_spid=victim_spid,
        processes=processes,
        objects=objects,
    )


def generate_deadlock_report_from_jsonl(
    jsonl_path: str,
    *,
    out_dir: str,
    source_xel: str,
    prefix_base: str,
    start_jst=None,
    end_jst=None,
    ranges=None,
) -> Dict[str, str]:
    """Generate a Markdown report from exported xml_deadlock_report events.

    Filtering and filename tag are based on XEL event timestamps converted to JST.
    """

    items: List[DeadlockItem] = []
    r = (None, None)

    for ev in iter_events(jsonl_path, event_name="xml_deadlock_report"):
        xml_report = ev.fields.get("xml_report")
        if not xml_report:
            continue

        dt = parse_iso(ev.timestamp or "")
        if dt is not None:
            if not in_range(dt, start_jst, end_jst):
                continue
            if ranges:
                from .ranges import in_any_range

                if not in_any_range(dt, ranges):
                    continue
            r = merge_range(r, dt)

        try:
            item = _parse_deadlock_xml(str(xml_report))
            item.timestamp = ev.timestamp
            items.append(item)
        except ET.ParseError:
            # Skip malformed XML
            continue

    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lo, hi = r
    tag = range_tag(lo, hi)
    prefix = f"{prefix_base}_{tag}"

    # Summary counts
    by_object = Counter()
    by_host = Counter()
    by_login = Counter()
    by_isolation = Counter()
    dtc_count = 0

    for it in items:
        for obj in it.objects:
            by_object[obj] += 1

        for p in it.processes:
            host = p.get("hostname")
            login = p.get("loginname")
            iso = p.get("isolationlevel")
            tran = p.get("transactionname")
            if host:
                by_host[host] += 1
            if login:
                by_login[login] += 1
            if iso:
                by_isolation[iso] += 1
            if tran and tran.lower() == "dtcxact":
                dtc_count += 1

    # Build markdown
    lines: List[str] = []
    lines.append("# Deadlock Report (from XEL)")
    lines.append("")
    lines.append(f"- Created: {created}")
    lines.append(f"- Source XEL: `{source_xel}`")
    lines.append(f"- Events: {len(items)} (xml_deadlock_report)")
    lines.append("")

    lines.append("## Quick findings")
    lines.append("")
    if dtc_count:
        lines.append(f"- DTCXact observed (process occurrences): **{dtc_count}**")
    if by_object:
        top_obj, top_cnt = by_object.most_common(1)[0]
        lines.append(f"- Most involved object: **{top_obj}** ({top_cnt})")
    lines.append("")

    def table(title: str, counter: Counter, limit: int = 15):
        lines.append(f"## {title}")
        lines.append("")
        if not counter:
            lines.append("(none)")
            lines.append("")
            return
        lines.append("Item | Count")
        lines.append("---|---:")
        for k, v in counter.most_common(limit):
            lines.append(f"{k} | {v}")
        lines.append("")

    table("Objects (top)", by_object)
    table("Hostnames (top)", by_host)
    table("Login names (top)", by_login)
    table("Isolation levels (top)", by_isolation)

    # Details (first N)
    lines.append("## Details (first 20)")
    lines.append("")
    for i, it in enumerate(items[:20], 1):
        lines.append(f"### Deadlock #{i}")
        lines.append("")
        if it.timestamp:
            lines.append(f"- Timestamp: {it.timestamp}")
        if it.victim_spid:
            lines.append(f"- Victim SPID: {it.victim_spid}")
        if it.objects:
            lines.append(f"- Objects: {', '.join(sorted(set(it.objects)))}")
        lines.append("")
        # Include inputbuf of victim if possible
        victim_sql = None
        if it.victim_spid:
            for p in it.processes:
                if p.get("spid") == it.victim_spid and p.get("inputbuf"):
                    victim_sql = p.get("inputbuf")
                    break
        if victim_sql:
            lines.append("Victim inputbuf:")
            lines.append("```sql")
            lines.append(victim_sql.strip())
            lines.append("```")
            lines.append("")

    md_path = os.path.join(out_dir, f"{prefix}_deadlock_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Excel summary
    rows: List[Dict[str, Any]] = []
    for it in items:
        dt = parse_iso(it.timestamp or "") if it.timestamp else None
        jst = to_jst(dt) if dt is not None else None

        victim = None
        offenders: List[str] = []
        for p in it.processes:
            spid = p.get("spid")
            if it.victim_spid and spid == it.victim_spid:
                victim = p
            elif spid:
                offenders.append(spid)

        victim_sql = None
        if victim and victim.get("inputbuf"):
            victim_sql = victim.get("inputbuf")

        rows.append(
            {
                "timestamp_jst": jst.strftime("%Y-%m-%d %H:%M:%S") if jst else (it.timestamp or ""),
                "victim_spid": it.victim_spid or "",
                "offender_spids": ",".join(sorted(set(offenders))),
                "hostname": (victim or {}).get("hostname", ""),
                "loginname": (victim or {}).get("loginname", ""),
                "isolationlevel": (victim or {}).get("isolationlevel", ""),
                "transactionname": (victim or {}).get("transactionname", ""),
                "objects": ";".join(sorted(set(it.objects))),
                "victim_sql": clean_excel_text(victim_sql or ""),
                "source_xel": source_xel,
            }
        )

    df = pd.DataFrame(rows)

    xlsx_path = os.path.join(out_dir, f"{prefix}_deadlock.xlsx")
    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="deadlocks")
            if by_object:
                obj_df = pd.DataFrame([{ "object": k, "count": v } for k, v in by_object.most_common()])
                obj_df.to_excel(w, index=False, sheet_name="objects")
    except Exception:
        # If Excel generation fails for some reason, still return markdown.
        xlsx_path = ""

    out: Dict[str, str] = {"md": md_path}
    if xlsx_path and os.path.exists(xlsx_path):
        out["xlsx"] = xlsx_path
    return out
