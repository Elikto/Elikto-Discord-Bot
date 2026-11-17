"""Découverte quotidienne de domaines du même réseau, sans web-search privée.

Deux niveaux :
* une liste initiale issue de sources publiques déjà examinées ;
* des annonces "ancien devient nouveau.com" retrouvées dans les pages
  de suivi publiquement accessibles (liste blanche de sources).
Chaque nouveau domaine est ensuite vérifié par le moniteur HTTP avant
son apparition dans le message public. Les découvertes sont persistées.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
import html
import re

from site_probe import clean_url
from name_probe import fetch_html_page

# Candidats identifiés dans les relevés publics datés du 10-12 novembre 2026.
# Ces observations ne prouvent ni affiliation, ni droits de diffusion.
SEEDS = {
    "tiprov.com": ("TIPROV", ["DOMGRAV", "IKROMI", "APLOUF", "FARLIZ", "GOZMAP", "WADROX"]),
    "timbov.com": ("TIMBOV", ["MINZIV", "XATAF", "WIFRAD", "YAKLAP", "VOGFO", "YOSKOV", "ZINZOV"]),
    "trimav.com": ("TRIMAV", ["TRIFAK", "OKMAZ", "YABLOM", "ROLZIO", "VATRAB"]),
    "varloz.com": ("VARLOZ", ["PALKAD", "VREDAP", "SOPONOV", "XAMOZ", "VADBAK"]),
    "pobram.com": ("POBRAM", ["PIDOOV", "KIDRAZ", "PURWAZ", "TOKRAV", "SORLAV"]),
    "bimdav.com": ("BIMDAV", ["ILMIV", "KOBIOM", "GADROV", "AVOBIV", "IMBRAV"]),
    "nandov.com": ("NANDOV", ["IPDRO", "KOVLIV", "NOKRAF", "VOZGOV"]),
    "limdav.com": ("LIMDAV", ["EVRIB", "KORDOZ", "IRAMIV"]),
    "dotivoz.com": ("DOTIVOZ", ["ZAKMAV", "GOZIRAV", "ZOMATOP"]),
    "padmov.com": ("PADMOV", ["NOPRAK", "TARBOB", "PADRAZ"]),
    "farsov.com": ("FARSOV", ["DOVLIP"]),
    "bintov.com": ("BINTOV", ["MIVDIP"]),
    "watof.com": ("WATOF", []),
    "woovaz.com": ("WOOVAZ", []),
    "bodlav.com": ("BODLAV", []),
    "rogzov.com": ("ROGZOV", []),
}

REFERENCE_PAGES = (
    "https://natchecker.com/fr/blog/domgrav-nouveau-nom",
    "https://alexitauzin.com/top-50-meilleurs-sites-streaming-gratuits/",
    "https://gtlf.fr/meilleurs-sites-streaming-gratuit/",
)
# Une annonce de migration explicite, pas un simple lien ou résultat sponsorisé.
MIGRATION = re.compile(
    r"(?i)\b([a-z][a-z0-9-]{3,29})\s+"
    r"(?:devient|annonce(?:\s+directement)?|a\s+renomm[ée]\s+(?:son\s+site\s+)?en)"
    r"\s+(?:https?://)?([a-z][a-z0-9-]{3,29})\.com\b"
)
# Les listes « Annoncé par X.com, Y.com et Z.com » contiennent le
# véritable historique que l'utilisateur veut afficher sous le nouveau nom.
DOMAIN_TOKEN = r"[a-z][a-z0-9-]{2,29}\\.com"
ANNOUNCED_BY = re.compile(
    rf"(?i)\\b(?P<current>{DOMAIN_TOKEN})\\s+Annonc[ée]\\s+par\\s+"
    rf"(?P<former>{DOMAIN_TOKEN}(?:\\s*(?:,|et)\\s*{DOMAIN_TOKEN})*)"
)
MAX_CANDIDATES = 40


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.active = False
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer"):
            self.skipping += 1
        if tag == "article":
            self.active = True
        if tag in ("p", "li", "td", "tr", "br", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer") and self.skipping:
            self.skipping -= 1
        if tag == "article":
            self.active = False
        if tag in ("p", "li", "td", "tr", "br"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)

    def text(self):
        return html.unescape(" ".join(self.parts))


def extract_migrations(page_html: str) -> list[dict]:
    parser = ArticleText()
    parser.feed(page_html[:150000])
    text = parser.text()
    result = {}
    for match in ANNOUNCED_BY.finditer(text):
        current = match.group("current")
        url = clean_url("https://" + current.lower() + "/")
        if not url:
            continue
        old_names = [
            domain.rsplit(".", 1)[0].upper()
            for domain in re.findall(DOMAIN_TOKEN, match.group("former"), flags=re.I)
        ]
        current_name = current.rsplit(".", 1)[0].upper()
        entry = result.setdefault(url, {
            "url": url, "name": current_name, "previous_names": [],
        })
        for name in old_names:
            if name != current_name and name not in entry["previous_names"]:
                entry["previous_names"].append(name)

    for former, successor in MIGRATION.findall(text):
        former, successor = former.upper(), successor.upper()
        if former == successor:
            continue
        url = clean_url(f"https://{successor.lower()}.com/")
        if not url or successor.lower() in ("google", "example", "amazon", "discord"):
            continue
        entry = result.setdefault(url, {"url": url, "name": successor,
                                        "previous_names": []})
        if former not in entry["previous_names"] and former not in (
            "SITE", "NOM", "DOMAINE", "CELUI", "ELLE", "AUTRE"
        ):
            entry["previous_names"].append(former)
    return list(result.values())


def _day_is_current(stamp: str | None, now: datetime) -> bool:
    if not stamp:
        return False
    try:
        prev = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return now - prev < timedelta(hours=20)
    except (ValueError, TypeError):
        return False


def update_candidates(tracked: dict, now: datetime, fetcher=fetch_html_page) -> dict:
    """Inclusion progressive. Une panne d'un article ne supprime rien."""
    candidates = tracked.setdefault("discovered_candidates", {})
    for domain, (name, history) in SEEDS.items():
        url = f"https://{domain}/"
        existing = candidates.setdefault(url, {
            "name": name, "previous_names": list(history),
            "source": "relevés publics 10-12/11/2026",
            "origin": "seed",
        })
        # Conserver les noms appris lors d'autres vérifications.
        existing.setdefault("name", name)
        aliases = existing.setdefault("previous_names", [])
        for former in history:
            if former not in aliases:
                aliases.append(former)

    if _day_is_current(tracked.get("last_internet_discovery"), now):
        return candidates

    tracked["last_internet_discovery"] = now.isoformat(timespec="seconds")
    sources = tracked.setdefault("discovery_sources", {})
    observations = {}
    for source in REFERENCE_PAGES:
        try:
            page = fetcher(source)
        except (ValueError, OSError, TimeoutError):
            page = None
        if not page or not page.get("html"):
            sources[source] = "inaccessible"
            continue
        sources[source] = "lue"
        discovered = extract_migrations(page["html"])
        for item in discovered:
            found = observations.setdefault(item["url"], {
                "name": item["name"], "previous_names": set(), "sources": set(),
            })
            found["previous_names"].update(item["previous_names"])
            found["sources"].add(source)

    # Nouveaux noms non seedés : au moins deux sources indépendantes
    # ou une migration reconnue depuis un domaine déjà suivi.
    known = {urlsplit(url).hostname for url in candidates}
    for url, finding in observations.items():
        if len(candidates) >= MAX_CANDIDATES and url not in candidates:
            continue
        if (url not in candidates and len(finding["sources"]) < 2
                and not any(name.lower() + ".com" in known
                            for name in finding["previous_names"])):
            continue
        current = candidates.setdefault(url, {
            "name": finding["name"], "previous_names": [],
            "origin": "web", "source": " ; ".join(sorted(finding["sources"])),
        })
        for old in sorted(finding["previous_names"]):
            if old not in current["previous_names"] and len(current["previous_names"]) < 30:
                current["previous_names"].append(old)
        current["last_observed"] = now.isoformat(timespec="seconds")
    print("DÉCOUVERTE WEB : ", len(candidates), "candidats distincts ;",
          sum(x == "lue" for x in sources.values()), "sources consultées.")
    return candidates


def candidate_groups(tracked: dict, now: datetime, fetcher=fetch_html_page) -> list[dict]:
    """Projections de nouveaux sites ; vérification HTTP faite par mirror_watch."""
    candidates = update_candidates(tracked, now, fetcher)
    groups = []
    for url, item in sorted(candidates.items(), key=lambda kv: kv[1]["name"]):
        canonical = clean_url(url)
        if not canonical:
            continue
        groups.append({
            "url": canonical, "name": item["name"],
            "previous_names": list(item.get("previous_names", [])),
            "source_urls": [], "announced": False, "pending": False,
            "discovered": True,
            "status": "pending", "last_checked": None,
        })
    return groups
