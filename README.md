# Bly TV Listings

Full-screen, auto-advancing slideshow of The Bly Team's listings for the office TV.

* Data: Google Sheet "Bly TV Listings Feed" (1rFoocO92dt3iqswf6IH6-oML7ZfhzdOAcVli5ujVJYU). Columns A-F (from the Closings Board "Listings" tab) hold Address, List Price, Bed/Bath, Sq Ft / Lot Size, DOM and Vacant/Occupied; columns H-J (from the "Closings" tab) hold Pending Address, Pending Price and Expected Close.
* The page fetches the feed's CSV when it loads and again each time the loop returns to the title slide. If the fetch fails, it keeps showing the last good data (it also has a built-in snapshot as a fallback).
* Keys: left/right arrows, Space, Home, F = fullscreen (double-click also toggles it).
* URL options: `?secs=10` sets seconds per slide; `?feed=<csv url>` uses a different CSV, e.g. a "Publish to web" CSV link.

* Market ticker: `market.json` at the repo root holds this week's headline (overwrite and push to refresh; see `/workspace/bly-tv-listings-update-leaderboard.md`). The bottom NEWS strip shows a gold MARKET badge then the headline, ahead of training items.

## Left board

The default TV view is a split screen. The left panel cycles `listings.json`: sale rows first, in HAR order, then for-lease rows (none while leases are off; the board then just pages through sales). Six rows show at a time. A lease uses the price slot for monthly rent (`$2,150/mo`), a blue **For Lease** badge, and a Lease placeholder where the photo would be. An under-contract lease stays on the board: same Lease placeholder, orange row, and **Pending** badge (the same pending treatment as a sale, so the rent price and Lease mark are what separate it). `?lpage=10` opens on that page of the rotation (1-based).

## Refreshing HAR listings

From the repo root (this is the twice-daily command):

```bash
python3 scripts/update_har.py
```

**Leases are off by default** (since 2026-11-03: HAR has blocked the rent page since Oct 30 and there is no other lease source). The plain command refreshes sales only: it does not fetch the rent page and writes `leases: []` with `leaseAsOf`/`leaseSource` set to `null`, so stale rents cannot come back. With zero leases the TV board shows sale rows only ("N listings", no lease rows, no lease date in the footer). To turn leases back on once there is a working source, run:

```bash
python3 scripts/update_har.py --leases
```

The script reads public HAR pages and rewrites `listings.json`:

* Sales — `https://www.har.com/deborah-bly/listings_bly` (the active section only), stored in `listings`.
* Leases (only with `--leases`) — `https://www.har.com/realestatepro/rent-by-agent/bly`, stored in the sibling `leases` array (`kind` is `lease`, `price` is the monthly rent on the card).

HAR often returns HTTP 403 or a captcha page to automated fetches. The script exits 2 when that happens. It does not retry, change its user agent, or fill in numbers.

* If the sale page is blocked or is not the expected listings page, `listings.json` is left unchanged.
* With `--leases`: if sales load and the lease page is blocked or has no portrait cards, the new sales are saved and the previous `leases` array is kept (empty while leases are off). Zero cards is a failed read, not an empty inventory.
* Lease cards use the same HAR portrait markup as sale cards (`cardv2--portrait`).

No client, lead, or lender fields are written. The 14-row lease scrape from 2026-10-28 is kept only as a test fixture (`scripts/fixtures/har-leases-2026-10-28.json`); it is no longer on the board. Tests: `python3 scripts/test_update_har.py`.

## Pending closings (closings.json)

`closings.json` holds the pending closings from the Closings Board sheet (`15k9G3UfIRNcTgxf3DW3nQesOW2KSCCoGZd5Ub0TNdO0`), "Closings" tab. The classic slideshow's pending slides read it first (same origin), then fall back to the feed sheet, then the saved data / built-in snapshot. `?closings=<url>` overrides the file. The ops hub's Listings & Closings tab reads the same file.

Refresh (twice daily):

1. Read the Closings tab columns `A:M` with the Google Sheets connector (`read_range`, read-only, displayed values) and save the raw JSON response to `/workspace/bly-tv-listings-closings-raw.json` (outside the repo: it contains agent, source and lender columns).
2. From the repo root: `python3 scripts/update_closings.py --from-rows /workspace/bly-tv-listings-closings-raw.json`, then commit and push `closings.json`.

Only address, price, expectedClose and status ("Pending") are written. Agent, lead source, lender, client names, emails, phone numbers and links are never published. Missing columns, an unreadable file or zero rows exit 2 and leave `closings.json` unchanged. Without `--from-rows` the script reads the public feed CSV instead (that only works if the feed sheet is shared publicly). Tests: `python3 scripts/test_update_closings.py`.
