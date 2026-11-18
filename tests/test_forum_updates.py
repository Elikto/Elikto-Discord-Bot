import unittest
from unittest.mock import patch
from forum_cards import all_text, BRAND
from forum_updates import (
    MARKER, render_forum_update, site_groups, sync_forum_updates,
    upsert_forum_update,
)

THREAD = "1562067560856289411"
OTHER = "1562067560856289412"
GUILD = "1430984645012357263"


def record(url, *, announced=None, status="online", older=(), name=None, active=True):
    return {
        "thread_id": THREAD,
        "title": url,
        "discord_url": f"https://discord.com/channels/{GUILD}/{THREAD}/{THREAD}",
        "site_url": url,
        "sources": [{
            "url": f"https://discord.com/channels/{GUILD}/{THREAD}/{THREAD}",
            "channel": "WebFlix", "kind": "fil",
        }],
        "active": active,
        "status": status,
        "site_name": name,
        "announced_url": announced,
        "announced_status": "online" if announced else None,
        "other_previous_urls": list(older),
    }


def shown(message):
    return "\n".join(all_text(message.get("components") or []))

class FakeAPI:
    def __init__(self):
        self.messages = {}
        self.calls = []
        self.nextid = 9000
        self.fail_post = False

    def call(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, json))
        if method == "GET" and path == "/users/@me":
            return {"id": "bot"}
        if "/messages/" in path and method == "GET":
            tid, mid = path.split("/")[2], path.split("/")[-1]
            for m in self.messages.get(tid, []):
                if m["id"] == mid:
                    return dict(m)
            raise RuntimeError("HTTP 404")
        if path.endswith("/messages") and method == "GET":
            tid = path.split("/")[2]
            return list(reversed(self.messages.get(tid, [])))
        if path.endswith("/messages") and method == "POST":
            if self.fail_post:
                raise RuntimeError("Discord POST : HTTP 403")
            tid = path.split("/")[2]
            self.nextid += 1
            msg = {"id": str(self.nextid), "author": {"id": "bot"},
                   "content": json.get("content"),
                   "components": json.get("components", []),
                   "flags": json.get("flags", 0)}
            self.messages.setdefault(tid, []).append(msg)
            return dict(msg)
        if "/messages/" in path and method == "PATCH":
            tid, mid = path.split("/")[2], path.split("/")[-1]
            for m in self.messages.get(tid, []):
                if m["id"] == mid:
                    if m["author"]["id"] != "bot":
                        raise AssertionError("Tentative de modification d'un message utilisateur")
                    m["content"] = json.get("content")
                    m["components"] = json.get("components", [])
                    m["flags"] = json.get("flags", 0)
                    return dict(m)
        raise AssertionError(f"Unexpected API call: {method} {path}")


def state():
    return {
        "records": {
            THREAD: {"thread_id": THREAD, "title": "WebFlix",
                     "discord_url": f"https://discord.com/channels/{GUILD}/{THREAD}"},
            OTHER: {"thread_id": OTHER, "title": "Films divers",
                    "discord_url": f"https://discord.com/channels/{GUILD}/{OTHER}"},
        },
        "server_links": {
            "https://webflix.art/": record(
                "https://webflix.art/", announced="https://webflix.surf/",
                older=["https://webflix.bio/"],
            ),
            "https://webflixfr.github.io/": record(
                "https://webflixfr.github.io/", name="Webflix",
            ),
        },
    }


