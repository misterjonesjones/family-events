import json, re, io, sys, os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import tz
from pypdf import PdfReader

from tagger import infer_tags, infer_flags
from ics import to_ics

HEADERS = {"User-Agent": "FamilyEventsAggregator/1.0 (GitHub Actions)"}

OUT_EVENTS = "events.json"
OUT_META = "events.meta.json"
OUT_ICS_ALL = "docs/all.ics"   # weil deine Website in /docs liegt

@dataclass
class Event:
    title: str
    start: str
    end: Optional[str]
    location: str
    source: str
    center: str
    url: str
    tags: List[str]
    flags: Dict[str, bool]
    description: str = ""

def safe_get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def safe_get_bytes(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content

def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")

def parse_time_range(s: str) -> Tuple[Optional[str], Optional[str]]:
    s = (s or "").replace("Uhr", "").strip().replace("–", "-").replace("—", "-")
    m = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", s)
    if not m:
        return None, None
    return m.group(1), m.group(2)

def dedup(events: List[Event]) -> List[Event]:
    seen = {}
    for e in events:
        key = (e.title.strip().lower(), e.start, e.center.strip().lower())
        seen[key] = e
    return list(seen.values())

# ----------------------------
# Karussell Baden
# ----------------------------
def fetch_karussell(url: str, default_location: str, center: str, tzinfo) -> List[Event]:
    html = safe_get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln for ln in text.split("\n") if ln.strip()]

    date_re = re.compile(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s+(\d{1,2})\.\s+([A-Za-zäöüÄÖÜ]+)\s+(\d{4})")
    time_re = re.compile(r"\b(\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2})\b")

    months = {
        "Januar": 1, "Februar": 2, "März": 3, "April": 4, "Mai": 5, "Juni": 6,
        "Juli": 7, "August": 8, "September": 9, "Oktober": 10, "November": 11, "Dezember": 12
    }

    events: List[Event] = []
    current_date: Optional[datetime] = None

    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        dm = date_re.search(ln)
        if dm:
            day = int(dm.group(2)); month = months.get(dm.group(3)); year = int(dm.group(4))
            if month:
                current_date = datetime(year, month, day, 0, 0, tzinfo=tzinfo)
            i += 1
            continue

        tm = time_re.search(ln)
        if current_date and tm:
            st, et = parse_time_range(tm.group(1))
            title = lines[i+1].strip() if i+1 < len(lines) else ""
            if title and not date_re.search(title) and not time_re.search(title):
                sh, sm = map(int, st.split(":")); eh, em = map(int, et.split(":"))
                start_dt = current_date.replace(hour=sh, minute=sm)
                end_dt = current_date.replace(hour=eh, minute=em)
                tags = infer_tags(title, "")
                flags = infer_flags(title, "")
                events.append(Event(
                    title=title,
                    start=iso(start_dt),
                    end=iso(end_dt),
                    location=default_location,
                    source="Karussell Baden",
                    center=center,
                    url=url,
                    tags=tags,
                    flags=flags,
                ))
                i += 2
                continue
        i += 1
    return events

# ----------------------------
# GZ Zürich (Programmseiten)
# ----------------------------
def fetch_gz_program(url: str, default_location: str, center: str, tzinfo) -> List[Event]:
    html = safe_get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln for ln in text.split("\n") if ln.strip()]

    date_re = re.compile(r"(Mo|Di|Mi|Do|Fr|Sa|So)\.\s+(\d{2})\.(\d{2})\.(\d{4})")
    time_re = re.compile(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})")

    events: List[Event] = []
    i = 0
    while i < len(lines):
        title = lines[i].strip()
        if len(title) < 3:
            i += 1
            continue

        dt = None; date_idx = None
        for j in range(i+1, min(i+10, len(lines))):
            m = date_re.search(lines[j])
            if m:
                dd = int(m.group(2)); mm = int(m.group(3)); yy = int(m.group(4))
                dt = datetime(yy, mm, dd, 0, 0, tzinfo=tzinfo)
                date_idx = j
                break
        if not dt:
            i += 1
            continue

        start_dt = None; end_dt = None; time_idx = None
        for j in range(date_idx+1, min(date_idx+8, len(lines))):
            m = time_re.search(lines[j].replace("Uhr", ""))
            if m:
                sh, sm = map(int, m.group(1).split(":"))
                eh, em = map(int, m.group(2).split(":"))
                start_dt = dt.replace(hour=sh, minute=sm)
                end_dt = dt.replace(hour=eh, minute=em)
                time_idx = j
                break

        if start_dt:
            clean_title = re.sub(r",\s*GZ\s+.*$", "", title).strip()
            desc = ""
            tags = infer_tags(clean_title, desc)
            flags = infer_flags(clean_title, desc)
            events.append(Event(
                title=clean_title,
                start=iso(start_dt),
                end=iso(end_dt) if end_dt else None,
                location=default_location,
                source="GZ Zürich",
                center=center,
                url=url,
                tags=tags,
                flags=flags,
                description=desc
            ))
            i = (time_idx or i) + 1
            continue

        i += 1
    return events

