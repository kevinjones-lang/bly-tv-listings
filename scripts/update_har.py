#!/usr/bin/env python3
"""Refresh listings.json + img/listings/ from Deborah Bly's public HAR.com listings page.
Usage: python3 scripts/update_har.py   (run from repo root). Stops (exit 2) on 403/captcha block; never works around it."""
import json, re, os, sys, html, io, urllib.request, datetime
from PIL import Image
URL = "https://www.har.com/deborah-bly/listings_bly"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "img", "listings")

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def txt(s): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()

def main():
    try:
        page = get(URL).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        sys.exit(f"BLOCKED/ERROR: HTTP {e.code} from {URL}; not retrying")
    a, r = page.find('id="active"'), page.find('id="rent"')
    if a < 0 or r < 0 or "Deborah Bly" not in page:
        sys.exit("BLOCKED?: expected active-listings section not found (captcha/challenge page?); stopping")
    sec = page[a:r]
    cards = re.split(r'<div class="cardv2\s+cardv2--portrait\s+listcards"', sec)[1:]
    out, seen = [], set()
    os.makedirs(IMG, exist_ok=True)
    for c in cards:
        lid = re.search(r'data-lid="(\d+)"', c)
        href = re.search(r'href="(/homedetail/[^"]+)"', c)
        if not lid or not href or lid.group(1) in seen: continue
        lid = lid.group(1); seen.add(lid)
        photo = re.search(r"background-image:url\('(https://mediahar[^']+)'", c)
        price = re.search(r'cardv2--portrait__body_price">\s*([^<]+)<', c)
        status = re.search(r'cardv2--portrait__body_status">.*?>([^<]+)</div>', c, re.S)
        addr = re.search(r'cardv2--portrait__body_address text-truncate">(.*?)</div>', c, re.S)
        feats = [txt(x) for x in re.findall(r"<li>(.*?)</li>", re.search(r'body_features.*?</ul>', c, re.S).group(0), re.S)] if 'body_features' in c else []
        street, city = "", ""
        if addr:
            parts = [txt(p) for p in re.split(r"<br\s*/?>", addr.group(1))]
            street, city = parts[0], (parts[1] if len(parts) > 1 else "")
        f = " | ".join(feats)
        m = lambda p: (re.search(p, f, re.I) or [None, None])[1]
        beds, full, half = m(r"(\d+)\s*beds?"), m(r"(\d+)\s*full baths?"), m(r"(\d+)\s*half baths?")
        sqft = m(r"([\d,]+)\s*Sqft"); lot = m(r"([\d.,]+\s*(?:acres?|lot sqft\.?))")
        ptype = feats[0].split(",")[-1].strip() if feats else ""
        rec = {"id": lid, "street": street, "city": city, "price": txt(price.group(1)) if price else "",
               "status": txt(status.group(1)) if status else "", "type": ptype,
               "beds": int(beds) if beds else None, "fullBaths": int(full) if full else None,
               "halfBaths": int(half) if half else None, "sqft": int(sqft.replace(",", "")) if sqft else None,
               "lot": lot, "url": "https://www.har.com" + html.unescape(href.group(1)).split("?")[0],
               "photoSource": photo.group(1) if photo else None, "photo": None}
        if photo:
            fn = f"img/listings/{lid}.jpg"; p = os.path.join(ROOT, fn)
            if not os.path.exists(p):
                try:
                    im = Image.open(io.BytesIO(get(photo.group(1).replace("/lr/", "/hr/")))).convert("RGB")
                    im.thumbnail((400, 400)); im.save(p, "JPEG", quality=82)
                except Exception as e:
                    print("photo failed", lid, e)
            if os.path.exists(p): rec["photo"] = fn
        out.append(rec)
    keep = {f"{x['id']}.jpg" for x in out}
    for fn in os.listdir(IMG):
        if fn.endswith(".jpg") and fn not in keep: os.remove(os.path.join(IMG, fn))
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).isoformat(timespec="minutes")
    json.dump({"asOf": now, "source": URL, "listings": out}, open(os.path.join(ROOT, "listings.json"), "w"), indent=2)
    print(f"{len(out)} active listings, {sum(1 for x in out if x['photo'])} with photos")

if __name__ == "__main__": main()
