#!/usr/bin/env python3
# fetch_events.py — events 2.0

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import tz
from dateutil.parser import parse as dtparse


DOCS_DIR = "docs"
EVENTS_JSON = os.path.join(DOCS_DIR, "events.json")
META_JSON = os.path.join(DOCS_DIR, "events.meta.json")
ALL_ICS = os.path.join(DOCS_DIR, "all.ics")


# ----------------------------
# Core model
# ----------------------------

@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: datetime
    end: Optional[datetime]
    url: str
    source_id: str
    source_name: str
    organizer: Optional[str] = None
    location: Optional[str] = None
    tags: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "url": self.url,
            "source": {"id": self.source_id, "name": self.source_name},
            "organizer": self.organizer,
            "location": self.location,
            "tags": list(self.tags),
        }


def stable_event_id(*parts: str) -> str:
    raw = "|".join(p.strip() for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# ----------------------------
# HTTP helpers
# ----------------------------

def make_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en;q=0.8",
    })
    return s


def get_soup(session: requests.Session, url: str, timeout: int = 30) -> BeautifulSoup:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ----------------------------
# Date parsing (robust-ish)
# ----------------------------

def parse_datetime_zh(text: str, tz_zh) -> Optional[datetime]:
    """
    Parse a datetime from German-ish strings, return tz-aware datetime in Europe/Zurich.
    """
    if not text:
        return None

    t = normalize_whitespace(text)

    # strip weekday prefixes like "Mo.", "Montag,"
    t = re.sub(r"^(Mo|Di|Mi|Do|Fr|Sa|So)\.?,?\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+", "", t, flags=re.IGNORECASE)

    try:
        dt = dtparse(t, dayfirst=True, fuzzy=True)
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz_zh)
    else:
        dt = dt.astimezone(tz_zh)

    return dt


def ensure_end(start: datetime, end: Optional[datetime]) -> Optional[datetime]:
    if end is None:
        return None
    if end < start:
        return None
    return end


