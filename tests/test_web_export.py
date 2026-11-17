import unittest
from datetime import datetime, timezone

from export_catalogue import serialize


class WebExportTests(unittest.TestCase):
    def test_public_forum_fiches_preserved(self):
        dataset = {
            ("10", "Streaming"): {
                "films": [
                    ("Ouvrir le forum", "https://discord.com/channels/1/10"),
                    ("Movix", "https://discord.com/channels/1/100"),
                    ("French Stream", "https://discord.com/channels/1/101"),
                ]
            },
            ("11", "Salons textuels"): {
                "général": [("#général", "https://discord.com/channels/1/11")]
            },
        }
        result = serialize(dataset, now=datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertEqual(result["stats"], {"categories": 2, "channels": 2, "entries": 2})
        self.assertEqual(result["visibility"], "public_everyone_only")
        self.assertEqual(result["categories"][-1]["name"], "Salons textuels")
        self.assertEqual(result["categories"][0]["channels"][0]["items"][1]["title"], "Movix")
        self.assertEqual(result["categories"][1]["channels"][0]["kind"], "text")
        self.assertNotIn("token", str(result).lower())

    def test_discussion_salon_last_and_category_icons_distinct(self):
        dataset = {
            ("10", "Streaming"): {
                "discussion": [("#discussion", "https://discord.com/channels/1/10")],
                "films": [
                    ("Ouvrir le forum", "https://discord.com/channels/1/12"),
                    ("A", "https://discord.com/channels/1/15"),
                ],
            },
            ("11", "Achat-Shopping"): {
                "cashback": [
                    ("Ouvrir le forum", "https://discord.com/channels/1/13"),
                    ("Promo", "https://discord.com/channels/1/16"),
                ]
            },
        }
        result = serialize(dataset)
        self.assertEqual(result["categories"][0]["channels"][-1]["name"], "discussion")
        self.assertEqual(result["categories"][1]["icon"], "🛍️")
        self.assertNotEqual(result["categories"][0]["color"], result["categories"][1]["color"])


if __name__ == "__main__":
    unittest.main()
