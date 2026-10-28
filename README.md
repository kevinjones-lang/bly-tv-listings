# Bly TV Listings

Full-screen, auto-advancing slideshow of The Bly Team's listings for the office TV.

* Data: Google Sheet "Bly TV Listings Feed" (1rFoocO92dt3iqswf6IH6-oML7ZfhzdOAcVli5ujVJYU). Columns A-F (from the Closings Board "Listings" tab) hold Address, List Price, Bed/Bath, Sq Ft / Lot Size, DOM and Vacant/Occupied; columns H-J (from the "Closings" tab) hold Pending Address, Pending Price and Expected Close.
* The page fetches the feed's CSV when it loads and again each time the loop returns to the title slide. If the fetch fails, it keeps showing the last good data (it also has a built-in snapshot as a fallback).
* Keys: left/right arrows, Space, Home, F = fullscreen (double-click also toggles it).
* URL options: `?secs=10` sets seconds per slide; `?feed=<csv url>` uses a different CSV, e.g. a "Publish to web" CSV link.

* Market ticker: `market.json` at the repo root holds this week's headline (overwrite and push to refresh; see `/workspace/bly-tv-listings-update-leaderboard.md`). The bottom NEWS strip shows a gold MARKET badge then the headline, ahead of training items.

## Left board

The default TV view is a split screen. The left panel cycles `listings.json`: sale rows first, in HAR order, then for-lease rows. Six rows show at a time. A lease uses the price slot for monthly rent (`$2,150/mo`), a blue **For Lease** badge, and a Lease placeholder where the photo would be. An under-contract lease stays on the board: same Lease placeholder, orange row, and **Pending** badge (the same pending treatment as a sale, so the rent price and Lease mark are what separate it). `?lpage=10` opens on that page of the rotation (1-based).

## Refreshing HAR listings

From the repo root:

```bash
python3 scripts/update_har.py
```

The script reads two public pages and rewrites `listings.json`:

* Sales — `https://www.har.com/deborah-bly/listings_bly` (the active section only), stored in `listings`.
* Leases — `https://www.har.com/realestatepro/rent-by-agent/bly`, stored in the sibling `leases` array (`kind` is `lease`, `price` is the monthly rent on the card).

HAR often returns HTTP 403 or a captcha page to automated fetches. The script exits 2 when that happens. It does not retry, change its user agent, or fill in numbers.

* If the sale page is blocked or is not the expected listings page, `listings.json` is left unchanged.
* If sales load and the lease page is blocked or has no portrait cards, the new sales are saved and the previous `leases` array is kept. Zero cards is a failed read, not an empty inventory, so a restyled page cannot wipe rents.
* Lease cards use the same HAR portrait markup as sale cards (`cardv2--portrait`). A future refresh replaces `leases` only when at least one card parses.

No client, lead, or lender fields are written. The lease list checked in on 2026-10-28 is the 14-row scrape of the rent-by-agent page from that day, so the board still has rents if HAR is blocking the script.