# ----------------------------
# Zentrum ELCH (PDF)
# ----------------------------
def fetch_elch_pdf(url: str, default_location: str, tzinfo, center_keywords: List[str]) -> List[Event]:
    pdf_bytes = safe_get_bytes(url)
    reader = PdfReader(io.BytesIO(pdf_bytes))

    raw_lines: List[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        raw_lines += [ln.strip() for ln in t.splitlines() if ln.strip()]

    dt_re = re.compile(r"^(MO|DI|MI|DO|FR|SA|SO)\s+(\d{2})\.(\d{2})\.(\d{2})\s+(\d{1,2})[.:](\d{2})\s*[–-]\s*(\d{1,2})[.:](\d{2})")

    events: List[Event] = []
    for idx, ln in enumerate(raw_lines):
        m = dt_re.match(ln.replace("–", "-"))
        if not m:
            continue
        dd = int(m.group(2)); mm = int(m.group(3)); yy = 2000 + int(m.group(4))
        sh = int(m.group(5)); sm = int(m.group(6))
        eh = int(m.group(7)); em = int(m.group(8))

        title = raw_lines[idx + 1].strip() if idx + 1 < len(raw_lines) else ""
        if not title or dt_re.match(title):
            continue

        window = " ".join(raw_lines[max(0, idx-2): min(len(raw_lines), idx+5)])
        inferred_center = "Zentrum ELCH"
        for kw in center_keywords:
            if kw.lower() in window.lower() or kw.lower() in title.lower():
                inferred_center = f"ELCH {kw}"
                break

        start_dt = datetime(yy, mm, dd, sh, sm, tzinfo=tzinfo)
        end_dt = datetime(yy, mm, dd, eh, em, tzinfo=tzinfo)

        tags = infer_tags(title, window)
        flags = infer_flags(title, window)

        events.append(Event(
            title=title,
            start=iso(start_dt),
            end=iso(end_dt),
            location=default_location,
            source="Zentrum ELCH",
            center=inferred_center,
            url=url,
            tags=tags,
            flags=flags,
            description=""
        ))

    return events

def load_prev_meta() -> Dict:
    try:
        with open(OUT_META, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}}

def main():
    with open("scripts/sources.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    tzinfo = tz.gettz(cfg.get("timezone", "Europe/Zurich"))
    center_keywords = cfg.get("elch_centers", [])

    all_events: List[Event] = []
    for src in cfg["sources"]:
        t = src["type"]
        try:
            if t == "karussell_jahresprogramm":
                all_events += fetch_karussell(src["url"], src["default_location"], src["center"], tzinfo)
            elif t == "gz_programm":
                all_events += fetch_gz_program(src["url"], src["default_location"], src["center"], tzinfo)
            elif t == "elch_pdf":
                all_events += fetch_elch_pdf(src["url"], src["default_location"], tzinfo, center_keywords)
            else:
                print("Unknown source type:", t)
        except Exception as e:
            print(f"Source failed ({src['id']}): {e}", file=sys.stderr)

    all_events = dedup(all_events)
    all_events.sort(key=lambda e: e.start)

    prev = load_prev_meta()
    seen = prev.get("seen", {})
    now_iso = datetime.now(tzinfo).isoformat(timespec="seconds")

    out_list: List[Dict] = []
    new_count = 0
    for e in all_events:
        key = f"{e.title}|{e.start}|{e.center}"
        is_new = key not in seen
        if is_new:
            new_count += 1
        seen[key] = now_iso
        d = asdict(e)
        d["is_new"] = is_new
        out_list.append(d)

    with open(OUT_EVENTS, "w", encoding="utf-8") as f:
        json.dump(out_list, f, ensure_ascii=False, indent=2)

    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump({"updated_at": now_iso, "seen": seen}, f, ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(OUT_ICS_ALL) or ".", exist_ok=True)
    with open(OUT_ICS_ALL, "w", encoding="utf-8") as f:
        f.write(to_ics(out_list))

    print(f"Wrote {len(out_list)} events. New since last run: {new_count}")

if __name__ == "__main__":
    main()
