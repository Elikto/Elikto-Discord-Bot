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
            payload = kwargs["json"]
            self.bot_messages[0]["components"] = payload.get("components", [])
            self.bot_messages[0]["flags"] = payload.get("flags", 0)
            self.bot_messages[0]["content"] = (
                "\n".join(mirror_watch.component_text(payload["components"]))
                if payload.get("components") else payload.get("content")
            )
            return self.bot_messages[0]
        if method == "POST" and path.endswith("/messages"):
            payload = kwargs["json"]
            text_content = (
                "\n".join(mirror_watch.component_text(payload["components"]))
                if payload.get("components") else payload.get("content")
            )
            message = {"id": "500", "author": {"id": "333", "bot": True},
                       "content": text_content,
                       "components": payload.get("components", []),
                       "flags": payload.get("flags", 0)}
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
        # Éviter tout accès au Web depuis les tests existants.
        no_discovery = patch("mirror_watch.candidate_groups", return_value=[])
        no_discovery.start()
        self.addCleanup(no_discovery.stop)

    def test_internet_discovered_rogzov_uses_exact_path_and_red_dropdown_warning(self):
        """Régression de la capture : 200 à la racine, Index of / sur le miroir."""
        good = "https://varloz.com/47b6f3a/home/varloz"
        bad = "https://rogzov.com/93zjfyqk6llf/"
        api = FakeAPI([human_message()])
        s = state()
        tracked = s.setdefault("tracked_threads", {}).setdefault(mirror_watch.THREAD_ID, {})
        # Ancienne donnée verte à la racine : ne doit pas être réutilisée.
        tracked["target_states"] = {
            "https://rogzov.com/": {
                "url": "https://rogzov.com/",
                "status": "online",
                "http_status": 200,
                "last_checked": self.now.isoformat(timespec="seconds"),
            }
        }
        discovered = [
            {"url": "https://rogzov.com/", "name": "ROGZOV",
             "source_urls": [], "status": "pending", "previous_names": []},
            {"url": "https://varloz.com/", "name": "VARLOZ",
             "source_urls": [], "status": "pending", "previous_names": []},
        ]
        checked = []
        inspected = []

        def check(url):
            checked.append(url)
            return response(url=url)

        def inspect(url):
            inspected.append(url)
            if url == bad:
                return {"page_health": "empty_index", "evidence": "html:directory_index"}
            if url == good:
                return {"name": "VARLOZ", "confidence": "strong", "evidence": "title"}
            return None

        with patch("mirror_watch.candidate_groups", return_value=discovered), patch(
            "mirror_watch.check_site", side_effect=check
        ), patch("mirror_watch.probe_site_name", side_effect=inspect), patch.dict(
            "os.environ", {"DISCORD_MIRROR_DROPDOWN_ENABLED": "1"}
        ):
            self.assertEqual(mirror_watch.sync_thread(api, s, self.now), "updated")

        self.assertIn(bad, checked)
        self.assertIn(good, checked)
        self.assertIn(bad, inspected)
        self.assertNotIn("https://rogzov.com/", checked)
        record = s["tracked_threads"][mirror_watch.THREAD_ID]
        self.assertEqual(record["target_states"][bad]["status"], "empty_index")
        self.assertNotIn("https://rogzov.com/", record["target_states"])
        displayed = {site["name"]: site for site in record["display_sites"]}
        self.assertEqual(displayed["ROGZOV"]["url"], bad)
        self.assertEqual(displayed["ROGZOV"]["status"], "empty_index")
        self.assertEqual(displayed["VARLOZ"]["url"], good)
        self.assertEqual(displayed["VARLOZ"]["status"], "online")
        self.assertEqual(len(api.bot_messages), 1)
        menus = [component for container in api.bot_messages[0]["components"]
                 for row in container.get("components", [])
                 for component in row.get("components", [])
                 if component.get("type") == 3]
        options = [option for menu in menus for option in menu.get("options", [])]
        bad_option = next(o for o in options if o["label"] == "ROGZOV")
        self.assertEqual(bad_option["value"], bad)
        self.assertIn("❌", bad_option["description"])
        good_option = next(o for o in options if o["label"] == "VARLOZ")
        self.assertIn("✅", good_option["description"])

    def test_root_domains_use_exact_reported_landing_paths(self):
        good = "https://varloz.com/47b6f3a/home/varloz"
        empty = "https://rogzov.com/93zjfyqk6llf/"
        roots = [
            {"url": "https://varloz.com/", "label": "VARLOZ", "status": "online"},
            {"url": "https://rogzov.com/", "label": "ROGZOV", "status": "online"},
        ]
        groups = mirror_watch.grouped_current_sites(roots)
        self.assertEqual({g["url"] for g in groups}, {good, empty})
        target_states = {}
        s = state()
        called = []
        def check(url):
            called.append(url)
            return response(url=url)
        def inspect(url):
            if url == empty:
                return {"page_health": "empty_index"}
            return {"name": "VARLOZ", "evidence": "title", "confidence": "medium"}
        with patch("mirror_watch.check_site", side_effect=check), patch(
            "mirror_watch.probe_site_name", side_effect=inspect
        ):
            cards = mirror_watch.decorate_destinations(
                groups, roots, target_states, self.now, s
            )
        self.assertEqual(set(called), {good, empty})
        self.assertEqual({g["url"]: g["status"] for g in cards},
                         {good: "online", empty: "empty_index"})

    def test_mirror_cards_show_red_cross_only_for_directory_index(self):
        good = "https://varloz.com/47b6f3a/home/varloz"
        empty = "https://rogzov.com/93zjfyqk6llf/"
        api = FakeAPI([human_message([good, empty])])
        s = state()
        def probe(url):
            if url == empty:
                return {"page_health": "empty_index", "evidence": "html:directory_index"}
            return {"name": "VARLOZ", "confidence": "strong", "evidence": "title"}
        with patch("mirror_watch.check_site", side_effect=lambda url: response(url=url)), patch(
            "mirror_watch.probe_site_name", side_effect=probe
        ):
            mirror_watch.sync_thread(api, s, self.now)
        entries = s["tracked_threads"][mirror_watch.THREAD_ID]["url_states"]
        self.assertEqual(entries[good]["status"], "online")
        self.assertEqual(entries[empty]["status"], "empty_index")
        card = api.bot_messages[0]["content"]
        self.assertIn("❌ ✦ [rogzov.com]", card)
        self.assertNotIn("❌ ✦ [VARLOZ]", card)
        self.assertIn("Page vide : index de répertoire détecté", card)

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
        self.assertIn("### ✦ [NANDOV]", api.bot_messages[0]["content"])
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
        with patch("mirror_watch.check_site",
                   return_value=response(url="https://nandov.com/")) as http, patch(
            "mirror_watch.probe_site_name", return_value=None
        ) as metadata:
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=5))
        # Le nouveau domaine partagé par les cinq alias est contrôlé
        # une seule fois pour sa disponibilité et pour son nom.
        http.assert_called_once_with("https://nandov.com/")
        metadata.assert_called_once_with("https://nandov.com/")
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

    def test_six_old_sites_reduced_to_four_new_names_with_unique_links(self):
        """MOMDAX->NANDOV, ZAKMAV/FOPOV->DOTIVOZ, etc."""
        originals = [
            ("momdax.com", "MOMDAX", "NANDOV", "nandov.com"),
            ("zakmav.com", "ZAKMAV", "DOTIVOZ", "dotivoz.com"),
            ("fopov.com", "FOPOV", "DOTIVOZ", "dotivoz.com"),
            ("dovlip.com", "DOVLIP", "FARSOV", "farsov.com"),
            ("zinzov.com", "ZINZOV", "TIMBOV", "timbov.com"),
            ("didvip.com", "DIDVIP", "FARSOV", "farsov.com"),
        ]
        api = FakeAPI([human_message([
            "https://" + domain + "/" for domain, _, _, _ in originals
        ])])
        s = state()
        urls = []
        states = {}
        for domain, old, new, destination in originals:
            url = "https://" + domain + "/"
            urls.append(url)
            states[url] = {
                "url": url, "label": domain, "site_name": old,
                "pending_name": new, "announced_url": "https://" + destination + "/",
                "status": "online", "last_checked": self.now.isoformat(),
                "name_attempted_at": self.now.isoformat(), "failures": 0,
                "http_status": 200,
            }
        s["tracked_threads"] = {
            mirror_watch.THREAD_ID: {"url_states": states, "urls": urls}
        }
        with patch("mirror_watch.check_site",
                   side_effect=lambda u: response(url=u)) as probe:
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=1))
        target_urls = [
            "https://nandov.com/", "https://dotivoz.com/",
            "https://farsov.com/", "https://timbov.com/"
        ]
        self.assertEqual([call.args[0] for call in probe.call_args_list], target_urls)
        tracked = s["tracked_threads"][mirror_watch.THREAD_ID]
        self.assertEqual(len(tracked["url_states"]), 6)
        self.assertEqual(len(tracked["display_sites"]), 4)
        self.assertEqual(len(tracked["target_states"]), 4)
        self.assertEqual([g["url"] for g in tracked["display_sites"]], target_urls)
        names = [g["name"] for g in tracked["display_sites"]]
        self.assertEqual(names, ["NANDOV", "DOTIVOZ", "FARSOV", "TIMBOV"])
        dotivoz = next(g for g in tracked["display_sites"] if g["name"] == "DOTIVOZ")
        farsov = next(g for g in tracked["display_sites"] if g["name"] == "FARSOV")
        self.assertEqual(dotivoz["previous_names"], ["ZAKMAV", "FOPOV"])
        self.assertEqual(farsov["previous_names"], ["DOVLIP", "DIDVIP"])
        message = api.bot_messages[0]["content"]
        self.assertIn("### ✦ [DOTIVOZ](https://dotivoz.com/)\n-# ↳ Ancien : ZAKMAV, FOPOV", message)
        self.assertIn("### ✦ [FARSOV](https://farsov.com/)\n-# ↳ Ancien : DOVLIP, DIDVIP", message)
        self.assertNotIn("HTTP", message)
        self.assertNotIn("🟡", message)
        self.assertNotIn("Accessible", message)
        self.assertNotIn("<t:", message)
        for name in names:
            self.assertEqual(message.count("### ✦ [" + name + "]("), 1)
        for url in target_urls:
            self.assertEqual(message.count(url), 1)
        for domain, _, _, _ in originals:
            self.assertNotIn("https://" + domain + "/", message)
        self.assertEqual(len(api.bot_messages), 1)
        probe.reset_mock()
        with patch("mirror_watch.check_site") as second_probe:
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=2))
        second_probe.assert_not_called()
        self.assertEqual(len(api.bot_messages), 1)

    def test_found_similar_sites_join_existing_four_without_duplicates(self):
        """La découverte ajoute les domaines valides, pas les alias répétés."""
        originals = [
            ("momdax.com", "MOMDAX", "NANDOV", "nandov.com"),
            ("zakmav.com", "ZAKMAV", "DOTIVOZ", "dotivoz.com"),
            ("fopov.com", "FOPOV", "DOTIVOZ", "dotivoz.com"),
        ]
        api = FakeAPI([human_message([
            "https://" + domain + "/" for domain, _, _, _ in originals
        ])])
        s = state()
        entries = {}
        for domain, old, new, target in originals:
            url = "https://" + domain + "/"
            entries[url] = {
                "url": url, "label": domain, "site_name": old,
                "pending_name": new, "announced_url": "https://" + target + "/",
                "status": "online", "last_checked": self.now.isoformat(),
                "name_attempted_at": self.now.isoformat(), "failures": 0,
            }
        s["tracked_threads"] = {
            mirror_watch.THREAD_ID: {"url_states": entries, "urls": list(entries)}
        }
        external = [
            {"url": "https://tiprov.com/", "name": "TIPROV",
             "previous_names": ["DOMGRAV", "IKROMI"], "source_urls": [],
             "discovered": True},
            {"url": "https://dotivoz.com/", "name": "DOTIVOZ",
             "previous_names": ["GOZIRAV"], "source_urls": [],
             "discovered": True},
            {"url": "https://unreachable.com/", "name": "UNREACHABLE",
             "previous_names": [], "source_urls": [], "discovered": True},
        ]
        def probe(url):
            if url == "https://unreachable.com/":
                return response("failed", 404, url)
            return response(url=url)
        with patch("mirror_watch.candidate_groups", return_value=external), patch(
            "mirror_watch.check_site", side_effect=probe
        ):
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=2))
        groups = s["tracked_threads"][mirror_watch.THREAD_ID]["display_sites"]
        self.assertEqual(len(groups), 3)  # NANDOV, DOTIVOZ, TIPROV
        self.assertEqual({g["name"] for g in groups}, {"NANDOV", "DOTIVOZ", "TIPROV"})
        self.assertEqual(
            next(x for x in groups if x["name"] == "DOTIVOZ")["previous_names"],
            ["ZAKMAV", "FOPOV", "GOZIRAV"],
        )
        self.assertEqual(api.bot_messages[0]["content"].count("[DOTIVOZ]"), 1)
        self.assertIn("[TIPROV](https://tiprov.com/)", api.bot_messages[0]["content"])
        self.assertNotIn("UNREACHABLE", api.bot_messages[0]["content"])
        self.assertEqual(len(api.bot_messages), 1)

    def test_minimal_summary_only_new_name_and_former_names(self):
        sites = [
            {"name": "DOTIVOZ", "url": "https://dotivoz.com/",
             "previous_names": ["ZAKMAV", "FOPOV"], "status": "online",
             "http_status": 200, "pending": True,
             "last_checked": self.now.isoformat()},
            {"name": "TIMBOV", "url": "https://timbov.com/",
             "previous_names": ["ZINZOV"], "status": "offline",
             "http_status": 503, "pending": False},
        ]
        out = mirror_watch.summary_message(sites, "Streaming HD miroir")
        self.assertEqual(out, (
            "🎬 Dernière sortie cinéma en HD **sans pub**, mais petit catalogue.\n"
            "✨ **Gratuit - Illimité - Ajouts quotidiens - Zéro pub**\n\n"
            "## ✦ SITES ACTUELS\n\n"
            "### ✦ [DOTIVOZ](https://dotivoz.com/)\n"
            "-# ↳ Ancien : ZAKMAV, FOPOV\n\n"
            "### ✦ [TIMBOV](https://timbov.com/)\n"
            "-# ↳ Ancien : ZINZOV"
        ))
        self.assertNotIn("HTTP", out)
        self.assertNotIn("Nouveau nom annoncé", out)
        self.assertNotIn("Inaccessible", out)

    def test_future_rename_of_a_new_domain_is_followed_without_duplicate(self):
        """MOMDAX->NANDOV, puis NANDOV->NOUVEAU, sans perdre l'historique."""
        api = FakeAPI([human_message(["https://momdax.com/"])])
        s = state()
        source = {
            "url": "https://momdax.com/", "label": "momdax.com",
            "site_name": "MOMDAX", "pending_name": "NANDOV",
            "announced_url": "https://nandov.com/", "status": "online",
            "last_checked": self.now.isoformat(), "failures": 0,
            "name_attempted_at": self.now.isoformat(),
        }
        s["tracked_threads"] = {
            mirror_watch.THREAD_ID: {
                "url_states": {"https://momdax.com/": source},
                "urls": ["https://momdax.com/"],
            }
        }
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)), patch(
            "mirror_watch.probe_site_name", return_value={
                "name": "NOUVEAU", "previous_name": "NANDOV",
                "announced_url": "https://nouveau.com/",
                "confidence": "strong", "evidence": "title:rename"
            }
        ) as names:
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=1))
        display = s["tracked_threads"][mirror_watch.THREAD_ID]["display_sites"]
        self.assertEqual(len(display), 1)
        self.assertEqual(display[0]["url"], "https://nouveau.com/")
        self.assertEqual(display[0]["name"], "NOUVEAU")
        self.assertIn("MOMDAX", display[0]["previous_names"])
        self.assertIn("NANDOV", display[0]["previous_names"])
        self.assertEqual(names.call_count, 2)
        self.assertIn("https://nouveau.com/", api.bot_messages[0]["content"])
        self.assertNotIn("https://nandov.com/", api.bot_messages[0]["content"])

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

    def test_reuses_historical_urls_when_source_messages_have_no_links(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch("mirror_watch.check_site",
                   side_effect=lambda u: response(url=u)):
            self.assertEqual(mirror_watch.sync_thread(api, s, self.now), "updated")
        self.assertEqual(len(api.bot_messages), 1)
        api.messages = [human_message([], name="Description sans lien URL")]
        # Le fil ne contient plus d'URL humaine : conserver les sources
        # validées, et éditer le même message de suivi.
        with patch("mirror_watch.check_site") as check:
            self.assertEqual(
                mirror_watch.sync_thread(api, s, self.now + timedelta(hours=2)),
                "updated"
            )
        check.assert_not_called()
        self.assertEqual(len(api.bot_messages), 1)
        self.assertEqual(len(s["tracked_threads"][mirror_watch.THREAD_ID]["urls"]), 5)
        content = api.bot_messages[0]["content"]
        self.assertIn("Dernière sortie cinéma en HD **sans pub**", content)
        self.assertIn("✨ **Gratuit - Illimité - Ajouts quotidiens - Zéro pub**", content)
        for url in URLS:
            self.assertIn(url, content)

    def test_v2_cards_are_colored_and_keep_original_message_id(self):
        api = FakeAPI([human_message()])
        state = {"tracked_threads": {
            mirror_watch.THREAD_ID: {"message_id": "500", "url_states": {}}
        }, "records": {}, "events": [], "event_sequence": 0}
        api.bot_messages.append({
            "id": "500", "author": {"id": "333", "bot": True},
            "content": "## ✦ SITES ACTUELS", "components": [], "flags": 0
        })
        with patch("mirror_watch.check_site", side_effect=lambda u: response(url=u)):
            self.assertEqual(mirror_watch.sync_thread(api, state, self.now), "updated")
        self.assertEqual(len(api.bot_messages), 1)
        msg = api.bot_messages[0]
        self.assertEqual(msg["id"], "500")
        self.assertEqual(msg["flags"], 32768)
        self.assertEqual(len(msg["components"]), 2)
        self.assertTrue(all(comp["type"] == 17 for comp in msg["components"]))
        self.assertTrue(all(comp["components"][0]["type"] == 10
                            for comp in msg["components"]))
        self.assertEqual(msg["components"][1]["accent_color"], mirror_watch.CARD_ACCENT)
        self.assertTrue(msg["components"][1]["spoiler"])
        self.assertIn("### ✦ [", msg["components"][1]["components"][0]["content"])
        api.calls.clear()
        with patch("mirror_watch.check_site"):
            self.assertEqual(
                mirror_watch.sync_thread(api, state, self.now + timedelta(hours=2)),
                "updated",
            )
        self.assertFalse(any(method in ("POST", "PATCH") for method, _, _ in api.calls))

    def test_one_visible_primary_url_and_revealable_mirror_container(self):
        items = [
            {"name": "PRINCIPAL", "url": "https://principal.example.org/",
             "status": "online", "source_urls": ["https://old.example.org/"]},
            {"name": "MIROIR", "url": "https://miroir.example.org/",
             "status": "online", "source_urls": []},
            {"name": "VIDE", "url": "https://vide.example.org/",
             "status": "empty_index", "source_urls": []},
        ]
        components = mirror_watch.render_site_cards(items, "Streaming HD")
        self.assertEqual(len(components), 2)
        visible = mirror_watch.component_text([components[0]])
        hidden = mirror_watch.component_text([components[1]])
        self.assertFalse(components[0].get("spoiler", False))
        self.assertTrue(components[1]["spoiler"])
        self.assertIn("https://principal.example.org/", "\n".join(visible))
        self.assertNotIn("https://miroir.example.org/", "\n".join(visible))
        self.assertNotIn("https://vide.example.org/", "\n".join(visible))
        self.assertIn("https://miroir.example.org/", "\n".join(hidden))
        self.assertIn("❌", "\n".join(hidden))

    def test_native_dropdown_contains_all_mirrors_and_no_spoiler(self):
        sites = [
            {"name": "PRIMARY", "url": "https://primary.example.org/",
             "status": "online", "source_urls": ["https://primary.example.org/"]},
            {"name": "MIRROR", "url": "https://mirror.example.org/",
             "status": "online", "source_urls": []},
            {"name": "EMPTY", "url": "https://empty.example.org/",
             "status": "empty_index", "source_urls": []},
        ]
        cards = mirror_watch.render_site_cards(
            sites, "Streaming HD miroir", dropdown_enabled=True
        )
        self.assertEqual(len(cards), 1)
        self.assertFalse(cards[0].get("spoiler", False))
        header = cards[0]["components"][0]["content"]
        self.assertIn("https://primary.example.org/", header)
        self.assertNotIn("https://mirror.example.org/", header)
        menu = cards[0]["components"][1]["components"][0]
        self.assertEqual(menu["type"], 3)
        self.assertEqual(menu["custom_id"], "streaming_hd_mirrors:v1:0")
        self.assertEqual([o["value"] for o in menu["options"]],
                         ["https://mirror.example.org/",
                          "https://empty.example.org/"])
        self.assertIn("❌", menu["options"][1]["description"])
        self.assertNotIn("spoiler", cards[0])

    def test_dropdown_enabled_on_sync_reuses_one_bot_message(self):
        api = FakeAPI([human_message()])
        s = state()
        with patch.dict("os.environ", {"DISCORD_MIRROR_DROPDOWN_ENABLED": "1"}), patch(
            "mirror_watch.check_site", side_effect=lambda url: response(url=url)
        ):
            mirror_watch.sync_thread(api, s, self.now)
            first = api.bot_messages[0]["id"]
            self.assertEqual(len(api.bot_messages), 1)
            comp = api.bot_messages[0]["components"]
            self.assertEqual(comp[0]["components"][1]["type"], 1)
            api.calls.clear()
            mirror_watch.sync_thread(api, s, self.now + timedelta(hours=2))
            self.assertEqual(len(api.bot_messages), 1)
            self.assertEqual(api.bot_messages[0]["id"], first)
            self.assertFalse(any(method in ("POST", "PATCH")
                                 for method, _, _ in api.calls))

    def test_cards_support_many_sites_without_hiding_them(self):
        sites = [
            {"name": "NAME" + str(i), "url": "https://example" + str(i) + ".org/",
             "previous_names": ["OLD" + str(i)]}
            for i in range(40)
        ]
        components = mirror_watch.render_site_cards(sites, "Streaming HD miroir")
        self.assertLessEqual(len(components) * 2, 40)
        all_text = "\n".join(mirror_watch.component_text(components))
        self.assertEqual(all_text.count("### ✦ [NAME"), 40)
        self.assertEqual(all_text.count("-# ↳ Ancien : OLD"), 40)

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
        self.assertIn("## 🎬 STREAMING HD", rendered)
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
        self.assertNotIn("Dernier contrôle", api.bot_messages[0]["content"])

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
        self.assertNotIn("à confirmer", api.bot_messages[0]["content"])  # détails réservés au monitoring

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
