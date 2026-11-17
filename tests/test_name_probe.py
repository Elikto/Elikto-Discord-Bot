import unittest
from unittest.mock import patch

from name_probe import parse_site_name, sanitized_name, fetch_html_page, probe_site_name


class BrandDetectionTests(unittest.TestCase):
    def test_og_site_name_overrides_seo_title(self):
        name = parse_site_name(
            '<html><head><title>Regarder des films gratuitement - Stream</title>'
            '<meta property="og:site_name" content="MOMDAX"></head></html>'
        )
        self.assertEqual(name, {"name": "MOMDAX", "evidence": "og:site_name",
                                "confidence": "strong"})

    def test_title_fallback_extracts_brand(self):
        name = parse_site_name("<html><title>NANDOV — Films et séries en HD</title></html>")
        self.assertEqual(name["name"], "NANDOV")
        self.assertEqual(name["confidence"], "medium")

    def test_application_name_is_supported(self):
        name = parse_site_name('<meta name="application-name" content="NANDOV">')
        self.assertEqual(name["name"], "NANDOV")
        self.assertEqual(name["evidence"], "application-name")

    def test_antibot_titles_do_not_create_false_renames(self):
        for title in ("Just a moment...", "Attention Required! | Cloudflare",
                      "Checking your browser before accessing", "Accès refusé",
                      "Access denied"):
            self.assertIsNone(parse_site_name("<title>" + title + "</title>"))

    def test_no_html_title_means_unknown_not_domain_guess(self):
        self.assertIsNone(parse_site_name("<html><body>Chargement</body></html>"))

    def test_discord_mentions_are_rejected(self):
        for value in ("@everyone", "https://bad.org", "<@123456789>", "Home"):
            self.assertIsNone(sanitized_name(value))

    def test_bounded_html_and_no_external_network_from_parser(self):
        payload = "<html><head><title>MOMDAX</title></head><body>"
        payload += '<script src="http://127.0.0.1/private"></script></body></html>'
        self.assertEqual(parse_site_name(payload)["name"], "MOMDAX")

    def test_requests_recheck_public_dns_and_refuse_private_targets(self):
        self.assertIsNone(fetch_html_page("http://127.0.0.1/admin"))
        self.assertIsNone(fetch_html_page("http://localhost/"))

    def test_probe_does_not_invent_name_on_failure(self):
        with patch("name_probe.fetch_html_page", return_value=None):
            self.assertIsNone(probe_site_name("https://example.org/"))

    def test_probe_returns_parsed_name_and_final_url(self):
        with patch("name_probe.fetch_html_page", return_value={
            "html": "<title>Momdax | Bienvenue</title>",
            "final_url": "https://new.example.org/"
        }):
            data = probe_site_name("https://example.org/")
        self.assertEqual(data["name"], "Momdax")
        self.assertEqual(data["final_url"], "https://new.example.org/")


if __name__ == "__main__":
    unittest.main()
