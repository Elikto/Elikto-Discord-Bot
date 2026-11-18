import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from site_probe import clean_url, extract_candidate_urls, check_site, other_domain
from site_monitor import watch_catalogue, record_check, load_state, due


def catalogue():
    return {"categories": [{
        "id": "1", "name": "Streaming", "channels": [{
            "name": "films", "kind": "forum",
            "items": [
                {"title": "Movix", "url": "https://discord.com/channels/1/100"},
                {"title": "Site sans lien", "url": "https://discord.com/channels/1/101"},
            ],
        }],
    }]}


def state():
    return {"version": 1, "records": {}, "events": [], "event_sequence": 0}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 13, tzinfo=timezone.utc)

    def test_extract_urls_from_starter_excludes_discord(self):
        message = {"content": (
            "**Lien** [site](https://movix.example.org/) "
            "https://discord.com/channels/1/100 "
            "https://movix.example.org/ "
            "http://127.0.0.1/private"
        )}
        self.assertEqual(extract_candidate_urls(message), ["https://movix.example.org/"])

    def test_private_or_sensitive_targets_never_accepted(self):
        for url in ("http://127.0.0.1", "http://192.168.1.1",
                    "http://169.254.169.254/latest", "http://localhost",
                    "http://intranet.local", "file:///etc/passwd",
                    "https://user:pass@example.org/",
                    "https://example.org:8080/"):
            self.assertIsNone(clean_url(url), url)

    def test_query_strings_not_in_public_output(self):
        self.assertEqual(clean_url("https://www.example.org/path?token=secret#note"),
                         "https://www.example.org/path")

    def test_redirect_cross_domain_only(self):
        self.assertFalse(other_domain("https://www.movix.fr", "https://movix.fr"))
        self.assertTrue(other_domain("https://movix.fr", "https://nandov.fr"))

    def test_http_head_fallback_get(self):
        with patch("site_probe.single_request", side_effect=[
            (405, None), (200, None)
        ]) as mocked:
            outcome = check_site("https://example.org/")
        self.assertEqual(outcome["result"], "online")
        self.assertEqual([c.args[1] for c in mocked.call_args_list], ["HEAD", "GET"])

    def test_head_503_browser_get_200_is_online(self):
        with patch("site_probe.single_request", side_effect=[
            (503, None), (200, None),
        ]) as request:
            result = check_site("https://example.org/")
        self.assertEqual(result["result"], "online")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual([c.args[1] for c in request.call_args_list],
                         ["HEAD", "GET"])

    def test_head_and_get_503_are_inconclusive_not_offline(self):
        with patch("site_probe.single_request", side_effect=[
            (503, None), (503, None),
        ]):
            result = check_site("https://example.org/")
        self.assertEqual(result["result"], "unknown")
        self.assertEqual(result["http_status"], 503)
        self.assertIn("temporaire", result["reason"])

    def test_head_404_get_200_does_not_mark_a_working_site_down(self):
        with patch("site_probe.single_request", side_effect=[
            (404, None), (200, None),
        ]):
            result = check_site("https://example.org/")
        self.assertEqual(result["result"], "online")

    def test_old_offline_zlibrary_503_is_corrected_not_alerted_as_down(self):
        url = "https://fr.z-library.ec/"
        record = {
            "thread_id": "100", "title": "Z-library",
            "discord_url": "https://discord.com/channels/1/100",
            "site_url": url, "status": "offline",
            "consecutive_failures": 2, "http_status": 503,
        }
        s = state()
        alerts = record_check(
            s, record,
            {"result": "failed", "http_status": 503,
             "final_url": url, "reason": "HTTP 503"},
            self.now,
        )
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(record["consecutive_failures"], 0)
        self.assertEqual([x["type"] for x in alerts], ["site_check_inconclusive"])
        self.assertNotIn("site_unreachable", [x["type"] for x in alerts])
        record_check(s, record, {
            "result": "unknown", "http_status": 503,
            "final_url": url,
        }, self.now + timedelta(days=1))
        self.assertEqual(len(s["events"]), 1)

    def test_stale_503_is_rechecked_same_day_after_probe_upgrade(self):
        url = "https://fr.z-library.ec/"
        current_state = state()
        current_state["records"] = {"100": {
            "thread_id": "100", "title": "Movix",
            "discord_url": "https://discord.com/channels/1/100",
            "site_url": url, "status": "offline",
            "http_status": 503, "consecutive_failures": 2,
            "last_checked": self.now.isoformat(),
            "last_discovery": self.now.isoformat(),
        }}
        with patch("site_monitor.check_site", return_value={
            "result": "unknown", "http_status": 503,
            "final_url": url, "reason": "Temporarily protected",
        }) as probe, patch("site_monitor.pick_site_url", return_value=None):
            _, alerts = watch_catalogue(
                object(), catalogue(), current_state,
                self.now + timedelta(minutes=4),
            )
        probe.assert_called_once_with(url)
        self.assertEqual(current_state["records"]["100"]["status"], "unknown")
        self.assertEqual(
            [ev["type"] for ev in alerts if ev["thread_id"] == "100"],
            ["site_check_inconclusive"],
        )

    def test_site_offline_only_after_two_distinct_checks(self):
        rec = {
            "thread_id": "100", "title": "Movix",
            "discord_url": "https://discord.com/channels/1/100",
            "site_url": "https://movix.example.org/",
            "status": "pending", "consecutive_failures": 0,
        }
        observations = {"result": "failed", "http_status": 404,
                        "final_url": "https://movix.example.org/", "reason": None}
        current_state = state()
        alert1 = record_check(current_state, rec, observations, self.now)
        self.assertEqual(rec["status"], "suspect")
        self.assertEqual(alert1, [])
        alert2 = record_check(current_state, rec, observations,
                              self.now + timedelta(hours=48))
        self.assertEqual(rec["status"], "offline")
        self.assertEqual(len(alert2), 1)
        self.assertEqual(alert2[0]["type"], "site_unreachable")

    def test_403_is_restricted_not_down(self):
        rec = {"thread_id": "100", "title": "X",
               "discord_url": "https://discord.com/channels/1/100",
               "status": "suspect", "consecutive_failures": 1}
        record_check(state(), rec, {"result": "restricted", "http_status": 403,
                                    "final_url": None}, self.now)
        self.assertEqual(rec["status"], "restricted")
        self.assertEqual(rec["consecutive_failures"], 0)

    def test_first_run_avoids_bulk_alert_spam_and_marks_missing(self):
        class FakeAPI: pass
        current_state = state()
        with patch("site_monitor.pick_site_url", side_effect=[
            "https://movix.example.org/", None
        ]), patch("site_monitor.check_site", return_value={
            "result": "online", "http_status": 200,
            "final_url": "https://movix.example.org/", "reason": None
        }) as probe:
            result, alerts = watch_catalogue(FakeAPI(), catalogue(), current_state, self.now)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["monitored"], 1)
        self.assertEqual(result["status_counts"], {"online": 1, "missing_url": 1})
        self.assertEqual(alerts, [])
        probe.assert_called_once()
        self.assertEqual(len(current_state["records"]), 2)

    def test_recheck_only_after_48_hours(self):
        class FakeAPI: pass
        current_state = state()
        with patch("site_monitor.pick_site_url", return_value="https://movix.example.org/"), patch(
            "site_monitor.check_site", return_value={
                "result": "online", "http_status": 200,
                "final_url": "https://movix.example.org/", "reason": None
            }
        ) as p:
            watch_catalogue(FakeAPI(), catalogue(), current_state, self.now)
            p.reset_mock()
            watch_catalogue(FakeAPI(), catalogue(), current_state,
                            self.now + timedelta(hours=6))
            p.assert_not_called()
            watch_catalogue(FakeAPI(), catalogue(), current_state,
                            self.now + timedelta(hours=48))
            self.assertEqual(p.call_count, 2)  # Deux fiches, un contrôle chacune à 48 h

    def test_tracked_thread_address_overrides_stale_starter_url(self):
        """L'adresse annoncée dans le fil ciblé pilote aussi la supervision HTTP."""
        class FakeAPI: pass
        track_id = "1430988094374547559"
        catalog = {"categories": [{"id": "1", "name": "Streaming",
            "channels": [{"name": "films", "kind": "forum",
                "items": [{"title": "Streaming HD miroir",
                    "url": "https://discord.com/channels/1/" + track_id}]}]}]}
        current_state = state()
        current_state["records"][track_id] = {
            "thread_id": track_id, "title": "Streaming HD miroir",
            "discord_url": "https://discord.com/channels/1/" + track_id,
            "site_url": "https://old.example.org/",
            "status": "online", "last_checked": self.now.isoformat(),
            "last_discovery": self.now.isoformat(),
            "consecutive_failures": 0,
        }
        current_state["tracked_threads"] = {
            track_id: {"address": "https://new.example.org/"}
        }
        with patch("site_monitor.pick_site_url") as discover, patch(
            "site_monitor.check_site", return_value={
                "result": "online", "http_status": 200,
                "final_url": "https://new.example.org/", "reason": None
            }
        ) as check:
            report, alerts = watch_catalogue(FakeAPI(), catalog, current_state, self.now)
        discover.assert_not_called()
        check.assert_called_once_with("https://new.example.org/")
        self.assertEqual(current_state["records"][track_id]["site_url"],
                         "https://new.example.org/")
        self.assertEqual(report["monitored"], 1)
        self.assertEqual(alerts[0]["type"], "address_changed")

    def test_webflix_migration_appears_in_forum_and_server_report(self):
        class FakeAPI:
            pass
        thread_id = "1562067560856289411"
        current = state()
        current["records"][thread_id] = {
            "thread_id": thread_id, "title": "WebFlix",
            "discord_url": f"https://discord.com/channels/1/{thread_id}",
            "site_url": "https://webflix.art/", "status": "unknown",
            "last_checked": self.now.isoformat(),
            "last_discovery": self.now.isoformat(),
        }
        current["server_links"] = {
            "https://webflix.art/": {
                "thread_id": thread_id, "title": "webflix.art",
                "discord_url": f"https://discord.com/channels/1/{thread_id}/{thread_id}",
                "site_url": "https://webflix.art/", "active": True,
                "status": "unknown", "last_checked": self.now.isoformat(),
                "announced_url": "https://webflix.surf/",
                "announced_status": "online", "announced_http_status": 200,
                "migration_evidence": "captures:2026-11-17",
            }
        }
        cat = {"categories": [{"name": "Streaming", "channels": [{
            "name": "films-et-series", "kind": "forum",
            "items": [{"title": "WebFlix",
                       "url": f"https://discord.com/channels/1/{thread_id}"}],
        }]}]}
        with patch("site_monitor.check_site") as check:
            report, _ = watch_catalogue(FakeAPI(), cat, current, self.now)
        check.assert_not_called()
        self.assertEqual(
            cat["categories"][0]["channels"][0]["items"][0]["monitor"]["announced_url"],
            "https://webflix.surf/"
        )
        self.assertEqual(report["server_sites"][0]["announced_url"],
                         "https://webflix.surf/")
        self.assertEqual(report["server_sites"][0]["announced_status"], "online")

    def test_directory_index_in_global_scan_overrides_http_ok_in_resource_card(self):
        class FakeAPI: pass
        current = state()
        url = "https://rogzov.com/93zjfyqk6llf/"
        current["records"]["100"] = {
            "thread_id": "100", "title": "Rogzov",
            "discord_url": "https://discord.com/channels/1/100",
            "site_url": url, "status": "online",
            "last_checked": self.now.isoformat(),
            "last_discovery": self.now.isoformat(),
        }
        current["server_links"] = {url: {
            "thread_id": "100", "site_url": url, "active": True,
            "status": "empty_index", "http_status": 200,
        }}
        cat = {"categories": [{"name": "Streaming", "channels": [{
            "name": "films-et-series", "kind": "forum",
            "items": [{"title": "Rogzov",
                       "url": "https://discord.com/channels/1/100"}],
        }]}]}
        with patch("site_monitor.check_site") as check:
            report, _ = watch_catalogue(FakeAPI(), cat, current, self.now)
        check.assert_not_called()
        self.assertEqual(current["records"]["100"]["status"], "empty_index")
        self.assertEqual(cat["categories"][0]["channels"][0]["items"][0]
                         ["monitor"]["status"], "empty_index")
        self.assertEqual(report["status_counts"]["empty_index"], 1)

    def test_mirror_urls_are_exported_and_counted_individually(self):
        class FakeAPI: pass
        thread_id = "1430988094374547559"
        catalog = {"categories": [{"id": "1", "name": "Streaming",
            "channels": [{"name": "films", "kind": "forum",
                "items": [{"title": "Streaming HD miroir",
                    "url": "https://discord.com/channels/1/" + thread_id}]}]}]}
        current_state = state()
        urls = ["https://one.example.org/", "https://two.example.org/"]
        current_state["tracked_threads"] = {
            thread_id: {
                "urls": urls,
                "url_states": {
                    urls[0]: {"url": urls[0], "label": "Un", "status": "online",
                              "site_name": "MOMDAX", "pending_name": "NANDOV",
                              "name_evidence": "og:site_name",
                              "name_last_checked": self.now.isoformat(),
                              "last_checked": self.now.isoformat(), "http_status": 200},
                    urls[1]: {"url": urls[1], "label": "Deux", "status": "offline",
                              "last_checked": self.now.isoformat(), "http_status": 503}
                }
            }
        }
        current_state["records"][thread_id] = {
            "thread_id": thread_id, "title": "Streaming HD miroir",
            "discord_url": "https://discord.com/channels/1/" + thread_id,
            "site_url": urls[0], "status": "online",
            "last_checked": self.now.isoformat(),
            "last_discovery": self.now.isoformat()
        }
        with patch("site_monitor.check_site") as mock_check:
            report, alerts = watch_catalogue(FakeAPI(), catalog, current_state, self.now)
        mock_check.assert_not_called()
        self.assertEqual(report["fiches"], 1)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["monitored"], 2)
        self.assertEqual(report["status_counts"], {"online": 1, "offline": 1})
        self.assertEqual(len(catalog["categories"][0]["channels"][0]["items"][0]["monitor"]["mirrors"]), 2)
        self.assertEqual(len(report["sites"][0]["mirrors"]), 2)
        mirror = report["sites"][0]["mirrors"][0]
        self.assertEqual(mirror["site_name"], "MOMDAX")
        self.assertEqual(mirror["pending_name"], "NANDOV")
        self.assertEqual(mirror["name_evidence"], "og:site_name")
        self.assertEqual(alerts, [])

    def test_title_change_creates_history_event(self):
        class FakeAPI: pass
        current_state = state()
        current_state["records"] = {
            "100": {
                "thread_id": "100", "title": "Ancien nom",
                "discord_url": "https://discord.com/channels/1/100",
                "site_url": None, "status": "missing_url",
                "last_discovery": self.now.isoformat(),
            }
        }
        with patch("site_monitor.pick_site_url", return_value=None):
            _, alerts = watch_catalogue(FakeAPI(), catalogue(), current_state,
                                       self.now + timedelta(hours=3))
        self.assertEqual(alerts[0]["type"], "title_changed")
        self.assertEqual(alerts[0]["previous"], "Ancien nom")

    def test_website_redirect_is_not_automatically_accepted_as_rename(self):
        rec = {"thread_id": "100", "title": "Movix",
               "discord_url": "https://discord.com/channels/1/100",
               "site_url": "https://movix.fr/",
               "status": "online", "redirect_url": None}
        alerts = record_check(state(), rec, {
            "result": "online", "http_status": 200, "final_url": "https://nandov.fr/"
        }, self.now)
        self.assertEqual(rec["site_url"], "https://movix.fr/")
        self.assertEqual(rec["redirect_url"], "https://nandov.fr/")
        self.assertEqual(alerts[0]["type"], "redirect_detected")
        self.assertIn("vérifier", alerts[0]["detail"])

    def test_due_malformed_date(self):
        self.assertTrue(due("invalid", self.now))
        self.assertFalse(due(self.now.isoformat(), self.now + timedelta(hours=12)))


if __name__ == "__main__":
    unittest.main()
