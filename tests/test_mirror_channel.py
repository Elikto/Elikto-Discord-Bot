"""Regression tests for Streaming HD mirrors in the new text channel."""
import unittest

from mirror_channel import (
    sync_mirror_channel, CHANNEL_NAME, TOPIC_MARKER,
)
from mirror_watch import THREAD_ID, GUILD_ID, render_site_cards
from forum_cards import last_check_line


def mirror_state():
    return {
        "tracked_threads": {
            THREAD_ID: {
                "name": "Streaming HD miroir",
                "display_sites": [
                    {"name": "PRINCIPAL", "url": "https://primary.example.org/",
                     "source_urls": ["https://original.example.org/"],
                     "status": "online", "page_health": "content",
                     "last_checked": "2026-11-19T06:02:00+00:00"},
                    {"name": "EN ATTENTE", "url": "https://pending.example.org/",
                     "source_urls": [], "status": "online",
                     "page_health": "unknown",
                     "last_checked": "2026-11-19T06:05:00+00:00"},
                    {"name": "EN PANNE", "url": "https://broken.example.org/",
                     "source_urls": [], "status": "empty_index",
                     "page_health": "empty_index",
                     "last_checked": "2026-11-19T06:07:00+00:00"},
                ],
            },
        },
    }


class FakeDiscord:
    def __init__(self):
        self.channels = [
            {"id": "111", "type": 4, "name": "🎬 Streaming · Sites"},
            {"id": THREAD_ID, "type": 11, "name": "Streaming HD miroir"},
        ]
        self.bot_messages = {}
        self.seq = 300
        self.calls = []

    def call(self, method, path, *, json=None, params=None):
        self.calls.append((method, path))
        if method == "GET" and path == f"/guilds/{GUILD_ID}/channels":
            return list(self.channels)
        if method == "GET" and path == "/users/@me":
            return {"id": "999"}
        if method == "POST" and path == f"/guilds/{GUILD_ID}/channels":
            self.seq += 1
            created = {"id": str(self.seq), **json}
            self.channels.append(created)
            return dict(created)
        if method == "PATCH" and path.startswith("/channels/") and "/messages/" not in path:
            cid = path.split("/")[2]
            record = next(c for c in self.channels if c["id"] == cid)
            record.update(json)
            return record
        if method == "GET" and path.endswith("/messages"):
            cid = path.split("/")[2]
            return list(self.bot_messages.get(cid, []))
        if method == "GET" and "/messages/" in path:
            cid = path.split("/")[2]
            mid = path.split("/")[-1]
            return next(m for m in self.bot_messages.get(cid, [])
                        if m["id"] == mid)
        if method == "POST" and path.endswith("/messages"):
            cid = path.split("/")[2]
            self.seq += 1
            item = {"id": str(self.seq), "author": {"id": "999"},
                    "components": json["components"], "flags": json["flags"],
                    "content": json["content"]}
            self.bot_messages.setdefault(cid, []).append(item)
            return dict(item)
        if method == "PATCH" and "/messages/" in path:
            cid = path.split("/")[2]
            mid = path.split("/")[-1]
            item = next(m for m in self.bot_messages[cid] if m["id"] == mid)
            item.update({"components": json["components"],
                         "flags": json["flags"], "content": json["content"]})
            return dict(item)
        raise AssertionError(f"Unexpected request: {method} {path}")


