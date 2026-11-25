#!/usr/bin/env python3
"""Parser and refresh tests for scripts/update_listings_from_sheet.py. No network."""
import json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_listings_from_sheet as uls
import update_har as uh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "listings-board-sample.json")
ALLOWED = {"id", "street", "city", "price", "status", "type", "beds", "fullBaths", "halfBaths",
           "sqft", "lot", "url", "photoSource", "photo", "agent"}
FORBIDDEN = {"source", "client", "lead", "lender", "owner", "tenant", "email", "phone",
             "buyer", "seller", "dom", "occupied", "occupancy"}


def parse_fixture():
    return uls.load_rows(FIXTURE)


class ParseTests(unittest.TestCase):
    def test_both_blocks_left_first_only_safe_fields(self):
        rows, skipped = parse_fixture()
        self.assertEqual([r["street"] for r in rows], [
            "1534 Wayside Drive",
            "3847 Ohana Lane",
            "1614 Roaring Springs Lane",
            "16610 Jolly Roger Road",
            "874 Pebblebank Lane",
        ])
        self.assertEqual(rows[0]["city"], "Texas City")
        self.assertEqual(rows[0]["price"], "$194,800")
        self.assertEqual(rows[0]["agent"], "Deb")
        self.assertEqual(rows[0]["status"], "Active")
        self.assertEqual(rows[0]["beds"], 3)
        self.assertEqual(rows[0]["fullBaths"], 2)
        self.assertIsNone(rows[0]["halfBaths"])
        self.assertEqual(rows[0]["sqft"], 1233)
        self.assertIsNone(rows[0]["lot"])
        self.assertEqual(rows[0]["photo"], None)
        self.assertEqual(rows[1]["type"], "Lot")
        self.assertEqual(rows[1]["lot"], "0.1052 Acres")
        self.assertIsNone(rows[1]["beds"])
        self.assertEqual(rows[2]["agent"], "Celia")
        self.assertEqual(rows[2]["beds"], 4)
        self.assertEqual(rows[2]["fullBaths"], 2)
        self.assertEqual(rows[2]["halfBaths"], 1)
        self.assertEqual(rows[2]["sqft"], 3137)
        self.assertEqual(rows[3]["beds"], 3)
        self.assertEqual(rows[3]["fullBaths"], 2)
        self.assertEqual(rows[4]["halfBaths"], 1)
        for r in rows:
            self.assertTrue(set(r) <= ALLOWED, set(r) - ALLOWED)
            self.assertTrue(FORBIDDEN.isdisjoint(r), r)
        dumped = json.dumps(rows)
        for bad in ("Call In", "Zillow", "SOI", "Ylopo", "Vacant", "Occupied", "buyer@", "281-555", "zoom.us"):
            self.assertNotIn(bad, dumped)
        self.assertEqual(len(skipped), 1)
        self.assertIn("99 Lease Lane", skipped[0][0])
        self.assertIn("lease", skipped[0][1])

    def test_next_block_agent_is_not_attached_to_left_row(self):
        rows, _ = parse_fixture()
        left = next(r for r in rows if r["street"] == "1534 Wayside Drive")
        right = next(r for r in rows if r["street"] == "1614 Roaring Springs Lane")
        self.assertEqual(left["agent"], "Deb")
        self.assertEqual(right["agent"], "Celia")
        self.assertNotEqual(left["agent"], right["agent"])

    def test_half_bath_zero_is_null(self):
        rows = uls.parse_board(uls.uc.board_rows([
            ["Agent", "Address", "List Price", "Bed/Bath", "sq ft"],
            ["Tannika", "9621 Wall St, Texas City", "$315,000", "4/4 & 2/0", "2,033"],
        ]))[0]
        self.assertEqual(rows[0]["beds"], 4)
        self.assertEqual(rows[0]["fullBaths"], 2)
        self.assertIsNone(rows[0]["halfBaths"])

    def test_optional_status_photo_and_har_url(self):
        rows, _ = uls.parse_board(uls.uc.board_rows([
            ["Agent", "Address", "List Price", "Bed/Bath", "sq ft", "Status", "HAR URL", "Photo URL"],
            ["Deb", "1 Main St, Houston", "$100,000", "3 & 2", "1,200", "Under Contract - P",
             "https://www.har.com/homedetail/x/1", "https://example.com/pic.jpg"],
            ["Deb", "2 Main St, Houston", "$200,000", "3 & 2", "1,200", "",
             "https://zoom.us/j/1", "javascript:alert(1)"],
        ]))
        self.assertEqual(rows[0]["status"], "Under Contract - P")
        self.assertEqual(rows[0]["url"], "https://www.har.com/homedetail/x/1")
        self.assertEqual(rows[0]["photoSource"], "https://example.com/pic.jpg")
        self.assertEqual(rows[1]["status"], "Active")
        self.assertEqual(rows[1]["url"], "")
        self.assertIsNone(rows[1]["photoSource"])

    def test_sensitive_address_and_zero_rows_fail(self):
        with self.assertRaises(uls.FeedError):
            uls.parse_board([["Address", "List Price"], ["buyer@example.com", "$1"]])
        with self.assertRaises(uls.FeedError):
            uls.parse_board([["Agent", "Source"], ["Deb", "SOI"]])
        with self.assertRaises(uls.FeedError):
            uls.parse_board([["Address", "List Price"], ["", ""]])


