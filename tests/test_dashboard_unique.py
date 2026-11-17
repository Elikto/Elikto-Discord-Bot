import unittest
from datetime import datetime, timezone

from dashboard_unique import build, render_dashboard, forum_link
from catalogue_v2 import total_components, total_chars


def sample():
    return {
        ("101", "Streaming"): {
            "films": [
                ("Ouvrir le forum", "https://discord.com/channels/1/101"),
                ("Movix", "https://discord.com/channels/1/111"),
                ("French Stream", "https://discord.com/channels/1/112")],
            "films-et-series": [
                ("Ouvrir le forum", "https://discord.com/channels/1/102"),
                ("Webflix", "https://discord.com/channels/1/113")]},
        ("102", "Téléchargement"): {
            "jeux-video": [
                ("Ouvrir le forum", "https://discord.com/channels/1/103"),
                ("Jeu A", "https://discord.com/channels/1/114")]},
        ("103", "Anti-pub"): {
            "android": [
                ("Ouvrir le forum", "https://discord.com/channels/1/104"),
                ("Blocage", "https://discord.com/channels/1/115")]},
    }


class DashboardTests(unittest.TestCase):
    def test_one_message_with_colored_category_cards(self):
        components = render_dashboard(sample(), now=datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertEqual(len(components), 5)  # accueil + 3 cartes + aide
        self.assertEqual(components[0]["type"], 17)
        self.assertEqual(components[1]["type"], 17)
        self.assertNotEqual(components[1]["accent_color"], components[3]["accent_color"])
        self.assertLessEqual(total_components(components), 40)
        self.assertLessEqual(total_chars(components), 4000)

    def test_all_public_fiches_linked_without_extra_messages(self):
        components = render_dashboard(sample())
        contents = " ".join(x.get("content", "")
                            for container in components if container.get("type") == 17
                            for x in container.get("components", []))
        for ref in ("<#111>", "<#112>", "<#113>", "<#114>", "<#115>"):
            self.assertIn(ref, contents)
        self.assertIn("<#102>", contents)

    def test_long_server_falls_back_to_forum_links(self):
        data = {
            (str(i), f"Catégorie {i}"): {
                "forum": [
                    ("Ouvrir le forum", f"https://discord.com/channels/1/{1000+i}")]
                    + [(f"Fiche {j}", f"https://discord.com/channels/1/{10000+i*1000+j}")
                       for j in range(70)]}
            for i in range(7)
        }
        components = render_dashboard(data)
        self.assertLessEqual(total_components(components), 40)
        self.assertLessEqual(total_chars(components), 4000)
        content = str(components)
        self.assertIn("fiches accessibles dans le forum", content)

    def test_categorized_not_blended(self):
        components = render_dashboard(sample())
        self.assertIn("STREAMING", components[1]["components"][0]["content"])
        self.assertIn("TÉLÉCHARGEMENT", components[2]["components"][0]["content"])

    def test_channel_mention_is_short_clickable_link(self):
        self.assertEqual(
            forum_link("https://discord.com/channels/123/456"), "<#456>"
        )
