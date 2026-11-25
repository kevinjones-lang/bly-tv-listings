#!/usr/bin/env python3
"""Write listings.json from the Closings Board "Listings" tab (HAR fallback).

The tab is the source of record when HAR.com returns 403 or a captcha and
scripts/update_har.py cannot refresh sales. Leases stay off (Kevin, 2026-11-03):
this script always writes leases: [] and leaseAsOf/leaseSource null.

Usage (repo root):
  python3 scripts/update_listings_from_sheet.py --from-rows FILE

FILE is the raw Listings tab cells (columns A:AE, displayed values) as the
Google Sheets connector's read_range returns them:
  {"ranges": [{"sheet": "Listings", "content": "<csv>"}]}
A plain JSON list of rows ([["Agent", ...], ...]) or {"values": [[...]]}
also works. Never write this raw file into the repo: it contains the lead
Source column, which is not published.

The tab holds side-by-side blocks (A-H and J-Q), each headed Agent, Source,
Address, List Price, Bed/Bath, sq ft, DOM, Vacant/Occupied. Optional later
columns (Status, Photo/Photo URL, HAR URL, Type) are read when present.
Left block first, then right, matching update_closings.py.

Only public listing fields are written: street, city, price, beds/baths/sqft
or lot, agent, status, type, and a photo/url when the sheet has a safe URL.
Lead Source, DOM, occupancy, client/seller names, phones, emails and meeting
links are never published. Lease/rent rows are skipped. If the sheet has no
photo URL, photo is null and the TV already shows the brand .noimg placeholder.

* Exit 2 and leave listings.json untouched if the file is unreadable, the
  Address/List Price columns are missing, or zero sale rows parse. Zero rows
  is a failed read, not an empty inventory.
* Status defaults to "Active" when the tab has no Status column (the Listings
  tab is the team's active-sale inventory). Vacant/Occupied is not a status.
* Existing img/listings photos are left in place (HAR can reuse them later).
  This script does not prune, and it does not invent HAR ids or photos.

The twice-daily refresh should try HAR first, then this command only if HAR
fails (so a working HAR day does not need a Sheets read):

  python3 scripts/update_har.py
  # on exit 2: read Listings!A1:AE400 (read-only, displayed values) to
  # /workspace/bly-tv-listings-listings-raw.json, then:
  python3 scripts/update_listings_from_sheet.py --from-rows /workspace/bly-tv-listings-listings-raw.json

Or, if the rows file is already saved, one command tries HAR and falls back:

  python3 scripts/update_har.py --from-rows /workspace/bly-tv-listings-listings-raw.json
"""
import datetime, json, os, re, sys, urllib.parse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_closings as uc
import update_har as uh

BOARD_SOURCE = "Closings Board sheet, Listings tab"
CT = ZoneInfo("America/Chicago")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = re.compile(r"\S+@\S+\.\S+")
PHONE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
LINK = re.compile(r"(https?://|www\.)|\b(zoom\.us|meet\.google|teams\.microsoft)\b", re.I)
HAR = re.compile(r"^https://(www\.)?har\.com/\S+$", re.I)
LEASE = re.compile(r"\b(for\s+lease|for\s+rent|lease|rental|rent)\b", re.I)
MONTHLY = re.compile(r"/\s*mo\b|per\s+month|/\s*month", re.I)
LOT_MARK = re.compile(r"\s*\(lots?\)\s*$", re.I)
BB = re.compile(r"^(\d+)(?:\s*/\s*\d+)?\s*&\s*(\d+)(?:\s*/\s*(\d+))?$")

# Headers searched to the LEFT of Address (agent only; Source is never read).
LEFT_COLS = {"agent": ("agent", "listing agent")}
# Headers searched between Address and the next block's Agent/Address.
RIGHT_COLS = {
    "price": ("list price", "price"),
    "bb": ("bed/bath", "bed / bath", "beds/baths"),
    "size": ("sq ft / lot size", "sq ft", "sqft"),
    "status": ("status",),
    "type": ("type", "property type"),
    "photo": ("photo url", "image url", "photo", "image"),
    "url": ("har url", "listing url", "url"),
}


class FeedError(Exception):
    pass


def stop(msg):
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def now_ct():
    return datetime.datetime.now(CT).isoformat(timespec="minutes")


def clean(s):
    return re.sub(r"[\s\u00a0]+", " ", s or "").strip()


def money(v):
    if re.fullmatch(r"\d+(\.\d+)?", v):
        n = float(v)
        return "$" + (f"{int(n):,}" if n.is_integer() else f"{n:,.2f}")
    return v


def sensitive(v):
    return bool(EMAIL.search(v) or PHONE.search(v) or LINK.search(v))


def is_lease(*parts):
    blob = " ".join(p for p in parts if p)
    return bool(LEASE.search(blob) or MONTHLY.search(blob))


def parse_bb(text):
    t = clean(text)
    if not t or t.upper() in ("N/A", "NA", "-", "—"):
        return None, None, None
    m = BB.fullmatch(t)
    if not m:
        return None, None, None
    beds, full = int(m.group(1)), int(m.group(2))
    half = int(m.group(3)) if m.group(3) is not None else None
    if half == 0:
        half = None
    return beds, full, half


def parse_size(text):
    t = clean(text)
    if not t or t in ("-", "—", "N/A", "NA"):
        return None, None
    if re.search(r"acre|\blot\b", t, re.I):
        if t.startswith("."):
            t = "0" + t
        return None, t
    n = t.replace(",", "")
    if re.fullmatch(r"\d+", n):
        return int(n), None
    return None, None