class MirrorChannelTests(unittest.TestCase):
    def test_new_channel_has_same_dropdown_and_scan_data_as_original_forum(self):
        api = FakeDiscord()
        state = mirror_state()
        cid, mid = sync_mirror_channel(api, state, dropdown_enabled=True)
        self.assertEqual(
            [c["name"] for c in api.channels if c.get("type") == 0],
            [CHANNEL_NAME],
        )
        channel = next(c for c in api.channels if c["id"] == cid)
        self.assertIn(TOPIC_MARKER, channel["topic"])
        self.assertEqual(channel["parent_id"], "111")
        self.assertEqual(len(api.bot_messages[cid]), 1)
        self.assertEqual(api.bot_messages[cid][0]["id"], mid)
        parts = api.bot_messages[cid][0]["components"][0]["components"]
        self.assertEqual(parts[1]["type"], 1)
        opts = {o["label"]: o for o in parts[1]["components"][0]["options"]}
        self.assertEqual(opts["EN ATTENTE"]["emoji"]["name"], "🟡")
        self.assertEqual(opts["EN PANNE"]["emoji"]["name"], "🔴")
        self.assertIn("❌ Index vide", opts["EN PANNE"]["description"])
        self.assertIn("19/11 07:07", opts["EN PANNE"]["description"])
        main_text = parts[0]["content"]
        self.assertIn("🟢 ✦ [PRINCIPAL]", main_text)
        self.assertIn("🕒 Dernier scan : 19/11/2026 à 07:07 (Paris)", main_text)
        self.assertEqual(state["streaming_mirror_channel"]["message_id"], mid)

        # Re-running always edits the same one message, never recreates
        # the channel or edits the old forum.
        api.calls.clear()
        cid2, mid2 = sync_mirror_channel(api, state, dropdown_enabled=True)
        self.assertEqual((cid, mid), (cid2, mid2))
        self.assertEqual(len(api.bot_messages[cid]), 1)
        self.assertFalse(any(m in ("POST", "PATCH") for m, _ in api.calls))
        self.assertFalse(any(THREAD_ID in p for _, p in api.calls))

    def test_reuses_existing_streaming_hd_salon_without_second_channel(self):
        api = FakeDiscord()
        api.channels.append({
            "id": "555", "type": 0, "name": "streaming-hd",
            "parent_id": "111",
            "topic": f"[elikto:site-channel:v1:{THREAD_ID}]",
        })
        api.bot_messages["555"] = [{
            "id": "899", "author": {"id": "999"}, "flags": 32768,
            "content": None, "components": [{
                "type": 17, "components": [{
                    "type": 10, "content": "## ✦ 🎬 Streaming HD ✦",
                }],
            }],
        }]
        state = mirror_state()
        state["site_channel_mirrors"] = {
            THREAD_ID: {"channel_id": "555", "message_id": "899"}
        }
        cid, mid = sync_mirror_channel(api, state, dropdown_enabled=True)
        self.assertEqual((cid, mid), ("555", "899"))
        self.assertEqual(len(api.bot_messages["555"]), 1)
        self.assertFalse(any(method == "POST" and path.endswith("/channels")
                             for method, path in api.calls))
        self.assertEqual(state["streaming_mirror_channel"]["channel_id"], "555")
        self.assertEqual(state["site_channel_mirrors"][THREAD_ID]["message_id"], "899")
        self.assertEqual(
            api.bot_messages["555"][0]["components"][0]["components"][1]
            ["components"][0]["type"], 3
        )

    def test_traffic_light_only_green_with_verified_html(self):
        source = mirror_state()["tracked_threads"][THREAD_ID]["display_sites"]
        source.insert(0, {
            "name": "HTTP 200 MAIS NON VERIFIE",
            "url": "https://unconfirmed.example.org/",
            "status": "online", "source_urls": ["https://unconfirmed.example.org/"],
            "last_checked": "2026-11-19T06:00:00+00:00",
        })
        parts = render_site_cards(
            source, "Mirrors", dropdown_enabled=True, status_badges=True
        )[0]["components"]
        options = {
            x["label"]: x for p in parts if p["type"] == 1
            for x in p["components"][0]["options"]
        }
        self.assertEqual(options["HTTP 200 MAIS NON VERIFIE"]["emoji"]["name"], "🟡")
        self.assertNotIn("✅", options["HTTP 200 MAIS NON VERIFIE"]["description"])
        self.assertIn("🟢", parts[0]["content"])

    def test_existing_managed_channel_is_reused_even_after_losing_state(self):
        api = FakeDiscord()
        state = mirror_state()
        cid, mid = sync_mirror_channel(api, state, dropdown_enabled=True)
        state.pop("streaming_mirror_channel")
        cid2, mid2 = sync_mirror_channel(api, state, dropdown_enabled=True)
        self.assertEqual((cid, mid), (cid2, mid2))
        self.assertEqual(len(api.bot_messages[cid]), 1)

    def test_reuses_legacy_empty_text_channel_without_touching_old_forum(self):
        api = FakeDiscord()
        api.channels.append({
            "id": "555", "type": 0, "name": CHANNEL_NAME,
            "parent_id": "111", "topic": "",
        })
        cid, _ = sync_mirror_channel(api, mirror_state(), dropdown_enabled=True)
        self.assertEqual(cid, "555")
        self.assertEqual(len([c for c in api.channels if c.get("type") == 0]), 1)
        self.assertIn(TOPIC_MARKER, api.channels[-1]["topic"])

    def test_no_scanned_sites_never_creates_empty_channel(self):
        api = FakeDiscord()
        with self.assertRaisesRegex(RuntimeError, "Aucun miroir"):
            sync_mirror_channel(api, {"tracked_threads": {}})
        self.assertFalse(any(method == "POST" for method, _ in api.calls))

    def test_fallback_spoiler_if_dropdown_endpoint_unavailable(self):
        api = FakeDiscord()
        cid, _ = sync_mirror_channel(api, mirror_state(), dropdown_enabled=False)
        comps = api.bot_messages[cid][0]["components"]
        self.assertEqual(len(comps), 2)
        self.assertTrue(comps[1]["spoiler"])
        self.assertIn("🔴", comps[1]["components"][0]["content"])


if __name__ == "__main__":
    unittest.main()
