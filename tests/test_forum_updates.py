import unittest
from datetime import datetime, timedelta, timezone
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
        if "/messages/" in path and method == "DELETE":
            tid, mid = path.split("/")[2], path.split("/")[-1]
            items = self.messages.get(tid, [])
            self.messages[tid] = [m for m in items if m["id"] != mid]
            return None
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
    def setUp(self):
        # Les tests de synchronisation n'accèdent jamais aux sites externes.
        no_html = patch("forum_updates.fetch_html_page", return_value=None)
        no_html.start()
        self.addCleanup(no_html.stop)
        # Uniquement pour ces tests de rendu : pas de requête vers Internet.
        image_ok = patch("forum_updates.preview_image_works", return_value=True)
        image_ok.start()
        self.addCleanup(image_ok.stop)


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
        self.assertEqual(len(api.messages[THREAD]), 1)
        original_id = api.messages[THREAD][0]["id"]
        self.assertIn(BRAND, shown(api.messages[THREAD][0]))
        self.assertNotIn(MARKER, shown(api.messages[THREAD][0]))
        unchanged = sync_forum_updates(api, data)
        self.assertEqual(unchanged["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 1)
        data["server_links"]["https://webflix.art/"]["announced_url"] = "https://webflix.next/"
        changed = sync_forum_updates(api, data)
        self.assertEqual(changed["updated"], 1)
        self.assertEqual(len(api.messages[THREAD]), 1)
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
        self.assertEqual(len(api.messages[THREAD]), 2)
        self.assertEqual(api.messages[THREAD][1]["author"]["id"], "bot")

    def test_rediscover_message_if_state_was_not_saved(self):
        data = state()
        api = FakeAPI()
        sync_forum_updates(api, data)
        message_id = api.messages[THREAD][0]["id"]
        data.pop("forum_updates")
        result = sync_forum_updates(api, data)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 1)
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
        published = api.messages[THREAD][-1]
        self.assertEqual(published["flags"], 32768)
        self.assertIn(original, shown(published))
        self.assertIn("**🌐 Site actif**\nhttps://webflix.surf/", shown(published))
        self.assertNotIn(MARKER, shown(published))
        self.assertNotIn("Message actualisé automatiquement", shown(published))
        same_id = published["id"]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        api.messages[THREAD][0]["content"] = original + "\n**Mise à jour du texte**"
        result = sync_forum_updates(api, data)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(api.messages[THREAD][-1]["id"], same_id)
        self.assertIn("**Mise à jour du texte**", shown(api.messages[THREAD][-1]))
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
        self.assertEqual(result["previews_created"], 0)
        self.assertEqual(len(api.messages[THREAD]), 2)
        card = shown(api.messages[THREAD][1])
        self.assertIn("✦ Reseam Manager ✦", card)
        self.assertIn("**🌐 Site actif**\nhttps://reseam.app/download/", card)
        self.assertIn("📝 Description", card)
        self.assertIn("Youtube, Instagram, tiktok, twitter, twitch etc", card)
        self.assertEqual(card.count("https://reseam.app/download/"), 1)
        self.assertFalse(any(
            m.get("content") == "https://reseam.app/download/"
            for m in api.messages[THREAD] if m["author"]["id"] == "bot"
        ))
        second = sync_forum_updates(api, data)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        self.assertEqual(api.messages[THREAD][0]["content"], original)

    def test_streaming_forum_marks_directory_listing_with_red_cross(self):
        from forum_cards import build_card
        groups = [
            {"url": "https://varloz.com/47b6f3a/home/varloz",
             "status": "online", "older": [], "announced": False},
            {"url": "https://rogzov.com/93zjfyqk6llf/",
             "status": "empty_index", "older": [], "announced": False},
        ]
        text = "\n".join(all_text(build_card(groups, "Films et séries", "Streaming")))
        self.assertIn("**🌐 Site actif**\nhttps://varloz.com/47b6f3a/home/varloz", text)
        self.assertIn("❌ **🌐 Site actif**\nhttps://rogzov.com/93zjfyqk6llf/", text)
        self.assertIn("Page vide : index de répertoire détecté", text)

    def test_credentials_in_a_separate_author_reply_appear_under_description(self):
        data = state()
        data["records"][THREAD]["title"] = "C411"
        api = FakeAPI()
        base = "Tracker semi-privé, inscription et ratio positif obligatoire."
        reply = "Id : exemple-utilisateur\nmdp : test-secret-123"
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": base},
            {"id": str(int(THREAD) + 1), "author": {"id": "human"},
             "content": reply},
        ]
        first = sync_forum_updates(api, data)
        self.assertEqual(first["created"], 1)
        cards = [message for message in api.messages[THREAD]
                 if message.get("components")]
        self.assertEqual(len(cards), 1)
        body = shown(cards[0])
        self.assertIn(base, body)
        self.assertIn("🔐 Identifiants", body)
        self.assertIn("👤 Utilisateur :** exemple-utilisateur", body)
        self.assertIn("🔑 Mot de passe :** ||test-secret-123||", body)
        self.assertEqual(body.count("test-secret-123"), 1)
        self.assertNotIn("test-secret-123", repr(data))
        self.assertEqual(api.messages[THREAD][1]["content"], reply)
        second = sync_forum_updates(api, data)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(len([m for m in api.messages[THREAD]
                              if m.get("components")]), 1)

    def test_all_monitored_public_forums_and_separate_credential_messages(self):
        data = state()
        data["records"][THREAD]["title"] = "Forum alpha"
        data["records"][OTHER]["title"] = "Forum beta"
        data["records"][OTHER]["site_url"] = "https://beta.example.org/"
        api = FakeAPI()
        alpha_secret = "demo-alpha-only"
        beta_secret = "demo-beta-only"
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": "Alpha description"},
            {"id": str(int(THREAD)+1), "author": {"id": "human"},
             "content": "Id : alpha_demo"},
            {"id": str(int(THREAD)+2), "author": {"id": "human"},
             "content": "mdp : " + alpha_secret},
        ]
        api.messages[OTHER] = [
            {"id": OTHER, "author": {"id": "human"}, "content": "Beta description"},
            {"id": str(int(OTHER)+1), "author": {"id": "human"},
             "content": "Email : beta@example.org\nMot de passe : " + beta_secret},
        ]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 2)
        alpha_card = next(m for m in api.messages[THREAD] if m.get("components"))
        beta_card = next(m for m in api.messages[OTHER] if m.get("components"))
        self.assertIn("alpha_demo", shown(alpha_card))
        self.assertIn("||" + alpha_secret + "||", shown(alpha_card))
        self.assertIn("beta@example.org", shown(beta_card))
        self.assertIn("||" + beta_secret + "||", shown(beta_card))
        self.assertNotIn(alpha_secret, shown(beta_card))
        self.assertNotIn(beta_secret, shown(alpha_card))
        self.assertNotIn(alpha_secret, repr(data))
        self.assertNotIn(beta_secret, repr(data))
        self.assertEqual(sync_forum_updates(api, data)["unchanged"], 2)

    def test_credentials_from_another_author_are_not_published(self):
        data = state()
        api = FakeAPI()
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "owner"}, "content": "Description"},
            {"id": str(int(THREAD) + 1), "author": {"id": "stranger"},
             "content": "Id : autre-utilisateur\nmdp : secret-autre"},
            {"id": str(int(THREAD) + 2), "author": {"id": "bot", "bot": True},
             "content": "Id : faux-utilisateur\nmdp : secret-bot"},
        ]
        sync_forum_updates(api, data)
        body = "\n".join(shown(m) for m in api.messages[THREAD] if m.get("components"))
        self.assertNotIn("Identifiants", body)
        self.assertNotIn("secret-autre", body)
        self.assertNotIn("secret-bot", body)

    def test_annas_archive_html_identity_proves_current_green_badge(self):
        from forum_updates import proven_html_content
        from forum_cards import build_card
        observed = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        record_ = {
            **record("https://annas-archive.cc/"),
            "site_name": "Anna’s Archive",
            "name_evidence": "application-name",
            "name_checked_at": observed,
            "last_checked": observed,
        }
        self.assertTrue(proven_html_content(record_))
        groups = site_groups([record_], "Annas-archive")
        self.assertTrue(groups[0]["verified"])
        content = "\n".join(all_text(build_card(groups, "Livres", "Annas-archive")))
        self.assertIn("✅", content)
        self.assertNotIn("Intégrité du contenu non confirmée", content)

    def test_http_200_alone_is_yellow_not_green(self):
        from forum_updates import proven_html_content
        from forum_cards import build_card
        record_ = {**record("https://sample.example.org/"), "http_status": 200}
        self.assertFalse(proven_html_content(record_))
        groups = site_groups([record_], "Sans preuve HTML")
        content = "\n".join(all_text(build_card(groups, "Catalogue", "Sans preuve HTML")))
        self.assertIn("🟡", content)
        self.assertNotIn("✅", content)

    def test_stale_or_failed_html_evidence_is_not_green(self):
        from forum_updates import proven_html_content
        stale = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        base = {**record("https://sample.example.org/"),
                "name_evidence": "title", "name_checked_at": stale}
        self.assertFalse(proven_html_content(base))
        self.assertFalse(proven_html_content({
            **base, "name_checked_at": fresh, "page_health": "unknown"
        }))
        self.assertFalse(proven_html_content({
            **base, "name_checked_at": fresh, "page_health": "empty_index"
        }))
        self.assertFalse(proven_html_content({
            **base, "name_checked_at": fresh, "status": "unknown"
        }))
        self.assertTrue(proven_html_content({
            **base, "name_checked_at": fresh, "page_health": "content"
        }))

    def test_every_migrated_site_displays_reference_before_active(self):
        from forum_cards import build_card
        groups = site_groups([{
            **record("https://fstream.info/", announced="https://fs05.lol/"),
            "status": "online", "page_health": "content",
            "announced_verified": True,
        }], "French Stream")
        self.assertEqual(groups[0]["reference_url"], "https://fstream.info/")
        self.assertEqual(groups[0]["url"], "https://fs05.lol/")
        card = build_card(groups, "Grand catalogue", "French Stream",
                          preview_image_url="https://fs05.lol/poster.png")
        parts = card[0]["components"]
        texts = [p["content"] for p in parts if p["type"] == 10]
        ref_idx = next(i for i, text in enumerate(texts)
                       if "**🔎 Site de référence**" in text)
        active_idx = next(i for i, text in enumerate(texts)
                          if "**🌐 Site actif**" in text)
        self.assertLess(ref_idx, active_idx)
        self.assertIn("https://fstream.info/", texts[ref_idx])
        self.assertIn("https://fs05.lol/", texts[active_idx])
        self.assertEqual(parts[-1]["type"], 12)

    def test_generic_site_without_reference_does_not_repeat_same_url(self):
        from forum_cards import build_card
        groups = site_groups([record("https://reseam.app/download/")],
                             "Reseam Manager")
        self.assertIsNone(groups[0].get("reference_url"))
        body = "\n".join(all_text(build_card(groups, "Appli", "Reseam Manager")))
        self.assertNotIn("Site de référence", body)
        self.assertEqual(body.count("https://reseam.app/download/"), 1)

    def test_coflix_directory_prefers_available_address_over_blocked_or_fake(self):
        from forum_updates import directory_candidates, validated_directory_groups
        from forum_cards import build_card
        portal = "https://coflix.domains/"
        content = (
            '<main><a href="https://coflix.esq/">CoFlix.Esq Adresse disponible '
            '27/07/2026</a><a href="https://coflix.ninja/">CoFlix.ninja '
            'Bloqué par les FAI</a><a href="https://coflix.fyi/">'
            'Coflix.fyi Faux site</a></main>'
        )
        self.assertEqual(directory_candidates(content, portal, "coflix"),
                         ["https://coflix.esq/"])
        old = [record("https://coflix.boo/", status="offline")]
        with patch("forum_updates.check_site", return_value={
            "result": "online",
        }), patch("forum_updates.fetch_html_page", return_value={"html": content}), patch(
            "forum_updates.probe_site_name", return_value={
                "name": "Coflix", "evidence": "title"
            }
        ):
            group = validated_directory_groups(old, "coflix")[0]
        self.assertEqual(group["reference_url"], portal)
        self.assertEqual(group["url"], "https://coflix.esq/")
        self.assertTrue(group["verified"])
        self.assertNotEqual(group["url"], "https://coflix.boo/")
        body = "\n".join(all_text(build_card([group], "Description", "Coflix")))
        self.assertLess(body.index("https://coflix.domains/"),
                        body.index("https://coflix.esq/"))

    def test_flemmix_observed_url_is_only_announced_until_validated(self):
        from forum_updates import validated_directory_groups
        from forum_cards import build_card
        hub = "https://ww1.wiflix-adresses.fun/"
        stale_html = (
            '<h2>Nom de domaine principal (Nouveau site)</h2>'
            '<a href="https://flemmix.casa/">flemmix.casa</a>'
            '<div>Domaine actif</div>'
        )
        env = {
            "FLEMMIX_OBSERVED_MAIN": "https://flemmix.uno/",
            "FLEMMIX_FAI_BLOCKED_HOSTS": "flemmix.casa,flemmix.prof",
        }
        def check(url):
            if url == hub:
                return {"result": "online", "final_url": hub}
            if url == "https://flemmix.uno/":
                return {"result": "unknown", "http_status": 503}
            raise AssertionError("An old FAI-blocked domain was checked: " + url)
        with patch.dict("os.environ", env), patch(
            "forum_updates.check_site", side_effect=check
        ) as checks, patch(
            "forum_updates.fetch_html_page", return_value={"html": stale_html}
        ), patch("forum_updates.probe_site_name") as identity:
            group = validated_directory_groups(
                [record("https://flemmix.mom/", status="parked")], "flemmix"
            )[0]
        self.assertEqual(checks.call_count, 2)
        identity.assert_not_called()
        self.assertTrue(group["no_active"])
        self.assertFalse(group["verified"])
        self.assertIsNone(group["url"] if not group["no_active"] else None)
        self.assertEqual(group["suggested_url"], "https://flemmix.uno/")
        body = "\n".join(all_text(build_card([group], "Flemmix", "Flemmix")))
        self.assertIn("https://ww1.wiflix-adresses.fun/", body)
        self.assertIn("Adresse annoncée (non vérifiée)", body)
        self.assertIn("https://flemmix.uno/", body)
        self.assertNotIn("https://flemmix.casa/", body)
        self.assertNotIn("https://flemmix.mom/", body)
        self.assertNotIn("✅", body)

    def test_flemmix_observed_url_can_become_green_only_after_html_probe(self):
        from forum_updates import validated_directory_groups
        hub = "https://ww1.wiflix-adresses.fun/"
        env = {
            "FLEMMIX_OBSERVED_MAIN": "https://flemmix.uno/",
            "FLEMMIX_FAI_BLOCKED_HOSTS": "flemmix.casa",
        }
        with patch.dict("os.environ", env), patch(
            "forum_updates.check_site", side_effect=lambda url: {
                "result": "online", "final_url": url
            }
        ), patch("forum_updates.fetch_html_page", return_value={
            "html": "<h2>ANCIENS DOMAINES - BLOQUÉS PAR LES FAI</h2>"
                    "<a href='https://flemmix.casa/'>flemmix.casa</a>"
        }), patch("forum_updates.probe_site_name", return_value={
            "name": "Flemmix", "page_health": "content",
            "final_url": "https://flemmix.uno/"
        }) as html:
            group = validated_directory_groups(
                [record("https://flemmix.mom/", status="parked")], "flemmix"
            )[0]
        html.assert_called_once_with("https://flemmix.uno/")
        self.assertEqual(group["url"], "https://flemmix.uno/")
        self.assertFalse(group["no_active"])
        self.assertTrue(group["verified"])
        self.assertIsNone(group["suggested_url"])

    def test_flemmix_2026_active_uno_and_isp_blocked_list(self):
        from forum_updates import directory_candidates, validated_directory_groups
        from forum_cards import build_card
        html = """
          <h1>Flemmix - Nos Adresses</h1>
          <p>En cas de blocage FAI, utilisez le lien marqué Actif.</p>
          <div>LIEN PRINCIPAL</div>
          <a href="https://flemmix.uno/">flemmix.uno</a>
          <div>Domaine actif - Utilisez toujours ce lien</div><b>ACTIF</b>
          <h2>DOMAINES ACTIFS</h2>
          <a href="https://french-anime.com/">french-anime.com</a>
          <p>Notre site dédié aux animés</p>
          <div>ANCIENS DOMAINES - BLOQUÉS PAR LES FAI</div>
          <a href="https://wiflix.red/">wiflix.red</a>
          <span>Ancien domaine principal - bloqué</span><b>BLOQUÉ</b>
          <a href="https://flemmix.men/">flemmix.men</a>
          <p>Ancien domaine - bloqué</p>
          <a href="https://flemmix.casa/">flemmix.casa</a>
          <p>Ancien domaine - bloqué</p>
          <a href="https://flemmix.prof/">flemmix.prof</a>
          <p>Ancien domaine - bloqué</p>
          <h2>FAUX DOMAINES - NE PAS UTILISER</h2>
          <a href="https://flemmix.faux/">flemmix.faux</a>
        """
        hub = "https://ww1.wiflix-adresses.fun/"
        self.assertEqual(directory_candidates(html, hub, "flemmix"),
                         ["https://flemmix.uno/"])
        with patch("forum_updates.check_site", side_effect=[
            {"result": "online", "final_url": hub},
            {"result": "online", "final_url": "https://flemmix.uno/"},
        ]) as checks, patch(
            "forum_updates.fetch_html_page", return_value={"html": html}
        ), patch("forum_updates.probe_site_name", return_value={
            "name": "Flemmix", "page_health": "content",
            "final_url": "https://flemmix.uno/"
        }) as inspect:
            group = validated_directory_groups(
                [record("https://flemmix.mom/", status="parked"),
                 record("https://flemmix.casa/", status="online")],
                "flemmix"
            )[0]
        self.assertEqual(checks.call_count, 2)
        inspect.assert_called_once_with("https://flemmix.uno/")
        self.assertEqual(group["url"], "https://flemmix.uno/")
        self.assertEqual(group["reference_url"], hub)
        self.assertTrue(group["verified"])
        self.assertFalse(group["no_active"])
        rendered = "\n".join(all_text(build_card([group], "Description", "Flemmix")))
        self.assertIn(hub, rendered)
        self.assertIn("https://flemmix.uno/", rendered)
        for blocked in ("flemmix.casa", "flemmix.prof", "flemmix.men",
                        "flemmix.mom", "wiflix.red"):
            self.assertNotIn(blocked, rendered)

    def test_flemmix_blocked_section_overrides_earlier_active_mention(self):
        from forum_updates import directory_candidates
        portal = "https://ww1.wiflix-adresses.fun/"
        html = """
        <div>LIEN PRINCIPAL</div>
        <a href="https://flemmix.casa/">flemmix.casa</a>
        <span>Domaine actif</span>
        <div>ANCIENS DOMAINES - BLOQUÉS PAR LES FAI</div>
        <a href="https://flemmix.casa/">flemmix.casa</a>
        <p>Ancien domaine - bloqué</p>
        <div>LIEN PRINCIPAL</div>
        <a href="https://flemmix.uno/">flemmix.uno</a>
        <span>ACTIF</span>
        """
        self.assertEqual(directory_candidates(html, portal, "flemmix"),
                         ["https://flemmix.uno/"])

    def test_flemmix_directory_reads_declared_active_and_ignores_old_domains(self):
        from forum_updates import directory_candidates, validated_directory_groups
        html = (
            '<section><a href="https://flemmix.casa/">flemmix.casa</a>'
            '<p>Nom de domaine principal (Nouveau site)</p>'
            '<a href="https://flemmix.prof/">flemmix.prof</a>'
            '<p>Ancien domaine (bloqué)</p></section>'
        )
        portal = "https://ww1.wiflix-adresses.fun/"
        self.assertEqual(directory_candidates(html, portal, "flemmix"),
                         ["https://flemmix.casa/"])
        with patch("forum_updates.check_site", return_value={"result": "online"}), patch(
            "forum_updates.fetch_html_page", return_value={"html": html}
        ), patch("forum_updates.probe_site_name",
                 return_value={"name": "Flemmix"}):
            group = validated_directory_groups(
                [record("https://flemmix.mom/", status="offline")], "flemmix"
            )[0]
        self.assertEqual(group["reference_url"], portal)
        self.assertEqual(group["url"], "https://flemmix.casa/")
        self.assertTrue(group["verified"])

    def test_excludes_all_isp_blocked_entries_even_when_servers_reply_200(self):
        from forum_updates import directory_candidates, validated_directory_groups
        from forum_cards import build_card
        hub = "https://coflix.domains/"
        html = """
          <h2>Adresses disponibles</h2>
          <a href="https://coflix.esq/">CoFlix.Esq Adresse disponible 27/07/2026</a>
          <h2>Bloqué par les FAI</h2>
          <a href="https://coflix.ninja/">Coflix.ninja Adresse disponible</a>
          <a href="https://coflix.observer/">Coflix.observer Domaine actif</a>
          <h2>Adresses disponibles</h2>
          <a href="https://coflix.space/">Coflix.space Bloqué par les FAI</a>
        """
        candidates = directory_candidates(html, hub, "coflix")
        self.assertEqual(candidates, ["https://coflix.esq/"])
        # Malgré la réponse HTTP 200 des anciens domaines bloqués,
        # le bot ne contrôle et n'affiche que la vraie adresse éligible.
        with patch("forum_updates.check_site", return_value={"result": "online"}), patch(
            "forum_updates.fetch_html_page", return_value={"html": html}
        ), patch("forum_updates.probe_site_name",
                 return_value={"name": "Coflix", "page_health": "content"}) as name_probe:
            group = validated_directory_groups(
                [record("https://coflix.ninja/", status="online")],
                "coflix",
            )[0]
        self.assertEqual(group["url"], "https://coflix.esq/")
        self.assertTrue(group["verified"])
        name_probe.assert_called_once_with("https://coflix.esq/")
        txt = "\n".join(all_text(build_card([group], "Description", "CoFlix")))
        self.assertNotIn("coflix.ninja", txt)
        self.assertNotIn("coflix.space", txt)

    def test_all_domains_blocked_by_fai_shows_reference_and_no_active_site(self):
        from forum_updates import validated_directory_groups
        from forum_cards import build_card
        html = """
          <h2>Bloqué par les FAI</h2>
          <a href="https://coflix.ninja/">Coflix.ninja Domaine actif</a>
          <a href="https://coflix.observer/">Coflix.observer Adresse disponible</a>
        """
        with patch("forum_updates.check_site", return_value={"result": "online"}) as check, patch(
            "forum_updates.fetch_html_page", return_value={"html": html}
        ), patch("forum_updates.probe_site_name") as probe:
            group = validated_directory_groups(
                [record("https://coflix.ninja/", status="online")], "coflix"
            )[0]
        self.assertEqual(check.call_count, 1)
        probe.assert_not_called()
        self.assertTrue(group["no_active"])
        self.assertFalse(group["verified"])
        body = "\n".join(all_text(build_card([group], "Coflix", "Coflix")))
        self.assertIn("https://coflix.domains/", body)
        self.assertIn("Aucune adresse active vérifiée", body)
        self.assertNotIn("coflix.ninja", body)
        self.assertNotIn("coflix.observer", body)
        self.assertEqual(body.count("https://coflix.domains/"), 1)

    def test_expired_parking_and_shortener_never_pass_directory_validation(self):
        from forum_updates import validated_directory_groups
        hub = "https://ww1.wiflix-adresses.fun/"
        html = ('<a href="https://flemmix.casa/">flemmix.casa</a>'
                '<p>Nom de domaine principal (Nouveau site)</p>')
        for health in ("parked", "interstitial", "empty_index", "blocked"):
            with self.subTest(health=health):
                with patch("forum_updates.check_site", return_value={"result":"online"}), patch(
                    "forum_updates.fetch_html_page", return_value={"html":html}
                ), patch("forum_updates.probe_site_name", return_value={
                    "page_health": health,
                    "final_url": "https://flemmix.casa/",
                }):
                    group = validated_directory_groups(
                        [record("https://flemmix.mom/", status="online")], "flemmix"
                    )[0]
                self.assertTrue(group["no_active"])
                self.assertFalse(group["verified"])
                self.assertEqual(group["reference_url"], hub)

    def test_directory_shortener_redirect_must_never_be_verified(self):
        from forum_updates import validated_directory_groups
        html = ('<a href="https://flemmix.casa/">flemmix.casa</a>'
                '<p>Nom de domaine principal (Nouveau site)</p>')
        with patch("forum_updates.check_site", side_effect=[
            {"result": "online", "final_url": "https://ww1.wiflix-adresses.fun/"},
            {"result": "online", "final_url": "https://www.dlink9.com/m6nL/"},
        ]), patch("forum_updates.fetch_html_page", return_value={"html": html}), patch(
            "forum_updates.probe_site_name"
        ) as inspect:
            result = validated_directory_groups(
                [record("https://flemmix.mom/", status="offline")], "flemmix"
            )[0]
        inspect.assert_not_called()
        self.assertEqual(result["reference_url"], "https://ww1.wiflix-adresses.fun/")
        self.assertEqual(result["url"], "https://ww1.wiflix-adresses.fun/")
        self.assertTrue(result["no_active"])
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["verified"])

    def test_directory_redirect_to_new_same_brand_domain_can_be_verified(self):
        from forum_updates import validated_directory_groups
        html = ('<a href="https://flemmix.casa/">flemmix.casa</a>'
                '<p>Nom de domaine principal (Nouveau site)</p>')
        with patch("forum_updates.check_site", side_effect=[
            {"result": "online"}, {"result": "online",
                                     "final_url": "https://flemmix.run/"},
        ]), patch("forum_updates.fetch_html_page", return_value={"html": html}), patch(
            "forum_updates.probe_site_name", return_value={"name": "Flemmix"}
        ) as inspect:
            result = validated_directory_groups(
                [record("https://flemmix.mom/", status="offline")], "flemmix"
            )[0]
        inspect.assert_called_once_with("https://flemmix.run/")
        self.assertEqual(result["url"], "https://flemmix.run/")
        self.assertTrue(result["verified"])

    def test_unverified_destination_is_not_claimed_online(self):
        from forum_updates import validated_directory_groups
        html = ('<a href="https://coflix.esq/">Coflix.Esq '
                'Adresse disponible 27/07/2026</a>')
        with patch("forum_updates.check_site", side_effect=[
            {"result": "online"}, {"result": "offline"}
        ]), patch("forum_updates.fetch_html_page", return_value={"html": html}):
            group = validated_directory_groups(
                [record("https://coflix.boo/", status="offline")], "coflix"
            )[0]
        self.assertEqual(group["reference_url"], "https://coflix.domains/")
        self.assertEqual(group["url"], "https://coflix.domains/")
        self.assertTrue(group["no_active"])
        self.assertEqual(group["status"], "unknown")
        self.assertFalse(group["verified"])

    def test_vip_secret_phrase_numbered_list_is_in_the_same_card(self):
        from forum_cards import build_card, split_vip_phrase
        example = (
            "Films et séries sans pubs.\n\n"
            "1. Connexion VIP par phrase secrète pour enlever les pubs :\n"
            "2. **montagne maison rouge tulipe souris montagne**\n"
            "3.\n"
        )
        cleaned, phrase = split_vip_phrase(example)
        self.assertEqual(phrase, "montagne maison rouge tulipe souris montagne")
        self.assertIn("Films et séries", cleaned)
        self.assertNotIn("montagne maison rouge", cleaned)
        card = build_card(
            [{"url": "https://movix.online/", "status": "online",
              "older": [], "announced": False}],
            example, "Movix"
        )
        body = "\n".join(all_text(card))
        self.assertIn("🔐 Accès VIP", body)
        self.assertIn(
            "Phrase secrète :** ||montagne maison rouge tulipe souris montagne||",
            body
        )
        self.assertEqual(body.count("montagne maison rouge tulipe souris montagne"), 1)

    def test_vip_phrase_in_separate_author_message(self):
        data = state()
        data["records"][THREAD]["title"] = "Autre site VIP"
        api = FakeAPI()
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": "Films HD sans pub"},
            {"id": str(int(THREAD) + 1), "author": {"id": "human"},
             "content": "Connexion VIP par phrase secrète :\n"
                        "**cinéma orange violet lundi jeudi avion**"},
        ]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 1)
        card = next(m for m in api.messages[THREAD] if m.get("components"))
        self.assertIn("🔐 Accès VIP", shown(card))
        self.assertIn("||cinéma orange violet lundi jeudi avion||", shown(card))
        self.assertNotIn("cinéma orange violet lundi jeudi avion", repr(data))

    def test_every_site_card_places_url_after_description_access_and_before_image(self):
        from forum_cards import build_card
        content = (
            "Détails du service.\n\n"
            "Id : exemple\n"
            "mdp : demo-only\n"
        )
        groups = [{"url": "https://sample.example.org/", "status": "online",
                   "older": [], "announced": False}]
        components = build_card(
            groups, content, "Site exemple",
            preview_image_url="https://sample.example.org/cover.jpg"
        )
        parts = components[0]["components"]
        title_i = next(i for i, p in enumerate(parts)
                       if p["type"] == 10 and "Site exemple" in p["content"])
        description_i = next(i for i, p in enumerate(parts)
                             if p["type"] == 10 and "Description" in p["content"])
        password_i = next(i for i, p in enumerate(parts)
                          if p["type"] == 10 and "Mot de passe" in p["content"])
        url_i = next(i for i, p in enumerate(parts)
                     if p["type"] == 10 and "https://sample.example.org/" in p["content"])
        image_i = next(i for i, p in enumerate(parts) if p["type"] == 12)
        self.assertTrue(title_i < description_i < password_i < url_i)
        self.assertEqual(url_i + 1, image_i)
        self.assertEqual(parts[image_i]["items"][0]["media"]["url"],
                         "https://sample.example.org/cover.jpg")

    def test_vip_url_is_also_immediately_before_preview(self):
        from forum_cards import build_card
        components = build_card(
            [{"url": "https://movix.online/", "status": "online",
              "older": [], "announced": False}],
            "Description", "Movix",
            credentials={"vip_phrase": "six mots de démonstration uniquement"},
            preview_image_url="https://movix.online/banner.png"
        )
        parts = components[0]["components"]
        self.assertIn("🔐 Accès VIP", parts[-3]["content"])
        self.assertIn("https://movix.online/", parts[-2]["content"])
        self.assertEqual(parts[-1]["type"], 12)

    def test_opengraph_preview_image_inside_container_without_second_url(self):
        data = state()
        data["records"][THREAD]["title"] = "Reseam Manager"
        data["server_links"] = {
            "https://reseam.app/download/": record("https://reseam.app/download/")
        }
        api = FakeAPI()
        api.messages[THREAD] = [{
            "id": THREAD, "author": {"id": "human"}, "content": "Appli de patch",
            "embeds": [{
                "url": "https://reseam.app/download/",
                "image": {"url": "https://reseam.app/static/cover.webp"},
            }],
        }]
        result = sync_forum_updates(api, data)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["previews_created"], 0)
        card = api.messages[THREAD][-1]
        self.assertEqual(card["flags"], 32768)
        container = card["components"][0]
        media = [x for x in container["components"] if x["type"] == 12]
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["items"][0]["media"]["url"],
                         "https://reseam.app/static/cover.webp")
        self.assertEqual(len(api.messages[THREAD]), 2)
        sync_forum_updates(api, data)
        self.assertEqual(len(api.messages[THREAD]), 2)

    def test_link_message_is_removed_when_image_becomes_available(self):
        data = state()
        api = FakeAPI()
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": "Films récents"},
        ]
        sync_forum_updates(api, data)
        self.assertEqual(len(api.messages[THREAD]), 2)
        # Un ancien bot a publie un deuxieme message URL, mais son ID
        # n'existe plus dans l'etat : la prochaine execution doit le trouver.
        api.messages[THREAD].append({
            "id": "8765", "author": {"id": "bot"},
            "content": "https://webflix.surf/", "components": [],
        })
        self.assertEqual(len(api.messages[THREAD]), 3)
        record_state = data["forum_updates"][THREAD]
        record_state["preview_image_checked_at"] = "2024-01-01T00:00:00+00:00"
        with patch("forum_updates.fetch_html_page", return_value={
            "html": '<meta property="og:image" content="https://webflix.surf/banner.jpg">',
            "final_url": "https://webflix.surf/",
        }):
            sync_forum_updates(api, data)
        self.assertEqual(len(api.messages[THREAD]), 2)
        card = next(m for m in api.messages[THREAD] if m.get("components"))
        self.assertTrue(any(comp["type"] == 12
                            for comp in card["components"][0]["components"]))
        self.assertFalse(any((m.get("content") or "").startswith("https://")
                             for m in api.messages[THREAD]))

    def test_duplicate_bot_url_removed_from_all_forums_without_image(self):
        data = state()
        data["server_links"] = {
            "https://alpha.example.org/": record("https://alpha.example.org/"),
            "https://beta.example.org/": {
                **record("https://beta.example.org/"),
                "sources": [{"url": f"https://discord.com/channels/{GUILD}/{OTHER}/{OTHER}"}],
            }
        }
        data["records"][OTHER]["site_url"] = "https://beta.example.org/"
        api = FakeAPI()
        for tid, url in ((THREAD, "https://alpha.example.org/"),
                         (OTHER, "https://beta.example.org/")):
            api.messages[tid] = [
                {"id": tid, "author": {"id": "human"}, "content": "Original " + url},
                {"id": str(int(tid) + 4), "author": {"id": "bot"},
                 "content": url, "components": []},
                {"id": str(int(tid) + 5), "author": {"id": "bot"},
                 "content": url, "components": []},
            ]
        summary = sync_forum_updates(api, data)
        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["duplicate_links_removed"], 4)
        for tid in (THREAD, OTHER):
            self.assertEqual(len(api.messages[tid]), 2)
            self.assertEqual(len([m for m in api.messages[tid] if m.get("components")]), 1)
            self.assertEqual(api.messages[tid][0]["author"]["id"], "human")
            self.assertNotIn("preview_message_id", data["forum_updates"][tid])

    def test_does_not_delete_members_url_or_bots_other_messages(self):
        data = state()
        api = FakeAPI()
        api.messages[THREAD] = [
            {"id": THREAD, "author": {"id": "human"}, "content": "Original"},
            {"id": "10", "author": {"id": "human"},
             "content": "https://webflix.surf/", "components": []},
            {"id": "11", "author": {"id": "bot"},
             "content": "Le site a change : https://webflix.surf/", "components": []},
            {"id": "12", "author": {"id": "bot"},
             "content": "https://webflix.surf/", "components": []},
        ]
        summary = sync_forum_updates(api, data)
        self.assertEqual(summary["duplicate_links_removed"], 1)
        self.assertTrue(any(m["id"] == "10" for m in api.messages[THREAD]))
        self.assertTrue(any(m["id"] == "11" for m in api.messages[THREAD]))
        self.assertFalse(any(m["id"] == "12" for m in api.messages[THREAD]))

    def test_invalid_image_does_not_create_broken_gallery_or_second_message(self):
        data = state()
        api = FakeAPI()
        api.messages[THREAD] = [{
            "id": THREAD, "author": {"id": "human"}, "content": "Description",
            "embeds": [{"url": "https://webflix.surf/",
                        "image": {"url": "https://webflix.surf/broken.png"}}],
        }]
        with patch("forum_updates.preview_image_works", return_value=False):
            summary = sync_forum_updates(api, data)
        self.assertEqual(summary["created"], 1)
        self.assertEqual(len(api.messages[THREAD]), 2)
        card = next(m for m in api.messages[THREAD] if m.get("components"))
        self.assertFalse(any(
            part["type"] == 12 for part in card["components"][0]["components"]
        ))
        self.assertFalse(any(
            str(m.get("content") or "").startswith("https://")
            for m in api.messages[THREAD] if m["author"]["id"] == "bot"
        ))

    def test_description_removes_only_duplicate_standalone_url_lines(self):
        from forum_cards import build_card
        raw = (
            "Description d'origine\nhttps://fstream.info/\n"
            "La page partenaire https://example-partenaire.fr/ est utile.\n"
            "https://fs05.lol/\nAutres informations"
        )
        groups = [{"url": "https://fs05.lol/", "reference_url": "https://fstream.info/",
                   "status": "online", "older": [], "announced": True}]
        card = build_card(groups, raw, "French Stream")
        txt = "\n".join(all_text(card))
        self.assertEqual(txt.count("https://fs05.lol/"), 1)
        self.assertEqual(txt.count("https://fstream.info/"), 1)
        self.assertIn("La page partenaire https://example-partenaire.fr/", txt)
        self.assertIn("Autres informations", txt)

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