class ForumUpdatesTests(unittest.TestCase):
    def test_webflix_displays_current_address_and_previous_domains(self):
        result = render_forum_update("WebFlix", list(state()["server_links"].values()))
        self.assertIn("https://webflix.surf/", result)
        self.assertIn("### 🔄 Adresses mises à jour · webflix.surf", result)
        self.assertIn("**✦ [webflix.surf](https://webflix.surf/)**", result)
        self.assertIn("**✦ [webflixfr.github.io](https://webflixfr.github.io/)**", result)
        self.assertIn("webflix.art, webflix.bio", result)
        self.assertIn("https://webflixfr.github.io/", result)
        self.assertEqual(result.count("https://webflix.surf/"), 1)
        self.assertIn(MARKER, result)
        self.assertNotIn("HTTP 200", result)

    def test_all_public_site_threads_receive_an_initial_card(self):
        data = state()
        del data["server_links"]["https://webflix.art/"]
        api = FakeAPI()
        summary = sync_forum_updates(api, data)
        self.assertEqual(summary["created"], 1)
        self.assertIn("webflixfr.github.io", shown(api.messages[THREAD][0]))
        self.assertNotIn(OTHER, api.messages)

    def test_first_migration_creates_once_and_subsequent_change_edits(self):
        data = state()
        api = FakeAPI()
        initial = sync_forum_updates(api, data)
        self.assertEqual(initial["created"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        original_id = api.messages[THREAD][0]["id"]
        self.assertIn(BRAND, shown(api.messages[THREAD][0]))
        self.assertNotIn(MARKER, shown(api.messages[THREAD][0]))
        unchanged = sync_forum_updates(api, data)
        self.assertEqual(unchanged["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        data["server_links"]["https://webflix.art/"]["announced_url"] = "https://webflix.next/"
        changed = sync_forum_updates(api, data)
        self.assertEqual(changed["updated"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        self.assertEqual(api.messages[THREAD][0]["id"], original_id)
        self.assertIn("https://webflix.next/", shown(api.messages[THREAD][0]))

    def test_existing_user_post_is_never_modified(self):
        data = state()
        api = FakeAPI()
        api.messages[THREAD] = [{
            "id": "1", "author": {"id": "human"}, "content": "Ancien lien"
        }]
        sync_forum_updates(api, data)
        self.assertEqual(api.messages[THREAD][0]["content"], "Ancien lien")
        self.assertEqual(len(api.messages[THREAD]), 3)
        self.assertEqual(api.messages[THREAD][1]["author"]["id"], "bot")

    def test_rediscover_message_if_state_was_not_saved(self):
        data = state()
        api = FakeAPI()
        sync_forum_updates(api, data)
        message_id = api.messages[THREAD][0]["id"]
        data.pop("forum_updates")
        result = sync_forum_updates(api, data)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        self.assertEqual(data["forum_updates"][THREAD]["message_id"], message_id)

    def test_new_url_event_updates_existing_forum(self):
        data = state()
        data["server_links"] = {
            "https://new.example.org/": record("https://new.example.org/"),
        }
        api = FakeAPI()
        event = [{"type": "server_site_added", "thread_id": THREAD}]
        result = sync_forum_updates(api, data, event)
        self.assertEqual(result["created"], 1)
        self.assertIn("https://new.example.org/", shown(api.messages[THREAD][0]))

    def test_per_forum_write_failure_is_recorded_not_silenced(self):
        data = state()
        api = FakeAPI()
        api.fail_post = True
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["thread_id"], THREAD)
        self.assertEqual(data["forum_updates"], {})

    def test_does_not_publish_into_unknown_or_private_threads(self):
        data = state()
        data["server_links"]["https://secret.org/"] = {
            **record("https://secret.org/"),
            "sources": [{"url": f"https://discord.com/channels/{GUILD}/333/444",
                         "channel": "privé"}],
            "announced_url": "https://public.org/",
        }
        api = FakeAPI()
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 1)
        self.assertNotIn("333", api.messages)
        self.assertNotIn("https://public.org/", shown(api.messages[THREAD][0]))

    def test_exact_description_is_embedded_and_updates_without_duplicate(self):
        data = state()
        api = FakeAPI()
        original = "Dernières sorties en HD **sans pub**.\n\nGratuit - Illimité."
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": original}
        ]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["descriptions_found"], 1)
        published = api.messages[THREAD][-2]
        self.assertEqual(published["flags"], 32768)
        self.assertIn(original, shown(published))
        self.assertIn("🌐 https://webflix.surf/", shown(published))
        self.assertNotIn(MARKER, shown(published))
        self.assertNotIn("Message actualisé automatiquement", shown(published))
        same_id = published["id"]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 3)
        api.messages[THREAD][0]["content"] = original + "\n**Mise à jour du texte**"
        result = sync_forum_updates(api, data)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(api.messages[THREAD][-2]["id"], same_id)
        self.assertIn("**Mise à jour du texte**", shown(api.messages[THREAD][-2]))
        self.assertEqual(api.messages[THREAD][0]["content"],
                         original + "\n**Mise à jour du texte**")


    def test_reseam_card_and_auto_preview(self):
        data = state()
        data["records"][THREAD]["title"] = "Reseam Manager"
        data["server_links"] = {
            "https://reseam.app/download/": record("https://reseam.app/download/")
        }
        api = FakeAPI()
        original = (
            "Application pour patcher plusieurs applications pour enlever toutes les pubs.\n\n"
            "Youtube, Instagram, tiktok, twitter, twitch etc\n\n"
            "https://reseam.app/download/"
        )
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": original}
        ]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["previews_created"], 1)
        self.assertEqual(len(api.messages[THREAD]), 3)
        card = shown(api.messages[THREAD][1])
        self.assertIn("✦ Reseam Manager ✦", card)
        self.assertIn("🌐 https://reseam.app/download/", card)
        self.assertIn("📝 Description", card)
        self.assertIn("Youtube, Instagram, tiktok, twitter, twitch etc", card)
        self.assertEqual(card.count("https://reseam.app/download/"), 1)
        self.assertEqual(api.messages[THREAD][2]["content"], "https://reseam.app/download/")
        second = sync_forum_updates(api, data)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 3)
        self.assertEqual(api.messages[THREAD][0]["content"], original)

    def test_missing_original_description_is_not_invented(self):
        data = state()
        api = FakeAPI()
        result = sync_forum_updates(api, data)
        self.assertEqual(result["description_missing"], 1)
        self.assertNotIn("Description d'origine", shown(api.messages[THREAD][0]))

    def test_primary_forum_link_used_if_global_scan_has_no_link(self):
        data = state()
        data["records"][OTHER]["site_url"] = "https://another.example.org/"
        api = FakeAPI()
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 2)
        self.assertIn("another.example.org", shown(api.messages[OTHER][0]))

    def test_mirror_maintains_its_own_bot_message(self):
        data = state()
        mirror_id = "1430988094374547559"
        data["records"][mirror_id] = {"title": "Streaming HD miroir"}
        data["server_links"]["https://old.example.org/"] = {
            **record("https://old.example.org/", announced="https://next.example.org/"),
            "sources": [{"url": f"https://discord.com/channels/{GUILD}/{mirror_id}/{mirror_id}"}],
        }
        api = FakeAPI()
        sync_forum_updates(api, data)
        self.assertNotIn(mirror_id, api.messages)


if __name__ == "__main__":
    unittest.main()
