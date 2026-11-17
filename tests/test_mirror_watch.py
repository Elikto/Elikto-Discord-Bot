import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mirror_watch


URLS = [
    "https://alpha.example.org/",
    "https://bravo.example.org/",
    "https://charlie.example.org/",
    "https://delta.example.org/",
    "https://echo.example.org/",
]


def human_message(urls=None, mid="100", name=None):
    body = "\n".join(urls if urls is not None else URLS)
    if name:
        body += "\n" + name
    return {"id": mid, "content": body, "author": {"id": "999", "bot": False},
            "embeds": []}


def state():
    return {"version": 1, "records": {}, "events": [], "event_sequence": 0}


def response(value="online", http=200, url=URLS[0]):
    return {"result": value, "http_status": http, "final_url": url, "reason": None}


class FakeAPI:
    def __init__(self, messages):
        self.messages = messages
        self.bot_messages = []
        self.name = "Streaming HD miroir"
        self.calls = []

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "GET" and path == f"/channels/{mirror_watch.THREAD_ID}":
            return {"id": mirror_watch.THREAD_ID, "guild_id": mirror_watch.GUILD_ID,
                    "type": 11, "name": self.name, "owner_id": "999"}
        if method == "GET" and path == "/users/@me":
            return {"id": "333"}
        if method == "GET" and path == f"/channels/{mirror_watch.THREAD_ID}/messages":
            return self.messages + self.bot_messages
        if method == "GET" and path.endswith("/messages/500"):
            return self.bot_messages[0]
        if method == "PATCH" and path == f"/channels/{mirror_watch.THREAD_ID}":
            self.name = kwargs["json"]["name"]
            return {"name": self.name}
        if method == "PATCH" and path.endswith("/messages/500"):
            self.bot_messages[0]["content"] = kwargs["json"]["content"]
            return self.bot_messages[0]
        if method == "POST" and path.endswith("/messages"):
            message = {"id": "500", "author": {"id": "333", "bot": True},
                       "content": kwargs["json"]["content"]}
            self.bot_messages.append(message)
            return message
        raise AssertionError((method, path))


class MirrorWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 13, tzinfo=timezone.utc)
        # Aucun accès réseau pendant les tests : un test distinct injecte
        # les noms HTML observés.
        no_html = patch("mirror_watch.probe_site_name", return_value=None)
        no_html.start()
        self.addCleanup(no_html.stop)

    def test_first_html_scan_creates_a_baseline_without_false_rename(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "MOMDAX", "confidence": "strong", "evidence": "og:site_name"
            }
        ) as identity:
            mirror_watch.sync_thread(api, s, self.now)
        self.assertEqual(identity.call_count, 5)
        entries = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"]
        self.assertEqual(entries[URLS[0]]["site_name"], "MOMDAX")
        self.assertEqual(s["events"], [])
        self.assertIn("MOMDAX", api.bot_messages[0]["content"])

    def test_detects_real_site_rename_over_two_independent_days(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "MOMDAX", "confidence": "strong", "evidence": "og:site_name"
            }
        ):
            mirror_watch.sync_thread(api, s, self.now)
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "NANDOV", "confidence": "strong", "evidence": "og:site_name"
            }
        ):
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        tracked = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]
        self.assertEqual(tracked["site_name"], "MOMDAX")
        self.assertEqual(tracked["pending_name"], "NANDOV")
        self.assertIn("Nouveau nom annoncé", api.bot_messages[0]["content"])
        self.assertEqual(len([e for e in s["events"] if e["type"] == "mirror_name_suspected"]), 5)
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "NANDOV", "confidence": "strong", "evidence": "og:site_name"
            }
        ):
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=2))
        self.assertEqual(tracked["site_name"], "NANDOV")
        self.assertIsNone(tracked["pending_name"])
        confirmed = [e for e in s["events"] if e["type"] == "mirror_name_changed"]
        self.assertEqual(len(confirmed), 5)
        self.assertEqual(confirmed[0]["previous"], "MOMDAX")
        self.assertEqual(confirmed[0]["current"], "NANDOV")
        self.assertEqual(api.name, "Streaming HD miroir")
        self.assertEqual(len(api.bot_messages), 1)

    def test_migrate_existing_html_announcement_into_old_and_new_names(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)):
            mirror_watch.sync_thread(api, s, self.now)
        state1 = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"]
        for entry in state1.values():
            entry["site_name"] = "momdax devient nandov.com"
            entry["name_attempted_at"] = self.now.isoformat()
        with patch("mirror_watch.check_site") as http, patch("mirror_watch.probe_site_name") as metadata:
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=5))
        http.assert_not_called()
        metadata.assert_not_called()
        entry = state1[URLS[0]]
        self.assertEqual(entry["site_name"], "MOMDAX")
        self.assertEqual(entry["pending_name"], "NANDOV")
        self.assertEqual(entry["announced_url"], "https://nandov.com/")
        self.assertIn("NANDOV", api.bot_messages[0]["content"])
        self.assertIn("https://nandov.com/", api.bot_messages[0]["content"])
        self.assertEqual(
            len([e for e in s["events"] if e["type"] == "mirror_name_suspected"]), 5
        )
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "NANDOV", "previous_name": "MOMDAX",
                "announced_url": "https://nandov.com/",
                "confidence": "strong", "evidence": "title:rename"
            }
        ):
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1, hours=6))
        self.assertEqual(entry["site_name"], "NANDOV")
        self.assertIsNone(entry["pending_name"])
        self.assertEqual(
            len([e for e in s["events"] if e["type"] == "mirror_name_changed"]), 5
        )

    def test_antibot_page_or_failed_request_never_renames(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "MOMDAX", "confidence": "medium", "evidence": "title"
            }
        ):
            mirror_watch.sync_thread(api, s, self.now)
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value=None
        ):
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        tracked = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]
        self.assertEqual(tracked["site_name"], "MOMDAX")
        self.assertIsNone(tracked.get("pending_name"))
        self.assertFalse(any(e["type"].startswith("mirror_name") for e in s["events"]))

    def test_same_name_different_case_or_accents_is_not_a_rename(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={"name": "FénoV", "evidence": "title"}
        ):
            mirror_watch.sync_thread(api, s, self.now)
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={"name": "FENOV", "evidence": "title"}
        ):
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        tracked = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]
        self.assertFalse(tracked.get("pending_name"))
        self.assertEqual(tracked["site_name"], "FénoV")

    def test_candidate_cleared_if_old_name_returns(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)):
            for day, name in [(0, "MOMDAX"), (1, "NANDOV"), (2, "MOMDAX")]:
                with patch("mirror_watch.probe_site_name", return_value={
                    "name": name, "evidence": "og:site_name"
                }):
                    mirror_watch.sync_thread(api, s, self.now + timedelta(days=day))
        tracked = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]
        self.assertEqual(tracked["site_name"], "MOMDAX")
        self.assertFalse(tracked["pending_name"])
        self.assertFalse(any(e["type"] == "mirror_name_changed" for e in s["events"]))

    def test_initial_five_urls_are_all_checked_and_shown_in_one_message(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)) as probe:
            self.assertEqual(mirror_watch.sync_thread(api, s, self.now), "updated")
            self.assertEqual(probe.call_count, 5)
        self.assertEqual(len(s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"]), 5)
        self.assertEqual(len(api.bot_messages), 1)
        rendered = api.bot_messages[0]["content"]
        for url in URLS:
            self.assertIn(url, rendered)
        self.assertIn("quotidienne", rendered)
        self.assertEqual(s["events"], [])

    def test_no_double_check_on_same_paris_calendar_day(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)) as probe:
            mirror_watch.sync_thread(api, s, self.now)
            probe.reset_mock()
            api.calls.clear()
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=5))
            probe.assert_not_called()
        self.assertEqual(len(api.bot_messages), 1)
        self.assertFalse(any(method in ("PATCH", "POST") for method, _, _ in api.calls))

    def test_avoid_double_check_just_after_midnight(self):
        previous = datetime(2026, 11, 17, 22, 10, tzinfo=timezone.utc)
        next_day = previous + timedelta(hours=2)
        self.assertTrue(mirror_watch.is_checked_today(previous.isoformat(), next_day))
        self.assertFalse(
            mirror_watch.is_checked_today(previous.isoformat(),
                                          previous + timedelta(hours=21))
        )

    def test_next_local_day_checks_all_five_again(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)) as probe:
            mirror_watch.sync_thread(api, s, self.now)
            probe.reset_mock()
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        self.assertEqual(probe.call_count, 5)
        self.assertIn("Dernier contrôle", api.bot_messages[0]["content"])

    def test_two_distinct_daily_failures_confirm_offline(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response("failed", 503, u)):
            mirror_watch.sync_thread(api, s, self.now)
            self.assertEqual(s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]["status"], "suspect")
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        states = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"]
        self.assertEqual(states[URLS[0]]["status"], "offline")
        self.assertEqual(len([e for e in s["events"] if e["type"] == "mirror_offline"]), 5)

    def test_recovery_is_recorded(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response("failed", 404, u)):
            mirror_watch.sync_thread(api, s, self.now)
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)):
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=2))
        self.assertEqual(s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]["status"], "online")
        self.assertEqual(len([e for e in s["events"] if e["type"] == "mirror_recovered"]), 5)

    def test_added_and_removed_link_updates_only_bot_message(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)):
            mirror_watch.sync_thread(api, s, self.now)
            api.calls.clear()
            replacement = "https://foxtrot.example.org/"
            api.messages = [human_message(URLS[1:] + [replacement])]
            mirror_watch.sync_thread(api, s, self.now + timedelta(days=1))
        text = api.bot_messages[0]["content"]
        self.assertNotIn(URLS[0], text)
        self.assertIn(replacement, text)
        self.assertEqual(len(api.bot_messages), 1)
        self.assertEqual({e["type"] for e in s["events"]},
                         {"mirror_added", "mirror_removed"})
        self.assertFalse(any("/messages/100" in path and method == "PATCH"
                             for method, path, _ in api.calls))

    def test_author_can_explicitly_rename_thread_but_site_label_cannot(self):
        api = FakeAPI([human_message(name="Nouveau nom : NANDOV")])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)):
            mirror_watch.sync_thread(api, s, self.now)
            self.assertEqual(api.name, "Streaming HD miroir")
            api.messages = [human_message(name="Nouveau nom du fil : Streaming HD miroir 2")]
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=6))
        self.assertEqual(api.name, "Streaming HD miroir 2")

    def test_other_users_urls_included_but_bot_links_excluded(self):
        user = {"id": "101", "content": "https://visitor.example.org",
                "author": {"id": "666", "bot": False}}
        bot = {"id": "102", "content": "https://ignore.example.org",
               "author": {"id": "333", "bot": True}}
        result = mirror_watch.find_urls([human_message(), user, bot], "333")
        self.assertEqual(len(result), 6)
        self.assertNotIn("https://ignore.example.org/", [x["url"] for x in result])

    def test_invalid_or_unreadable_source_creates_no_message(self):
        api = FakeAPI([human_message([])])
        self.assertEqual(mirror_watch.sync_thread(api, state(), self.now), "blocked")
        self.assertEqual(len(api.bot_messages), 0)

    def test_redirect_keeps_original_url(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url="https://other.example.org/")):
            mirror_watch.sync_thread(api, s, self.now)
        entry = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]
        self.assertEqual(entry["url"], URLS[0])
        self.assertEqual(entry["redirect_url"], "https://other.example.org/")
        self.assertIn("à confirmer", api.bot_messages[0]["content"])

    def test_restricted_does_not_count_as_failure(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site", side_effect=lambda u: response("restricted", 403, u)):
            mirror_watch.sync_thread(api, s, self.now)
        entry = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"][URLS[0]]
        self.assertEqual(entry["status"], "restricted")
        self.assertEqual(entry["failures"], 0)


if __name__ == "__main__":
    unittest.main()
