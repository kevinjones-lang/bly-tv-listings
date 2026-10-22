#!/usr/bin/env python3
"""Write training.json (next 30 days) from the public ICS feed of the "Bly Team Training" Google Calendar.
Usage (repo root): python3 scripts/update_training.py [days]
Needs: pip install --user --break-system-packages icalendar recurring-ical-events
Privacy: no attendees, no URLs, meeting IDs or passwords are written - only title, times, place type, short text."""
import sys, os, re, json, html, datetime, urllib.request
from zoneinfo import ZoneInfo
import icalendar, recurring_ical_events
CAL_ID = "e2cc7b52c72b20cfd9d1387045e6906798e8f91ed23dd90a7c40f37ccca24234@group.calendar.google.com"
ICS = "https://calendar.google.com/calendar/ical/" + CAL_ID.replace("@", "%40") + "/public/basic.ics"
CT = ZoneInfo("America/Chicago")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = re.compile(r"(\bweek\s+)?\b(pass(word|code)?|pwd|pin|meeting id|webinar id|dial|one tap)\b.*$", re.I)
URL = re.compile(r"(https?://|www\.)\S+|\S+@\S+\.\S+", re.I)

def clean(s, n=None):
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    s = URL.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -|:·")
    s = SECRET.sub("", s).strip(" -|:·,")
    s = re.sub(r"^(register here|description changed)\s*:?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*(join with|click here|register here)\s*:?\s*$", "", s, flags=re.I).strip(" -|:·,")
    if n and len(s) > n:
        s = s[:n].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
    return s

def place(loc, desc):
    t = f"{loc} {desc}".lower()
    if "zoom.us" in t or "zoom" in t: return "Zoom"
    if "meet.google" in t: return "Google Meet"
    if "teams.microsoft" in t: return "Teams"
    if "exp world" in t: return "eXp World"
    if "office" in t or "217 e main" in t: return "In office"
    if "har.com/education" in t: return "HAR.com"
    if loc and not URL.search(loc): return clean(loc, 40)
    return ""

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    req = urllib.request.Request(ICS, headers={"User-Agent": "Mozilla/5.0"})
    cal = icalendar.Calendar.from_ical(urllib.request.urlopen(req, timeout=60).read())
    now = datetime.datetime.now(CT); end = now + datetime.timedelta(days=days)
    out, seen = [], set()
    for e in recurring_ical_events.of(cal).between(now, end):
        if str(e.get("STATUS", "")).upper() == "CANCELLED" or str(e.get("CLASS", "")).upper() in ("PRIVATE", "CONFIDENTIAL"):
            continue
        s, f = e.get("DTSTART").dt, (e.get("DTEND") or e.get("DTSTART")).dt
        allday = not isinstance(s, datetime.datetime)
        if allday:
            s = datetime.datetime.combine(s, datetime.time(0), CT); f = datetime.datetime.combine(f, datetime.time(0), CT)
        s, f = s.astimezone(CT), f.astimezone(CT)
        if f < now: continue
        title = clean(e.get("SUMMARY"), 70)
        key = (title, s.isoformat())
        if not title or key in seen: continue
        seen.add(key)
        loc, desc = str(e.get("LOCATION", "")), str(e.get("DESCRIPTION", ""))
        out.append({"title": title, "start": s.isoformat(), "end": f.isoformat(), "allDay": allday,
                    "where": place(loc, desc), "detail": clean(desc, 120)})
    out.sort(key=lambda x: x["start"])
    json.dump({"asOf": now.isoformat(timespec="minutes"), "calendar": "Bly Team Training", "days": days, "events": out},
              open(os.path.join(ROOT, "training.json"), "w"), indent=2, ensure_ascii=False)
    print(f"{len(out)} events in next {days} days")

if __name__ == "__main__": main()
