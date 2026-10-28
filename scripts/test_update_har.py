#!/usr/bin/env python3
"""Parser and refresh tests for scripts/update_har.py. No network."""
import io, json, os, shutil, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_har as uh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "har-leases-2026-10-28.json")
FORBIDDEN = {"client", "lead", "lender", "owner", "tenant", "email", "phone", "buyer", "seller"}


def card(lid, street, city, price, status, feats, photo=None):
    img = ""
    if photo:
        img = f"<div style=\"background-image:url('{photo}')\"></div>"
    items = "".join(f"<li>{feat}</li>" for feat in feats)
    return f"""<div class="cardv2 cardv2--portrait listcards" data-lid="{lid}">
<a href="/homedetail/{street.lower().replace(' ', '-')}/{lid}?x=1"></a>
{img}
<div class="cardv2--portrait__body_price"> {price} </div>
<div class="cardv2--portrait__body_status"><div class="badge">{status}</div></div>
<div class="cardv2--portrait__body_address text-truncate">{street}<br>{city}</div>
<ul class="cardv2--portrait__body_features">{items}</ul>
</div>"""


SALE_CARD = card("11353643", "3056 Arbor Ranch Ct", "League City, TX 77539", "$330,000", "Active",
                 ["Single-Family", "4 beds", "2 full baths", "2,100 Sqft"],
                 "https://mediahar.harstatic.com/1/lr/2.jpeg")
RENT_CARD = card("10507595", "2912 Dunrich Ct", "League City, TX 77573", "$2,150", "Active",
                 ["Rental - Single Family Detached", "3 beds", "2 full baths", "1,448 Sqft"])
RENT_UC = card("8770598", "313 Winding Oak Ln S", "League City, TX 77573", "$3,100/mo", "Under Contract - P",
               ["Rental - Single Family Detached", "4 beds", "3 full baths", "1 half baths", "3,345 Sqft"])
SALE_PAGE = f'<html><div id="active">{SALE_CARD}</div><div id="rent">{RENT_CARD}</div>Deborah Bly</html>'
RENT_PAGE = f"<html><h1>Rentals</h1>{RENT_CARD}{RENT_UC}<p>px-captcha loader</p></html>"
CAPTCHA = "<html><title>Access to this page has been denied</title><meta content='px-captcha'></html>"


