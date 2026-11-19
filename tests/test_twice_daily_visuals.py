"""Deux scans par jour et aperçu/logo sur chaque carte sans images factices."""
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from forum_cards import all_text, build_card, last_check_line
from forum_updates import (
    image_candidates_from_html, logo_candidates_from_html, cached_site_preview,
)
from site_channels import groups_for_mirror, polish_mirror_components

GUILD = "1430984645012357263"
TR4KER = "1572752361967853713"


class ScheduleAndVisualsTests(unittest.TestCase):
    def test_workflow_has_two_paris_scans_even_during_daylight_saving(self):
        workflow = Path(".github/workflows/catalogue.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '0 7,18 * * *'", workflow)
        self.assertIn('timezone: "Europe/Paris"', workflow)
        self.assertEqual(workflow.count("cron:"), 2)
        self.assertIn("cron: '17 * * * *'", workflow)
        self.assertIn("github.event.schedule != '17 * * * *'", workflow)
        self.assertIn("github.event_name == 'schedule'", workflow)
        self.assertIn("FORCE_DAILY_INTEGRITY_SCAN", workflow)

    def test_browser_logo_can_recover_verified_html_after_403(self):
        from forum_updates import cached_site_preview
        tracking = {}
        browser = {
            "name": "Cinezzz",
            "page_health": "content",
            "final_url": "https://cinezzz.club/",
            "html": '<link rel="icon" type="image/png" href="/img/logo.png">',
        }
        with patch.dict("os.environ", {"PLAYWRIGHT_BROWSER_FALLBACK": "1"}), patch(
            "forum_updates.image_candidates_from_discord_starter", return_value=[]
        ), patch(
            "forum_updates.fetch_html_page", return_value=None
        ), patch(
            "browser_probe.probe_browser_site", return_value=browser
        ) as probe, patch(
            "forum_updates.preview_image_works",
            side_effect=lambda u: u.endswith("/img/logo.png")
        ):
            found = cached_site_preview(
                object(), "100", "https://cinezzz.club/", tracking
            )
        self.assertEqual(found, "https://cinezzz.club/img/logo.png")
        self.assertEqual(tracking["preview_image_kind"], "logo")
        probe.assert_called_once_with(
            "https://cinezzz.club/", include_html=True
        )

    def test_last_scan_date_hour_is_localized_to_paris(self):
        self.assertIn(
            "🕒 Dernier scan : 19/11/2026 à 07:03 (Paris)",
            last_check_line("2026-11-19T06:03:19+00:00"),
        )
        self.assertIn(
            "🕒 Dernier scan : 19/07/2026 à 07:03 (Paris)",
            last_check_line("2026-07-19T05:03:19+00:00"),
        )
        self.assertIn("Dernier scan : non disponible", last_check_line(None))

    def test_all_cards_show_scan_date_including_unavailable_sites(self):
        for status, verified in (
            ("online", True), ("unknown", False),
            ("restricted", False), ("offline", False),
        ):
            with self.subTest(status=status):
                card = build_card([{
                    "url": "https://tr4ker.net/", "status": status,
                    "verified": verified, "older": [],
                    "checked_at": "2026-11-19T17:11:00+00:00",
                }], "", "Tr4ker")
                rendered = "\n".join(all_text(card))
                self.assertEqual(rendered.count("Dernier scan :"), 1)
                self.assertIn("19/11/2026 à 18:11 (Paris)", rendered)

    def test_open_graph_preview_has_priority_over_favicon(self):
        html = (
            '<meta property="og:image" content="/cover.webp">'
            '<link rel="apple-touch-icon" href="/icon-180.png">'
            '<link rel="icon" type="image/png" href="/favicon-32x32.png">'
        )
        self.assertEqual(image_candidates_from_html(html, "https://tr4ker.net/"),
                         ["https://tr4ker.net/cover.webp"])
        self.assertEqual(logo_candidates_from_html(html, "https://tr4ker.net/"),
                         ["https://tr4ker.net/icon-180.png",
                          "https://tr4ker.net/favicon-32x32.png"])
        tracking = {}
        with patch("forum_updates.image_candidates_from_discord_starter",
                   return_value=[]), patch(
                       "forum_updates.fetch_html_page",
                       return_value={"html": html, "final_url": "https://tr4ker.net/"}
                   ), patch("forum_updates.preview_image_works",
                            side_effect=lambda url: url.endswith("/cover.webp")):
            chosen = cached_site_preview(
                object(), TR4KER, "https://tr4ker.net/", tracking
            )
        self.assertEqual(chosen, "https://tr4ker.net/cover.webp")
        self.assertEqual(tracking["preview_image_kind"], "preview")

    def test_logo_found_as_backup_after_broken_open_graph_image(self):
        html = (
            '<meta property="og:image" content="/bad-cover.jpg">'
            '<link rel="apple-touch-icon" sizes="180x180" href="/touch.png">'
            '<img alt="Logo Tr4ker" src="/logo.png">'
        )
        tracking = {}
        with patch("forum_updates.image_candidates_from_discord_starter",
                   return_value=[]), patch(
                       "forum_updates.fetch_html_page",
                       return_value={"html": html, "final_url": "https://tr4ker.net/"}
                   ), patch("forum_updates.preview_image_works",
                            side_effect=lambda url: url.endswith("/logo.png")):
            chosen = cached_site_preview(
                object(), TR4KER, "https://tr4ker.net/", tracking
            )
        self.assertEqual(chosen, "https://tr4ker.net/logo.png")
        self.assertEqual(tracking["preview_image_kind"], "logo")

    def test_known_png_favicon_path_works_if_html_is_blocked(self):
        tracking = {}
        with patch("forum_updates.image_candidates_from_discord_starter",
                   return_value=[]), patch(
                       "forum_updates.fetch_html_page", return_value=None
                   ), patch("forum_updates.preview_image_works",
                            side_effect=lambda url: url.endswith("/favicon-32x32.png")):
            result = cached_site_preview(
                object(), TR4KER, "https://tr4ker.net/", tracking
            )
        self.assertEqual(result, "https://tr4ker.net/favicon-32x32.png")
        self.assertEqual(tracking["preview_image_kind"], "logo")

    def test_wordpress_logo_filename_without_alt_is_detected(self):
        html = (
            '<img src="/wp-content/themes/theme/images/logoo.png">'
            '<img alt="" src="/wp-content/themes/theme/images/logo.webp">'
            '<img alt="" src="/wp-content/themes/theme/images/poster.webp">'
        )
        self.assertEqual(
            logo_candidates_from_html(html, "https://skidrowreloaded.com/"),
            [
                "https://skidrowreloaded.com/wp-content/themes/theme/images/logoo.png",
                "https://skidrowreloaded.com/wp-content/themes/theme/images/logo.webp",
            ],
        )

    def test_svg_ico_local_data_and_private_urls_not_published(self):
        html = (
            '<link rel="icon" href="data:image/png;base64,AA">'
            '<link rel="icon" href="http://localhost/logo.png">'
            '<link rel="icon" href="/favicon.svg">'
            '<link rel="icon" href="/favicon.ico">'
            '<link rel="icon" href="/icon.png">'
        )
        self.assertEqual(logo_candidates_from_html(html, "https://tr4ker.net/"),
                         ["https://tr4ker.net/icon.png"])

    def test_logo_renders_as_compact_discord_section_not_large_gallery(self):
        group = {"url": "https://tr4ker.net/", "verified": True,
                 "status": "online", "older": [],
                 "checked_at": "2026-11-19T06:00:00+00:00"}
        source = {"title": "Tr4ker", "tid": TR4KER,
                  "source_category_name": "Telechargement",
                  "source_forum_name": "torrent"}
        card = polish_mirror_components(
            build_card([group], "", "Tr4ker",
                       preview_image_url="https://tr4ker.net/favicon.png",
                       preview_kind="logo"), source, [group],
        )
        parts = card[0]["components"]
        thumbnail = [p for p in parts if p.get("type") == 9]
        self.assertEqual(len(thumbnail), 1)
        self.assertEqual(thumbnail[0]["accessory"]["type"], 11)
        self.assertEqual(thumbnail[0]["accessory"]["media"]["url"],
                         "https://tr4ker.net/favicon.png")
        self.assertFalse(any(p.get("type") == 12 for p in parts))
        self.assertIn("Dernier scan : 19/11/2026 à 07:00 (Paris)",
                      "\n".join(all_text(card)))

    def test_mirror_reuses_current_domains_scan_not_older_domains_timestamp(self):
        old = {"site_url": "https://previous-domain.example.net/",
               "last_checked": "2026-11-19T17:00:00+00:00"}
        tid = TR4KER
        data = {
            "server_links": {
                "https://previous-domain.example.net/": {
                    "thread_id": tid, "active": True,
                    "site_url": "https://previous-domain.example.net/",
                    "status": "online",
                    "last_checked": old["last_checked"],
                }
            },
            "records": {tid: old},
            "forum_updates": {tid: {
                "active_url": "https://new-domain.example.net/",
                "active_status": "unknown", "active_verified": False,
                "active_checked_at": "2026-11-19T06:01:00+00:00",
            }},
        }
        source = {"tid": tid, "title": "Site", "source_category_name": "Streaming"}
        checked = groups_for_mirror(data, source)[0]["checked_at"]
        self.assertEqual(checked, "2026-11-19T06:01:00+00:00")


if __name__ == "__main__":
    unittest.main()
