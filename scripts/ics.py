from datetime import datetime
from typing import Dict, List, Optional
import hashlib

def esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

def uid_for(event: Dict) -> str:
    base = f"{event.get('title','')}|{event.get('start','')}|{event.get('url','')}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest() + "@family-events"

def to_ics(events: List[Dict], prodid: str = "-//Family Events//CH//DE") -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for e in events:
        start = e.get("start")
        if not start:
            continue
        dtstart = datetime.fromisoformat(start).strftime("%Y%m%dT%H%M%S")
        dtend = None
        if e.get("end"):
            dtend = datetime.fromisoformat(e["end"]).strftime("%Y%m%dT%H%M%S")

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid_for(e)}",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{dtstart}",
        ]
        if dtend:
            lines.append(f"DTEND:{dtend}")
        lines.append(f"SUMMARY:{esc(e.get('title',''))}")
        lines.append(f"LOCATION:{esc(e.get('location',''))}")
        desc_parts = []
        if e.get("source"):
            desc_parts.append(f"Quelle: {e['source']}")
        if e.get("center"):
            desc_parts.append(f"Zentrum: {e['center']}")
        if e.get("url"):
            desc_parts.append(f"Link: {e['url']}")
        lines.append(f"DESCRIPTION:{esc(' | '.join(desc_parts))}")
        if e.get("url"):
            lines.append(f"URL:{esc(e['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"