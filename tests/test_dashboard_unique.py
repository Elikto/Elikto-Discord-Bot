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
        self.assertEqual(len(components), 4)  # accueil + 3 cartes, aucun message supplementaire
        self.assertEqual(components[0]["type"], 17)
        self.assertEqual(components[1]["type"], 17)
        self.assertNotEqual(components[1]["accent_color"], components[3]["accent_color"])
        self.assertLessEqual(total_components(components), 40)
        self.assertLessEqual(total_chars(components), 4000)

    def test_web_button_points_to_live_catalogue(self):
        components = render_dashboard(sample())
        home = components[0]["components"]
        links = [component.get("accessory") for component in home if component.get("type") == 9]
        self.assertTrue(any(link.get("url") == "https://elikto.github.io/Elikto-Discord-Bot/"
                            and link.get("label") == "Ouvrir le site"
                            and link.get("style") == 5 for link in links))
        self.assertLessEqual(total_components(components), 40)

    def test_all_public_fiches_linked_without_extra_messages(self):
        components = render_dashboard(sample())
        contents = " ".join(x.get("content", "")
                            for container in components if container.get("type") == 17
                            for x in container.get("components", []))
        for name in ("Movix", "French Stream", "Webflix", "Jeu A", "Blocage"):
            self.assertIn("[" + name + "](", contents)
        self.assertIn("[Forum](https://discord.com/channels/1/102)", contents)
        self.assertNotIn("<#", contents)
        self.assertNotIn("#inconnu", contents)

    def test_never_silently_hide_sites_if_exceeding_discord_limit(self):
        data = {
            (str(i), f"Catégorie {i}"): {
                "forum": [
                    ("Ouvrir le forum", f"https://discord.com/channels/1/{1000+i}")]
                    + [(f"Fiche {j}", f"https://discord.com/channels/1/{10000+i*1000+j}")
                       for j in range(70)]}
            for i in range(7)
        }
        with self.assertRaisesRegex(ValueError, "Aucune fiche ne sera masquee"):
            render_dashboard(data)

    def test_real_28_fiches_are_all_visible_in_one_message(self):
        """Regression sur les vrais 28 noms du serveur, sans contenu prive."""
        from pathlib import Path
        import json
        source = Path(__file__).resolve().parents[1] / "data" / "monitor_state.json"
        if not source.exists():
            self.skipTest("Le premier état de surveillance n'a pas encore été exporté.")
        state = json.loads(source.read_text(encoding="utf-8"))
        records = state.get("records", {})
        if len(records) < 28:
            self.skipTest("Ce serveur n'a pas encore ses 28 fiches de référence.")
        data = {}
        for record in records.values():
            category = (record.get("category", "?"), record.get("forum", "?"))
            cat_key = ("cat_" + category[0], category[0])
            forums = data.setdefault(cat_key, {})
            if category[1] not in forums:
                forums[category[1]] = [
                    ("Ouvrir le forum",
                     "https://discord.com/channels/1430984645012357263/1234567890123456789")
                ]
            forums[category[1]].append((record["title"], record["discord_url"]))
        data[("empty_arcade", "Borne d'arcade")] = {}
        data[("empty_salons", "Salons textuels")] = {
            "discussion": [("#discussion", "https://discord.com/channels/1430984645012357263/1234567890123456789")]
        }
        components = render_dashboard(data)
        content = "\\n".join(
            child.get("content", "")
            for root in components if root.get("type") == 17
            for child in root.get("components", [])
        )
        for rec in records.values():
            self.assertIn(rec["discord_url"], content, rec["title"])
        self.assertNotIn("fiches accessibles dans le forum", content)
        self.assertEqual(len(components), len(data) + 1)  # accueil + 7 catégories
        self.assertLessEqual(total_chars(components), 4000)
        self.assertLessEqual(total_components(components), 40)

    def test_categorized_not_blended(self):
        components = render_dashboard(sample())
        self.assertIn("STREAMING", components[1]["components"][0]["content"])
        self.assertIn("TÉLÉCHARGEMENT", components[2]["components"][0]["content"])

    def test_named_clickable_link_without_unknown_mentions(self):
        self.assertEqual(
            forum_link("https://discord.com/channels/123/456", "Movix"),
            "[Movix](https://discord.com/channels/123/456)"
        )

    def test_discussion_category_and_salon_are_last(self):
        from catalogue_v2 import ordered, salon_sort
        data = {
            ("2", "Discussion"): {"discussion": [("#discussion", "https://discord.com/channels/1/10")]},
            ("1", "Streaming"): {
                "discussion": [("#discussion", "https://discord.com/channels/1/11")],
                "films": [("Ouvrir le forum", "https://discord.com/channels/1/12")]},
        }
        self.assertEqual(ordered(data)[-1][0][1], "Discussion")
        self.assertEqual(
            list(sorted(data[("1", "Streaming")].items(), key=salon_sort))[-1][0],
            "discussion"
        )
        components = render_dashboard(data)
        titles = [x["components"][0]["content"] for x in components if x.get("type") == 17]
        self.assertIn("DISCUSSION", titles[-1])

    def test_actual_server_categories_have_distinct_specific_emojis(self):
        from catalogue_v2 import category_visuals, icon, ordered
        names = ("Salons textuels", "Streaming", "Anti-pub",
                 "Telechargement", "ressource", "Achat-Shopping",
                 "Borne d'arcade")
        data = {(str(i), name): {} for i, name in enumerate(names)}
        appearances = category_visuals(data)
        self.assertEqual(appearances[("5", "Achat-Shopping")][1], "🛍️")
        self.assertEqual(appearances[("6", "Borne d'arcade")][1], "🕹️")
        self.assertEqual(appearances[("4", "ressource")][1], "📚")
        self.assertEqual(appearances[("0", "Salons textuels")][1], "🗨️")
        self.assertEqual(ordered(data)[-1][0][1], "Salons textuels")
        self.assertEqual(len({item[1] for item in appearances.values()}), 7)
        self.assertEqual(icon("discussion"), "💬")
        self.assertEqual(icon("extension-navigateur"), "🧩")
        self.assertEqual(icon("comparateur-prix"), "🏷️")
        self.assertEqual(icon("cashback"), "💰")
        self.assertEqual(icon("transfert-de-fichiers"), "📤")

    def test_distinct_emojis_for_generic_categories(self):
        from catalogue_v2 import category_visuals
        data = {
            (str(i), name): {"infos": [("#infos", f"https://discord.com/channels/1/{i+40}")]}
            for i, name in enumerate(
                ("Archives", "Divers", "Favoris", "Ressources", "Logiciels", "Discussion")
            )
        }
        appearances = category_visuals(data)
        icons = [value[1] for value in appearances.values()]
        self.assertEqual(len(icons), len(set(icons)))
        self.assertEqual(appearances[("5", "Discussion")][1], "💬")
