#!/usr/bin/env python3
"""Write closings.json (pending closings) from the public "Bly TV Listings Feed" Google Sheet.

Feed: https://docs.google.com/spreadsheets/d/1rFoocO92dt3iqswf6IH6-oML7ZfhzdOAcVli5ujVJYU
Read the same way index.html reads it: the gviz CSV first, then the export CSV.
Columns are matched by exact header name, as on the TV: "Pending Address",
"Pending Price", "Expected Close" (fed from the Closings Board "Closings" tab).

Usage (repo root):
  python3 scripts/update_closings.py                      # public feed CSV (needs the feed shared publicly)
  python3 scripts/update_closings.py --from-rows FILE     # Closings Board "Closings" tab, read via the
                                                          # Google Sheets connector (source of record)
No extra packages.

--from-rows FILE takes the raw Closings tab cells (columns A:M, displayed values)
as the connector's read_range returns them: {"ranges": [{"content": "<csv>"}]}.
A plain JSON list of rows ([["Agent", ...], ...]) or {"values": [[...]]} also works.
The tab holds side-by-side blocks (A-F and H-M), each headed Agent, Source,
Address, List Price, Lender, Close Date. Only Address, List Price and Close Date
are read (left block first, then right, the same order as the TV feed); Agent,
Source and Lender are never read. Blank rows and the total row (a price with no
address) are skipped.

* Exit 2 and leave closings.json untouched if the fetch fails, the sheet is not
  public (Google answers with a sign-in HTML page), the pending columns are
  missing, or zero pending rows parse. Zero rows is a failed read, not an empty
  pipeline, so a broken feed cannot wipe the list.
* Rows are kept with the TV's rule: address and price present, address not a
  "#..." note and not a repeated header.
* Privacy: only address, price, expectedClose, status, and (when the feed has
  such a column) agent and a HAR url are written. Buyer, seller, client, lender,
  email, phone, notes, and meeting links are never read into the output, and any
  value that looks like an email, phone number, or link is dropped.
"""
import csv, datetime, io, json, os, re, sys, urllib.error, urllib.request
from zoneinfo import ZoneInfo

FEED_ID = "1rFoocO92dt3iqswf6IH6-oML7ZfhzdOAcVli5ujVJYU"
BASE = "https://docs.google.com/spreadsheets/d/" + FEED_ID
URLS = [BASE + "/gviz/tq?tqx=out:csv&headers=1", BASE + "/export?format=csv"]
SOURCE = "Bly TV Listings Feed sheet"
BOARD_SOURCE = "Closings Board sheet, Closings tab"
CT = ZoneInfo("America/Chicago")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
# Exact header names (lowercased). Same names as PCOLS in index.html; agent/url are optional.
COLS = {"address": ["pending address"], "price": ["pending price"], "expectedClose": ["expected close"],
        "agent": ["pending agent", "listing agent"], "url": ["pending har url", "har url", "pending url"]}
REQUIRED = ("address", "price")
EMAIL = re.compile(r"\S+@\S+\.\S+")
PHONE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
LINK = re.compile(r"(https?://|www\.)|\b(zoom\.us|meet\.google|teams\.microsoft)\b", re.I)
HAR = re.compile(r"^https://(www\.)?har\.com/\S+$", re.I)


class FeedError(Exception):
    pass


def stop(msg):
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def now_ct():
    return datetime.datetime.now(CT).isoformat(timespec="minutes")


