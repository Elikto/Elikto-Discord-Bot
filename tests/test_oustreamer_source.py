"""OùStreamer: evidence extraction and caching, entirely offline."""
import unittest
from datetime import datetime, timedelta, timezone
from oustreamer_source import SOURCE_URL, parse_oustreamer_changes, read_changes

NOW = datetime(2026, 11, 19, 8, tzinfo=timezone.utc)


class OustreamerSourceTests(unittest.TestCase):
    def test_explicit_visible_cross_tld_change(self):
        page = '<div class="row"><strong>Coflix</strong>' \
               '<span>coflix.boo</span> → <a href="https://coflix.red/">coflix.red</a></div>'
        result = parse_oustreamer_changes(page)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["old_url"], "https://coflix.boo/")
        self.assertEqual(result[0]["url"], "https://coflix.red/")

    def test_json_explicit_redirect_only(self):
        page = '<script id="__NEXT_DATA__" type="application/json">' \
               '{"props":{"pageProps":{"sites":[' \
               '{"status":"redirected","url":"https://site.boo/",' \
               '"redirectUrl":"https://site.red/"},' \
               '{"status":"active","url":"https://fine.com/"}]}}}' \
               '</script>'
        rows = parse_oustreamer_changes(page)
        self.assertEqual([(r["old_url"], r["url"]) for r in rows], [
            ("https://site.boo/", "https://site.red/")
        ])

    def test_status_and_name_only_are_not_address_proof(self):
        page = '<div>Movix Active 248ms <button>Visit</button></div>' \
               '<div>Wiflix Blocked 10002ms</div>' \
               '<div>Wawacity Redirected 322ms</div>' \
               '<script>const untrusted = "coflix.boo → coflix.fake";</script>'
        self.assertEqual(parse_oustreamer_changes(page), [])

    def test_rejects_malformed_local_and_unrelated_scraper_links(self):
        page = "localhost → 127.0.0.1 coflix.boo → app.oustreamer.com " \
               "coflix.boo → coflix.boo"
        self.assertEqual(parse_oustreamer_changes(page), [])

    def test_cached_and_unavailable_fetch_does_not_invent(self):
        state = {}
        calls = []
        def fetch(url):
            calls.append(url)
            return {"html": "<div>coflix.boo → coflix.red</div>"}
        first = read_changes(state, NOW, fetcher=fetch)
        second = read_changes(state, NOW + timedelta(hours=3), fetcher=lambda _: 1/0)
        self.assertEqual(first, second)
        self.assertEqual(calls, [SOURCE_URL])
        failed = read_changes(state, NOW + timedelta(hours=4),
                              fetcher=lambda _: None, force=True)
        self.assertEqual(failed, [])
        self.assertEqual(state["oustreamer_observations"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
