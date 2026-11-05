#!/usr/bin/env python3
"""22 Saxonbury Road: booking sheet and cleaning schedule tools (rebuilt 5th November 2026).
VERSION = 1

Claude reads and writes the Google Sheet, Gmail and Drive through the connectors; this file does the
logic so every run gives the same answer. See RUNBOOK.md for the 6pm daily check.

Commands
  check        work out what Laura needs, from the sheet rows and the record of what she was last sent
  pdf          make Laura's cleaning schedule PDF from a plan made by "check"
  parse-email  read an Airbnb or Vrbo confirmation email (plain text) into a booking row
  feeds        compare the Airbnb and Vrbo calendar feeds (22sxr-website repo, calendar/cache) with the sheet

Rules (agreed with Mark)
  * Laura sees the 3 most recent past stays, then every stay that is in progress or arrives within 3 months.
    Stays further ahead are held and issued once they come within 3 months.
  * A new booking, change or cancellation within the next 3 months is sent straight away (standard update).
    One arriving within 7 days uses the short notice email and Mark is prompted to WhatsApp Laura too.
  * Held bookings that come within 3 months are collected and sent once a week (round up day, default Monday),
    unless a standard update goes first, which carries them too.
  * The Rev number goes up by one on every issue, and appears in the file name, the PDF footer and the top of the sheet.
  * Emails to Laura are only ever saved as drafts for Mark. Laura never sees the money (£££-Net) column.
  * Past stays (departure before today) are shaded light grey in the sheet.
"""
VERSION = 1
import argparse, base64, datetime as dt, hashlib, json, re, sys
from pathlib import Path

try:
    from dateutil.relativedelta import relativedelta
except ImportError:  # pragma: no cover
    relativedelta = None

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY3 = [d[:3] for d in DAYS]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
LAURA_FIELDS = ["arrive", "depart", "name", "party", "dogs", "notes"]

OPENINGS = ["I hope you're well and having a good week.", "I hope all is well with you.",
            "I hope you've had a lovely weekend.", "Hope you're keeping well!",
            "I hope you're having a good week so far."]
CLOSINGS = ["Thank you as ever for all your hard work. The guests always comment on how lovely the house is.",
            "Many thanks, and thank you for keeping everything looking so good.",
            "Thanks so much, Laura. We really appreciate everything you do.",
            "Thank you as always for looking after the house so well.",
            "With many thanks and best wishes."]


# ---------------------------------------------------------------- dates and words
def d(s):
    """Parse 21/11/2026, 2026-11-21 or a date."""
    if isinstance(s, dt.date):
        return s
    s = str(s).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return dt.date.fromisoformat(s)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        raise ValueError(f"not a date: {s!r}")
    return dt.date(int(m[3]), int(m[2]), int(m[1]))


def uk(x):
    return d(x).strftime("%d/%m/%Y")


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def long_day(x):
    x = d(x)
    return f"{DAYS[x.weekday()]} {ordinal(x.day)} {MONTHS[x.month - 1]}"


def span(a, b):
    """Friday 21st to Sunday 23rd November / Monday 28th December to Sunday 3rd January"""
    a, b = d(a), d(b)
    if a.month == b.month and a.year == b.year:
        return f"{DAYS[a.weekday()]} {ordinal(a.day)} to {DAYS[b.weekday()]} {ordinal(b.day)} {MONTHS[b.month - 1]}"
    return f"{long_day(a)} to {long_day(b)}"


def add_months(x, n):
    if relativedelta:
        return x + relativedelta(months=n)
    m = x.month - 1 + n
    y, m = x.year + m // 12, m % 12 + 1
    import calendar
    return dt.date(y, m, min(x.day, calendar.monthrange(y, m)[1]))


def guests(party):
    """'5A 2C' -> 7, '8A + 1C' -> 9, '8' -> 8. None if unreadable."""
    nums = [int(n) for n in re.findall(r"\d+", str(party or ""))]
    return sum(nums) if nums else None


def num_word(n):
    return ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"][n] if 0 <= n <= 10 else str(n)


# ---------------------------------------------------------------- the sheet
def norm_name(s):
    return re.sub(r"[^a-z]", "", str(s or "").lower())


