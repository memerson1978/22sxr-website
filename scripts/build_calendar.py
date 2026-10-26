#!/usr/bin/env python3
"""Build the 22 Saxonbury Road master calendar.

Reads the Airbnb and Vrbo calendar feeds (URLs come from environment
variables, stored as GitHub Actions secrets) plus data/direct-bookings.csv,
and writes:

  calendar/for-airbnb.ics   Vrbo + direct bookings   (Airbnb imports this;
                            it imports Simply Owners separately)
  calendar/for-vrbo.ics     Airbnb + Simply Owners + direct (Vrbo imports this)
  calendar/master.ics       every booking, labelled by source
  calendar/availability.json  booked date ranges for the website calendar
  calendar/status.json      when each source was last read successfully

Only dates and the channel name are ever written. Guest names that appear in
the platform feeds (Vrbo puts first names in the summary) are dropped.

Safety rule: if a feed cannot be read, or suddenly comes back with no future
bookings when it previously had some, the last good copy is kept
(calendar/cache/<source>.json). An empty or broken download must never open
up dates that are really booked.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("SXR_OUT") or ROOT / "calendar")
CACHE = OUT / "cache"
DIRECT = Path(os.environ.get("SXR_DIRECT") or ROOT / "data" / "direct-bookings.csv")
PROPERTY = "22 Saxonbury Road, Southbourne"
UA = "Mozilla/5.0 (22sxr-calendar; +https://github.com/memerson1978/22sxr-website)"

SOURCES = {
    "airbnb": "AIRBNB_ICS_URL",
    "vrbo": "VRBO_ICS_URL",
    "simplyowners": "SIMPLYOWNERS_ICS_URL",
}


def today() -> dt.date:
    override = os.environ.get("SXR_TODAY")
    return dt.date.fromisoformat(override) if override else dt.date.today()


# ---------------------------------------------------------------- parsing
def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_date(value: str) -> dt.date:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", value)
    if not m:
        raise ValueError(f"bad date {value!r}")
    return dt.date(int(m[1]), int(m[2]), int(m[3]))


def parse_ics(text: str, source: str) -> list[dict]:
    """Return [{start, end, kind}] for one platform feed."""
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("not an iCalendar file")
    events, cur = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur and "end" in cur:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            name = key.split(";", 1)[0].upper()
            if name == "DTSTART":
                cur["start"] = parse_date(val)
            elif name == "DTEND":
                cur["end"] = parse_date(val)
            elif name == "SUMMARY":
                cur["summary"] = val.strip()
    out = []
    for e in events:
        summary = e.get("summary", "")
        # Airbnb marks dates outside its booking window as "Airbnb (Not available)".
        # Those are not bookings, and passing them on would close Vrbo for months.
        if source == "airbnb" and "not available" in summary.lower():
            continue
        if e["end"] <= e["start"]:
            continue
        out.append({"start": e["start"], "end": e["end"], "source": source})
    return out


def read_direct() -> list[dict]:
    out = []
    if not DIRECT.exists():
        return out
    with DIRECT.open(newline="") as fh:
        rows = (r for r in fh if r.strip() and not r.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            start = dt.date.fromisoformat(row["arrive"].strip())
            end = dt.date.fromisoformat(row["depart"].strip())
            if end <= start:
                raise ValueError(f"direct booking ends before it starts: {row}")
            out.append({"start": start, "end": end, "source": "direct",
                        "ref": (row.get("type") or "DIRECT").strip().upper()})
    return out


# ---------------------------------------------------------------- feeds
def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def cache_path(source: str) -> Path:
    return CACHE / f"{source}.json"


def load_cache(source: str) -> dict | None:
    p = cache_path(source)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    data["events"] = [{"start": dt.date.fromisoformat(e["start"]),
                       "end": dt.date.fromisoformat(e["end"]),
                       "source": source} for e in data["events"]]
    return data


def save_cache(source: str, events: list[dict], read_at: str, cached: dict | None) -> None:
    """Rewrite the copy only when the bookings change, so the repo isn't updated every hour."""
    rows = sorted({(e["start"].isoformat(), e["end"].isoformat()) for e in events})
    rows = [{"start": s, "end": e} for s, e in rows]
    if cached is not None:
        old = sorted({(e["start"].isoformat(), e["end"].isoformat()) for e in cached["events"]})
        if [{"start": s, "end": e} for s, e in old] == rows:
            return
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path(source).write_text(json.dumps({"read_at": read_at, "events": rows}, indent=1) + "\n")


def future(events: list[dict], day: dt.date) -> list[dict]:
    return [e for e in events if e["end"] > day]


