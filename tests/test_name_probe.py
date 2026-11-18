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

    def test_fstream_landing_portal_link_updates_without_hardcoding_domain(self):
        from name_probe import parse_site_migration
        html = """
          <html><head><title>French Stream - Accès au site</title></head>
          <body><header><a href='/installer'>Installer</a></header>
            <h2>URL Éphémère Du moment</h2>
            <a href='https://fs99.lol/'>fs99.lol</a>
            <section><a href='https://tv-unrelated.example.org/'>Regarder la TV</a>
            <a href='https://music-unrelated.example.org/'>Musique</a></section>
          </body></html>
        """
        migration = parse_site_migration(html, "https://fstream.info/")
        self.assertEqual(migration["announced_url"], "https://fs99.lol/")
        self.assertEqual(migration["previous_url"], "https://fstream.info/")
        self.assertEqual(migration["evidence"], "html:official_address_link")

    def test_html_meta_refresh_new_domain_supported_with_public_url_only(self):
        from name_probe import parse_site_migration
        html = (
            '<html><head><meta http-equiv="refresh" '
            'content="0; URL=https://nouveau-stream.example.org/"></head></html>'
        )
        result = parse_site_migration(html, "https://ancien-stream.example.org/")
        self.assertEqual(result["announced_url"], "https://nouveau-stream.example.org/")
        self.assertEqual(result["evidence"], "html:meta_refresh")
        self.assertIsNone(parse_site_migration(
            '<meta http-equiv="refresh" content="0; url=http://127.0.0.1/admin">',
            "https://ancien-stream.example.org/"
        ))
        self.assertIsNone(parse_site_migration(
            '<meta http-equiv="refresh" content="60; url=https://ads.example.org/">',
            "https://ancien-stream.example.org/"
        ))

    def test_generic_adresse_actuelle_and_no_false_ads(self):
        from name_probe import parse_site_migration
        html = """
           <div>Adresse actuelle :</div>
           <a href="https://nouveau-site.example.org/">Cliquez pour accéder</a>
           <a href="https://tracker-ad.example.org/">Publicité</a>
        """
        migration = parse_site_migration(html, "https://ancien-site.example.org/")
        self.assertEqual(
            migration["announced_url"], "https://nouveau-site.example.org/"
        )
        no_announcement = """
           <a href="https://pub.example.org/">Publicité</a>
           <div>Site de films et séries</div>
           <a href="https://social.example.org/">Nous suivre</a>
        """
        self.assertIsNone(parse_site_migration(
            no_announcement, "https://ancien-site.example.org/"
        ))

    def test_announced_link_rejects_unsafe_internal_destination(self):
        from name_probe import parse_site_migration
        for href in ("http://127.0.0.1/private", "http://localhost/admin",
                     "https://user:password@example.org/",
                     "https://original.example.org/page"):
            raw = (f"<h2>Nouvelle adresse officielle</h2>"
                   f"<a href='{href}'>Visiter</a>")
            result = parse_site_migration(raw, "https://original.example.org/")
            self.assertIsNone(result, href)

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

    def test_streaming_page_distinguishes_varloz_from_rogzov_index(self):
        from name_probe import is_empty_directory_index
        good_url = "https://varloz.com/47b6f3a/home/varloz"
        empty_url = "https://rogzov.com/93zjfyqk6llf/"
        good_html = (
            "<html><head><title>VARLOZ — Films et séries</title></head>"
            "<body><main><h1>Films populaires</h1>"
            "<article>Films et nouveautés</article></main></body></html>"
        )
        empty_html = (
            "<html><head><title>Index of /93zjfyqk6llf/</title></head>"
            "<body><h1>Index of /93zjfyqk6llf/</h1><hr>"
            "<pre><a href='../'>Parent Directory</a></pre></body></html>"
        )
        self.assertFalse(is_empty_directory_index(good_html))
        self.assertTrue(is_empty_directory_index(empty_html))
        with patch("name_probe.fetch_html_page", side_effect=lambda url: {
            "html": good_html if url == good_url else empty_html, "final_url": url
        }):
            valid = probe_site_name(good_url)
            empty = probe_site_name(empty_url)
        self.assertEqual(valid["name"], "VARLOZ")
        self.assertEqual(empty["page_health"], "empty_index")
        self.assertNotIn("name", empty)

    def test_index_variants_and_javascript_shell_not_false_positive(self):
        from name_probe import is_empty_directory_index
        self.assertTrue(is_empty_directory_index(
            "<html><title>Directory listing for /foo/</title>"
            "<body><h1>Directory listing for /foo/</h1></body></html>"
        ))
        self.assertTrue(is_empty_directory_index(
            "<html><h1>Index of /file/</h1><pre></pre></html>"
        ))
        self.assertFalse(is_empty_directory_index(
            "<div id='root'></div><script src='/app.js'></script>"
        ))
        self.assertFalse(is_empty_directory_index(
            "<title>Index of popular movies - films et séries</title>"
        ))

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