def has_concrete_date(text: str) -> bool:
    """
    Our definition of "concrete date":
      - contains a 4-digit year (20xx) OR
      - contains dd.mm.yyyy
    """
    if not text:
        return False
    t = normalize_whitespace(text)
    if re.search(r"\b20\d{2}\b", t):
        return True
    if re.search(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", t):
        return True
    return False


# ----------------------------
# Source base
# ----------------------------

class Source:
    def __init__(self, cfg: Dict[str, Any], defaults: Dict[str, Any], session: requests.Session, tz_zh):
        self.cfg = cfg
        self.defaults = defaults
        self.session = session
        self.tz_zh = tz_zh

    @property
    def id(self) -> str:
        return self.cfg["id"]

    @property
    def name(self) -> str:
        return self.cfg["name"]

    def fetch(self) -> List[Event]:
        raise NotImplementedError


# ----------------------------
# ELCH: listing -> detail, only dated
# ----------------------------

class ElchHtmlSource(Source):
    def fetch(self) -> List[Event]:
        out: List[Event] = []
        for center in self.cfg.get("centers", []):
            listing_url = center["listing_url"]
            organizer = center.get("name") or center.get("id") or "ELCH"

            listing_soup = get_soup(self.session, listing_url)
            detail_urls = self._extract_detail_urls(listing_soup, listing_url)

            for durl in sorted(detail_urls):
                ev = self._parse_detail(durl, organizer=organizer)
                if ev:
                    out.append(ev)

        return out

    def _extract_detail_urls(self, soup: BeautifulSoup, base_url: str) -> set[str]:
        urls: set[str] = set()
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            abs_url = urljoin(base_url, href)

            # stay on same host
            if urlparse(abs_url).netloc != urlparse(base_url).netloc:
                continue

            # ELCH details usually contain /angebote/.../detail/... or /angebote-accu/detail/...
            if "/detail/" in abs_url and "/angebote" in abs_url:
                urls.add(abs_url)

        return urls

    def _parse_detail(self, url: str, organizer: str) -> Optional[Event]:
        soup = get_soup(self.session, url)

        h1 = soup.select_one("h1")
        if not h1:
            return None
        title = normalize_whitespace(h1.get_text(" "))

        # Guard: some pages look like generic opening-hours/holiday text
        page_text = normalize_whitespace(soup.get_text(" "))
        if "Öffnungszeiten während der Schulferien" in page_text:
            return None

        lines = [normalize_whitespace(x) for x in soup.get_text("\n").split("\n") if normalize_whitespace(x)]

        # Try to find a date-ish line
        date_line = None
        for i, line in enumerate(lines):
            if has_concrete_date(line):
                date_line = line
                break

        # Must have concrete date (your requirement)
        if not date_line:
            return None

        start_dt = parse_datetime_zh(date_line, self.tz_zh)
        if not start_dt:
            # sometimes date and time are split across lines; try combining next line
            idx = lines.index(date_line)
            if idx + 1 < len(lines):
                start_dt = parse_datetime_zh(date_line + " " + lines[idx + 1], self.tz_zh)
        if not start_dt:
            return None

        # Optional end time (rare on ELCH pages; keep minimal)
        end_dt = None

        # Optional location after an "Ort" label
        location = None
        for i, line in enumerate(lines):
            if line.lower() == "ort" and i + 1 < len(lines):
                location = lines[i + 1]
                break

        eid = stable_event_id("elch", organizer, url, start_dt.isoformat(), title)
        return Event(
            id=eid,
            title=title,
            start=start_dt,
            end=end_dt,
            url=url,
            source_id=self.id,
            source_name=self.name,
            organizer=organizer,
            location=location,
            tags=("elch",),
        )


# ----------------------------
# GZ: index page (all houses) -> detail pages
# ----------------------------

class GzIndexSource(Source):
    def fetch(self) -> List[Event]:
        index_url = self.cfg["index_url"]
        max_pages = int(self.cfg.get("max_pages", 3))

        detail_urls: set[str] = set()
        pages_to_scan: List[str] = [index_url]
        seen_pages: set[str] = set()

        # Without knowing server-side pagination, we scan a few discovered internal pages
        for _ in range(max_pages):
            if not pages_to_scan:
                break
            page_url = pages_to_scan.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            soup = get_soup(self.session, page_url)
            html = str(soup)

            # Extract all /gz-.../angebote/.../ URLs from entire HTML (includes scripts)
            for m in re.finditer(r"https?://gz-zh\.ch/gz-[^/]+/angebote/[^\"'\s<>()]+/?", html):
                detail_urls.add(m.group(0).rstrip(")"))

            # Try to discover a few more relevant pages (very conservative)
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                abs_url = urljoin(page_url, href)
                if urlparse(abs_url).netloc != "gz-zh.ch":
                    continue
                # only keep a small set of listing-like pages
                if abs_url.endswith("/") and any(k in abs_url for k in ["/angebote", "/programm", "/kinder", "/familien"]):
                    if abs_url not in seen_pages and len(pages_to_scan) < 10:
                        pages_to_scan.append(abs_url)

        events: List[Event] = []
        for url in sorted(detail_urls):
            events.extend(self._parse_detail(url))

        return events

    def _parse_detail(self, url: str) -> List[Event]:
        soup = get_soup(self.session, url)

        h1 = soup.select_one("h1")
        if not h1:
            return []
        title = normalize_whitespace(h1.get_text(" "))

        lines = [normalize_whitespace(x) for x in soup.get_text("\n").split("\n") if normalize_whitespace(x)]

        # Find date line like "Fr., 27. Feb. 2026"
        date_line = next((t for t in lines if re.search(r"\b20\d{2}\b", t)), None)
        if not date_line:
            return []

        # Find time range like "10:00–11:30" or "10:00 - 11:30"
        time_line = next((t for t in lines if re.search(r"\b\d{1,2}:\d{2}\s*[–-]\s*\d{1,2}:\d{2}\b", t)), None)

        # Organizer often appears as a line starting with "GZ "
        organizer = next((t for t in lines if t.startswith("GZ ")), None)

        start_dt = parse_datetime_zh(f"{date_line} {time_line or ''}", self.tz_zh)
        if not start_dt:
            start_dt = parse_datetime_zh(date_line, self.tz_zh)
        if not start_dt:
            return []

        end_dt = None
        if time_line:
            m = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", time_line)
            if m:
                hh, mm = map(int, m.group(2).split(":"))
                end_dt = ensure_end(start_dt, start_dt.replace(hour=hh, minute=mm))

        eid = stable_event_id("gz", url, start_dt.isoformat(), title)
        return [Event(
            id=eid,
            title=title,
            start=start_dt,
            end=end_dt,
            url=url,
            source_id=self.id,
            source_name=self.name,
            organizer=organizer,
            location=organizer,
            tags=("gz",),
        )]


# ----------------------------
# Karussell: crawl multiple entry points -> event pages -> extract all dates
# ----------------------------

class KarussellCrawlSource(Source):
    def fetch(self) -> List[Event]:
        start_urls: List[str] = self.cfg.get("start_urls", [])
        max_depth = int(self.cfg.get("max_depth", 2))
        max_event_pages = int(self.cfg.get("max_event_pages", 400))

        visited: set[str] = set()
        event_pages: set[str] = set()

        queue: List[Tuple[str, int]] = [(u, 0) for u in start_urls]

        while queue:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            # hard stop to avoid runaway
            if len(visited) > 2000:
                break

            soup = get_soup(self.session, url)
            html = str(soup)

            # Collect event pages
            for m in re.finditer(r"https?://www\.karussell-baden\.ch/angebote/event/\d+/[^\"'\s<>()]+/?", html):
                event_pages.add(m.group(0).rstrip(")"))
                if len(event_pages) >= max_event_pages:
                    break

            if len(event_pages) >= max_event_pages:
                break

            if depth < max_depth:
                for a in soup.select("a[href]"):
                    href = (a.get("href") or "").strip()
                    if not href:
                        continue
                    nxt = urljoin(url, href)
                    parsed = urlparse(nxt)
                    if parsed.netloc not in {"www.karussell-baden.ch", "karussell-baden.ch"}:
                        continue
                    if "/angebote/" not in nxt:
                        continue

                    # stay focused
                    if any(p in nxt for p in ["/angebote/uebersicht", "/angebote/aktuell", "/angebote/themen", "/angebote/event", "/angebote/jahresprogramm"]):
                        if nxt not in visited:
                            queue.append((nxt, depth + 1))

        events: List[Event] = []
        for ep in sorted(event_pages):
            events.extend(self._parse_event_page(ep))

        return events

    def _parse_event_page(self, url: str) -> List[Event]:
        soup = get_soup(self.session, url)
        h1 = soup.select_one("h1")
        if not h1:
            return []
        title = normalize_whitespace(h1.get_text(" "))

        lines = [normalize_whitespace(x) for x in soup.get_text("\n").split("\n") if normalize_whitespace(x)]

        # Location: after a "Wo" label
        location = None
        for i, t in enumerate(lines):
            if t.lower() == "wo" and i + 1 < len(lines):
                location = lines[i + 1]
                break

        date_blocks: List[str] = []

        # "Findet statt am" + next line
        for i, t in enumerate(lines):
            if t.lower().startswith("findet statt am") and i + 1 < len(lines):
                date_blocks.append(lines[i + 1])

        # "Weitere Daten" area: collect lines that contain year + time
        in_more = False
        for t in lines:
            if t.lower().startswith("weitere daten"):
                in_more = True
                continue
            if in_more:
                if t.startswith("©") or t.lower() in {"impressum", "datenschutzerklärung"}:
                    break
                if re.search(r"\b20\d{2}\b", t) and re.search(r"\b\d{1,2}:\d{2}\b", t):
                    date_blocks.append(t)

        out: List[Event] = []
        for blk in date_blocks:
            start = parse_datetime_zh(blk, self.tz_zh)
            if not start:
                continue

            # Optional end time like "14:30 bis 15:30" or "14:30 - 15:30"
            end = None
            m = re.search(r"(\d{1,2}:\d{2})\s*(bis|[-–])\s*(\d{1,2}:\d{2})", blk)
            if m:
                hh, mm = map(int, m.group(3).split(":"))
                end = ensure_end(start, start.replace(hour=hh, minute=mm))

            eid = stable_event_id("karussell", url, start.isoformat(), title)
            out.append(Event(
                id=eid,
                title=title,
                start=start,
                end=end,
                url=url,
                source_id=self.id,
                source_name=self.name,
                organizer="Karussell Baden",
                location=location,
                tags=("karussell",),
            ))

        return out


# ----------------------------
# Writers: JSON + meta + ICS
# ----------------------------

def write_json(events: List[Event]) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(EVENTS_JSON, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)


def write_meta(meta: Dict[str, Any]) -> None:
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(META_JSON, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def write_ics(events: List[Event]) -> None:
    """
    Minimal ICS writer without extra deps.
    Times are stored in UTC (DTSTART/DTEND with trailing Z).
    """
    def esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    def dt_utc(dt: datetime) -> str:
        return dt.astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")

    now = datetime.now(tz=tz.UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//family-events//events 2.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for e in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{e.id}@family-events")
        lines.append(f"DTSTAMP:{now}")
        lines.append(f"SUMMARY:{esc(e.title)}")
        lines.append(f"DTSTART:{dt_utc(e.start)}")
        if e.end:
            lines.append(f"DTEND:{dt_utc(e.end)}")
        if e.url:
            lines.append(f"URL:{esc(e.url)}")
        desc = f"Quelle: {e.source_name}"
        if e.organizer:
            desc += f" | {e.organizer}"
        lines.append(f"DESCRIPTION:{esc(desc)}")
        if e.location:
            lines.append(f"LOCATION:{esc(e.location)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(ALL_ICS, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")


# ----------------------------
# Runner
# ----------------------------

SOURCE_TYPES = {
    "elch_html": ElchHtmlSource,
    "gz_index": GzIndexSource,
    "karussell_crawl": KarussellCrawlSource,
}


def dedupe_events(events: List[Event]) -> List[Event]:
    seen = set()
    out: List[Event] = []
    for e in sorted(events, key=lambda x: (x.start, x.title, x.url)):
        if e.id in seen:
            continue
        seen.add(e.id)
        out.append(e)
    return out


def load_config(path: str = "sources.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    cfg = load_config("sources.yaml")
    defaults = cfg.get("defaults", {})
    tzid = defaults.get("timezone", "Europe/Zurich")
    tz_zh = tz.gettz(tzid)

    session = make_session(defaults.get("user_agent", "family-events/2.0"))

    sources_cfg = [s for s in cfg.get("sources", []) if s.get("enabled", True)]

    all_events: List[Event] = []
    per_source: Dict[str, Any] = {}

    for s_cfg in sources_cfg:
        sid = s_cfg.get("id", "unknown")
        stype = s_cfg.get("type")
        sname = s_cfg.get("name", sid)

        klass = SOURCE_TYPES.get(stype)
        if not klass:
            per_source[sid] = {"name": sname, "type": stype, "error": f"Unknown source type: {stype}"}
            continue

        src = klass(s_cfg, defaults, session, tz_zh)

        t0 = datetime.now(tz=tz.UTC)
        try:
            events = src.fetch()
            ok = True
            err = None
        except Exception as e:
            events = []
            ok = False
            err = f"{type(e).__name__}: {e}"
        t1 = datetime.now(tz=tz.UTC)

        all_events.extend(events)
        per_source[sid] = {
            "name": sname,
            "type": stype,
            "ok": ok,
            "error": err,
            "count": len(events),
            "seconds": round((t1 - t0).total_seconds(), 3),
        }

        print(f"[{sid}] {len(events)} events ({per_source[sid]['seconds']}s)")

    all_events = dedupe_events(all_events)

    meta = {
        "version": "events 2.0",
        "generated_at": datetime.now(tz=tz.UTC).isoformat(),
        "timezone": tzid,
        "count": len(all_events),
        "sources": per_source,
    }

    write_json(all_events)
    write_meta(meta)
    write_ics(all_events)

    print(f"OK: wrote {len(all_events)} unique events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
