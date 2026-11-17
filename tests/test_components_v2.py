import unittest
from catalogue_v2 import (
    FLAG_V2, marker_id, render_categories, render_home, total_chars,
    total_components, normalize,
)

def fixture():
    return {
        ("10", "Streaming"): {
            "films": [
                ("Ouvrir le forum", "https://discord.com/channels/1/10"),
                ("Movix", "https://discord.com/channels/1/101"),
                ("WebFlix", "https://discord.com/channels/1/102"),
            ],
            "films-et-series": [
                ("Ouvrir le forum", "https://discord.com/channels/1/20"),
                ("Flemmix", "https://discord.com/channels/1/103"),
            ],
        },
        ("20", "Anti-pub"): {
            "android": [("Ouvrir le salon", "https://discord.com/channels/1/30")]
        }
    }

class V2Tests(unittest.TestCase):
    def test_one_home_and_two_categories(self):
        pages = render_categories(fixture())
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][1], "10")
        self.assertEqual(pages[0][2][0]["type"], 17)
        self.assertEqual(pages[0][2][0]["accent_color"], 0x9C84F7)

    def test_home_real_link_buttons(self):
        categories = render_categories(fixture())
        links = {cat: "https://discord.com/channels/1/2/" + str(i+100)
                 for i, (_, cat, _) in enumerate(categories)}
        home = render_home(fixture(), links)
        children = home[0]["components"]
        sections = [c for c in children if c["type"] == 9]
        self.assertEqual(len(sections), 2)
        self.assertTrue(all(s["accessory"]["style"] == 5 for s in sections))
        self.assertTrue(all(s["accessory"]["url"].startswith("https://discord.com/")
                            for s in sections))

    def test_category_contains_real_sections_and_links(self):
        components = render_categories(fixture())[0][2]
        sections = [c for c in components[0]["components"] if c["type"] == 9]
        self.assertGreaterEqual(len(sections), 2)
        self.assertTrue(any("Movix" in s["components"][0]["content"] for s in sections))
        self.assertTrue(all(s["accessory"]["style"] == 5 for s in sections))

    def test_large_forum_is_paginated_within_limits(self):
        data = {("10", "Streaming"): {"films": [
            ("Ouvrir le forum", "https://discord.com/channels/1/10")
        ] + [("Site " + str(i), "https://discord.com/channels/1/" + str(i))
             for i in range(500)]}}
        categories = render_categories(data)
        self.assertGreater(len(categories), 1)
        for _, _, components in categories:
            self.assertLessEqual(total_components(components), 40)
            self.assertLessEqual(total_chars(components), 4000)

    def test_marker_is_stable_and_distinct(self):
        self.assertEqual(marker_id("elikto:catalog:index"),
                         marker_id("elikto:catalog:index"))
        self.assertNotEqual(marker_id("elikto:catalog:index"),
                            marker_id("elikto:catalog:cat:10:1"))

    def test_normalize_ignores_generated_component_ids(self):
        desired = [{"type": 17, "id": 3, "components": [{"type": 10, "content": "A"}]}]
        actual = [{"type": 17, "id": 3, "components": [
            {"type": 10, "id": 1, "content": "A"}]}]
        self.assertEqual(normalize(desired), normalize(actual))

    def test_flag_v2(self):
        self.assertEqual(FLAG_V2, 32768)
