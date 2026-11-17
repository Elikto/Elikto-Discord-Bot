"""Contrôles de structure du catalogue web : une seule navigation et accès direct aux fiches."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "site"


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.nav_count = 0
        self.tab_navigation = 0
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "nav":
            self.nav_count += 1
        if attributes.get("id") in ("categoryTabs", "categoryNavTop"):
            self.tab_navigation += 1
        if tag == "script":
            self.scripts.append(attributes.get("src"))


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles.css").read_text(encoding="utf-8")
        cls.parser = Parser()
        cls.parser.feed(cls.html)

    def test_one_category_menu_only(self):
        self.assertEqual(self.parser.nav_count, 1)
        self.assertEqual(self.parser.tab_navigation, 0)
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))
        self.assertIn("categoryItems", self.parser.ids)
        self.assertNotIn("categoryTabs", self.parser.ids)

    def test_catalogue_showcases_resources_not_forum_cards(self):
        self.assertIn("resourceGrid", self.parser.ids)
        self.assertIn("resourceCard(item)", self.js)
        self.assertIn("item.title", self.js)
        self.assertIn("item.url", self.js)
        self.assertIn("https:", self.js)
        self.assertIn("noopener noreferrer", self.js)

    def test_discussions_are_separate_at_end(self):
        self.assertIn("discussionSection", self.parser.ids)
        self.assertIn("isDiscussion(category, channel)", self.js)
        self.assertIn("ui.discussionSection.hidden", self.js)
        self.assertLess(self.html.index('id="resourceGrid"'),
                        self.html.index('id="discussionSection"'))

    def test_monitoring_controls_and_history(self):
        for key in ("healthSelect", "healthyCount", "incidentCount",
                    "restrictedCount", "missingCount", "monitorBanner",
                    "historyList", "lastControl"):
            self.assertIn(key, self.parser.ids)
        self.assertIn("monitoring.json", self.js)
        self.assertIn("updateHealthSummary", self.js)
        self.assertIn("renderHistory", self.js)
        self.assertIn("offline", self.js)
        self.assertIn("missing_url", self.js)
        self.assertIn("redirect_url", self.js)
        self.assertIn("site_url", self.js)
        self.assertIn("noopener noreferrer", self.js)
        self.assertNotIn("categoryTabs", self.html)

    def test_multiple_mirrors_with_separate_statuses(self):
        self.assertIn("monitor.mirrors", self.js)
        self.assertIn("mirrors-list", self.js)
        self.assertIn("mirror-status", self.js)
        self.assertIn("mirrorStatuses", self.js)
        self.assertIn("mirror-row", self.css)

    def test_mobile_reuses_the_same_menu(self):
        self.assertIn('id="menuButton"', self.html)
        self.assertIn('id="backdrop"', self.html)
        self.assertIn(".sidebar.is-open", self.css)
        self.assertIn("chooseCategory", self.js)

    def test_no_mock_content_in_public_html(self):
        self.assertNotIn("Movix", self.html)
        self.assertNotIn("WebFlix", self.html)
        self.assertEqual(self.parser.scripts, ["./app.js?v=5"])


if __name__ == "__main__":
    unittest.main()
