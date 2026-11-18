"""Tests hors réseau de la nouvelle fiche publique Torrent / Tr4ker."""
import unittest
from unittest.mock import patch

from tr4ker_forum import (
    SEED_MARKER, SITE_URL, STARTER_CONTENT, find_torrent_forum,
    existing_tr4ker, ensure_tr4ker_forum,
)
from forum_cards import source_description
from server_link_watch import find_server_links
from site_monitor import pick_site_url


class FakeDiscord:
    def __init__(self, *, forum=True, existing=False, archived=False):
        self.forum = forum
        self.thread_id = "1700000000000000000"
        self.existing = existing
        self.is_archived = archived
        self.posts = []
        self.messages = []
        self.guild_id = "1430984645012357263"

    def call(self, method, path, *, json=None, params=None):
        if method == "GET" and path == "/users/@me":
            return {"id": "999"}
        if method == "GET" and path.endswith("/channels") and path.startswith("/guilds/"):
            return [
                {"id": "200", "name": "torrent", "type": 15},
                {"id": "201", "name": "torrent", "type": 0},
                {"id": "202", "name": "autres", "type": 15},
            ] if self.forum else [{"id": "201", "name": "torrent", "type": 0}]
        if method == "GET" and path.endswith("/threads/active"):
            threads = (
                [{"id": self.thread_id, "name": "Tr4ker", "parent_id": "200"}]
                if self.existing and not self.is_archived else []
            )
            return {"threads": threads}
        if method == "POST" and path == "/channels/200/threads":
            self.posts.append(json)
            return {"id": self.thread_id, "name": json["name"]}
        if method == "GET" and path == f"/channels/{self.thread_id}/messages/{self.thread_id}":
            return {"id": self.thread_id, "author": {"id": "999"},
                    "content": STARTER_CONTENT}
        raise AssertionError(f"Unexpected request: {method} {path}")

    def archived(self, forum_id):
        if self.existing and self.is_archived and forum_id == "200":
            yield {"id": self.thread_id, "name": "Tr4ker",
                   "parent_id": "200"}


class Tr4kerForumTests(unittest.TestCase):
    def test_creates_exactly_one_thread_in_torrent(self):
        api = FakeDiscord()
        new_id, created = ensure_tr4ker_forum(api)
        self.assertTrue(created)
        self.assertEqual(new_id, api.thread_id)
        self.assertEqual(len(api.posts), 1)
        body = api.posts[0]
        self.assertEqual(body["name"], "Tr4ker")
        self.assertIn(SITE_URL, body["message"]["content"])
        self.assertIn(SEED_MARKER, body["message"]["content"])
        self.assertEqual(body["message"]["allowed_mentions"], {"parse": []})
        self.assertNotIn("password", str(body).lower())
        self.assertNotIn("username", str(body).lower())

    def test_reuses_existing_active_and_archived_threads(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                api = FakeDiscord(existing=True, archived=archived)
                tid, created = ensure_tr4ker_forum(api)
                self.assertEqual(tid, api.thread_id)
                self.assertFalse(created)
                self.assertEqual(api.posts, [])

    def test_does_not_post_if_torrent_forum_is_missing(self):
        api = FakeDiscord(forum=False)
        with self.assertRaisesRegex(RuntimeError, "Forum Torrent absent"):
            ensure_tr4ker_forum(api)
        self.assertEqual(api.posts, [])

    def test_only_seeded_bot_starter_is_scanned(self):
        api = FakeDiscord()
        api.messages = [
            {"id": api.thread_id, "author": {"id": "999"},
             "content": STARTER_CONTENT},
            {"id": "1700000000000000001", "author": {"id": "999"},
             "content": "https://fake.example.org/"},
            {"id": "1700000000000000002", "author": {"id": "555"},
             "content": "Pas de nouveau lien"},
        ]
        locations = ([{"id": api.thread_id, "name": "Tr4ker",
                       "kind": "fil", "parent_id": "200"}], [])
        with patch("server_link_watch.public_thread_locations",
                   return_value=locations), patch(
                   "server_link_watch.all_messages",
                   return_value=iter(api.messages)):
            found, stats, failures = find_server_links(api)
        self.assertEqual(set(found), {SITE_URL})
        self.assertEqual(stats["unique_urls"], 1)
        self.assertEqual(failures, [])

    def test_starter_description_and_monitoring_url(self):
        api = FakeDiscord()
        body = source_description(api, api.thread_id, "999")
        self.assertIn("Tracker BitTorrent francophone", body)
        self.assertNotIn(SEED_MARKER, body)
        self.assertEqual(
            pick_site_url(api, api.thread_id, "Tr4ker"), SITE_URL,
        )

    def test_other_bot_message_not_republished_as_description(self):
        api = FakeDiscord()
        with patch.object(api, "call", return_value={
            "author": {"id": "999"},
            "content": "https://another.example.org/"
        }):
            self.assertEqual(source_description(api, api.thread_id, "999"), "")


if __name__ == "__main__":
    unittest.main()
