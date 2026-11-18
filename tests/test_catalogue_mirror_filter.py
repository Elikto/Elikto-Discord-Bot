"""Le catalogue unique ne doit pas indexer les salons miroir en double."""
import unittest

from sync_catalogue import GUILD_ID, get_items


class FakeAPI:
    def call(self, method, path, *, params=None, json=None):
        if (method, path) == ("GET", f"/guilds/{GUILD_ID}"):
            return {"roles": [
                {"id": GUILD_ID, "permissions": str(1 << 10)}
            ]}
        if (method, path) == ("GET", f"/guilds/{GUILD_ID}/channels"):
            return [
                {"id": "100", "type": 4, "name": "Telechargement"},
                {"id": "200", "type": 15, "name": "torrent", "parent_id": "100"},
                {"id": "300", "type": 4, "name": "Telechargement · Sites"},
                {"id": "400", "type": 0, "name": "tr4ker", "parent_id": "300",
                 "topic": "Fiche miroir [elikto:site-channel:v1:1572752361967853713]"},
                {"id": "401", "type": 0, "name": "autre-site", "parent_id": "300",
                 "topic": "Fiche miroir [elikto:site-channel:v1:1431054520477024297]"},
                {"id": "500", "type": 0, "name": "général", "parent_id": "100"},
            ]
        if (method, path) == ("GET", f"/guilds/{GUILD_ID}/threads/active"):
            return {"threads": [{
                "id": "1572752361967853713", "parent_id": "200",
                "name": "Tr4ker", "type": 11,
            }]}
        raise AssertionError((method, path))

    def archived(self, forum_id):
        return []


class CatalogueMirrorTests(unittest.TestCase):
    def test_bot_owned_text_mirrors_not_duplicated_in_main_catalogue(self):
        grouped = get_items(FakeAPI())
        all_categories = {name: forums for (_, name), forums in grouped.items()}
        self.assertIn("Telechargement", all_categories)
        self.assertNotIn("Telechargement · Sites", all_categories)
        entries = all_categories["Telechargement"]["torrent"]
        self.assertEqual(
            [title for title, _ in entries if title == "Tr4ker"], ["Tr4ker"]
        )
        self.assertIn("général", all_categories["Telechargement"])

    def test_explicit_bot_marker_is_required_for_exclusion(self):
        from sync_catalogue import SITE_MIRROR_TOPIC_MARKER
        self.assertEqual(SITE_MIRROR_TOPIC_MARKER,
                         "[elikto:site-channel:v1:")


if __name__ == "__main__":
    unittest.main()