def get_source(source: str, env: str, day: dt.date, now: str) -> tuple[list[dict], dict]:
    url = os.environ.get(env, "").strip()
    cached = load_cache(source)
    status = {"source": source}
    try:
        if not url:
            raise RuntimeError(f"{env} is not set")
        events = parse_ics(fetch(url), source)
        if cached and future(cached["events"], day) and not future(events, day):
            raise RuntimeError("feed returned no future bookings but the last good copy had some")
        save_cache(source, events, now, cached)
        status.update(ok=True, bookings=len(future(events, day)))
        return events, status
    except Exception as exc:  # keep the last good copy
        status.update(ok=False, error=str(exc)[:200])
        if cached:
            status.update(using_copy_from=cached["read_at"], bookings=len(future(cached["events"], day)))
            return cached["events"], status
        status["bookings"] = 0
        return [], status


# ---------------------------------------------------------------- output
def ics(events: list[dict], name: str, labelled: bool) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//22sxr//master calendar//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{name}"]
    for e in sorted(events, key=lambda x: (x["start"], x["source"])):
        label = {"airbnb": "Airbnb", "vrbo": "Vrbo", "simplyowners": "Simply Owners",
                 "direct": "Direct"}[e["source"]]
        summary = f"Booked ({label})" if labelled else "Booked"
        uid = f"{e['source']}-{e['start']:%Y%m%d}-{e['end']:%Y%m%d}@22sxr"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{e['start']:%Y%m%d}T000000Z",
                  f"DTSTART;VALUE=DATE:{e['start']:%Y%m%d}", f"DTEND;VALUE=DATE:{e['end']:%Y%m%d}",
                  f"SUMMARY:{summary}", "TRANSP:OPAQUE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def merge_ranges(events: list[dict]) -> list[dict]:
    spans = sorted((e["start"], e["end"]) for e in events)
    merged: list[list[dt.date]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [{"from": s.isoformat(), "to": e.isoformat()} for s, e in merged]


def overlaps(events: list[dict]) -> list[str]:
    """Double bookings: two different channels holding the same night."""
    issues = []
    ev = sorted(events, key=lambda x: x["start"])
    for i, a in enumerate(ev):
        for b in ev[i + 1:]:
            if b["start"] >= a["end"]:
                break
            # The Simply Owners calendar has often mirrored bookings taken elsewhere,
            # so an overlap with it is not treated as a double booking.
            if a["source"] != b["source"] and "simplyowners" not in (a["source"], b["source"]):
                issues.append(f"{a['source']} {a['start']}..{a['end']} overlaps "
                              f"{b['source']} {b['start']}..{b['end']}")
    return issues


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def main() -> int:
    day = today()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    OUT.mkdir(exist_ok=True)

    feeds, statuses = {}, []
    for source, env in SOURCES.items():
        feeds[source], st = get_source(source, env, day, now)
        statuses.append(st)
    direct = read_direct()
    keep_from = day - dt.timedelta(days=90)   # drop old history from the feeds

    def recent(evs):
        return [e for e in evs if e["end"] >= keep_from]

    airbnb, vrbo, direct = recent(feeds["airbnb"]), recent(feeds["vrbo"]), recent(direct)
    so = recent(feeds["simplyowners"])
    everything = airbnb + vrbo + so + direct

    # Airbnb already imports the Simply Owners calendar directly, so it is left out of Airbnb's feed.
    write_if_changed(OUT / "for-airbnb.ics", ics(vrbo + direct, f"{PROPERTY} (for Airbnb)", False))
    write_if_changed(OUT / "for-vrbo.ics", ics(airbnb + so + direct, f"{PROPERTY} (for Vrbo)", False))
    write_if_changed(OUT / "master.ics", ics(everything, f"{PROPERTY} master", True))
    write_if_changed(OUT / "availability.json", json.dumps(
        {"property": PROPERTY, "booked": merge_ranges(future(everything, day))}, indent=1) + "\n")

    issues = overlaps(future(everything, day))
    # No timestamps here, so the file (and the repo) only changes when something real changes.
    status = {"sources": statuses, "direct_bookings": len(future(direct, day)),
              "double_bookings": issues}
    write_if_changed(OUT / "status.json", json.dumps(status, indent=1) + "\n")

    print(json.dumps({"checked_at": now, **status}, indent=1))
    # Fail the run (GitHub emails the owner) if a source is down with no copy, or dates clash.
    broken = [s for s in statuses if not s["ok"] and "using_copy_from" not in s]
    return 1 if broken or issues else 0


if __name__ == "__main__":
    sys.exit(main())
