import json, re, sys, os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import tz

from tagger import infer_tags, infer_flags
from ics import to_ics

HEADERS = {"User-Agent": "FamilyEventsAggregator/1.0 (GitHub Actions)"}

# GitHub Pages served from /docs
OUT_EVENTS_DOCS = "docs/events.json"
OUT_META_DOCS = "docs/events.meta.json"
OUT_ICS_DOCS = "docs/all.ics"

# optional (debug)
OUT_EVENTS_ROOT = "events.json"
OUT_META_ROOT = "events.meta.json"

@dataclass
class Event:
    title: str
    start: Optional[str]          # ISO or null (for recurring/offers)
    end: Optional[str]            # ISO or null
    location: str
    source: str
    center: str
    url: str
    tags: List[str]
    flags: Dict[str, bool]
    kind: str                     # "dated" | "recurring"
    schedule_text: str = ""       # for recurring/offers
    description: str = ""
    is_new: bool = False

def safe_get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")

def write_json(path: str, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_prev_meta(path: str) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}}

def dedup(events: List[Event]) -> List[Event]:
    seen = {}
    for e in events:
        key = (e.title.strip().lower(), e.start or "", e.center.strip().lower(), e.kind)
        seen[key] = e
    return list(seen.values())

# -------------------------
# Date/Time parsing helpers
# -------------------------
GER_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12
}

def parse_time_range(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = (text or "").replace("Uhr", "").replace("–", "-").replace("—", "-")
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", t)
    if not m:
        # sometimes only one time like "14:00"
        m2 = re.search(r"\b(\d{1,2})[:.](\d{2})\b", t)
        if not m2:
            return None, None
        hh = int(m2.group(1)); mm = int(m2.group(2))
        return f"{hh:02d}:{mm:02d}", None
    sh = int(m.group(1)); sm = int(m.group(2)); eh = int(m.group(3)); em = int(m.group(4))
    return f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}"

def parse_german_date_anywhere(text: str) -> Optional[Tuple[int,int,int]]:
    """
    Tries:
      - 30.05.2026
      - 30.5.2026
      - 30.05.26
      - 30. Mai 2026
      - Samstag, 30.05.2026
    Returns (yyyy, mm, dd)
    """
    s = (text or "").strip()

    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", s)
    if m:
        dd = int(m.group(1)); mm = int(m.group(2)); yy = int(m.group(3))
        if yy < 100:
            yy += 2000
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return yy, mm, dd

    m = re.search(r"\b(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(\d{4})\b", s)
    if m:
        dd = int(m.group(1))
        mon = m.group(2).lower().replace("ä", "ae").replace("ö","oe").replace("ü","ue")
        yy = int(m.group(3))
        mm = GER_MONTHS.get(mon)
        if mm:
            return yy, mm, dd

    return None