def booking_from_row(row, rownum=None):
    """A sheet row (columns A to K) -> booking dict. Money stays in 'net' and is never shown to Laura."""
    row = list(row) + [""] * (11 - len(row))
    net, src, dday, arrive, aday, depart, nights, name, party, dogs, notes = [str(c).strip() for c in row[:11]]
    b = {"row": rownum, "net": net, "source": src, "arrive": d(arrive).isoformat(), "depart": d(depart).isoformat(),
         "nights": nights, "name": name, "party": party, "dogs": dogs, "notes": notes,
         "arrive_day": dday, "depart_day": aday}
    return b


def validate(b):
    """Catch copying mistakes: weekday names and nights must agree with the dates."""
    problems = []
    a, z = d(b["arrive"]), d(b["depart"])
    if z <= a:
        problems.append("departure is not after arrival")
    if b.get("nights") and str((z - a).days) != str(b["nights"]).strip():
        problems.append(f"nights {b['nights']} but dates give {(z - a).days}")
    for key, day in (("arrive_day", a), ("depart_day", z)):
        v = (b.get(key) or "").strip()[:3].title()
        if v and v != DAY3[day.weekday()]:
            problems.append(f"{key} says {b[key]} but {uk(day)} is a {DAYS[day.weekday()]}")
    return problems


def key(b):
    return f"{b['arrive']}|{b['depart']}|{norm_name(b['name'])}"


def laura_view(bookings, today):
    """3 most recent past stays + stays in progress or arriving within 3 months (sorted)."""
    horizon = add_months(today, 3)
    past = sorted([b for b in bookings if d(b["depart"]) < today], key=lambda b: (b["depart"], b["arrive"]))
    now = sorted([b for b in bookings if d(b["depart"]) >= today and d(b["arrive"]) <= horizon],
                 key=lambda b: b["arrive"])
    held = sorted([b for b in bookings if d(b["arrive"]) > horizon], key=lambda b: b["arrive"])
    return past[-3:], now, held, horizon


def laura_part(b):
    return {k: (b.get(k) or "").strip() if isinstance(b.get(k), str) or b.get(k) is None else b.get(k) for k in LAURA_FIELDS}


def match(prev, cur):
    """Pair bookings across two lists: same dates first, then same guest name (date change)."""
    pairs, used_p, used_c = [], set(), set()
    for i, c in enumerate(cur):
        for j, p in enumerate(prev):
            if j not in used_p and p["arrive"] == c["arrive"] and p["depart"] == c["depart"]:
                pairs.append((p, c)); used_p.add(j); used_c.add(i); break
    for i, c in enumerate(cur):
        if i in used_c:
            continue
        for j, p in enumerate(prev):
            if j not in used_p and norm_name(p["name"]) and norm_name(p["name"]) == norm_name(c["name"]):
                pairs.append((p, c)); used_p.add(j); used_c.add(i); break
    new = [c for i, c in enumerate(cur) if i not in used_c]
    gone = [p for j, p in enumerate(prev) if j not in used_p]
    return pairs, new, gone


# ---------------------------------------------------------------- email wording
def describe_new(bs):
    def one(b):
        g = guests(b["party"])
        return f"{span(b['arrive'], b['depart'])}" + (f" for {g} guests" if g else "")
    if len(bs) == 1:
        return f"there's a new booking from {one(bs[0])}"
    items = [one(b) for b in bs]
    return f"there are {num_word(len(bs))} new bookings: " + ", ".join(items[:-1]) + f" and {items[-1]}"


def describe_change(p, c):
    bits = []
    surname = c["name"].split()[-1] if c["name"].split() else c["name"]
    who = f"the {surname} booking" if surname and len(c["name"].split()) > 1 else f"{c['name']}'s booking"
    when = MONTHS[d(c["arrive"]).month - 1]
    if (p["arrive"], p["depart"]) != (c["arrive"], c["depart"]):
        bits.append(f"has moved to {span(c['arrive'], c['depart'])}")
    if guests(p["party"]) != guests(c["party"]) or (p["dogs"] or "") != (c["dogs"] or ""):
        g = guests(c["party"])
        dogs = int(c["dogs"]) if str(c["dogs"]).strip().isdigit() else 0
        what = f"{g} guests" if g else c["party"]
        if dogs:
            what += f" and {dogs} dog{'s' if dogs != 1 else ''}"
        elif str(p["dogs"]).strip() not in ("", "0"):
            what += " and no dogs"
        bits.append(f"has changed to {what}")
    if (p["notes"] or "") != (c["notes"] or "") and c["notes"]:
        bits.append(f"now has a note: \"{c['notes']}\"")
    if norm_name(p["name"]) != norm_name(c["name"]) and not bits:
        bits.append(f"is now under the name {c['name']}")
    if not bits:
        return None
    return f"{who} in {when} " + " and ".join(bits)