class ParseTests(unittest.TestCase):
    def test_sale_page_ignores_rent_section(self):
        sales = uh.parse_sale_page(SALE_PAGE)
        self.assertEqual(len(sales), 1)
        sale = sales[0]
        self.assertEqual(sale["id"], "11353643")
        self.assertNotIn("kind", sale)
        self.assertNotIn("rent", sale)
        self.assertEqual(sale["price"], "$330,000")
        self.assertEqual(sale["beds"], 4)
        self.assertEqual(sale["fullBaths"], 2)
        self.assertIsNone(sale["halfBaths"])
        self.assertEqual(sale["sqft"], 2100)
        self.assertEqual(sale["street"], "3056 Arbor Ranch Ct")
        self.assertTrue(sale["url"].endswith("/11353643"))
        self.assertNotIn("?", sale["url"])

    def test_sale_challenge_stops(self):
        with self.assertRaises(uh.Blocked):
            uh.parse_sale_page(CAPTCHA)

    def test_lease_cards_keep_rent_and_under_contract(self):
        leases = uh.parse_lease_page(RENT_PAGE)
        self.assertEqual([x["id"] for x in leases], ["10507595", "8770598"])
        active, pending = leases
        self.assertEqual(active["kind"], "lease")
        self.assertEqual(active["rent"], "$2,150")
        self.assertEqual(active["price"], "$2,150")
        self.assertEqual(active["status"], "Active")
        self.assertEqual(active["beds"], 3)
        self.assertEqual(active["sqft"], 1448)
        self.assertIsNone(active["photo"])
        self.assertEqual(pending["status"], "Under Contract - P")
        self.assertEqual(pending["rent"], "$3,100/mo")
        self.assertEqual(pending["fullBaths"], 3)
        self.assertEqual(pending["halfBaths"], 1)

    def test_agent_page_lease_section_skips_sales(self):
        leases = uh.parse_lease_page(SALE_PAGE)
        self.assertEqual([x["id"] for x in leases], ["10507595"])
        self.assertEqual(leases[0]["kind"], "lease")

    def test_duplicate_lid_dropped(self):
        page = RENT_CARD + RENT_CARD
        self.assertEqual(len(uh.parse_lease_page(page)), 1)

    def test_empty_or_captcha_does_not_parse_as_zero_inventory(self):
        with self.assertRaises(uh.Blocked) as ctx:
            uh.parse_lease_page(CAPTCHA)
        self.assertIn("not found", str(ctx.exception))
        with self.assertRaises(uh.Blocked):
            uh.parse_lease_page("<html><p>no cards</p></html>")

    def test_fetch_403_is_blocked_without_retry_copy(self):
        def boom(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=io.BytesIO(b""))
        orig = uh.get
        uh.get = boom
        try:
            with self.assertRaises(uh.Blocked) as ctx:
                uh.fetch_page(uh.RENT_URL)
        finally:
            uh.get = orig
        self.assertEqual(str(ctx.exception), f"BLOCKED/ERROR: HTTP 403 from {uh.RENT_URL}; not retrying")

    def test_photo_403_does_not_block_the_listing(self):
        def boom(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=io.BytesIO(b""))
        orig = uh.get
        uh.get = boom
        tmp = tempfile.mkdtemp()
        try:
            self.assertIsNone(uh.save_photo("1", "https://mediahar.example/lr/a.jpeg", tmp))
        finally:
            uh.get = orig
            shutil.rmtree(tmp)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._save = uh.save_photo
        uh.save_photo = lambda lid, photo_url, img_dir=None: None
        self.path = os.path.join(self.tmp, "listings.json")
        self.existing = {
            "asOf": "2026-10-27T13:05-05:00",
            "source": uh.SALE_URL,
            "leaseAsOf": "2026-10-28T14:30:00-05:00",
            "leaseSource": uh.RENT_URL,
            "listings": [{"id": "old", "street": "Old Sale", "price": "$1"}],
            "leases": [{"id": "10507595", "kind": "lease", "street": "2912 Dunrich Ct", "rent": "$2,150", "price": "$2,150"}],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.existing, f)

    def tearDown(self):
        uh.save_photo = self._save
        shutil.rmtree(self.tmp)

    def test_sale_block_leaves_file_unchanged_and_exits_2(self):
        before = open(self.path, encoding="utf-8").read()
        def fetch(url):
            raise uh.Blocked(f"BLOCKED/ERROR: HTTP 403 from {url}; not retrying")
        with self.assertRaises(SystemExit) as ctx:
            uh.main(root=self.tmp, fetch=fetch, clock=lambda: "2026-10-28T16:00-05:00")
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(open(self.path, encoding="utf-8").read(), before)

    def test_lease_block_saves_sales_and_keeps_previous_rents(self):
        def fetch(url):
            if url == uh.RENT_URL:
                raise uh.Blocked(f"BLOCKED/ERROR: HTTP 403 from {url}; not retrying")
            return SALE_PAGE
        with self.assertRaises(SystemExit) as ctx:
            uh.main(root=self.tmp, fetch=fetch, clock=lambda: "2026-10-28T16:00-05:00")
        self.assertEqual(ctx.exception.code, 2)
        doc = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual(doc["asOf"], "2026-10-28T16:00-05:00")
        self.assertEqual([x["id"] for x in doc["listings"]], ["11353643"])
        self.assertEqual(doc["listings"][0]["price"], "$330,000")
        self.assertNotIn("kind", doc["listings"][0])
        self.assertEqual(doc["leases"], self.existing["leases"])
        self.assertEqual(doc["leaseAsOf"], "2026-10-28T14:30:00-05:00")

    def test_both_pages_replace_leases(self):
        def fetch(url):
            return SALE_PAGE if url == uh.SALE_URL else RENT_PAGE
        doc = uh.run(root=self.tmp, fetch=fetch, clock=lambda: "2026-10-28T16:00-05:00")
        self.assertEqual([x["id"] for x in doc["listings"]], ["11353643"])
        self.assertEqual([x["id"] for x in doc["leases"]], ["10507595", "8770598"])
        self.assertEqual(doc["leases"][0]["rent"], "$2,150")
        self.assertEqual(doc["leases"][1]["status"], "Under Contract - P")
        self.assertEqual(doc["leaseAsOf"], "2026-10-28T16:00-05:00")
        self.assertEqual(doc["leaseSource"], uh.RENT_URL)


class SeedTests(unittest.TestCase):
    def test_committed_leases_match_scrape_and_keep_under_contract(self):
        seed = json.load(open(FIXTURE, encoding="utf-8"))
        doc = json.load(open(os.path.join(ROOT, "listings.json"), encoding="utf-8"))
        self.assertEqual(len(doc["listings"]), 52)
        self.assertEqual(doc["leaseSource"], seed["source"])
        self.assertEqual(doc["leaseAsOf"], seed["asOf"])
        self.assertEqual(len(doc["leases"]), seed["count"])
        self.assertEqual(len(doc["leases"]), 14)
        by_id = {x["listingId"]: x for x in doc["leases"]}
        self.assertEqual(set(by_id), {x["listingId"] for x in seed["listings"]})
        pending = []
        for src in seed["listings"]:
            rec = by_id[src["listingId"]]
            for key in ("street", "city", "rent", "status", "beds", "baths", "sqft", "lot", "propertyType", "harUrl", "listingId", "photoUrl"):
                self.assertEqual(rec[key], src[key], key)
            self.assertEqual(rec["kind"], "lease")
            self.assertEqual(rec["price"], src["rent"])
            self.assertEqual(rec["url"], src["harUrl"])
            self.assertEqual(rec["id"], src["listingId"])
            self.assertIsNone(rec["photo"])
            self.assertTrue(FORBIDDEN.isdisjoint(rec))
            if "contract" in src["status"].lower():
                pending.append(rec["street"])
        self.assertEqual(pending, ["313 Winding Oak Ln S"])
        self.assertTrue(all(x.get("kind") != "lease" for x in doc["listings"]))


if __name__ == "__main__":
    unittest.main()
