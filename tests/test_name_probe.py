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

    def test_french_announced_rename_extracts_both_names(self):
        from name_probe import announced_rename
        examples = (
            ("momdax devient nandov.com", "MOMDAX", "NANDOV", "https://nandov.com/"),
            ("zakmav devient dotivoz.com", "ZAKMAV", "DOTIVOZ", "https://dotivoz.com/"),
            ("dovlip devient farsov.com", "DOVLIP", "FARSOV", "https://farsov.com/"),
        )
        for raw, before, after, domain in examples:
            with self.subTest(raw=raw):
                result = announced_rename(raw)
                self.assertEqual(result["previous_name"], before)
                self.assertEqual(result["name"], after)
                self.assertEqual(result["announced_url"], domain)
                name = parse_site_name("<title>" + raw + "</title>")
                self.assertEqual(name["name"], after)
                self.assertEqual(name["previous_name"], before)
                self.assertEqual(name["evidence"], "title:rename")

    def test_random_title_is_not_a_rename(self):
        from name_probe import announced_rename
        self.assertIsNone(announced_rename("NANDOV — Films et séries en HD"))
        self.assertIsNone(announced_rename("MOMDAX devient MOMDAX.com"))

    def test_webflix_popup_announces_surf_without_http_redirect(self):
        from name_probe import parse_site_migration
        body = """
        <div class='modal'>
          <strong>webflix.art est en train d'être bloqué en France</strong>
          <p>Passe sur Webflix.surf, même compte.</p>
          <button>Utiliser Webflix.surf</button>
          <small>Nouvelle adresse officielle - compte et listes synchronisés</small>
        </div>"""
        notice = parse_site_migration(body, "https://webflix.art/")
        self.assertEqual(notice["announced_url"], "https://webflix.surf/")
        self.assertEqual(notice["previous_url"], "https://webflix.art/")
        self.assertEqual(notice["evidence"], "html:migration_notice")
        notice2 = parse_site_migration(
            "<script>const title='Passe sur Webflix.surf, même compte'</script>",
            "https://webflix.bio/"
        )
        self.assertEqual(notice2["announced_url"], "https://webflix.surf/")

    def test_notice_must_not_be_random_link_or_bad_domain(self):
        from name_probe import parse_site_migration
        self.assertIsNone(parse_site_migration(
            '<a href="https://unrelated.org">Voir le site</a>',
            "https://webflix.art/",
        ))
        self.assertIsNone(parse_site_migration(
            'Passe sur http://127.0.0.1, même compte',
            "https://webflix.art/",
        ))
        self.assertIsNone(parse_site_migration(
            'Utiliser random-ads.example.org',
            "https://webflix.art/",
        ))

    def test_probe_works_on_html_notice_without_title_change(self):
        from name_probe import probe_site_migration
        with patch("name_probe.fetch_html_page", return_value={
            "html": "<title>Webflix</title><div>Passe sur Webflix.surf, même compte</div>",
            "final_url": "https://webflix.art/",
        }):
            notice = probe_site_migration("https://webflix.art/")
        self.assertEqual(notice["announced_url"], "https://webflix.surf/")

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
