import unittest
from sync_catalogue import VIEW_CHANNEL, public_channel, chunks, render


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

    def test_render_categories(self):
        data = {("2", "Streaming"): {"films": [("Site A", "https://discord.com/channels/1/123")]}}
        pages = render(data)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][0], "elikto:catalog:index")
        self.assertIn("Site A", pages[1][2]["description"])


if __name__ == "__main__":
    unittest.main()
