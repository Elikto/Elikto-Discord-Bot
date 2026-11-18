"""Tests sans réseau : miroirs de forums Discord en salons textuels."""
import unittest
from unittest.mock import patch

from site_channels import (
    channel_slug, mirror_category_name, catalogue_threads, sync_mirrors,
    safe_source_description, groups_for_mirror, CHANNEL_MARKER,
    destination_category_name, mirror_card, PREFIX,
)
from forum_cards import all_text

GUILD = "1430984645012357263"
TR4KER = "1572752361967853713"
C411 = "1431075479110032441"
WEBFLIX = "1562067560856289411"

def catalogue():
    return {
        "visibility": "public_everyone_only",
        "categories": [
            {"id": "100", "name": "Streaming", "channels": [{
                "kind": "forum", "name": "films-et-series",
                "url": f"https://discord.com/channels/{GUILD}/110",
                "items": [{"title": "WebFlix", "url": f"https://discord.com/channels/{GUILD}/{WEBFLIX}"}],
            }]},
            {"id": "200", "name": "Téléchargement", "channels": [{
                "kind": "forum", "name": "torrent",
                "url": f"https://discord.com/channels/{GUILD}/210",
                "items": [
                    {"title": "Tr4ker", "url": f"https://discord.com/channels/{GUILD}/{TR4KER}"},
                    {"title": "C411", "url": f"https://discord.com/channels/{GUILD}/{C411}"},
                ],
            }]},
            {"id": "300", "name": "privé", "channels": [{
                "kind": "text", "name": "salon-interne",
                "url": f"https://discord.com/channels/{GUILD}/310",
                "items": [],
            }]},
        ],
    }


def state():
    refs = [(TR4KER, "https://tr4ker.net/"),
            (C411, "https://c411.org/"),
            (WEBFLIX, "https://webflix.surf/")]
    state_ = {
        "records": {},
        "events": [],
        "server_links": {},
        "forum_updates": {},
    }
    for tid, url in refs:
        state_["records"][tid] = {"thread_id": tid, "site_url": url,
                                   "title": url, "status": "online"}
        state_["forum_updates"][tid] = {
            "active_url": url, "active_status": "online", "active_verified": True
        }
        state_["server_links"][url] = {
            "site_url": url, "status": "online",
            "sources": [{
                "url": f"https://discord.com/channels/{GUILD}/{tid}/{tid}",
                "kind": "fil",
            }], "active": True,
            "last_checked": "2026-11-18T23:00:00+00:00",
        }
    return state_


class FakeDiscord:
    def __init__(self):
        self.channels = [
            {"id": "100", "name": "Streaming", "type": 4,
             "permission_overwrites": []},
            {"id": "200", "name": "Téléchargement", "type": 4,
             "permission_overwrites": []},
            {"id": "110", "name": "films-et-series", "type": 15,
             "parent_id": "100", "permission_overwrites": []},
            {"id": "210", "name": "torrent", "type": 15,
             "parent_id": "200", "permission_overwrites": []},
        ]
        self.messages = {}
        self.originals = {
            TR4KER: (
                "✦ **Tr4ker** ✦\n\n📝 **Description**\n"
                "Tracker BitTorrent francophone proposant un catalogue de torrents"
                " (films, séries et autres catégories).\n\n"
                "🌐 **Site :** https://tr4ker.net/\n\n"
                "🔄 Adresse contrôlée automatiquement chaque jour par le bot.\n"
                "[elikto-site-seed:tr4ker:v1]"
            ),
            C411: (
                "📝 **Description**\nTracker semi-privé francophone.\n"
                "🔐 Identifiants\n👤 Utilisateur : compte@example.org\n"
                "🔑 Mot de passe : secret-password-123\n"
                "https://c411.org/"
            ),
            WEBFLIX: (
                "✦ WebFlix ✦\n📝 Description\n"
                "Interface moderne et catalogue important.\n"
                "https://webflix.surf/"
            ),
        }
        self.seq = 400
        self.actions = []

    def call(self, method, path, *, json=None, params=None):
        self.actions.append((method, path, json))
        if method == "GET" and path == f"/guilds/{GUILD}/channels":
            return list(self.channels)
        if method == "GET" and path == f"/guilds/{GUILD}":
            return {"roles": [{"id": GUILD, "permissions": str((1 << 10) | (1 << 11))}]}
        if method == "GET" and path == "/users/@me":
            return {"id": "999"}
        if method == "POST" and path == f"/guilds/{GUILD}/channels":
            self.seq += 1
            rec = {"id": str(self.seq), **json}
            self.channels.append(rec)
            return dict(rec)
        if method == "PATCH" and path.startswith("/channels/") and "/messages/" not in path:
            channel_id = path.split("/")[2]
            rec = next(c for c in self.channels if str(c["id"]) == channel_id)
            rec.update(json)
            return dict(rec)
        if method == "GET" and "/messages/" in path:
            cid, mid = path.split("/")[2], path.split("/")[-1]
            if cid in self.originals and cid == mid:
                return {"id": mid, "author": {"id": ("999" if cid == TR4KER else "member")},
                        "content": self.originals[cid]}
            item = next((m for m in self.messages.get(cid, []) if m["id"] == mid), None)
            if not item:
                raise RuntimeError("HTTP 404")
            return dict(item)
        if method == "GET" and path.endswith("/messages"):
            cid = path.split("/")[2]
            return list(reversed(self.messages.get(cid, [])))
        if method == "POST" and path.endswith("/messages"):
            cid = path.split("/")[2]
            self.seq += 1
            item = {"id": str(self.seq), "author": {"id": "999"},
                    "flags": json.get("flags"), "components": json.get("components"),
                    "content": json.get("content")}
            self.messages.setdefault(cid, []).append(item)
            return dict(item)
        if method == "PATCH" and "/messages/" in path:
            cid, mid = path.split("/")[2], path.split("/")[-1]
            item = next(m for m in self.messages[cid] if m["id"] == mid)
            item["components"] = json["components"]
            return dict(item)
        raise AssertionError(f"Unexpected {method} {path}")