def get(url):
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(url + sep + "_=" + str(int(datetime.datetime.now().timestamp())), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig", "replace")


def clean(s):
    return re.sub(r"[\s\u00a0]+", " ", s or "").strip()


def money(v):
    # Same as the TV: a bare number is shown as $1,234; anything else as typed.
    if re.fullmatch(r"\d+(\.\d+)?", v):
        n = float(v)
        return "$" + (f"{int(n):,}" if n.is_integer() else f"{n:,.2f}")
    return v


def sensitive(v):
    return bool(EMAIL.search(v) or PHONE.search(v) or LINK.search(v))


def parse_feed(text):
    if re.match(r"^\s*<", text):
        raise FeedError("not CSV (feed not public? Google returned an HTML/sign-in page)")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise FeedError("empty feed")
    head = [clean(h).lower() for h in rows[0]]
    idx = {}
    for key, names in COLS.items():
        j = next((i for i, h in enumerate(head) if h in names), None)
        if j is None:
            if key in REQUIRED:
                raise FeedError("pending columns missing (need 'Pending Address' and 'Pending Price')")
            continue
        idx[key] = j
    out = []
    for r in rows[1:]:
        o = {k: clean(r[j]) if j < len(r) else "" for k, j in idx.items()}
        a = o.get("address", "")
        if not a or not o.get("price") or a.startswith("#") or re.search("address", a, re.I):
            continue
        if sensitive(a):
            continue
        rec = {"address": a, "price": money(o["price"])}
        close = o.get("expectedClose", "")
        if close and not sensitive(close):
            rec["expectedClose"] = close
        rec["status"] = "Pending"
        agent = o.get("agent", "")
        if agent and not sensitive(agent):
            rec["agent"] = agent
        url = o.get("url", "")
        if HAR.match(url):
            rec["url"] = url
        out.append(rec)
    if not out:
        raise FeedError("zero pending rows parsed")
    return out


def board_rows(raw):
    """Raw connector output -> list of rows (lists of strings)."""
    if isinstance(raw, dict) and isinstance(raw.get("ranges"), list):
        rows = []
        for rng in raw["ranges"]:
            if "content" in rng:
                rows += list(csv.reader(io.StringIO(rng["content"])))
            else:
                rows += rng.get("values", [])
        return rows
    if isinstance(raw, dict) and isinstance(raw.get("values"), list):
        return raw["values"]
    if isinstance(raw, list):
        return raw
    raise FeedError("unrecognized rows file (expected connector read_range JSON or a list of rows)")


def parse_board(rows):
    rows = [[clean(str(c)) for c in r] for r in rows]
    if len(rows) < 2:
        raise FeedError("empty Closings tab")
    head = [h.lower() for h in rows[0]]
    starts = [i for i, h in enumerate(head) if h == "address"]
    blocks = []
    for n, a in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(head)
        price = next((i for i in range(a + 1, end) if head[i] == "list price"), None)
        close = next((i for i in range(a + 1, end) if head[i] == "close date"), None)
        if price is not None:
            blocks.append((a, price, close))
    if not blocks:
        raise FeedError("Closings tab columns missing (need 'Address' and 'List Price')")
    out = []
    for a, p, c in blocks:
        for r in rows[1:]:
            cell = lambda j: r[j] if j is not None and j < len(r) else ""
            addr, price = cell(a), cell(p)
            if not addr or not price or addr.startswith("#") or re.search("address", addr, re.I) or sensitive(addr):
                continue
            rec = {"address": addr, "price": money(price)}
            close = cell(c)
            if close and not sensitive(close):
                rec["expectedClose"] = close
            rec["status"] = "Pending"
            out.append(rec)
    if not out:
        raise FeedError("zero pending rows parsed from Closings tab")
    return out


def load_rows(path):
    try:
        with open(path, encoding="utf-8") as f:
            return parse_board(board_rows(json.load(f)))
    except (OSError, ValueError) as e:
        raise FeedError(f"cannot read rows file {path}: {e}")


def fetch_closings(fetch=None):
    fetch = fetch or get
    errors = []
    for u in URLS:
        try:
            return parse_feed(fetch(u))
        except (FeedError, urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"{u}: {getattr(e, 'code', '') or ''} {e}".strip())
    raise FeedError("; ".join(errors))


def write_document(doc, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def run(root=None, fetch=None, clock=None, rows_file=None):
    root = root or ROOT
    clock = clock or now_ct
    try:
        closings = load_rows(rows_file) if rows_file else fetch_closings(fetch)
    except FeedError as e:
        stop(f"BLOCKED/ERROR: closings source unreadable; closings.json left unchanged. {e}")
    doc = {"asOf": clock(), "source": BOARD_SOURCE if rows_file else SOURCE, "closings": closings}
    write_document(doc, os.path.join(root, "closings.json"))
    return doc


def main(root=None, fetch=None, clock=None, argv=None):
    argv = sys.argv[1:] if argv is None else argv
    rows_file = None
    if argv:
        if len(argv) != 2 or argv[0] != "--from-rows":
            stop("usage: update_closings.py [--from-rows FILE]")
        rows_file = argv[1]
    doc = run(root, fetch, clock, rows_file)
    total = sum(float(re.sub(r"[^\d.]", "", c["price"]) or 0) for c in doc["closings"])
    print(f"{len(doc['closings'])} pending closings, total ${total:,.0f}")


if __name__ == "__main__":
    main()
