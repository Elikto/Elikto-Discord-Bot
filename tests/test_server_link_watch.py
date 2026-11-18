"""Tests du contrôle complet du serveur sans accéder à Discord ou Internet."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import server_link_watch as watch
from sync_catalogue import VIEW_CHANNEL

NOW = datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)


def human(mid, content, **extra):
    return {"id": str(mid), "content": content, "author": {"id": "77", "bot": False},
            "embeds": [], **extra}


class FakeDiscord:
    def __init__(self):
        self.calls = []
        self.channels = [
            {"id": "10", "type": 4, "name": "Public"},
            {"id": "11", "type": 0, "name": "discussion", "parent_id": "10"},
            {"id": "12", "type": 15, "name": "liens", "parent_id": "10"},
            {"id": "13", "type": 5, "name": "annonces", "parent_id": "10"},
            {"id": "14", "type": 16, "name": "medias", "parent_id": "10"},
            {"id": "15", "type": 0, "name": "interdit", "parent_id": "10",
             "permission_overwrites": [{"id": watch.GUILD_ID, "type": 0,
                                         "deny": str(VIEW_CHANNEL), "allow": "0"}]},
            {"id": "16", "type": 0, "name": "nsfw", "parent_id": "10", "nsfw": True},
        ]
        self.threads = [
            {"id": "21", "type": 11, "parent_id": "12", "name": "Site Film"},
            {"id": "22", "type": 11, "parent_id": "11", "name": "Fil de discussion"},
            {"id": "23", "type": 12, "parent_id": "12", "name": "Secret"},
            {"id": "24", "type": 11, "parent_id": "15", "name": "Thread privé parent"},
        ]
        self.archive = {
            "12": [{"id": "25", "type": 11, "parent_id": "12", "name": "Ancien site"}],
            "11": [{"id": "26", "type": 11, "parent_id": "11", "name": "Ancien lien"}],
        }
        self.messages = {
            "11": [human(101, "https://alpha.example.org https://beta.example.org")],
            "13": [human(103, "Ancienne annonce www.gamma.example.org/page")],
            "21": [human(201, "Le film : alpha.example.org puis https://delta.example.org")],
            "22": [human(202, "https://epsilon.example.org")],
            "25": [human(205, "https://zeta.example.org")],
            "26": [human(206, "https://eta.example.org")],
            "15": [human(150, "https://secret.example.org")],
            "23": [human(230, "https://private.example.org")],
            "24": [human(240, "https://hidden.example.org")],
            "16": [human(160, "https://nsfw.example.org")],
        }

    def call(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, params))
        if path == "/users/@me":
            return {"id": "999"}
        if path == f"/guilds/{watch.GUILD_ID}":
            return {"roles": [{"id": watch.GUILD_ID,
                               "permissions": str(VIEW_CHANNEL)}]}
        if path == f"/guilds/{watch.GUILD_ID}/channels":
            return self.channels
        if path == f"/guilds/{watch.GUILD_ID}/threads/active":
            return {"threads": self.threads}
        if "/messages" in path:
            ch = path.split("/")[2]
            messages = self.messages.get(ch, [])
            if params and params.get("before"):
                messages = [m for m in messages if int(m["id"]) < int(params["before"])]
            return list(sorted(messages, key=lambda m: int(m["id"]), reverse=True))[:100]
        raise AssertionError(path)

    def archived(self, forum_id):
        yield from self.archive.get(forum_id, [])


def state():
    return {"version": 1, "records": {}, "events": [], "event_sequence": 0}


def response(url, result="online"):
    return {"result": result, "http_status": 200 if result == "online" else 503,
            "final_url": url, "reason": None}


class ServerWatchTests(unittest.TestCase):
    def setUp(self):
        # Ne jamais lancer de recherche réseau pendant les tests.
        notice = patch("server_link_watch.probe_site_migration", return_value=None)
        notice.start()
        self.addCleanup(notice.stop)

    def test_scans_all_public_channels_forums_archives_and_text_threads(self):
        api = FakeDiscord()
        result, counts, errors = watch.find_server_links(api)
        urls = set(result)
        for url in ("https://alpha.example.org/", "https://beta.example.org/",
                    "https://gamma.example.org/page", "https://delta.example.org/",
                    "https://epsilon.example.org/", "https://zeta.example.org/",
                    "https://eta.example.org/"):
            self.assertIn(url, urls, url)
        self.assertEqual(len(urls), 7)
        self.assertEqual(counts["locations_discovered"], 6)
        self.assertEqual(errors, [])
        for forbidden in ("secret", "private", "hidden", "nsfw"):
            self.assertFalse(any(forbidden in x for x in urls))
        self.assertFalse(any("/channels/15/messages" in path for _, path, _ in api.calls))
        self.assertFalse(any("/channels/23/messages" in path for _, path, _ in api.calls))

    def test_extracts_embeds_components_bare_domains_and_multiple_links(self):
        msg = human(10, "plain-sample.org https://site.test.xyz/abc")
        msg["embeds"] = [
            {"description": "https://embed.example.org/", "fields": [
                {"value": "www.field-site.org/path"}]}
        ]
        msg["components"] = [{
            "type": 1, "components": [{"type": 2, "style": 5,
                                      "url": "https://button-site.org/"}]
        }]
        urls = set(watch.extract_links(msg))
        self.assertIn("https://plain-sample.org/", urls)
        self.assertIn("https://site.test.xyz/abc", urls)
        self.assertIn("https://embed.example.org/", urls)
        self.assertIn("https://field-site.org/path", urls)
        self.assertIn("https://button-site.org/", urls)

    def test_ignores_discord_links_and_media_assets(self):
        msg = human(10, "https://discord.com/channels/1/2 "
                    "https://discord.gg/invite https://example.org/logo.png "
                    "https://example.org/ https://example.org/")
        self.assertEqual(list(watch.extract_links(msg)), ["https://example.org/"])

    def test_scans_past_first_hundred_messages_and_deduplicates(self):
        api = FakeDiscord()
        api.messages["11"] = [human(1000+i, "https://alpha.example.org/")
                              for i in range(125)]
        api.messages["11"][0]["content"] += " https://buried.example.org/"
        found, counts, errors = watch.find_server_links(api)
        self.assertIn("https://buried.example.org/", found)
        self.assertEqual(found["https://alpha.example.org/"]["source_count"], 126)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(counts["messages_read"], 125)

    def test_daily_schedule_respects_paris_calendar_and_minimum_20h(self):
        checked = datetime(2026, 11, 17, 22, tzinfo=timezone.utc)
        self.assertFalse(watch.due_daily(checked.isoformat(), checked + timedelta(hours=2)))
        self.assertTrue(watch.due_daily(checked.isoformat(), checked + timedelta(hours=21)))
        self.assertFalse(watch.due_daily(checked.isoformat(), checked + timedelta(hours=10)))

    def test_all_websites_checked_daily_and_no_repeat_on_same_day(self):
        api = FakeDiscord()
        s = state()
        with patch("server_link_watch.check_site",
                   side_effect=lambda url: response(url)) as probe, patch(
            "server_link_watch.probe_site_name", return_value=None
        ):
            report, alerts = watch.watch_server_links(api, s, NOW)
            self.assertTrue(report["complete"])
            self.assertEqual(report["sites"], 7)
            self.assertEqual(probe.call_count, 7)
            self.assertEqual(alerts, [])
            probe.reset_mock()
            watch.watch_server_links(api, s, NOW + timedelta(hours=5))
            probe.assert_not_called()
            watch.watch_server_links(api, s, NOW + timedelta(days=1))
            self.assertEqual(probe.call_count, 7)

    def test_new_migration_parser_version_rescans_fstream_same_day(self):
        from server_link_watch import MIGRATION_PROBE_VERSION
        original = "https://fstream.info/"
        latest = "https://fs5.lol/"
        s = state()
        s["server_links"] = {original: {
            "site_url": original, "title": "French Stream",
            "thread_id": "100",
            "discord_url": "https://discord.com/channels/1/100/110",
            "status": "online", "active": True,
            "last_checked": NOW.isoformat(),
            "name_attempted_at": NOW.isoformat(),
            "migration_attempted_at": NOW.isoformat(),
            "migration_probe_version": MIGRATION_PROBE_VERSION - 1,
            "sources": [{
                "url": "https://discord.com/channels/1/100/110",
                "channel": "French Stream", "kind": "forum",
            }],
        }}
        found = {original: {
            "source_count": 1,
            "sources": s["server_links"][original]["sources"]
        }}
        def discover(_):
            return found, {"unique_urls": 1}, []
        with patch("server_link_watch.check_site",
                   side_effect=lambda url: response(url)), patch(
            "server_link_watch.probe_site_name",
            return_value={"name": "French Stream", "evidence": "title"}
        ), patch("server_link_watch.probe_site_migration", return_value={
            "announced_url": latest, "evidence": "html:official_address_text"
        }) as migration:
            report, _ = watch.watch_server_links(
                FakeDiscord(), s, NOW, discover=discover
            )
            migration.assert_called_once_with(original)
            self.assertEqual(report["migration_checked"], 1)
            self.assertEqual(s["server_links"][original]["announced_url"], latest)
            self.assertEqual(s["server_links"][original]["migration_probe_version"],
                             MIGRATION_PROBE_VERSION)
            watch.watch_server_links(FakeDiscord(), s, NOW, discover=discover)
            migration.assert_called_once()

    def test_fstream_new_address_announced_and_verified_for_forum_card(self):
        from forum_updates import site_groups
        url = "https://fstream.info/"
        new = "https://fs99.lol/"
        found = {url: {"source_count": 1, "sources": [{
            "url": "https://discord.com/channels/1/100/110",
            "channel": "French Stream", "kind": "forum"
        }]}}
        def discover(_):
            return found, {"unique_urls": 1}, []
        def identity(value):
            if value == url or value == new:
                return {"name": "French Stream", "confidence": "strong",
                        "evidence": "og:site_name"}
            return None
        with patch("server_link_watch.check_site",
                   side_effect=lambda value: response(value)), patch(
            "server_link_watch.probe_site_name", side_effect=identity
        ), patch(
            "server_link_watch.probe_site_migration", return_value={
                "announced_url": new,
                "previous_url": url,
                "evidence": "html:official_address_link",
            }
        ):
            s = state()
            report, _ = watch.watch_server_links(FakeDiscord(), s, NOW, discover=discover)
        rec = s["server_links"][url]
        self.assertEqual(rec["announced_url"], new)
        self.assertEqual(rec["announced_status"], "online")
        self.assertEqual(rec["announced_page_health"], "content")
        self.assertTrue(rec["announced_verified"])
        groups = site_groups([rec], "French Stream")
        self.assertEqual(groups[0]["url"], new)
        self.assertTrue(groups[0]["verified"])
        self.assertEqual(report["new_addresses_checked"], 1)

    def test_official_http_redirect_updates_address_for_any_site(self):
        original = "https://example-old.org/"
        new = "https://example-new.org/"
        found = {original: {"source_count": 1, "sources": [{
            "url": "https://discord.com/channels/1/100/110",
            "channel": "Films", "kind": "forum"
        }]}}
        def discover(_):
            return found, {"unique_urls": 1}, []
        def checking(url):
            out = response(url)
            if url == original:
                out["final_url"] = new
            return out
        with patch("server_link_watch.check_site", side_effect=checking), patch(
            "server_link_watch.probe_site_name",
            return_value={"name": "Example", "evidence": "title"}
        ):
            s = state()
            report, _ = watch.watch_server_links(
                FakeDiscord(), s, NOW, discover=discover
            )
        rec = s["server_links"][original]
        self.assertEqual(rec["redirect_url"], new)
        self.assertEqual(rec["announced_url"], new)
        self.assertTrue(rec["announced_verified"])
        self.assertEqual(report["new_addresses_checked"], 1)

    def test_scheduled_18h_forces_even_if_checked_earlier_today(self):
        url = "https://fstream.info/"
        proposed = "https://fs99.lol/"
        found = {url: {"source_count": 1, "sources": [{
            "url": "https://discord.com/channels/1/100/110",
            "channel": "French Stream", "kind": "forum"
        }]}}
        def discover(_):
            return found, {"unique_urls": 1}, []
        s = state()
        s["server_links"] = {url: {
            "site_url": url, "title": "French Stream", "thread_id": "100",
            "status": "online", "active": True, "last_checked": NOW.isoformat(),
            "name_attempted_at": NOW.isoformat(),
            "migration_attempted_at": NOW.isoformat(),
            "announced_last_checked": NOW.isoformat(),
            "announced_url": proposed,
            "sources": found[url]["sources"], "source_count": 1,
            "first_seen": NOW.isoformat(),
        }}
        with patch.dict("os.environ", {"FORCE_DAILY_INTEGRITY_SCAN": "1"}), patch(
            "server_link_watch.check_site",
            side_effect=lambda value: response(value)
        ) as http, patch(
            "server_link_watch.probe_site_name",
            return_value={"name": "French Stream", "evidence": "title"}
        ) as html_probe, patch(
            "server_link_watch.probe_site_migration",
            return_value={"announced_url": proposed}
        ) as migration:
            report, _ = watch.watch_server_links(
                FakeDiscord(), s, NOW, discover=discover
            )
        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["new_addresses_checked"], 1)
        self.assertEqual(http.call_count, 2)
        self.assertEqual(html_probe.call_count, 2)
        migration.assert_called_once_with(url)

    def test_directory_index_is_not_counted_as_a_working_streaming_site(self):
        good = "https://varloz.com/47b6f3a/home/varloz"
        empty = "https://rogzov.com/93zjfyqk6llf/"
        api = FakeDiscord()
        s = state()
        found = {
            good: {"source_count": 1, "sources": [{
                "url": "https://discord.com/channels/1/21/201", "channel": "Streaming", "kind": "fil"
            }]},
            empty: {"source_count": 1, "sources": [{
                "url": "https://discord.com/channels/1/22/202", "channel": "Streaming", "kind": "fil"
            }]},
        }
        def discover(api):
            return found, {"unique_urls": 2}, []
        def identity(url):
            if url == empty:
                return {"page_health": "empty_index", "evidence": "html:directory_index"}
            return {"name": "VARLOZ", "evidence": "title", "confidence": "medium"}
        with patch("server_link_watch.check_site", side_effect=lambda url: response(url)), patch(
            "server_link_watch.probe_site_name", side_effect=identity
        ):
            watch.watch_server_links(api, s, NOW, discover=discover)
        self.assertEqual(s["server_links"][good]["status"], "online")
        self.assertEqual(s["server_links"][empty]["status"], "empty_index")
        self.assertEqual(s["server_links"][empty]["http_status"], 200)

    def test_failure_in_one_channel_preserves_old_discoveries(self):
        api = FakeDiscord()
        s = state()
        with patch("server_link_watch.check_site", side_effect=lambda u: response(u)), patch(
            "server_link_watch.probe_site_name", return_value=None
        ):
            watch.watch_server_links(api, s, NOW)
        prior = len(s["server_links"])
        original_call = api.call
        def fail_one(method, path, **kwargs):
            if path == "/channels/11/messages":
                raise RuntimeError("Discord GET /channels/12/messages : HTTP 403")
            return original_call(method, path, **kwargs)
        api.call = fail_one
        with patch("server_link_watch.check_site", side_effect=lambda u: response(u)), patch(
            "server_link_watch.probe_site_name", return_value=None
        ):
            report, _ = watch.watch_server_links(api, s, NOW + timedelta(days=1))
        self.assertFalse(report["complete"])
        self.assertEqual(len(s["server_links"]), prior)
        self.assertTrue(any(x["reason"] == "messages_inaccessibles"
                            for x in s["server_scan"]["errors"]))

    def test_new_site_in_public_channel_notifies_once(self):
        api = FakeDiscord()
        s = state()
        with patch("server_link_watch.check_site", side_effect=lambda u: response(u)), patch(
            "server_link_watch.probe_site_name", return_value=None
        ):
            watch.watch_server_links(api, s, NOW)
            api.messages["11"].append(human(300, "https://new.example.org/"))
            report, alerts = watch.watch_server_links(
                api, s, NOW + timedelta(days=1)
            )
            self.assertEqual(report["sites"], 8)
            self.assertEqual(len([e for e in alerts
                                  if e["type"] == "server_site_added"]), 1)

    def test_webflix_art_notice_is_detected_and_target_is_checked(self):
        api = FakeDiscord()
        api.messages["11"].append(human(902, "https://webflix.art/"))
        s = state()
        observed = []
        def probe(url):
            observed.append(url)
            return response(url)
        with patch("server_link_watch.check_site", side_effect=probe), patch(
            "server_link_watch.probe_site_name", return_value={"name": "Webflix"}
        ):
            _, alerts = watch.watch_server_links(api, s, NOW)
        record = s["server_links"]["https://webflix.art/"]
        self.assertEqual(record["announced_url"], "https://webflix.surf/")
        self.assertEqual(record["migration_evidence"], "captures:2026-11-17")
        self.assertEqual(record["other_previous_urls"], ["https://webflix.bio/"])
        self.assertEqual(record["announced_status"], "online")
        self.assertEqual(record["announced_http_status"], 200)
        self.assertEqual(observed.count("https://webflix.surf/"), 1)
        self.assertFalse(any(e["alert"] for e in s["events"]
                             if e["type"] == "server_migration_announced"))

    def test_webflix_notice_changes_to_newer_announcement_without_stale_fallback(self):
        api = FakeDiscord()
        api.messages["11"].append(human(902, "https://webflix.art/"))
        s = state()
        with patch("server_link_watch.check_site", side_effect=lambda url: response(url)), patch(
            "server_link_watch.probe_site_name", return_value=None
        ):
            watch.watch_server_links(api, s, NOW)
        later = NOW + timedelta(days=1)
        with patch("server_link_watch.check_site", side_effect=lambda url: response(url)), patch(
            "server_link_watch.probe_site_migration",
            side_effect=lambda url: {
                "announced_url": "https://webflix.next/",
                "evidence": "html:migration_notice",
            } if url == "https://webflix.art/" else None
        ), patch("server_link_watch.probe_site_name", return_value=None):
            _, alerts = watch.watch_server_links(api, s, later)
        rec = s["server_links"]["https://webflix.art/"]
        self.assertEqual(rec["announced_url"], "https://webflix.next/")
        self.assertEqual(rec["announced_status"], "online")
        self.assertEqual(len([e for e in alerts
                              if e["type"] == "server_migration_announced"]), 1)

    def test_change_of_site_name_requires_two_daily_checks(self):
        api = FakeDiscord()
        s = state()
        with patch("server_link_watch.check_site", side_effect=lambda u: response(u)), patch(
            "server_link_watch.probe_site_name",
            return_value={"name": "FIRST", "evidence": "og:site_name"}
        ):
            watch.watch_server_links(api, s, NOW)
        with patch("server_link_watch.check_site", side_effect=lambda u: response(u)), patch(
            "server_link_watch.probe_site_name",
            return_value={"name": "SECOND", "evidence": "og:site_name"}
        ):
            _, alerts = watch.watch_server_links(api, s, NOW + timedelta(days=1))
            self.assertEqual(len([x for x in alerts
                                  if x["type"] == "server_name_suspected"]), 7)
            _, alerts2 = watch.watch_server_links(api, s, NOW + timedelta(days=2))
        self.assertEqual(len([x for x in alerts2
                              if x["type"] == "server_name_changed"]), 7)
        self.assertEqual(s["server_links"]["https://alpha.example.org/"]["site_name"],
                         "SECOND")


if __name__ == "__main__":
    unittest.main()