class MirrorTests(unittest.TestCase):
    def test_plans_real_public_forums_only(self):
        result = catalogue_threads(catalogue())
        self.assertEqual({x["tid"] for x in result}, {TR4KER, C411, WEBFLIX})
        self.assertEqual(channel_slug("C411 - La Méga !"), "c411-la-mega")
        self.assertEqual(mirror_category_name("Téléchargement", 2),
                         "Téléchargement · Sites 2")
        source = {"source_category_name": "Téléchargement",
                  "source_forum_name": "torrent"}
        self.assertEqual(destination_category_name(source),
                         "📥 Téléchargement · Torrents")

    def test_creates_parallel_categories_channels_and_cards_idempotently(self):
        api = FakeDiscord()
        current = state()
        first = sync_mirrors(api, catalogue(), current)
        self.assertEqual(first["categories_created"], 2)
        self.assertEqual(first["channels_created"], 3)
        self.assertEqual(first["cards_created"], 3)
        self.assertEqual(first["errors"], [])
        mirrored = current["site_channel_mirrors"]
        self.assertEqual(set(mirrored), {TR4KER, C411, WEBFLIX})
        self.assertTrue(any(c["name"] == "📥 Téléchargement · Torrents"
                            for c in api.channels))
        for tid in (TR4KER, C411, WEBFLIX):
            cid = mirrored[tid]["channel_id"]
            ch = next(ch for ch in api.channels if ch["id"] == cid)
            self.assertEqual(ch["type"], 0)
            self.assertEqual(CHANNEL_MARKER.search(ch["topic"]).group(1), tid)
            self.assertEqual(len(api.messages[cid]), 1)
            self.assertTrue(any("Ancien forum" == part["accessory"]["label"]
                                for part in api.messages[cid][0]["components"][0]["components"]
                                if part.get("type") == 9))

        tr4ker_text = "\n".join(all_text(api.messages[
            mirrored[TR4KER]["channel_id"]][0]["components"]))
        self.assertEqual(tr4ker_text.count("https://tr4ker.net/"), 1)
        self.assertEqual(tr4ker_text.count("**📝 Description**"), 0)
        self.assertNotIn("Tracker BitTorrent francophone proposant", tr4ker_text)
        c411_text = "\n".join(all_text(api.messages[
            mirrored[C411]["channel_id"]][0]["components"]))
        self.assertIn("Tracker semi-privé francophone.", c411_text)
        self.assertNotIn("secret-password-123", c411_text)
        self.assertNotIn("compte@example.org", c411_text)
        self.assertNotIn("Identifiants", c411_text)
        self.assertEqual(c411_text.count("https://c411.org/"), 1)

        second = sync_mirrors(api, catalogue(), current)
        self.assertEqual(second["categories_created"], 0)
        self.assertEqual(second["channels_created"], 0)
        self.assertEqual(second["cards_created"], 0)
        self.assertEqual(second["cards_unchanged"], 3)
        self.assertFalse(any(method in ("DELETE", "PATCH") and
                             path.startswith(f"/channels/{TR4KER}")
                             for method, path, _ in api.actions))

    def test_existing_channels_recovered_without_saved_state(self):
        api = FakeDiscord()
        current = state()
        sync_mirrors(api, catalogue(), current)
        current.pop("site_channel_mirrors", None)
        second = sync_mirrors(api, catalogue(), current)
        self.assertEqual(second["channels_created"], 0)
        self.assertEqual(second["cards_created"], 0)
        self.assertEqual(second["cards_unchanged"], 3)

    def test_existing_download_channels_are_moved_without_recreation(self):
        """Former 14-channel folder becomes Torrents; Books/Games get their own."""
        api = FakeDiscord()
        api.channels.extend([
            {"id": "211", "name": "jeux-video", "type": 15, "parent_id": "200"},
            {"id": "212", "name": "livres", "type": 15, "parent_id": "200"},
            {"id": "400", "name": "Telechargement · Sites", "type": 4,
             "permission_overwrites": []},
        ])
        game = "1672752361967853711"
        book = "1672752361967853712"
        d = catalogue()
        d["categories"][1]["channels"].extend([
            {"kind": "forum", "name": "jeux-video",
             "url": f"https://discord.com/channels/{GUILD}/211",
             "items": [{"title": "Fitgirl repacks",
                        "url": f"https://discord.com/channels/{GUILD}/{game}"}]},
            {"kind": "forum", "name": "livres",
             "url": f"https://discord.com/channels/{GUILD}/212",
             "items": [{"title": "Z-library",
                        "url": f"https://discord.com/channels/{GUILD}/{book}"}]},
        ])
        api.originals[game] = "Bibliothèque de jeux."
        api.originals[book] = "Bibliothèque de livres."
        st = state()
        for tid, domain in [(game, "https://fitgirl-repacks.site/"),
                            (book, "https://z-library.ec/")]:
            st["records"][tid] = {"thread_id": tid, "title": tid,
                                   "site_url": domain, "status": "online"}
            st["forum_updates"][tid] = {
                "active_url": domain, "active_status": "online",
                "active_verified": True,
            }
        sources = {x["tid"]: x for x in catalogue_threads(d)}
        st["site_channel_mirrors"] = {}
        for cid, tid, label in [
            ("401", TR4KER, "tr4ker"),
            ("402", game, "fitgirl-repacks"),
            ("403", book, "z-library"),
        ]:
            api.channels.append({
                "id": cid, "name": label, "type": 0, "parent_id": "400",
                "topic": PREFIX + tid + "]",
            })
            previous_id = "500" + cid
            api.messages[cid] = [{
                "id": previous_id, "author": {"id": "999"},
                "content": None, "flags": 32768,
                "components": mirror_card(api, st, sources[tid], "999"),
            }]
            st["site_channel_mirrors"][tid] = {
                "channel_id": cid, "message_id": previous_id
            }
        # C411 is not already mirrored. It must be created in Torrents,
        # whereas the other three channels retain their IDs and messages.
        first = sync_mirrors(api, d, st)
        self.assertEqual(first["categories_renamed"], 1)
        self.assertEqual(first["channels_moved"], 2)
        self.assertEqual(first["categories_created"], 3)  # Livres, Jeux et Streaming
        self.assertEqual(first["channels_created"], 2)  # C411 et WebFlix, non préexistants
        # Cards may be refreshed during a daily sync, but their message
        # IDs and channel IDs must be preserved throughout the move.
        self.assertTrue(any(
            cat["id"] == "400" and cat["name"] == "📥 Téléchargement · Torrents"
            for cat in api.channels
        ))
        dest = {cat["name"]: cat["id"] for cat in api.channels if cat["type"] == 4}
        self.assertEqual(
            next(ch["parent_id"] for ch in api.channels if ch["id"] == "401"),
            "400"
        )
        self.assertEqual(
            next(ch["parent_id"] for ch in api.channels if ch["id"] == "402"),
            dest["🎮 Téléchargement · Jeux vidéo"]
        )
        self.assertEqual(
            next(ch["parent_id"] for ch in api.channels if ch["id"] == "403"),
            dest["📚 Téléchargement · Livres"]
        )
        for tid in (TR4KER, game, book):
            self.assertEqual(
                st["site_channel_mirrors"][tid]["message_id"],
                "500" + st["site_channel_mirrors"][tid]["channel_id"]
            )
        self.assertFalse(any(
            method == "DELETE" for method, _, _ in api.actions
        ))
        second = sync_mirrors(api, d, st)
        self.assertEqual(second["categories_renamed"], 0)
        self.assertEqual(second["channels_moved"], 0)
        self.assertEqual(second["channels_created"], 0)
        self.assertEqual(second["categories_created"], 0)

    def test_future_download_site_keeps_its_source_forum_group(self):
        api = FakeDiscord()
        api.channels.extend([
            {"id": "211", "name": "jeux-video", "type": 15, "parent_id": "200"},
            {"id": "212", "name": "livres", "type": 15, "parent_id": "200"},
        ])
        d = catalogue()
        d["categories"][1]["channels"].extend([
            {"kind": "forum", "name": "jeux-video",
             "url": f"https://discord.com/channels/{GUILD}/211",
             "items": []},
            {"kind": "forum", "name": "livres",
             "url": f"https://discord.com/channels/{GUILD}/212",
             "items": []},
        ])
        st = state()
        sync_mirrors(api, d, st)
        extra = "1672752361967853714"
        d["categories"][1]["channels"][1]["items"].append({
            "title": "Nouveau jeu",
            "url": f"https://discord.com/channels/{GUILD}/{extra}",
        })
        api.originals[extra] = "Jeux à télécharger"
        st["records"][extra] = {
            "thread_id": extra, "site_url": "https://example.net/",
            "status": "unknown",
        }
        result = sync_mirrors(api, d, st)
        self.assertEqual(result["channels_created"], 1)
        parent = next(
            ch["parent_id"] for ch in api.channels
            if ch["id"] == st["site_channel_mirrors"][extra]["channel_id"]
        )
        category = next(ch for ch in api.channels if ch["id"] == parent)
        self.assertEqual(category["name"], "🎮 Téléchargement · Jeux vidéo")

    def test_daily_sync_updates_same_message_when_current_domain_changes(self):
        api = FakeDiscord()
        current = state()
        sync_mirrors(api, catalogue(), current)
        old_id = current["site_channel_mirrors"][WEBFLIX]["message_id"]
        current["forum_updates"][WEBFLIX]["active_url"] = "https://webflix.next/"
        second = sync_mirrors(api, catalogue(), current)
        self.assertEqual(second["cards_updated"], 1)
        self.assertEqual(current["site_channel_mirrors"][WEBFLIX]["message_id"], old_id)
        msg = api.messages[current["site_channel_mirrors"][WEBFLIX]["channel_id"]][0]
        self.assertIn("https://webflix.next/", "\n".join(all_text(msg["components"])))

    def test_no_sensitive_or_generic_description_replicated(self):
        api = FakeDiscord()
        sources = {s["tid"]:s for s in catalogue_threads(catalogue())}
        self.assertEqual(safe_source_description(api, sources[TR4KER], "999"), "")
        c411 = safe_source_description(api, sources[C411], "999")
        self.assertIn("Tracker semi-privé francophone.", c411)
        self.assertNotIn("mot de passe", c411.casefold())

    def test_discards_private_forums_that_were_since_hidden(self):
        api = FakeDiscord()
        api.channels[-1]["nsfw"] = True
        result = sync_mirrors(api, catalogue(), state())
        self.assertEqual(result["channels_created"], 1)
        self.assertEqual(result["cards_created"], 1)
        self.assertNotIn(C411, {
            k for k in state().get("site_channel_mirrors", {})
        })

    def test_newly_added_future_thread_gets_channel_automatically(self):
        api = FakeDiscord()
        current = state()
        sync_mirrors(api, catalogue(), current)
        future = "1672752361967853713"
        future_cat = catalogue()
        future_cat["categories"][1]["channels"][0]["items"].append({
            "title": "Nouveau site", "url": f"https://discord.com/channels/{GUILD}/{future}"
        })
        api.originals[future] = "Une vraie description."
        current["records"][future] = {"thread_id": future,
                                       "site_url": "https://example.net/",
                                       "status": "unknown"}
        next_run = sync_mirrors(api, future_cat, current)
        self.assertEqual(next_run["channels_created"], 1)
        self.assertEqual(next_run["cards_created"], 1)


if __name__ == "__main__":
    unittest.main()