class WriteTests(unittest.TestCase):
    def test_writes_document_and_leaves_leases_off(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "listings.json")
            with open(path, "w") as f:
                json.dump({"keep": True, "listings": [{"id": "old"}], "leases": [{"id": "r"}]}, f)
            doc = uls.main(root=tmp, clock=lambda: "2026-11-25T11:45-06:00",
                           argv=["--from-rows", FIXTURE], save_photo=lambda *a, **k: None)
            self.assertEqual(doc["asOf"], "2026-11-25T11:45-06:00")
            self.assertEqual(doc["source"], uls.BOARD_SOURCE)
            self.assertIsNone(doc["leaseAsOf"])
            self.assertIsNone(doc["leaseSource"])
            self.assertEqual(doc["leases"], [])
            written = json.load(open(path, encoding="utf-8"))
            self.assertEqual(len(written["listings"]), 5)
            self.assertTrue(all(x.get("kind") != "lease" for x in written["listings"]))
        finally:
            shutil.rmtree(tmp)

    def test_bad_rows_file_leaves_listings_untouched(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "listings.json")
            with open(path, "w") as f:
                f.write('{"keep": true}')
            cases = {
                "missing.json": None,
                "nocols.json": {"ranges": [{"content": "Agent,Source\nDeb,SOI"}]},
                "zero.json": {"ranges": [{"content": "Agent,Address,List Price\n,,,\n,,\"$5\","}]},
            }
            for name, body in cases.items():
                src = os.path.join(tmp, name)
                if body is not None:
                    with open(src, "w") as f:
                        json.dump(body, f)
                with self.assertRaises(SystemExit) as ctx:
                    uls.main(root=tmp, argv=["--from-rows", src])
                self.assertEqual(ctx.exception.code, 2)
                self.assertEqual(open(path, encoding="utf-8").read(), '{"keep": true}')
        finally:
            shutil.rmtree(tmp)


class FallbackTests(unittest.TestCase):
    def test_update_har_falls_back_to_sheet_when_blocked(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "listings.json")
            with open(path, "w") as f:
                json.dump({"listings": [{"id": "old"}], "leases": []}, f)
            def fetch(url):
                raise uh.Blocked(f"BLOCKED/ERROR: HTTP 403 from {url}; not retrying")
            uh.main(root=tmp, fetch=fetch, clock=lambda: "2026-11-25T11:45-06:00",
                    argv=["--from-rows", FIXTURE])
            doc = json.load(open(path, encoding="utf-8"))
            self.assertEqual(doc["source"], uls.BOARD_SOURCE)
            self.assertEqual(len(doc["listings"]), 5)
            self.assertEqual(doc["listings"][0]["street"], "1534 Wayside Drive")
            self.assertEqual(doc["leases"], [])
        finally:
            shutil.rmtree(tmp)

    def test_update_har_prefers_har_when_it_works(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "listings.json")
            with open(path, "w") as f:
                json.dump({"listings": [], "leases": []}, f)
            sale = (
                '<html><div id="active"><div class="cardv2 cardv2--portrait listcards" data-lid="1">'
                '<a href="/homedetail/1-main/1"></a>'
                '<div class="cardv2--portrait__body_price"> $100,000 </div>'
                '<div class="cardv2--portrait__body_status"><div class="badge">Active</div></div>'
                '<div class="cardv2--portrait__body_address text-truncate">1 Main<br>Houston, TX</div>'
                '<ul class="cardv2--portrait__body_features"><li>Single-Family</li><li>3 beds</li>'
                '<li>2 full baths</li><li>1,200 Sqft</li></ul></div></div>'
                '<div id="rent"></div>Deborah Bly</html>'
            )
            orig = uh.save_photo
            uh.save_photo = lambda *a, **k: None
            try:
                uh.main(root=tmp, fetch=lambda url: sale, clock=lambda: "2026-11-25T11:45-06:00",
                        argv=["--from-rows", FIXTURE])
            finally:
                uh.save_photo = orig
            doc = json.load(open(path, encoding="utf-8"))
            self.assertEqual(doc["source"], uh.SALE_URL)
            self.assertEqual(doc["listings"][0]["id"], "1")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
