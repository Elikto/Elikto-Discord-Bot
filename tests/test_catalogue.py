import unittest
import sync_catalogue
from sync_catalogue import VIEW_CHANNEL, public_channel, chunks, render, ensure_catalog_channel


class CatalogueTests(unittest.TestCase):
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

    def test_render_categories(self):
        data = {("2", "Streaming"): {"films": [("Site A", "https://discord.com/channels/1/123")]}}
        pages = render(data)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][0], "elikto:catalog:index")
        self.assertIn("Site A", pages[1][2]["description"])


if __name__ == "__main__":
    unittest.main()