def describe_cancel(b):
    a, z = d(b["arrive"]), d(b["depart"])
    weekend = "that weekend" if a.weekday() >= 4 else "for that stay"
    first = ordinal(a.day) + ("" if a.month == z.month else f" {MONTHS[a.month - 1]}")
    return (f"the stay from {first} to {ordinal(z.day)} {MONTHS[z.month - 1]} has been cancelled, "
            f"so no clean is needed {weekend}")


def join_summary(parts):
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "; ".join(parts[:-1]) + "; and " + parts[-1]


def dogs_line(bs):
    with_dogs = [b for b in bs if str(b.get("dogs", "")).strip().isdigit() and int(b["dogs"]) > 0]
    if not with_dogs:
        return ""
    def one(b, first):
        n = int(b["dogs"])
        return (f"{b['name']}'s stay includes" if first else f"{b['name']}'s includes") + f" {n} dog{'s' if n != 1 else ''}"
    items = [one(b, i == 0) for i, b in enumerate(with_dogs)]
    return "Just a heads up that " + (items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]) + "."


# ---------------------------------------------------------------- the check
def check(sheet, state, today, weekly_day=None, now_time=None):
    """sheet: {"a1","b1","title","rows":[[A..K],...],"first_row":n}
    state: {"rev_sheet", "last_sent": {"rev","issued","rows":[...]}, "known": [keys], "pending": {...}|null}"""
    today = d(today)
    weekly_day = weekly_day or state.get("weekly_day") or "Mon"
    bookings, problems = [], []
    for i, r in enumerate(sheet["rows"]):
        if not any(str(c).strip() for c in r):
            continue
        rn = sheet.get("first_row", 5) + i
        try:
            b = booking_from_row(r, rn)
        except ValueError as e:
            problems.append(f"row {rn}: {e}"); continue
        for p in validate(b):
            problems.append(f"row {rn} ({b['name']}): {p}")
        bookings.append(b)
    past3, now, held, horizon = laura_view(bookings, today)

    last = state.get("last_sent") or {"rev": None, "rows": []}
    sent_rows = [dict(r) for r in last["rows"]]
    known = set(state.get("known") or [])
    future = [b for b in bookings if d(b["depart"]) >= today]

    # compare what Laura has with what she should have now (future stays only: past ones never need an email)
    sent_future = [r for r in sent_rows if d(r["depart"]) >= today]
    pairs, new, gone = match(sent_future, now)
    changed = [(p, c) for p, c in pairs if any((p.get(k) or "") != (c.get(k) or "") for k in LAURA_FIELDS)]
    # a stay Laura has that is no longer in the window but still in the sheet (pushed out by a date change) is not a cancellation
    gone_cancelled = [g for g in gone if not any(norm_name(g["name"]) == norm_name(b["name"]) and g["name"] for b in held)]

    brand_new = [b for b in new if key(b) not in known]
    came_in = [b for b in new if key(b) in known]          # held bookings now within 3 months
    urgent_cut = today + dt.timedelta(days=7)
    urgent = [b for b in brand_new if d(b["arrive"]) <= urgent_cut] + \
             [c for p, c in changed if d(c["arrive"]) <= urgent_cut] + \
             [g for g in gone_cancelled if d(g["arrive"]) <= urgent_cut]

    is_round_up_day = DAY3[today.weekday()] == weekly_day[:3].title()
    if brand_new or changed or gone_cancelled:
        kind = "short" if (urgent and len(brand_new) == 1 and not changed and not gone_cancelled) else "standard"
    elif came_in and is_round_up_day:
        kind = "roundup"
    else:
        kind = "none"

    # wording
    parts = []
    if brand_new:
        parts.append(describe_new(brand_new))
    for p, c in changed:
        parts.append(describe_change(p, c))
    for g in gone_cancelled:
        parts.append(describe_cancel(g))
    if came_in and kind != "none":
        lead = "the schedule now also includes " if parts else ""
        items = [f"{span(b['arrive'], b['depart'])}" + (f" ({guests(b['party'])} guests)" if guests(b['party']) else "") for b in came_in]
        parts.append(lead + ("the stay from " if len(items) == 1 else "the stays from ") + " and ".join(items)
                     if lead else (("there's a stay from " if len(items) == 1 else "there are stays from ") + " and ".join(items)))
    summary = join_summary(parts)

    last_rev = int(state.get("rev_sheet") or 0)
    pending = state.get("pending")
    rev = pending["rev"] if pending else last_rev + 1
    if pending and kind != "none":
        # a draft for this Rev is already waiting in Gmail: only replace it if something changed since it was made
        def fut(rows):
            return sorted(json.dumps(laura_part(r), sort_keys=True) for r in rows if d(r["depart"]) >= today)
        if fut(pending["rows"]) == fut(now):
            kind = "waiting"
    email = None
    if kind not in ("none", "waiting"):
        o = OPENINGS[rev % len(OPENINGS)]
        cl = CLOSINGS[rev % len(CLOSINGS)]
        in_update = brand_new + [c for p, c in changed] + came_in
        dl = dogs_line(in_update)
        if kind == "short":
            b = brand_new[0]
            g = guests(b["party"])
            subject = f"New booking this week at 22 Saxonbury Road, {long_day(b['arrive'])} (Rev {rev})"
            body = (f"Hi Laura,\n\n{o}\n\nJust a quick one: we've had a booking come in for "
                    f"{(str(g) + ' guests') if g else b['party']} arriving on {long_day(b['arrive'])}, so please could you "
                    f"schedule a changeover clean for {long_day(b['arrive'])}.\n\nI've attached the updated schedule.\n\n"
                    + (dl + "\n\n" if dl else "") + f"{cl}\n\nMark")
        elif kind == "roundup":
            subject = f"22 Saxonbury Road: booking schedule for the next three months (Rev {rev})"
            body = (f"Hi Laura,\n\n{o}\n\nHere's the updated schedule covering the next three months. Since I last sent "
                    f"it over, {summary}.\n\n" + (dl + "\n\n" if dl else "") + f"{cl}\n\nMark")
        else:
            subject = f"22 Saxonbury Road: updated booking schedule (Rev {rev})"
            body = (f"Hi Laura,\n\n{o}\n\nI've attached the latest booking schedule for 22 Saxonbury Road. Since the "
                    f"last one, {summary}.\n\n" + (dl + "\n\n" if dl else "") + f"{cl}\n\nMark")
        email = {"to": state.get("laura_email") or [], "cc": state.get("cc") or [], "subject": subject, "body": body,
                 "whatsapp_prompt": bool(urgent)}

    fy = sheet.get("title") or ""
    pdf_rows = [dict(laura_part(b), source=b["source"], past=True) for b in past3] + \
               [dict(laura_part(b), source=b["source"], past=False) for b in now]
    try:
        from zoneinfo import ZoneInfo
        uk_now = dt.datetime.now(ZoneInfo("Europe/London"))
    except Exception:  # pragma: no cover
        uk_now = dt.datetime.now()
    stamp = (now_time or uk_now).strftime("%d/%m/%Y %H:%M")
    fy_code = re.search(r"FY(\d{4})-(\d{2})", fy)
    fy_tag = f"FY{fy_code[1][2:]}-{fy_code[2]}" if fy_code else "FY"
    plan = {
        "today": today.isoformat(), "horizon": horizon.isoformat(), "kind": kind, "rev": rev,
        "pdf_name": f"Rev-{rev:03d}_22_Saxonbury_Road_{fy_tag}_Cleaning_Schedule.pdf",
        "title": fy, "stamp": stamp, "pdf_rows": pdf_rows,
        "held": [{"arrive": b["arrive"], "depart": b["depart"], "name": b["name"]} for b in held],
        "changes": {"new": [laura_part(b) for b in brand_new], "changed": [[laura_part(p), laura_part(c)] for p, c in changed],
                    "cancelled": [laura_part(g) for g in gone_cancelled], "came_in": [laura_part(b) for b in came_in]},
        "email": email, "problems": problems,
        "sheet_updates": {
            "grey_rows_through": max([b["row"] for b in bookings if d(b["depart"]) < today and b["row"]] or [0]),
            "on_issue": {"A1": f"Rev {rev}", "B1": f"Updated {today.strftime('%d/%m/%Y')}",
                         "spreadsheet_title": f"Rev-{rev:03d} 22 Saxonbury Road Booking Summary"}},
        "new_state": {
            "known": sorted(key(b) for b in future),
            "if_issued": {"rev": rev, "issued": today.isoformat(),
                          "rows": [laura_part(b) for b in past3 + now]},
        },
    }
    return plan


