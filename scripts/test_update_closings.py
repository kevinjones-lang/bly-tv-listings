#!/usr/bin/env python3
"""Parser and refresh tests for scripts/update_closings.py. No network."""
import json, os, shutil, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_closings as uc

BOARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "closings-board-2026-10-30.json")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tv-feed-2026-10-30.csv")
ALLOWED = {"address", "price", "expectedClose", "status", "agent", "url"}
SIGNIN = "<!DOCTYPE html><html><div class='title-text'>Sign in to your Google Account</div></html>"


def fixture():
    return open(FIXTURE, encoding="utf-8").read()


class ParseTests(unittest.TestCase):
    def test_fixture_keeps_tv_rows_and_only_safe_fields(self):
        rows = uc.parse_feed(fixture())
        self.assertEqual([r["address"] for r in rows],
                         ["920 Bruce Ave, League City", "4830 Houston Drive, Galveston", "5610 Blackbird Lane, Hitchcock (NH)"])
        self.assertEqual(rows[0], {"address": "920 Bruce Ave, League City", "price": "$45,000", "expectedClose": "extended", "status": "Pending"})
        self.assertEqual(rows[1]["price"], "$231,390")  # bare number formatted like the TV
        for r in rows:
            self.assertTrue(set(r) <= ALLOWED, r)
        dumped = json.dumps(rows)
        for bad in ("Secret", "Seller", "buyer@", "zoom.us", "281-555"):
            self.assertNotIn(bad, dumped)

    def test_optional_agent_and_har_url_when_present(self):
        text = ("Pending Address,Pending Price,Expected Close,Listing Agent,HAR URL\n"
                '"1 Main St, Houston","$100,000",11/1,Deb,https://www.har.com/homedetail/x/1\n'
                '"2 Main St, Houston","$200,000",,ada@example.com,https://zoom.us/j/1\n')
        rows = uc.parse_feed(text)
        self.assertEqual(rows[0]["agent"], "Deb")
        self.assertEqual(rows[0]["url"], "https://www.har.com/homedetail/x/1")
        self.assertEqual(rows[1], {"address": "2 Main St, Houston", "price": "$200,000", "status": "Pending"})

    def test_signin_page_missing_columns_and_zero_rows_fail(self):
        for text in (SIGNIN, "Address,List Price\nA,1\n", "Pending Address,Pending Price,Expected Close\n,,\n"):
            with self.assertRaises(uc.FeedError):
                uc.parse_feed(text)


class BoardTests(unittest.TestCase):
    """--from-rows: Closings Board "Closings" tab as the Sheets connector returns it (blocks A-F and H-M)."""

    def test_both_blocks_left_first_only_safe_fields(self):
        rows = uc.load_rows(BOARD)
        self.assertEqual(rows, [
            {"address": "920 Bruce Ave, League City", "price": "$45,000", "expectedClose": "extended", "status": "Pending"},
            {"address": "4830 Houston Drive, Galveston", "price": "$231,390", "expectedClose": "11/06", "status": "Pending"},
            {"address": "5610 Blackbird Lane, Hitchcock (NH)", "price": "$217,995", "expectedClose": "TBD", "status": "Pending"},
            {"address": "11319 Windy Dawn Dr, Pearland", "price": "$445,000", "expectedClose": "11/12", "status": "Pending"},
            {"address": "3722 WOODLACE DRIVE, HUMBLE", "price": "$245,000", "status": "Pending"},
        ])
        dumped = json.dumps(rows)
        for bad in ("Deb", "Abbie", "SOI", "Zillow", "CASH", "MORTGAGE", "Lending", "@", "zoom", "281-555", "$939,385"):
            self.assertNotIn(bad, dumped)

    def test_list_of_rows_input(self):
        rows = uc.parse_board(uc.board_rows([["Agent", "Address", "List Price", "Close Date"], ["Deb", "1 Main St", "$1,000", "11/1"]]))
        self.assertEqual(rows, [{"address": "1 Main St", "price": "$1,000", "expectedClose": "11/1", "status": "Pending"}])

    def test_bad_rows_file_leaves_closings_untouched(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "closings.json")
            with open(path, "w") as f:
                f.write('{"keep": true}')
            cases = {"missing.json": None,
                     "nocols.json": {"ranges": [{"content": "Agent,Source\nDeb,SOI"}]},
                     "zero.json": {"ranges": [{"content": "Agent,Address,List Price,Close Date\n,,,\n,,\"$5\","}]}}
            for name, body in cases.items():
                src = os.path.join(tmp, name)
                if body is not None:
                    with open(src, "w") as f:
                        json.dump(body, f)
                with self.assertRaises(SystemExit) as ctx:
                    uc.main(root=tmp, argv=["--from-rows", src])
                self.assertEqual(ctx.exception.code, 2, name)
                with open(path) as f:
                    self.assertEqual(f.read(), '{"keep": true}')
        finally:
            shutil.rmtree(tmp)

    def test_from_rows_writes_board_source(self):
        tmp = tempfile.mkdtemp()
        try:
            uc.main(root=tmp, clock=lambda: "2026-10-30T12:00-05:00", argv=["--from-rows", BOARD])
            with open(os.path.join(tmp, "closings.json")) as f:
                doc = json.load(f)
            self.assertEqual(doc["source"], uc.BOARD_SOURCE)
            self.assertEqual(len(doc["closings"]), 5)
        finally:
            shutil.rmtree(tmp)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "closings.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"asOf": "old", "closings": [{"address": "keep", "price": "$1"}]}, f)
        self.before = open(self.path, encoding="utf-8").read()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def check_untouched(self, fetch):
        with self.assertRaises(SystemExit) as ctx:
            uc.main(root=self.tmp, fetch=fetch, clock=lambda: "2026-10-30T12:00-05:00", argv=[])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(open(self.path, encoding="utf-8").read(), self.before)

    def test_http_error_leaves_file(self):
        def boom(url):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)
        self.check_untouched(boom)

    def test_private_sheet_leaves_file(self):
        self.check_untouched(lambda url: SIGNIN)

    def test_success_writes_document_and_falls_back_to_export(self):
        def fetch(url):
            return SIGNIN if "gviz" in url else fixture()
        doc = uc.run(root=self.tmp, fetch=fetch, clock=lambda: "2026-10-30T12:00-05:00")
        saved = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual(saved, doc)
        self.assertEqual(set(saved), {"asOf", "source", "closings"})
        self.assertEqual(saved["source"], "Bly TV Listings Feed sheet")
        self.assertEqual(len(saved["closings"]), 3)

    def test_clock_is_chicago_offset(self):
        self.assertRegex(uc.now_ct(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d-0[56]:00$")


if __name__ == "__main__":
    unittest.main()
