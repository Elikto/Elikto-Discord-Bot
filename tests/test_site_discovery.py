"""Tests de découverte sans appels réseau réels."""
import unittest
from datetime import datetime, timedelta, timezone

import site_discovery


NOW = datetime(2026, 11, 17, 22, 10, tzinfo=timezone.utc)


def page(text):
    return {"html": "<article><p>" + text + "</p></article>", "final_url": "https://source.test/"}


class SiteDiscoveryTests(unittest.TestCase):
    def test_seeded_sites_are_twelve_unique_candidates(self):
        tracked = {}
        candidates = site_discovery.update_candidates(tracked, NOW, fetcher=lambda _: None)
        self.assertEqual(len(candidates), 16)
        self.assertEqual(candidates["https://tiprov.com/"]["previous_names"],
                         ["DOMGRAV", "IKROMI", "APLOUF", "FARLIZ", "GOZMAP", "WADROX"])
        self.assertEqual(candidates["https://bintov.com/"]["name"], "BINTOV")
        self.assertIn("https://watof.com/", candidates)

    def test_natchecker_annonce_par_complete_history(self):
        sample = (
            "<article><h2>Domgrav nouveau nom</h2>"
            "<p>tiprov.com</p>"
            "<p>Annoncé par domgrav.com, ikromi.com, aplouf.com, "
            "farliz.com, gozmap.com et wadrox.com</p>"
            "<p>varloz.com</p>"
            "<p>Annoncé par palkad.com, vredap.com, soponov.com, "
            "xamoz.com et vadbak.com</p>"
            "<p>bimdav.com</p>"
            "<p>Annoncé par ilmiv.com, kobiom.com, gadrov.com, "
            "avobiv.com et imbrav.com</p></article>"
        )
        result = {
            item["url"]: item["previous_names"]
            for item in site_discovery.extract_migrations(sample)
        }
        self.assertEqual(result["https://tiprov.com/"], [
            "DOMGRAV", "IKROMI", "APLOUF", "FARLIZ", "GOZMAP", "WADROX"
        ])
        self.assertEqual(result["https://varloz.com/"], [
            "PALKAD", "VREDAP", "SOPONOV", "XAMOZ", "VADBAK"
        ])
        self.assertEqual(result["https://bimdav.com/"], [
            "ILMIV", "KOBIOM", "GADROV", "AVOBIV", "IMBRAV"
        ])

    def test_existing_candidate_accumulates_all_historical_names(self):
        tracked = {}
        old = site_discovery.update_candidates(tracked, NOW, lambda _: None)
        old["https://bimdav.com/"]["previous_names"] = ["ILMIV"]
        new = site_discovery.update_candidates(
            tracked, NOW + timedelta(days=1),
            lambda _: page(
                "bimdav.com Annoncé par ilmiv.com, kobiom.com, gadrov.com, "
                "avobiv.com et imbrav.com"
            )
        )
        self.assertEqual(new["https://bimdav.com/"]["previous_names"], [
            "ILMIV", "KOBIOM", "GADROV", "AVOBIV", "IMBRAV"
        ])

    def test_article_extracts_renames_but_not_random_links(self):
        html = (
            "<article><p>Domgrav devient TIPROV.com</p>"
            "<p>Fozlim annonce bintov.com</p>"
            "<a href='https://google.com/'>Google</a>"
            "<script>evil devient 127.0.0.1</script>"
            "</article>"
        )
        result = site_discovery.extract_migrations(html)
        observed = {x["url"]: x for x in result}
        self.assertEqual(set(observed), {
            "https://tiprov.com/", "https://bintov.com/"
        })
        self.assertEqual(observed["https://tiprov.com/"]["previous_names"], ["DOMGRAV"])

    def test_new_domain_requires_two_independent_articles(self):
        tracked = {}
        def fetch(url):
            if "natchecker" in url or "gtlf" in url:
                return page("ANCIENV devient NOUVEAU.com")
            return page("La recherche ne montre aucune migration.")
        candidates = site_discovery.update_candidates(tracked, NOW, fetch)
        self.assertIn("https://nouveau.com/", candidates)
        self.assertEqual(candidates["https://nouveau.com/"]["name"], "NOUVEAU")
        self.assertIn("ANCIENV", candidates["https://nouveau.com/"]["previous_names"])

    def test_single_source_cannot_publish_unrelated_domain(self):
        tracked = {}
        def fetch(url):
            return page("FAUXNOM devient mauvaise.com") if "gtlf" in url else None
        candidates = site_discovery.update_candidates(tracked, NOW, fetch)
        self.assertNotIn("https://mauvaise.com/", candidates)

    def test_unreachable_articles_do_not_erase_previous_discovery(self):
        tracked = {}
        first = site_discovery.update_candidates(
            tracked, NOW,
            fetcher=lambda u: page("ANCIENV devient NOUVEAU.com"),
        )
        self.assertIn("https://nouveau.com/", first)
        second = site_discovery.update_candidates(
            tracked, NOW + timedelta(days=1), fetcher=lambda u: None,
        )
        self.assertIn("https://nouveau.com/", second)
        self.assertEqual(len(site_discovery.candidate_groups(
            tracked, NOW + timedelta(days=1), fetcher=lambda u: None
        )), len(second))

    def test_no_article_recheck_on_same_day(self):
        tracked = {}
        site_discovery.update_candidates(tracked, NOW, fetcher=lambda u: None)
        def forbidden(url):
            raise AssertionError("L'article ne doit pas être relu")
        second = site_discovery.update_candidates(
            tracked, NOW + timedelta(hours=5), fetcher=forbidden
        )
        self.assertEqual(len(second), 16)

    def test_private_ips_and_invalid_addresses_rejected(self):
        html = "<article>MonSite devient localhost.com" \
               " Autre devient 127.0.0.1</article>"
        urls = [row["url"] for row in site_discovery.extract_migrations(html)]
        self.assertNotIn("http://127.0.0.1/", urls)
        self.assertFalse(any("127.0.0.1" in u for u in urls))

    def test_group_projection_is_distinct(self):
        tracked = {}
        rows = site_discovery.candidate_groups(tracked, NOW, fetcher=lambda _: None)
        self.assertEqual(len({row["url"] for row in rows}), 16)
        self.assertTrue(all(not row["source_urls"] for row in rows))
        self.assertEqual(rows[0]["name"], "BIMDAV")


if __name__ == "__main__":
    unittest.main()