# ---------------------------------------------------------------- the PDF (same look as Rev 38)
def make_pdf(plan, out, test=False):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle

    W, H = landscape(A4)
    c = canvas.Canvas(str(out), pagesize=(W, H), pageCompression=1)
    c.setTitle("22 Saxonbury Road cleaning schedule")
    c.setAuthor("Mark Emerson")
    small = ParagraphStyle("n", fontName="Helvetica", fontSize=8, leading=9.5)
    head = ["Source", "Date Arriving", "", "Date Departing", "", "Nights", "Name", "Party Size", "Dog(s)", "Notes"]
    data = [[plan["title"]] + [""] * 9, head]
    for r in plan["pdf_rows"]:
        a, z = d(r["arrive"]), d(r["depart"])
        data.append([r.get("source", ""), DAYS[a.weekday()], uk(a), DAYS[z.weekday()], uk(z), str((z - a).days),
                     r["name"], r["party"], r["dogs"], Paragraph(r["notes"] or "", small)])
    widths = [36, 62, 58, 62, 58, 34, 120, 60, 38, 230]
    t = Table(data, colWidths=widths, rowHeights=[16, 16] + [None] * (len(data) - 2))
    yellow, green, peach, cream, grey = (colors.HexColor("#FFFF00"), colors.HexColor("#92D050"),
                                         colors.HexColor("#F7CAAC"), colors.HexColor("#FEF2CB"), colors.HexColor("#D8D8D8"))
    st = [("SPAN", (0, 0), (-1, 0)), ("SPAN", (1, 1), (2, 1)), ("SPAN", (3, 1), (4, 1)),
          ("BACKGROUND", (0, 0), (-1, 0), cream), ("FONT", (0, 0), (-1, 1), "Helvetica-Bold", 8.5),
          ("ALIGN", (0, 0), (-1, 1), "CENTER"), ("BACKGROUND", (1, 1), (2, 1), green),
          ("BACKGROUND", (3, 1), (5, 1), peach), ("BACKGROUND", (6, 1), (9, 1), yellow),
          ("FONT", (0, 2), (-1, -1), "Helvetica", 8), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("ALIGN", (1, 2), (1, -1), "RIGHT"), ("ALIGN", (3, 2), (3, -1), "RIGHT"),
          ("ALIGN", (5, 2), (5, -1), "CENTER"), ("ALIGN", (7, 2), (8, -1), "CENTER"),
          ("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    for i, r in enumerate(plan["pdf_rows"]):
        if r.get("past"):
            st.append(("BACKGROUND", (0, i + 2), (-1, i + 2), grey))
    t.setStyle(TableStyle(st))
    tw, th = t.wrap(W, H)
    x0, top = (W - tw) / 2, H - 36
    t.drawOn(c, x0, top - th)
    c.setFont("Helvetica", 8)
    c.drawString(x0, top - th - 14, "Grey rows are recent stays that have finished. Bookings more than three months ahead "
                                    "will be added nearer the time.")
    rev_label = f"Rev-{plan['rev']:03d}"
    c.setFont("Helvetica", 7.5)
    c.drawString(x0, 22, f"{rev_label}    Updated {plan['stamp']}" + ("    TEST, NOT ISSUED" if test else ""))
    c.drawRightString(x0 + tw, 22, "Page 1")
    if test:
        c.saveState(); c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12)); c.setFont("Helvetica-Bold", 90)
        c.translate(W / 2, H / 2 - 40); c.rotate(18); c.drawCentredString(0, 0, "TEST"); c.restoreState()
    c.showPage(); c.save()
    return out


