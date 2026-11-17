import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import thread_watch


def source_message(mid="12", text="Nom actuel : MOMDAX\nAdresse actuelle : https://example.org/"):
    return {"id": mid, "author": {"id": "999"}, "content": text, "embeds": []}


class FakeAPI:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []
        self.bot_messages = []
        self.channel_name = "Streaming HD miroir"

    def call(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, json))
        if method == "GET" and path == f"/channels/{thread_watch.THREAD_ID}":
            return {"id": thread_watch.THREAD_ID, "guild_id": thread_watch.GUILD_ID,
                    "type": 11, "owner_id": "999", "name": self.channel_name}
        if method == "GET" and path == f"/channels/{thread_watch.THREAD_ID}/messages":
            return self.messages + self.bot_messages
        if method == "GET" and path == "/users/@me":
            return {"id": "333"}
        if method == "PATCH" and path == f"/channels/{thread_watch.THREAD_ID}":
            self.channel_name = json["name"]
            return {"name": self.channel_name}
        if method == "POST" and path == f"/channels/{thread_watch.THREAD_ID}/messages":
            msg = {"id": "500", "author": {"id": "333"}, "content": json["content"]}
            self.bot_messages.append(msg)
            return msg
        if method == "GET" and path.endswith("/messages/500"):
            return self.bot_messages[0]
        if method == "PATCH" and path.endswith("/messages/500"):
            self.bot_messages[0]["content"] = json["content"]
            return self.bot_messages[0]
        raise AssertionError(f"Unexpected API call: {method} {path}")


def empty_state():
    return {"version": 1, "records": {}, "events": [], "event_sequence": 0}


class ThreadWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 22, tzinfo=timezone.utc)

    def test_blocked_message_intent_never_posts_or_renames(self):
        api = FakeAPI([source_message(text="")])
        outcome = thread_watch.sync_thread(api, empty_state(), self.now)
        self.assertEqual(outcome, "blocked")
        self.assertFalse(any(method != "GET" for method, _, _ in api.calls))

    def test_first_sync_creates_one_bot_message_and_updates_title(self):
        api = FakeAPI([source_message()])
        s = empty_state()
        self.assertEqual(thread_watch.sync_thread(api, s, self.now), "updated")
        self.assertEqual(api.channel_name, "MOMDAX")
        self.assertEqual(len(api.bot_messages), 1)
        self.assertIn("https://example.org/", api.bot_messages[0]["content"])
        self.assertEqual(s["tracked_threads"][thread_watch.THREAD_ID]["message_id"], "500")
        self.assertEqual(s["events"], [])

    def test_same_information_does_not_spam_or_repeat_patches(self):
        api = FakeAPI([source_message()])
        s = empty_state()
        thread_watch.sync_thread(api, s, self.now)
        api.calls.clear()
        thread_watch.sync_thread(api, s, self.now)
        self.assertFalse(any(method != "GET" for method, _, _ in api.calls))
        self.assertEqual(len(api.bot_messages), 1)

    def test_changed_name_and_address_edits_only_bot_message(self):
        api = FakeAPI([source_message()])
        state = empty_state()
        thread_watch.sync_thread(api, state, self.now)
        api.calls.clear()
        api.messages.append(source_message(mid="14", text=(
            "Nouveau nom: NANDOV\nNouvelle adresse: https://new.example.org/"
        )))
        outcome = thread_watch.sync_thread(api, state, self.now)
        self.assertEqual(outcome, "updated")
        self.assertEqual(api.channel_name, "NANDOV")
        self.assertEqual(len(api.bot_messages), 1)
        self.assertIn("new.example.org", api.bot_messages[0]["content"])
        self.assertEqual([e["type"] for e in state["events"]],
                         ["title_changed", "address_changed"])
        edits = [(method, path) for method, path, _ in api.calls if method == "PATCH"]
        self.assertEqual(len(edits), 2)
        self.assertNotIn(f"/channels/{thread_watch.THREAD_ID}/messages/12",
                         [path for _, path in edits])

    def test_ambiguous_multiple_addresses_requires_explicit_confirmation(self):
        api = FakeAPI([source_message(text=(
            "https://example.org\nhttps://different.org"
        ))])
        self.assertEqual(thread_watch.sync_thread(api, empty_state(), self.now),
                         "ambiguous")
        self.assertFalse(api.bot_messages)

    def test_only_thread_owners_messages_are_authoritative(self):
        api = FakeAPI([
            source_message(text="https://example.org"),
            {"id": "15", "author": {"id": "666"},
             "content": "Nouveau nom: FAUX\nNouvelle adresse: https://bad.org"}
        ])
        state = empty_state()
        self.assertEqual(thread_watch.sync_thread(api, state, self.now), "updated")
        self.assertEqual(api.channel_name, "Streaming HD miroir")
        self.assertEqual(state["tracked_threads"][thread_watch.THREAD_ID]["address"],
                         "https://example.org/")

    def test_redirect_never_renames_thread_from_domain_alone(self):
        api = FakeAPI([source_message(text="https://example.org")])
        state = empty_state()
        state["records"][thread_watch.THREAD_ID] = {
            "redirect_url": "https://new.example.org"
        }
        thread_watch.sync_thread(api, state, self.now)
        self.assertEqual(api.channel_name, "Streaming HD miroir")
        self.assertIn("à vérifier", api.bot_messages[0]["content"])

    def test_explicit_new_address_preferred_over_old_link(self):
        msg1 = source_message(text="https://example.org/")
        msg2 = source_message(mid="13", text="Nouvelle adresse: https://new.example.org/")
        _, url, reason = thread_watch.source_info([msg1, msg2])
        self.assertEqual(url, "https://new.example.org/")
        self.assertEqual(reason, "annonce explicite de l'auteur")

    def test_cannot_post_returns_permission_denied_not_crash(self):
        api = FakeAPI([source_message(text="https://example.org/")])
        original = api.call
        def deny(method, path, **kwargs):
            if method == "POST":
                raise RuntimeError("Discord 403")
            return original(method, path, **kwargs)
        api.call = deny
        self.assertEqual(thread_watch.sync_thread(api, empty_state(), self.now),
                         "permission_denied")


if __name__ == "__main__":
    unittest.main()
