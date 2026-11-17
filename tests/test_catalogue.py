import unittest
import sync_catalogue
from sync_catalogue import (VIEW_CHANNEL, public_channel, chunks, render,
                            ensure_catalog_channel, category_style, overview_embed,
                            category_sort)


class CatalogueTests(unittest.TestCase):
    def test_import_public_numbered_thread_movix(self):
        """A thread named '#1 Movix' must not be confused with a text-channel marker."""
        class FakeAPI:
            def call(self, method, path, **kwargs):
                if path.endswith("/threads/active"):
                    return {"threads": [{
                        "id": "1430991985572839547",
                        "name": "#1 Movix", "type": 11,
                        "parent_id": "20",
                    }]}
                if path.endswith("/channels"):
                    return [
                        {"id": "10", "type": 4, "name": "Streaming"},
                        {"id": "20", "type": 15, "name": "films-et-series", "parent_id": "10"},
                        {"id": "30", "type": 0, "name": "discussion", "parent_id": "10"},
                    ]
                if path.endswith("/guilds/" + sync_catalogue.GUILD_ID):
                    return {"roles": [{
                        "id": sync_catalogue.GUILD_ID,
                        "permissions": str(VIEW_CHANNEL)
                    }]}
                raise AssertionError(path)

            def archived(self, forum_id):
                return iter(())

        grouped = sync_catalogue.get_items(FakeAPI())
        forum = grouped[("10", "Streaming")]["films-et-series"]
        self.assertIn(
            ("#1 Movix",
             "https://discord.com/channels/" +
             sync_catalogue.GUILD_ID + "/1430991985572839547"),
            forum
        )
        salon = grouped[("10", "Streaming")]["discussion"]
        self.assertEqual(salon[0][0], "Ouvrir le salon")

    def test_private_forum_excluded(self):
        cat = {"id": "10", "type": 4, "permission_overwrites": [
            {"id": "1", "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}]}
        f = {"id": "11", "type": 15, "parent_id": "10", "permission_overwrites": []}
        self.assertFalse(public_channel(f, VIEW_CHANNEL, {"10": cat}, "1"))

    def test_public_forum(self):
        f = {"id": "11", "type": 15, "permission_overwrites": []}
        self.assertTrue(public_channel(f, VIEW_CHANNEL, {}, "1"))

    def test_nsfw_excluded(self):
        f = {"id": "11", "type": 15, "nsfw": True}
        self.assertFalse(public_channel(f, VIEW_CHANNEL, {}, "1"))

    def test_pagination(self):
        parts = chunks(["a" * 120] * 50)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 3400 for p in parts))


    def test_auto_create_catalogue(self):
        class FakeAPI:
            def __init__(self):
                self.created = 0

            def call(self, method, path, json=None):
                if method == "GET":
                    return []
                self.created += 1
                self.payload = json
                return {"id": "999", **json}
        fake = FakeAPI()
        old = sync_catalogue.CHANNEL_ID
        try:
            sync_catalogue.CHANNEL_ID = ""
            self.assertEqual(ensure_catalog_channel(fake), "999")
            self.assertEqual(fake.created, 1)
            self.assertEqual(fake.payload["name"], "catalogue")
        finally:
            sync_catalogue.CHANNEL_ID = old

    def test_auto_reuse_existing_catalogue(self):
        class FakeAPI:
            def call(self, method, path, json=None):
                if method != "GET":
                    raise AssertionError("Creation interdite : salon existant")
                return [{"id": "123", "type": 0, "name": "catalogue",
                         "topic": "[elikto-catalogue:v1]"}]
        old = sync_catalogue.CHANNEL_ID
        try:
            sync_catalogue.CHANNEL_ID = ""
            self.assertEqual(ensure_catalog_channel(FakeAPI()), "123")
        finally:
            sync_catalogue.CHANNEL_ID = old

    def test_auto_refuse_foreign_catalogue(self):
        class FakeAPI:
            def call(self, method, path, json=None):
                if method != "GET":
                    raise AssertionError("Ne jamais ecraser un salon existant")
                return [{"id": "123", "type": 0, "name": "catalogue",
                         "topic": "Salon manuel"}]
        old = sync_catalogue.CHANNEL_ID
        try:
            sync_catalogue.CHANNEL_ID = ""
            with self.assertRaisesRegex(RuntimeError, "existe deja"):
                ensure_catalog_channel(FakeAPI())
        finally:
            sync_catalogue.CHANNEL_ID = old


    def test_category_visual_identity(self):
        streaming = category_style("Streaming")
        anti_pub = category_style("anti-pub")
        self.assertEqual(streaming[1], "🎬")
        self.assertEqual(anti_pub[1], "🛡️")
        self.assertNotEqual(streaming[2], anti_pub[2])

    def test_catalog_navigation_links(self):
        data = {
            ("2", "Streaming"): {"films": [
                ("Ouvrir le forum", "https://discord.com/channels/1/2"),
                ("Site A", "https://discord.com/channels/1/123")]}
        }
        link = "https://discord.com/channels/1/888/999"
        embed = overview_embed(data, {"2": link})
        self.assertIn(link, embed["description"])
        self.assertIn("1 fiche(s)", embed["description"])
        self.assertIn("7", str(category_style("Streaming")[2]))

    def test_category_order(self):
        data = [(("22", "Anti-pub"), {}), (("11", "Streaming"), {})]
        ordered = sorted(data, key=category_sort)
        self.assertEqual(ordered[0][0][1], "Streaming")

    def test_compact_embed_limit(self):
        data = {("2", "Streaming"): {
            "films": [("Ouvrir le forum", "https://discord.com/channels/1/2")] +
                     [("Site " + str(i), "https://discord.com/channels/1/" + str(i))
                      for i in range(350)]}}
        pages = render(data)
        self.assertGreater(len(pages), 2)
        self.assertTrue(all(len(e[2]["description"]) <= 4096 for e in pages))

    def test_render_categories(self):
        data = {("2", "Streaming"): {"films": [("Site A", "https://discord.com/channels/1/123")]}}
        pages = render(data)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][0], "elikto:catalog:index")
        self.assertIn("Site A", pages[1][2]["description"])


if __name__ == "__main__":
    unittest.main()