# ---------------------------------------------------------------- reading booking emails
MON3 = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}


def _party(adults, children):
    return f"{adults}A" + (f" {children}C" if children else "")


def parse_email(text, subject="", received=None):
    """Airbnb 'Reservation confirmed' or Vrbo 'Instant Booking from' / booking confirmed email -> booking fields."""
    t = text.replace("\r", "")
    out = {"source": None, "name": None, "arrive": None, "depart": None, "party": None, "dogs": "", "net": None,
           "code": None}
    year = (received or dt.date.today()).year if not isinstance(received, str) else d(received).year
    if "airbnb" in t.lower() or "airbnb" in subject.lower():
        out["source"] = "ABNB"
        m = re.search(r"Reservation confirmed - (.+?) arrives", subject)
        if m:
            out["name"] = m[1].strip()
        m = re.search(r"Check-in\s+Checkout\s+\w{3}, (\w{3}) (\d{1,2})\s+\w{3}, (\w{3}) (\d{1,2})", t)
        if m:
            base = received if isinstance(received, dt.date) else dt.date(year, 1, 1)
            a = dt.date(year, MON3[m[1].lower()], int(m[2]))
            if isinstance(received, dt.date) and a < received - dt.timedelta(days=2):
                a = a.replace(year=a.year + 1)
            z = dt.date(a.year, MON3[m[3].lower()], int(m[4]))
            if z <= a:
                z = z.replace(year=z.year + 1)
            out["arrive"], out["depart"] = a.isoformat(), z.isoformat()
        m = re.search(r"GUESTS\s+(.+)", t)
        if m:
            g = m[1].lower()
            ad = re.search(r"(\d+) adult", g); ch = re.search(r"(\d+) child", g); inf = re.search(r"(\d+) infant", g)
            pets = re.search(r"(\d+) pet", g)
            kids = (int(ch[1]) if ch else 0) + (int(inf[1]) if inf else 0)
            out["party"] = _party(int(ad[1]) if ad else 0, kids)
            if pets:
                out["dogs"] = pets[1]
        m = re.search(r"YOU EARN\s+£([\d,]+\.\d\d)", t)
        if m:
            out["net"] = m[1]
        m = re.search(r"CONFIRMATION CODE\s+(\w+)", t)
        if m:
            out["code"] = m[1]
    elif "vrbo" in t.lower() or "homeaway" in t.lower() or "vrbo" in subject.lower():
        out["source"] = "VRBO"
        m = re.search(r"Holidaymaker Name:\s*(.+)", t)
        out["name"] = m[1].strip() if m else None
        m = re.search(r"Dates:\s*(\d{1,2}) (\w{3})(?: (\d{4}))? - (\d{1,2}) (\w{3}) (\d{4})", t)
        if m:
            z = dt.date(int(m[6]), MON3[m[5].lower()], int(m[4]))
            a = dt.date(int(m[3]) if m[3] else z.year, MON3[m[2].lower()], int(m[1]))
            if a > z:
                a = a.replace(year=a.year - 1)
            out["arrive"], out["depart"] = a.isoformat(), z.isoformat()
        m = re.search(r"Guests:\s*(.+)", t)
        if m:
            g = m[1].lower()
            ad = re.search(r"(\d+) adult", g); ch = re.search(r"(\d+) child", g); pets = re.search(r"(\d+) pet", g)
            out["party"] = _party(int(ad[1]) if ad else 0, int(ch[1]) if ch else 0)
            if pets:
                out["dogs"] = pets[1]
        m = re.search(r"Estimated payout\**\s+£\s*([\d,]+\.\d\d)", t)
        if m:
            out["net"] = m[1]
        m = re.search(r"Reservation ID:\s*(\S+)", t)
        if m:
            out["code"] = m[1]
    return out


