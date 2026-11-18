"""Tests isolés : aucune connexion réseau et aucun vrai navigateur lancé."""
import os
import unittest
from unittest.mock import MagicMock, patch

from browser_probe import browser_page_identity, probe_browser_site


class BrowserProbeTests(unittest.TestCase):
    def test_cinezzz_rendered_catalog_is_confirmed(self):
        html = (
            "<html><head><title>CINEZZZ.com - Films et Séries</title></head>"
            "<body><nav>Accueil Films Séries</nav>"
            "<article>Un grand catalogue de films et des épisodes récents</article>"
            "</body></html>"
        )
        result = browser_page_identity(
            html, "https://cinezzz.club/",
            body_text="Accueil Films Séries - Un grand catalogue de films et épisodes récents",
            navigation_count=8,
        )
        self.assertEqual(result["name"], "CINEZZZ.com")
        self.assertEqual(result["page_health"], "content")
        self.assertEqual(result["verification_method"], "chromium")

    def test_brand_only_empty_or_parked_page_cannot_be_green(self):
        html = "<html><title>Cinezzz</title><body>Site vide</body></html>"
        self.assertIsNone(browser_page_identity(
            html, "https://cinezzz.club/", body_text="Site vide", navigation_count=1
        ))
        parked = (
            "<title>Cinezzz</title><body>Domain registration has expired. "
            "This domain is for sale. More details about the domain here</body>"
        )
        self.assertIsNone(browser_page_identity(
            parked, "https://cinezzz.club/",
            body_text="Domain registration has expired. This domain is for sale. More details",
            navigation_count=8,
        ))
        unrelated = (
            "<title>Advertisements</title><body>Browse movies and series "
            "with our sponsored partners and read the offers online</body>"
        )
        self.assertIsNone(browser_page_identity(
            unrelated, "https://cinezzz.club/",
            body_text="Browse movies and series with our sponsored partners and offers",
            navigation_count=8,
        ))

    def test_invalid_internal_url_is_never_opened(self):
        self.assertIsNone(probe_browser_site("http://127.0.0.1/"))

    def test_chromium_works_without_exposing_discord_token(self):
        html = (
            "<html><title>Cinezzz</title><body>Films Séries Accueil "
            "Catalogue Streaming Comédie Action Aventure Romance</body></html>"
        )
        p = MagicMock()
        browser = p.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.goto.return_value.status = 200
        page.url = "https://cinezzz.club/"
        page.content.return_value = html
        page.locator.return_value.inner_text.return_value = (
            "Films Séries Accueil Catalogue Streaming Comédie Action Aventure Romance"
        )
        page.locator.return_value.count.return_value = 5
        manager = MagicMock()
        manager.__enter__.return_value = p
        with patch("playwright.sync_api.sync_playwright", return_value=manager), patch(
            "browser_probe.resolve_public_ip", return_value="8.8.8.8"
        ), patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "secret-test"}):
            result = probe_browser_site("https://cinezzz.club/")
            args = p.chromium.launch.call_args.kwargs
            self.assertNotIn("DISCORD_BOT_TOKEN", args["env"])
            self.assertEqual(result["verification_method"], "chromium")
            guard = context.route.call_args.args[1]
            route = MagicMock()
            request = MagicMock()
            request.url = "http://127.0.0.1/admin"
            request.resource_type = "document"
            guard(route, request)
            route.abort.assert_called_once()
            self.assertFalse(route.continue_.called)

    def test_http_403_from_chromium_never_becomes_green(self):
        p = MagicMock()
        browser = p.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.goto.return_value.status = 403
        manager = MagicMock()
        manager.__enter__.return_value = p
        with patch("playwright.sync_api.sync_playwright", return_value=manager), patch(
            "browser_probe.resolve_public_ip", return_value="8.8.8.8"
        ):
            self.assertIsNone(probe_browser_site("https://cinezzz.club/"))
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