def split_address(addr):
    lot = bool(LOT_MARK.search(addr))
    core = LOT_MARK.sub("", addr).strip()
    if "," in core:
        street, city = core.split(",", 1)
    else:
        street, city = core, ""
    return clean(street), clean(city), "Lot" if lot else ""


def listing_id(street, city, used):
    raw = re.sub(r"[^a-z0-9]+", "-", (street + " " + city).lower()).strip("-") or "listing"
    slug, n = raw, 2
    while slug in used:
        slug = f"{raw}-{n}"
        n += 1
    used.add(slug)
    return slug


def safe_http(url):
    u = clean(url)
    if not re.match(r"^https?://", u, re.I):
        return ""
    if re.search(r"zoom\.us|meet\.google|teams\.microsoft|teams\.live|webex\.com", u, re.I):
        return ""
    if re.match(r"^javascript:", u, re.I):
        return ""
    return u


def header_index(head, names, start, end):
    want = set(names)
    for i in range(start, end):
        if i < len(head) and head[i] in want:
            return i
    return None


def parse_board(rows):
    rows = [[clean(str(c)) for c in r] for r in rows]
    if len(rows) < 2:
        raise FeedError("empty Listings tab")
    head = [h.lower() for h in rows[0]]
    starts = [i for i, h in enumerate(head) if h == "address"]
    if not starts:
        raise FeedError("Listings tab columns missing (need 'Address' and 'List Price')")
    agents_at = [i for i, h in enumerate(head) if h == "agent"]
    blocks = []
    for n, a in enumerate(starts):
        next_addr = starts[n + 1] if n + 1 < len(starts) else len(head)
        next_agent = next((i for i in agents_at if i > a), next_addr)
        right_end = min(next_agent, next_addr)
        left_start = starts[n - 1] + 1 if n else 0
        # If the previous address block ended at an Agent header, left_start
        # is already that Agent column; otherwise walk back from Address.
        price = header_index(head, RIGHT_COLS["price"], a + 1, right_end)
        if price is None:
            continue
        idx = {"address": a, "price": price}
        for key, names in LEFT_COLS.items():
            j = header_index(head, names, left_start, a)
            if j is not None:
                idx[key] = j
        for key, names in RIGHT_COLS.items():
            if key == "price":
                continue
            j = header_index(head, names, a + 1, right_end)
            if j is not None:
                idx[key] = j
        blocks.append(idx)
    if not blocks:
        raise FeedError("Listings tab columns missing (need 'Address' and 'List Price')")
    out, used, skipped = [], set(), []
    for idx in blocks:
        for r in rows[1:]:
            cell = lambda k: r[idx[k]] if k in idx and idx[k] < len(r) else ""
            addr, price = cell("address"), cell("price")
            if not addr or not price or addr.startswith("#") or re.search("address", addr, re.I):
                continue
            if sensitive(addr) or sensitive(price):
                skipped.append((addr or price, "sensitive address or price"))
                continue
            typ = cell("type")
            status = cell("status") or "Active"
            if is_lease(addr, price, typ, status, cell("bb")):
                skipped.append((addr, "lease/rent row (leases stay off)"))
                continue
            street, city, lot_type = split_address(addr)
            if not street:
                continue
            beds, full, half = parse_bb(cell("bb"))
            sqft, lot = parse_size(cell("size"))
            rec = {
                "id": listing_id(street, city, used),
                "street": street,
                "city": city,
                "price": money(price),
                "status": status if not sensitive(status) else "Active",
                "type": lot_type or typ,
                "beds": beds,
                "fullBaths": full,
                "halfBaths": half,
                "sqft": sqft,
                "lot": lot,
                "url": safe_http(cell("url")),
                "photoSource": safe_http(cell("photo")) or None,
                "photo": None,
            }
            agent = cell("agent")
            if agent and not sensitive(agent) and agent.lower() not in ("agent", "listing agent"):
                rec["agent"] = agent
            out.append(rec)
    if not out:
        raise FeedError("zero sale rows parsed from Listings tab")
    return out, skipped


def load_rows(path):
    try:
        with open(path, encoding="utf-8") as f:
            return parse_board(uc.board_rows(json.load(f)))
    except (OSError, ValueError) as e:
        raise FeedError(f"cannot read rows file {path}: {e}")


def attach_photos(sales, img_dir, save=None):
    save = save or uh.save_photo
    for rec in sales:
        src = rec.get("photoSource")
        if not src:
            rec["photo"] = None
            continue
        rec["photo"] = save(rec["id"], src, img_dir)


def write_document(doc, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def run(root=None, clock=None, rows_file=None, save_photo=None):
    root = root or ROOT
    clock = clock or now_ct
    path = os.path.join(root, "listings.json")
    if not rows_file:
        raise FeedError("usage: update_listings_from_sheet.py --from-rows FILE")
    sales, skipped = load_rows(rows_file)
    attach_photos(sales, os.path.join(root, "img", "listings"), save=save_photo)
    doc = uh.listing_document(sales, [], clock(), None, None)
    doc["source"] = BOARD_SOURCE
    write_document(doc, path)
    return doc, skipped


def main(root=None, clock=None, argv=None, save_photo=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "--from-rows":
        stop("usage: update_listings_from_sheet.py --from-rows FILE")
    try:
        doc, skipped = run(root=root, clock=clock, rows_file=argv[1], save_photo=save_photo)
    except FeedError as e:
        stop(f"BLOCKED/ERROR: Listings tab unreadable; listings.json left unchanged. {e}")
    nphoto = sum(1 for x in doc["listings"] if x.get("photo"))
    print(f"{len(doc['listings'])} sale listings from Closings Board Listings tab, {nphoto} with photos; leases off")
    for addr, why in skipped:
        print(f"skipped: {addr} ({why})", file=sys.stderr)
    return doc


if __name__ == "__main__":
    main()