def sheet_row(b):
    """booking fields -> sheet row A..K (USER_ENTERED values)."""
    a, z = d(b["arrive"]), d(b["depart"])
    return [b.get("net") or "", b.get("source") or "", DAY3[a.weekday()], uk(a), DAY3[z.weekday()], uk(z),
            str((z - a).days), b.get("name") or "", b.get("party") or "", b.get("dogs") or "", b.get("notes") or ""]


# ---------------------------------------------------------------- calendar feeds vs the sheet
def feeds(sheet, cache_dir, today):
    today = d(today)
    rows = []
    for i, r in enumerate(sheet["rows"]):
        if any(str(c).strip() for c in r):
            try:
                rows.append(booking_from_row(r, sheet.get("first_row", 5) + i))
            except ValueError:
                pass
    out = {"missing_from_sheet": [], "missing_from_feed": [], "ok": []}
    for ch, code in (("airbnb", "ABNB"), ("vrbo", "VRBO")):
        p = Path(cache_dir) / f"{ch}.json"
        if not p.exists():
            out.setdefault("errors", []).append(f"no {ch} cache")
            continue
        ev = [e for e in json.loads(p.read_text())["events"] if d(e["end"]) >= today]
        mine = [b for b in rows if b["source"] == code and d(b["depart"]) >= today]
        for e in ev:
            hit = [b for b in rows if b["arrive"] == e["start"] and b["depart"] == e["end"]]
            (out["ok"] if hit else out["missing_from_sheet"]).append({"source": code, "arrive": e["start"], "depart": e["end"]})
        for b in mine:
            if not any(e["start"] == b["arrive"] and e["end"] == b["depart"] for e in ev):
                out["missing_from_feed"].append({"source": code, "arrive": b["arrive"], "depart": b["depart"],
                                                 "name": b["name"], "row": b["row"]})
    return out


