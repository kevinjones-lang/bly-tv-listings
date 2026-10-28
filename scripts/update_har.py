#!/usr/bin/env python3
"""Refresh listings.json + img/listings/ from public HAR.com pages.

Sales:  https://www.har.com/deborah-bly/listings_bly  (active section only)
Leases: https://www.har.com/realestatepro/rent-by-agent/bly

Usage: python3 scripts/update_har.py   (run from repo root). Needs Pillow.

HAR often answers with HTTP 403 or a captcha page. This script stops on that
(exit 2) and never retries, changes identity, or fills in numbers.

* A blocked or unreadable sale page leaves listings.json untouched.
* A blocked or unreadable lease page still saves a successful sale refresh
  and keeps the previous leases array. It does not clear or invent rents.
* Leases are replaced only when the rent page parses as HAR portrait cards
  (the same cardv2 markup as the sale page). Zero cards is treated as a failed
  read, not as an empty inventory, so a restyled or challenge page cannot wipe
  the lease list.

Sale records stay in listings. Lease records are the sibling leases array
(kind "lease", price set to the monthly rent shown on the card).
"""
import datetime, html, io, json, os, re, sys, urllib.error, urllib.request
from PIL import Image

SALE_URL = "https://www.har.com/deborah-bly/listings_bly"
RENT_URL = "https://www.har.com/realestatepro/rent-by-agent/bly"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "img", "listings")
PORTRAIT = r'<div class="cardv2\s+cardv2--portrait\s+listcards"'


class Blocked(Exception):
    pass


def stop(msg):
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_page(url):
    try:
        return get(url).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise Blocked(f"BLOCKED/ERROR: HTTP {e.code} from {url}; not retrying")


def txt(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def now_ct():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).isoformat(timespec="minutes")


def parse_cards(sec, kind):
    out, seen = [], set()
    for c in re.split(PORTRAIT, sec)[1:]:
        lid_m = re.search(r'data-lid="(\d+)"', c)
        href = re.search(r'href="(/homedetail/[^"]+)"', c)
        if not lid_m or not href or lid_m.group(1) in seen:
            continue
        lid = lid_m.group(1)
        seen.add(lid)
        photo = re.search(r"background-image:url\('(https://mediahar[^']+)'", c)
        price = re.search(r'cardv2--portrait__body_price">\s*([^<]+)<', c)
        status = re.search(r'cardv2--portrait__body_status">.*?>([^<]+)</div>', c, re.S)
        addr = re.search(r'cardv2--portrait__body_address text-truncate">(.*?)</div>', c, re.S)
        feats = []
        if "body_features" in c:
            block = re.search(r"body_features.*?</ul>", c, re.S)
            if block:
                feats = [txt(x) for x in re.findall(r"<li>(.*?)</li>", block.group(0), re.S)]
        street, city = "", ""
        if addr:
            parts = [txt(p) for p in re.split(r"<br\s*/?>", addr.group(1))]
            street, city = parts[0], (parts[1] if len(parts) > 1 else "")
        joined = " | ".join(feats)
        def grab(pat):
            m = re.search(pat, joined, re.I)
            return m.group(1) if m else None
        beds, full, half = grab(r"(\d+)\s*beds?"), grab(r"(\d+)\s*full baths?"), grab(r"(\d+)\s*half baths?")
        sqft, lot = grab(r"([\d,]+)\s*Sqft"), grab(r"([\d.,]+\s*(?:acres?|lot sqft\.?))")
        ptype = feats[0].split(",")[-1].strip() if feats else ""
        price_txt = txt(price.group(1)) if price else ""
        status_txt = txt(status.group(1)) if status else ""
        shared = {
            "street": street,
            "city": city,
            "price": price_txt,
            "status": status_txt,
            "type": ptype,
            "beds": int(beds) if beds else None,
            "fullBaths": int(full) if full else None,
            "halfBaths": int(half) if half else None,
            "sqft": int(sqft.replace(",", "")) if sqft else None,
            "lot": lot,
            "url": "https://www.har.com" + html.unescape(href.group(1)).split("?")[0],
            "photoSource": photo.group(1) if photo else None,
            "photo": None,
        }
        rec = {"id": lid}
        if kind == "lease":
            rec["kind"] = "lease"
        for key, value in shared.items():
            rec[key] = value
            if kind == "lease" and key == "price":
                rec["rent"] = price_txt
        out.append(rec)
    return out