# -------------------------
# ELCH scraping
# -------------------------
def elch_extract_detail_links(listing_url: str, html: str) -> List[str]:
    """
    Find detail links from the offers listing.
    We keep any link that contains '/detail/' or looks like an offer detail.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        if "/detail/" in href:
            links.add(urljoin(listing_url, href))

    # fallback: sometimes "Weitere Infos" buttons may not have /detail/ but still a detail-ish URL
    if not links:
        for a in soup.find_all("a", string=re.compile(r"Weitere\s+Infos|Details|Mehr\s+Infos", re.I)):
            href = a.get("href", "").strip()
            if href:
                links.add(urljoin(listing_url, href))

    return sorted(links)

def elch_parse_detail_page(detail_url: str, center: str, default_location: str, tzinfo) -> Optional[Event]:
    html = safe_get(detail_url)
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    h1 = soup.find(["h1", "h2"])
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        title = "ELCH Angebot"

    full_text = soup.get_text("\n", strip=True)
    schedule_text = ""
    # try to find a nearby line containing time or schedule
    for line in full_text.split("\n"):
        if re.search(r"\d{1,2}[:.]\d{2}", line) or re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", line):
            schedule_text = line.strip()
            break

    # find date anywhere
    ymd = parse_german_date_anywhere(full_text)
    if not ymd:
        # no concrete date: store as recurring/offer
        tags = infer_tags(title, full_text)
        flags = infer_flags(title, full_text)
        return Event(
            title=title,
            start=None,
            end=None,
            location=default_location,
            source="Zentrum ELCH",
            center=center,
            url=detail_url,
            tags=tags,
            flags=flags,
            kind="recurring",
            schedule_text=schedule_text or "",
            description=""
        )

    yy, mm, dd = ymd

    # time range: search the first line with time
    st, et = None, None
    for line in full_text.split("\n"):
        if re.search(r"\d{1,2}[:.]\d{2}", line):
            st, et = parse_time_range(line)
            if st:
                break

    # default to 00:00 if no time
    if not st:
        st = "00:00"

    sh, sm = map(int, st.split(":"))
    start_dt = datetime(yy, mm, dd, sh, sm, tzinfo=tzinfo)

    end_iso = None
    if et:
        eh, em = map(int, et.split(":"))
        end_dt = datetime(yy, mm, dd, eh, em, tzinfo=tzinfo)
        end_iso = iso(end_dt)

    tags = infer_tags(title, full_text)
    flags = infer_flags(title, full_text)

    return Event(
        title=title,
        start=iso(start_dt),
        end=end_iso,
        location=default_location,
        source="Zentrum ELCH",
        center=center,
        url=detail_url,
        tags=tags,
        flags=flags,
        kind="dated",
        schedule_text=schedule_text or "",
        description=""
    )

def fetch_elch_angebote(listing_url: str, center: str, default_location: str, tzinfo) -> List[Event]:
    html = safe_get(listing_url)
    detail_links = elch_extract_detail_links(listing_url, html)

    events: List[Event] = []
    for link in detail_links:
        try:
            e = elch_parse_detail_page(link, center, default_location, tzinfo)
            if e:
                events.append(e)
        except Exception as ex:
            print(f"ELCH detail failed: {link} -> {ex}", file=sys.stderr)

    # Also include a few top-level offer items as recurring (fallback),
    # but only if we found nothing.
    if not events:
        soup = BeautifulSoup(html, "html.parser")
        # heuristic: headings in offer cards
        for h in soup.select("h3, h4"):
            t = h.get_text(" ", strip=True)
            if t and len(t) > 3:
                tags = infer_tags(t, "")
                flags = infer_flags(t, "")
                events.append(Event(
                    title=t,
                    start=None, end=None,
                    location=default_location,
                    source="Zentrum ELCH",
                    center=center,
                    url=listing_url,
                    tags=tags, flags=flags,
                    kind="recurring",
                    schedule_text="",
                ))
                if len(events) >= 20:
                    break

    return events

# -------------------------
# GZ scraping (programm -> cards -> detail link)
# -------------------------
def fetch_gz_program(program_url: str, center: str, default_location: str, tzinfo) -> List[Event]:
    html = safe_get(program_url)
    soup = BeautifulSoup(html, "html.parser")

    events: List[Event] = []

    # Heuristik: Jede Programm-Karte enthält typischerweise:
    # - Heading (h2/h3) mit Titel (oft "..., GZ XYZ")
    # - Datumzeile wie "Di. 24.02.2026"
    # - Zeit wie "14:00–17:30 Uhr"
    # - einen Link zur Detailseite unter /angebote/<slug>/
    #
    # Wir iterieren über headings, die "GZ" enthalten, und nehmen den umgebenden Block.
    headings = soup.find_all(["h2", "h3"])
    for h in headings:
        htxt = h.get_text(" ", strip=True)
        if "GZ" not in htxt:
            continue

        # Finde einen sinnvollen Container um das Heading herum
        container = h.find_parent(["article", "li", "section", "div"]) or h.parent
        if not container:
            continue

        block_text = container.get_text("\n", strip=True)

        # Datum
        dm = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So)\.\s+(\d{2})\.(\d{2})\.(\d{4})", block_text)
        if not dm:
            continue
        dd = int(dm.group(2)); mm = int(dm.group(3)); yy = int(dm.group(4))

        # Zeit
        st, et = parse_time_range(block_text)
        if not st:
            # manchmal über Zeilen verteilt
            st, et = parse_time_range(" ".join(block_text.split("\n")))
        if not st:
            st = "00:00"

        sh, sm = map(int, st.split(":"))
        start_dt = datetime(yy, mm, dd, sh, sm, tzinfo=tzinfo)

        end_iso = None
        if et:
            eh, em = map(int, et.split(":"))
            end_dt = datetime(yy, mm, dd, eh, em, tzinfo=tzinfo)
            end_iso = iso(end_dt)

        # Detail-Link (wichtig!)
        detail_url = program_url
        a = container.select_one('a[href*="/angebote/"]')
        if a and a.get("href"):
            detail_url = urljoin(program_url, a["href"])

        # Titel bereinigen: ", GZ X" entfernen
        title = re.sub(r",\s*GZ\s+.*$", "", htxt).strip()
        if not title or len(title) < 3:
            continue

        tags = infer_tags(title, block_text)
        flags = infer_flags(title, block_text)

        events.append(Event(
            title=title,
            start=iso(start_dt),
            end=end_iso,
            location=default_location,
            source="GZ Zürich",
            center=center,
            url=detail_url,
            tags=tags,
            flags=flags,
            kind="dated",
            schedule_text="",
        ))

    return dedup(events)

# -------------------------
# Karussell Baden
# -------------------------
def fetch_karussell_jahresprogramm(url: str, center: str, default_location: str, tzinfo) -> List[Event]:
    html = safe_get(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln for ln in text.split("\n") if ln.strip()]

    date_re = re.compile(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s+(\d{1,2})\.\s+([A-Za-zäöüÄÖÜ]+)\s+(\d{4})")
    time_re = re.compile(r"\b(\d{1,2}[:.]\d{2}\s*[–-]\s*\d{1,2}[:.]\d{2})\b")

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
            day = int(dm.group(2))
            month_name = dm.group(3)
            year = int(dm.group(4))
            month = months.get(month_name)
            if month:
                current_date = datetime(year, month, day, 0, 0, tzinfo=tzinfo)
            i += 1
            continue

        tm = time_re.search(ln)
        if current_date and tm:
            st, et = parse_time_range(tm.group(1))
            title = lines[i+1].strip() if i+1 < len(lines) else ""
            if title and not date_re.search(title):
                sh, sm = map(int, st.split(":"))
                start_dt = current_date.replace(hour=sh, minute=sm)

                end_iso = None
                if et:
                    eh, em = map(int, et.split(":"))
                    end_dt = current_date.replace(hour=eh, minute=em)
                    end_iso = iso(end_dt)

                tags = infer_tags(title, "")
                flags = infer_flags(title, "")

                events.append(Event(
                    title=title,
                    start=iso(start_dt),
                    end=end_iso,
                    location=default_location,
                    source="Karussell Baden",
                    center=center,
                    url=url,
                    tags=tags,
                    flags=flags,
                    kind="dated",
                    schedule_text="",
                ))
                i += 2
                continue

        i += 1

    return dedup(events)

def fetch_karussell_uebersicht(url: str, center: str, default_location: str, tzinfo=None) -> List[Event]:
    html = safe_get(url)
    soup = BeautifulSoup(html, "html.parser")

    # Alle Event-Detailseiten sammeln (wichtig: /angebote/event/<id>/<slug>/)
    event_links = set()
    for a in soup.select('a[href*="/angebote/event/"]'):
        href = a.get("href", "").strip()
        if href:
            event_links.add(urljoin(url, href))

    events: List[Event] = []

    # Jede Detailseite parsen: Datum wie "Donnerstag, 02. April 2026"
    # und Zeit wie "13:00 bis 14:30 Uhr" oder ähnlich
    for link in sorted(event_links):
        try:
            dhtml = safe_get(link)
            dsoup = BeautifulSoup(dhtml, "html.parser")

            title = ""
            h1 = dsoup.find(["h1", "h2"])
            if h1:
                title = h1.get_text(" ", strip=True)
            if not title:
                continue

            text = dsoup.get_text("\n", strip=True)

            # Datum: entweder "02. April 2026" oder "02.04.2026"
            ymd = parse_german_date_anywhere(text)
            if not ymd:
                # wenn keine konkrete Datumangabe, dann als recurring aufnehmen
                tags = infer_tags(title, text)
                flags = infer_flags(title, text)
                events.append(Event(
                    title=title,
                    start=None,
                    end=None,
                    location=default_location,
                    source="Karussell Baden",
                    center=center,
                    url=link,
                    tags=tags,
                    flags=flags,
                    kind="recurring",
                    schedule_text="",
                ))
                continue

            yy, mm, dd = ymd

            # Zeit: oft "13:00 bis 14:30 Uhr"
            # parse_time_range versteht "-", daher normalisieren wir "bis" -> "-"
            tnorm = text.replace(" bis ", " - ").replace("–", "-").replace("—", "-")
            st, et = parse_time_range(tnorm)
            if not st:
                st = "00:00"

            sh, sm = map(int, st.split(":"))
            start_dt = datetime(yy, mm, dd, sh, sm, tzinfo=tzinfo) if tzinfo else datetime(yy, mm, dd, sh, sm)

            end_iso = None
            if et:
                eh, em = map(int, et.split(":"))
                end_dt = datetime(yy, mm, dd, eh, em, tzinfo=tzinfo) if tzinfo else datetime(yy, mm, dd, eh, em)
                end_iso = iso(end_dt)

            tags = infer_tags(title, text)
            flags = infer_flags(title, text)

            events.append(Event(
                title=title,
                start=iso(start_dt),
                end=end_iso,
                location=default_location,
                source="Karussell Baden",
                center=center,
                url=link,
                tags=tags,
                flags=flags,
                kind="dated",
                schedule_text="",
            ))
        except Exception as ex:
            print(f"Karussell event detail failed: {link} -> {ex}", file=sys.stderr)

    return dedup(events)

def fetch_ideesport_minimove(url: str, center: str, default_location: str, tzinfo) -> List[Event]:
    """
    IdéeSport MiniMove pages have a section "Veranstaltungsdaten" like:
      Sonntag 11.01.2026 14:30 - 17:00
    And later a line:
      Ort: Schule Letzi, ...
    We create one dated Event per row.
    """
    html = safe_get(url)
    soup = BeautifulSoup(html, "html.parser")

    # Title from H1 if available (e.g. "MiniMove Letzi")
    page_title = ""
    h1 = soup.find("h1")
    if h1:
        page_title = h1.get_text(" ", strip=True)
    if not page_title:
        page_title = center

    text = soup.get_text("\n", strip=True)

    # Location line: "### Ort: ..."
    loc = default_location
    mloc = re.search(r"\bOrt:\s*(.+)", text)
    if mloc:
        loc = mloc.group(1).strip()

    # Rows: weekday dd.mm.yyyy hh:mm - hh:mm
    # Example: "Sonntag 11.01.2026 14:30 - 17:00"
    row_re = re.compile(
        r"\b(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)\s+"
        r"(\d{2})\.(\d{2})\.(\d{4})\s+"
        r"(\d{1,2}[:.]\d{2})\s*[-–—]\s*(\d{1,2}[:.]\d{2})"
    )

    events: List[Event] = []
    for rm in row_re.finditer(text):
        dd = int(rm.group(2))
        mm = int(rm.group(3))
        yy = int(rm.group(4))
        st = rm.group(5).replace(".", ":")
        et = rm.group(6).replace(".", ":")

        sh, sm = map(int, st.split(":"))
        start_dt = datetime(yy, mm, dd, sh, sm, tzinfo=tzinfo)

        end_iso = None
        if et:
            eh, em = map(int, et.split(":"))
            end_dt = datetime(yy, mm, dd, eh, em, tzinfo=tzinfo)
            end_iso = iso(end_dt)

        # MiniMove is typically free and without signup (often stated on page).
        # We still run infer_* to keep consistent tagging.
        tags = infer_tags(page_title, text)
        flags = infer_flags(page_title, text)
        flags["gratis"] = True
        if "anmeldung" not in flags:
            flags["anmeldung"] = False

        events.append(Event(
            title=page_title,
            start=iso(start_dt),
            end=end_iso,
            location=loc,
            source="IdéeSport",
            center=center,
            url=url,
            tags=tags,
            flags=flags,
            kind="dated",
            schedule_text="",
        ))

    return dedup(events)

# -------------------------
# Main
# -------------------------
def main():
    with open("scripts/sources.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    tzinfo = tz.gettz(cfg.get("timezone", "Europe/Zurich"))

    all_events: List[Event] = []

    for src in cfg["sources"]:
        stype = src["type"]
        url = src["url"]
        center = src.get("center", "")
        loc = src.get("default_location", "")

        try:
            if stype == "elch_angebote":
                all_events += fetch_elch_angebote(url, center, loc, tzinfo)
            elif stype == "gz_programm":
                all_events += fetch_gz_program(url, center, loc, tzinfo)
            elif stype == "ideesport_minimove":
                all_events += fetch_ideesport_minimove(url, center, loc, tzinfo)
            elif stype == "karussell_jahresprogramm":
                all_events += fetch_karussell_jahresprogramm(url, center, loc, tzinfo)
            elif stype == "karussell_uebersicht":
                all_events += fetch_karussell_uebersicht(url, center, loc, tzinfo)
            else:
                print("Unknown source type:", stype, file=sys.stderr)
        except Exception as e:
            print(f"Source failed ({src.get('id','?')}): {e}", file=sys.stderr)

    all_events = dedup(all_events)

    # mark "new"
    prev = load_prev_meta(OUT_META_DOCS)
    seen = prev.get("seen", {})
    now_iso = datetime.now(tzinfo).isoformat(timespec="seconds")

    out: List[Dict] = []
    for e in all_events:
        key = f"{e.title}|{e.start or ''}|{e.center}|{e.kind}|{e.url}"
        is_new = key not in seen
        seen[key] = now_iso
        d = asdict(e)
        d["is_new"] = is_new
        out.append(d)

    # sort: dated first by start, then recurring by title
    def sort_key(d: Dict):
        if d.get("kind") == "dated" and d.get("start"):
            return (0, d["start"], d.get("title",""))
        return (1, d.get("title",""), d.get("center",""))

    out.sort(key=sort_key)

    meta = {"updated_at": now_iso, "seen": seen}

    # write docs (served)
    write_json(OUT_EVENTS_DOCS, out)
    write_json(OUT_META_DOCS, meta)

    # write root (debug)
    write_json(OUT_EVENTS_ROOT, out)
    write_json(OUT_META_ROOT, meta)

    # ICS: only dated events (recurring offers without dates are excluded)
    dated_only = [x for x in out if x.get("kind") == "dated" and x.get("start")]
    os.makedirs(os.path.dirname(OUT_ICS_DOCS) or ".", exist_ok=True)
    with open(OUT_ICS_DOCS, "w", encoding="utf-8") as f:
        f.write(to_ics(dated_only))

    print(f"Wrote {len(out)} items ({len(dated_only)} dated)")

if __name__ == "__main__":
    main()