# ---------------------------------------------------------------- command line
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    c1 = sp.add_parser("check"); c1.add_argument("--sheet", required=True); c1.add_argument("--state", required=True)
    c1.add_argument("--today"); c1.add_argument("--weekly-day", default=None); c1.add_argument("--out", default="plan.json")
    c2 = sp.add_parser("pdf"); c2.add_argument("--plan", required=True); c2.add_argument("--out"); c2.add_argument("--test", action="store_true")
    c2.add_argument("--b64", action="store_true", help="also write <out>.b64 for the Gmail and Drive connectors")
    c3 = sp.add_parser("parse-email"); c3.add_argument("--file", required=True); c3.add_argument("--subject", default="")
    c3.add_argument("--received")
    c4 = sp.add_parser("feeds"); c4.add_argument("--sheet", required=True); c4.add_argument("--cache", required=True); c4.add_argument("--today")
    a = ap.parse_args()
    if a.cmd == "check":
        today = d(a.today) if a.today else dt.date.today()
        plan = check(json.loads(Path(a.sheet).read_text()), json.loads(Path(a.state).read_text()), today, a.weekly_day)
        Path(a.out).write_text(json.dumps(plan, indent=1, ensure_ascii=False))
        print(json.dumps({k: plan[k] for k in ("kind", "rev", "pdf_name", "changes", "problems")}, indent=1, ensure_ascii=False))
        if plan["email"]:
            print("\nSUBJECT:", plan["email"]["subject"]); print(plan["email"]["body"])
    elif a.cmd == "pdf":
        plan = json.loads(Path(a.plan).read_text())
        out = a.out or plan["pdf_name"].replace(".pdf", "_TEST.pdf" if a.test else ".pdf")
        make_pdf(plan, out, a.test)
        raw = Path(out).read_bytes()
        if a.b64:
            Path(str(out) + ".b64").write_text(base64.b64encode(raw).decode())
        print(out, len(raw), "bytes, sha256", hashlib.sha256(raw).hexdigest()[:16])
    elif a.cmd == "parse-email":
        rec = d(a.received) if a.received else None
        b = parse_email(Path(a.file).read_text(), a.subject, rec)
        print(json.dumps({"booking": b, "sheet_row": sheet_row(b) if b["arrive"] else None}, indent=1))
    elif a.cmd == "feeds":
        print(json.dumps(feeds(json.loads(Path(a.sheet).read_text()), a.cache, d(a.today) if a.today else dt.date.today()), indent=1))


if __name__ == "__main__":
    main()