def parse_sale_page(page):
    active_at, rent_at = page.find('id="active"'), page.find('id="rent"')
    if active_at < 0 or rent_at < 0 or "Deborah Bly" not in page:
        raise Blocked("BLOCKED?: expected active-listings section not found (captcha/challenge page?); stopping")
    return parse_cards(page[active_at:rent_at], "sale")


def lease_section(page):
    rent_at = page.find('id="rent"')
    if rent_at < 0:
        return page
    active_at = page.find('id="active"')
    end = len(page)
    if active_at > rent_at:
        end = active_at
    else:
        # Bound the section at the next element id. A bare id=" search also
        # matches data-lid=" on the card itself.
        tail_at = rent_at + len('id="rent"')
        nxt = re.search(r'[\s<]id="', page[tail_at:])
        end = tail_at + nxt.start() if nxt else len(page)
    return page[rent_at:end]


def parse_lease_page(page):
    cards = parse_cards(lease_section(page), "lease")
    if not cards:
        raise Blocked("BLOCKED?: expected rental listing cards not found (captcha/challenge page?); stopping")
    return cards


def save_photo(lid, photo_url, img_dir=None):
    if not photo_url or not lid:
        return None
    img_dir = img_dir or IMG
    os.makedirs(img_dir, exist_ok=True)
    fn = f"{lid}.jpg"
    path = os.path.join(img_dir, fn)
    if not os.path.exists(path):
        try:
            im = Image.open(io.BytesIO(get(photo_url.replace("/lr/", "/hr/")))).convert("RGB")
            im.thumbnail((400, 400))
            im.save(path, "JPEG", quality=82)
        except Exception as e:
            print("photo failed", lid, e)
    return f"img/listings/{fn}" if os.path.exists(path) else None


def prune_photos(sales, leases, img_dir=None):
    img_dir = img_dir or IMG
    if not os.path.isdir(img_dir):
        return
    keep = {f"{rec['id']}.jpg" for rec in list(sales) + list(leases) if rec.get("id")}
    for fn in os.listdir(img_dir):
        if fn.endswith(".jpg") and fn not in keep:
            os.remove(os.path.join(img_dir, fn))


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def listing_document(sales, leases, sale_asof, lease_asof):
    return {
        "asOf": sale_asof,
        "source": SALE_URL,
        "leaseAsOf": lease_asof,
        "leaseSource": RENT_URL,
        "listings": sales,
        "leases": leases,
    }


def write_document(doc, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def preserved_leases(existing):
    prev = existing.get("leases") if isinstance(existing, dict) else None
    leases = [x for x in prev if isinstance(x, dict)] if isinstance(prev, list) else []
    asof = existing.get("leaseAsOf") if isinstance(existing, dict) else None
    return leases, asof


def run(root=None, fetch=None, clock=None):
    root = root or ROOT
    fetch = fetch or fetch_page
    clock = clock or now_ct
    path = os.path.join(root, "listings.json")
    img_dir = os.path.join(root, "img", "listings")
    existing = load_json(path)
    sale_page = fetch(SALE_URL)
    sales = parse_sale_page(sale_page)
    stamped = clock()
    for rec in sales:
        rec["photo"] = save_photo(rec["id"], rec.get("photoSource"), img_dir)
    blocked = None
    try:
        leases = parse_lease_page(fetch(RENT_URL))
        for rec in leases:
            rec["photo"] = save_photo(rec["id"], rec.get("photoSource"), img_dir)
        lease_asof = stamped
    except Blocked as e:
        blocked = e
        leases, lease_asof = preserved_leases(existing)
    prune_photos(sales, leases, img_dir)
    doc = listing_document(sales, leases, stamped, lease_asof)
    write_document(doc, path)
    nphoto = sum(1 for x in sales if x.get("photo"))
    lphoto = sum(1 for x in leases if x.get("photo"))
    print(f"{len(sales)} sale listings, {nphoto} with photos; {len(leases)} leases, {lphoto} with photos")
    if blocked:
        stop(f"{blocked} Sale listings were updated; previous leases were left unchanged.")
    return doc


def main(root=None, fetch=None, clock=None):
    try:
        run(root=root, fetch=fetch, clock=clock)
    except Blocked as e:
        stop(str(e))


if __name__ == "__main__":
    main()
