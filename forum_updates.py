"""Republier les adresses actualisees dans les fils de forum Discord publics.

Le bot ne modifie jamais les messages de membres. Il gere une reponse
par fil et reutilise son identifiant, meme si une synchronisation echoue.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from site_probe import (clean_url, check_site, resolve_public_ip,
                        PublicHTTPSConnection, PublicHTTPConnection, USER_AGENT)
from name_probe import fetch_html_page, probe_site_name
from forum_cards import (source_description, credentials_from_author_posts,
                         build_card, all_text, normalize, is_own_card,
                         card_digest, V2_FLAG)

MARKER = "[elikto-forum-suivi:v1]"
MIRROR_THREAD = "1430988094374547559"
TEAMFLIX_THREAD = "1565898044518568187"
CHANGE_EVENTS = {
    "server_site_added", "server_site_removed", "server_name_changed",
    "server_name_suspected", "server_migration_announced",
    "address_changed", "title_changed", "redirect_detected",
}


def safe_label(value, limit=70):
    return re.sub(r"[\r\n*[\]<>\x60@]", " ", str(value or ""))[:limit].strip()


def domain(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def forum_sources(record, forum_ids):
    ids = set()
    for source in record.get("sources") or []:
        parts = (source.get("url") or "").split("/")
        if len(parts) >= 2 and parts[-2] in forum_ids:
            ids.add(parts[-2])
    return ids - {MIRROR_THREAD}


def proven_html_content(record, now=None):
    """Proof of a site page, not just HTTP 200.

    Historical scans stored the HTML <title>/OG identity but no page_health.
    Upgrade that existing observation when it is recent; never promote
    HTTP-only, stale, blocked or directory-index results to green.
    """
    if record.get("status") not in ("online", "restricted"):
        return False
    # Pour un HTTP 403, seule une page réellement lue (pas un ancien
    # titre mis en cache) permet de confirmer l'accessibilité.
    if record.get("status") == "restricted" and record.get("page_health") != "content":
        return False
    if record.get("page_health") in (
        "unknown", "empty_index", "parked", "blocked", "interstitial"
    ):
        return False
    evidence = record.get("name_evidence")
    last = record.get("name_checked_at")
    if evidence not in ("og:site_name", "application-name", "title",
                        "og:title", "twitter:title", "html:title_brand") or not last:
        return False
    try:
        checked = datetime.fromisoformat(last.replace("Z", "+00:00"))
        now = now or datetime.now(timezone.utc)
        if checked.tzinfo is None:
            return False
        age = now - checked
        return timedelta(0) <= age <= timedelta(hours=48)
    except (ValueError, TypeError, OverflowError):
        return False


def site_groups(records, forum_title):
    """Conserver la source d'une annonce ET sa destination actuelle.

    Les fiches distinguent le portail de référence (s'il existe) du domaine
    actif ; une ancienne URL en échec n'est pas arbitrairement qualifiée de
    « référence ». Lorsque le portail fonctionne encore et annonce une
    migration, il sert de référence.
    """
    groups = {}
    for rec in records:
        if not rec.get("active"):
            continue
        original = clean_url(rec.get("site_url"))
        if not original:
            continue
        successor = clean_url(rec.get("announced_url")) if rec.get("announced_url") else None
        url = successor or original
        key = domain(url)
        if not key:
            continue
        reference = clean_url(rec.get("reference_url"))
        if not reference and successor and rec.get("status") == "online":
            reference = original
        if reference == url:
            reference = None
        verified = (bool(rec.get("announced_verified")) if successor else
                    proven_html_content(rec))
        if key not in groups:
            groups[key] = {
                "url": url, "name": domain(url), "reference_url": reference,
                "older": [], "announced": bool(successor), "verified": verified,
                "status": rec.get("announced_status") if successor else rec.get("status"),
            }
        entry = groups[key]
        if successor:
            entry["url"] = successor
            entry["announced"] = True
            entry["status"] = rec.get("announced_status")
            entry["verified"] = verified
            entry["reference_url"] = reference or entry.get("reference_url")
            entry["name"] = domain(successor)
            for old in [original, *(rec.get("other_previous_urls") or [])]:
                older = domain(old)
                if older and older != key and older not in entry["older"]:
                    entry["older"].append(older)
    return sorted(groups.values(), key=lambda item: (not item["announced"], item["url"]))


MOVIX_HUB = "https://movix.online/"


class MovixDirectoryParser(HTMLParser):
    """Lit uniquement le premier lien sous « Adresse officielle active »."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_heading = False
        self.heading = []
        self.active_section = False
        self.candidates = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.in_heading = True
            self.heading = []
            return
        if tag == "a" and self.active_section:
            href = dict(attrs).get("href")
            if href:
                self.candidates.append(href)

    def handle_data(self, data):
        if self.in_heading:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self.in_heading:
            self.in_heading = False
            self.active_section = "adresse officielle active" in (
                " ".join(self.heading).casefold()
            )


def is_movix_forum(title):
    return bool(re.search(r"(?i)(?<![a-z])movix(?![a-z])", str(title or "")))


def validated_movix_groups(linked):
    """Ne plus republier movix.club/Site (404) comme une adresse valide.

    movix.online est l'annuaire indiqué par le propriétaire du forum :
    une destination découverte dans sa rubrique dédiée n'est retenue
    que si le serveur cible répond effectivement en HTTP.
    """
    previous = []
    for item in linked:
        for url in (item.get("site_url"), item.get("announced_url")):
            old = clean_url(url)
            if old and domain(old) != domain(MOVIX_HUB):
                host = domain(old)
                if host not in previous:
                    previous.append(host)
    hub_check = check_site(MOVIX_HUB)
    hub_status = hub_check.get("result", "unknown")
    target = None
    target_status = None
    target_verified = False
    if hub_status in ("online", "restricted"):
        try:
            page = fetch_html_page(MOVIX_HUB)
        except (OSError, TimeoutError, ValueError, UnicodeError):
            page = None
        if page:
            parser = MovixDirectoryParser()
            try:
                parser.feed(page["html"])
            except (ValueError, AssertionError):
                pass
            for href in parser.candidates[:10]:
                proposed = clean_url(urljoin(MOVIX_HUB, href))
                if not proposed or domain(proposed) == domain(MOVIX_HUB):
                    continue
                if not domain(proposed).startswith("movix."):
                    continue
                result = check_site(proposed)
                if result.get("result") == "online":
                    final = clean_url(result.get("final_url") or proposed)
                    # Une redirection publicitaire ne prouve pas un site actif.
                    if not final or not domain(final).startswith("movix."):
                        continue
                    identity = probe_site_name(final)
                    if identity and identity.get("page_health") == "empty_index":
                        continue
                    target = final
                    target_status = "online"
                    target_verified = bool(identity and
                                           "movix" in str(identity.get("name") or "").casefold())
                    if target_verified:
                        break
    # Si la destination est non vérifiée, montrer la page de référence,
    # jamais l'ancienne URL 404 comme si elle était encore valide.
    current = target or MOVIX_HUB
    return [{
        "url": current,
        "name": domain(current),
        "reference_url": MOVIX_HUB if target else None,
        "older": previous,
        "announced": bool(target),
        "verified": target_verified,
        "status": target_status if target else hub_status,
        "directory_url": MOVIX_HUB if target else None,
    }]



COFLIX_HUB = "https://coflix.domains/"
FLEMMIX_HUB = "https://ww1.wiflix-adresses.fun/"

DIRECTORIES = {
    "coflix": (COFLIX_HUB, "coflix."),
    "flemmix": (FLEMMIX_HUB, "flemmix."),
}


def directory_kind(title):
    label = str(title or "").casefold()
    for kind in DIRECTORIES:
        if re.search(r"(?<![a-z])" + kind + r"(?![a-z])", label):
            return kind
    return None


def folded_directory_text(text):
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(c for c in value if not unicodedata.combining(c))


def is_blocked_directory_entry(text):
    """Blocage FAI, ancien domaine, clone ou adresse expirée."""
    value = folded_directory_text(text)
    return bool(re.search(
        r"bloqu|blocked|ancien domaine|old domain|inactif|expir|"
        r"faux (?:site|domaine)|fake|frauduleux|clone|parking|"
        r"domain(?:e)? (?:suspendu|parque)|non disponible",
        value,
    ))


class StreamingDirectoryParser(HTMLParser):
    """Conserve les catégories de l'annuaire et exclut les domaines FAI bloqués.

    Les libellés de cartes sont parfois des DIV plutôt que des H2 et le badge
    « ACTIF » se trouve après le lien, voire hors de l'ancre.
    """
    DOMAIN_TOKEN = re.compile(r"(?<![\w-])(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,24}\b", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self.current = None
        self.heading_tag = None
        self.heading_text = []
        self.blocked_category = False
        self.active_category = False
        self.blocked_hosts = set()
        self.recent_text = ""

    def _section(self, value):
        folded = folded_directory_text(value)
        if re.search(
            r"(?:anciens? domaines?|old domains?).{0,55}(?:bloqu|blocked|fai)"
            r"|(?:bloqu|blocked).{0,45}(?:fai|domaines?)"
            r"|(?:fai|domaines?).{0,45}(?:bloqu|blocked)",
            folded,
        ):
            self.blocked_category = True
            self.active_category = False
        elif re.search(r"faux domaines?|sites? frauduleux|fake domains?", folded):
            self.blocked_category = True
            self.active_category = False
        elif re.search(
            r"^(?:domaines actifs?|liens? (?:principal|principaux)|"
            r"adresses disponibles?|adresses officielles?|"
            r"nouveaux? domaines?|nos sites)\b",
            folded,
        ):
            self.blocked_category = False
            self.active_category = True

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_tag = tag
            self.heading_text = []
        if tag == "a":
            self.current = {
                "href": dict(attrs).get("href"), "text": "",
                "after": "", "before": self.recent_text[-140:],
                "blocked_category": self.blocked_category,
                "active_category": self.active_category,
            }

    def handle_data(self, value):
        clean = re.sub(r"\s+", " ", value)
        if not clean.strip():
            return
        was_blocked = self.blocked_category
        was_active = self.active_category
        # Un intitulé à l'intérieur d'une ancre (ex. « Adresse disponible »)
        # ne peut JAMAIS annuler la catégorie « Bloqué par les FAI ».
        if self.heading_tag is None and self.current is None:
            self._section(clean)
        category_changed = (
            was_blocked != self.blocked_category
            or was_active != self.active_category
        )
        if self.heading_tag is not None:
            self.heading_text.append(clean)
        if self.current is not None:
            self.current["text"] += " " + clean
        elif self.entries and self.heading_tag is None and not category_changed:
            # Ne jamais inclure l'en-tête « Bloqués par les FAI » dans le
            # texte du site actif précédent : cela donnait un faux rejet.
            self.entries[-1]["after"] += " " + clean
        self.recent_text = (self.recent_text + " " + clean)[-500:]
        if self.blocked_category:
            self.blocked_hosts.update(
                match.group().lower() for match in self.DOMAIN_TOKEN.finditer(clean)
            )

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.entries.append(self.current)
            self.current = None
        if tag == self.heading_tag:
            heading = " ".join(self.heading_text)
            self._section(heading)
            self.heading_tag = None
            self.heading_text = []


def directory_candidates(html_text, hub, kind):
    """Proposer uniquement les sites annoncés actifs, jamais les domaines FAI.

    Un domaine dans la liste « bloqué » est exclu même s'il apparaît plus
    haut parmi les liens principaux : le statut bloqué a priorité.
    """
    parsed = StreamingDirectoryParser()
    try:
        parsed.feed(str(html_text or "")[:96000])
    except (ValueError, AssertionError):
        return []
    expected = DIRECTORIES[kind][1]
    candidates = []
    for entry in parsed.entries:
        proposed = clean_url(urljoin(hub, entry["href"] or "")) if entry["href"] else None
        if not proposed:
            continue
        host = domain(proposed)
        if not re.fullmatch(r"(?i)" + re.escape(expected) + r"[a-z0-9-]{2,24}", host):
            continue
        if host in parsed.blocked_hosts or entry.get("blocked_category"):
            continue
        local = folded_directory_text(
            entry["text"] + " " + entry["after"][:110]
        )
        # Les libellés d'avant le lien servent à reconnaître « Lien principal ».
        before = folded_directory_text(entry.get("before", ""))[-95:]
        if is_blocked_directory_entry(local):
            continue
        if kind == "coflix":
            suitable = bool(re.search(
                r"adresse disponible|domaine actif|site actif|(?<!in)actif\b",
                local,
            )) or (entry.get("active_category") and bool(entry["text"].strip()))
        else:
            suitable = bool(re.search(
                r"nom de domaine principal|domaine actif|nouveau site|"
                r"\blien principal\b|\bactif\b",
                local,
            )) or (
                bool(re.search(r"\blien principal\b", before))
            ) or bool(entry.get("active_category"))
        if suitable and proposed not in candidates:
            candidates.append(proposed)
    return candidates[:8]

def validated_directory_groups(linked, kind):
    """Référence connue et adresse courante du répertoire (vérifiée séparément).

    Aucun ancien domaine en panne DNS ne devient « actif » par défaut.
    Lorsque l'annuaire ou la destination est inaccessible, afficher
    l'incertitude dans la fiche, sans inventer une nouvelle adresse.
    """
    hub = DIRECTORIES[kind][0]
    previous = []
    for item in linked:
        original = clean_url(item.get("site_url"))
        if original and original != hub:
            older = domain(original)
            if older and older not in previous:
                previous.append(older)

    proposals = []
    if check_site(hub).get("result") in ("online", "restricted"):
        try:
            page = fetch_html_page(hub, timeout=6)
        except (OSError, TimeoutError, ValueError, UnicodeError):
            page = None
        if page:
            proposals = directory_candidates(page["html"], hub, kind)

    # Source de secours fournie par l'utilisateur et datée dans le workflow.
    # Un domaine observé n'est JAMAIS présumé actif : il est vérifié exactement
    # comme les adresses extraites de l'annuaire et ne reçoit pas de coche verte
    # si la sonde ne reconnaît pas le site final.
    if kind == "flemmix":
        observed_blocked = {
            value.strip().lower()
            for value in os.getenv("FLEMMIX_FAI_BLOCKED_HOSTS", "").split(",")
            if value.strip()
        }
        proposals = [
            url for url in proposals if domain(url) not in observed_blocked
        ]
        extra = clean_url(os.getenv("FLEMMIX_OBSERVED_MAIN", ""))
        if (extra and re.fullmatch(r"flemmix\.[a-z0-9-]{2,24}", domain(extra))
                and domain(extra) not in observed_blocked
                and extra not in proposals):
            proposals.append(extra)

    found = None
    verified = False
    expected_prefix = DIRECTORIES[kind][1]
    for proposed in proposals:
        result = check_site(proposed)
        if result.get("result") != "online":
            continue
        destination = clean_url(result.get("final_url") or proposed)
        # Lien publicitaire/intermédiaire ou raccourcisseur : aucune coche verte.
        if not destination or not domain(destination).startswith(expected_prefix):
            continue
        identity = probe_site_name(destination)
        if not identity:
            continue
        if identity.get("page_health") in (
            "empty_index", "parked", "interstitial", "blocked",
        ):
            continue
        # Un simple titre arbitraire ou HTTP 200 n'est pas une preuve.
        label = folded_directory_text(identity.get("name"))
        brand_matches = ("coflix" in label if kind == "coflix"
                         else "flemmix" in label or "wiflix" in label)
        if not brand_matches:
            continue
        if identity.get("page_health") not in (None, "content"):
            continue
        final_html_url = clean_url(identity.get("final_url") or destination)
        if not final_html_url or not domain(final_html_url).startswith(expected_prefix):
            continue
        found = final_html_url
        verified = True
        break

    # Ne pas présenter un ancien domaine expiré, bloqué FAI ou un lien
    # raccourci comme « actif ». Le portail est la seule référence affichée.
    return [{
        "url": found or hub,
        "name": domain(found or hub),
        "reference_url": hub,
        "no_active": not bool(found),
        "suggested_url": (proposals[0] if not found and proposals else None),
        # Historique conservé dans le suivi, pas dans la carte : ne pas
        # republier d'anciens domaines marqués Bloqué par les FAI.
        "older": [],
        "announced": bool(proposals and found),
        "verified": verified,
        "status": "online" if found else "unknown",
    }]


class SitePreviewParser(HTMLParser):
    """Métadonnées d'image déjà publiées par la page, sans exécuter de JS."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "twitter:image",
                       "twitter:image:src"}:
                self.images.setdefault(key, attributes.get("content") or "")
        elif tag == "link" and "image_src" in (
                attributes.get("rel") or "").lower().split():
            self.images.setdefault("image_src", attributes.get("href") or "")


def safe_preview_image(raw, page_url):
    """Image HTTPS publique, sans query/fragment/identifiants ni SVG."""
    if not raw or len(str(raw)) > 1400:
        return None
    absolute = urljoin(page_url, str(raw).strip())
    cleaned = clean_url(absolute)
    if not cleaned or urlsplit(cleaned).scheme != "https":
        return None
    if urlsplit(cleaned).path.lower().endswith((".svg", ".ico", ".pdf")):
        return None
    return cleaned


def image_candidates_from_html(html_text, page_url):
    """Images proposées, dans l'ordre de préférence OpenGraph / Twitter."""
    parser = SitePreviewParser()
    try:
        parser.feed(str(html_text or "")[:96000])
    except (ValueError, AssertionError):
        return []
    results = []
    for key in ("og:image", "og:image:url", "twitter:image",
                "twitter:image:src", "image_src"):
        candidate = safe_preview_image(parser.images.get(key), page_url)
        if candidate and candidate not in results:
            results.append(candidate)
    return results


def image_from_page_html(html_text, page_url):
    """Compatibilité tests et anciens appels : première métadonnée d'image."""
    candidates = image_candidates_from_html(html_text, page_url)
    return candidates[0] if candidates else None


def preview_image_works(url):
    """Lire un petit échantillon de l'image depuis un hôte public (anti-SSRF).

    Un og:image peut être un HTML, un 403 anti-hotlink ou un ancien domaine.
    Ne jamais publier de Media Gallery si les octets ne sont pas une image.
    """
    current = safe_preview_image(url, url)
    if not current:
        return False
    for _ in range(4):
        parts = urlsplit(current)
        host = parts.hostname
        if not host:
            return False
        port = 443 if parts.scheme == "https" else 80
        connection = None
        try:
            ip = resolve_public_ip(host, port)
            connection = (PublicHTTPSConnection(host, ip, port, 4)
                          if parts.scheme == "https"
                          else PublicHTTPConnection(host, ip, port, 4))
            connection.request("GET", parts.path or "/", headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*",
                "Accept-Encoding": "identity",
                "Range": "bytes=0-63",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                candidate = safe_preview_image(
                    response.getheader("Location"), current
                ) if response.getheader("Location") else None
                if not candidate:
                    return False
                current = candidate
                continue
            if response.status not in (200, 206):
                return False
            content_type = (response.getheader("Content-Type") or "").lower()
            if not content_type.startswith("image/"):
                return False
            if "svg" in content_type or "icon" in content_type:
                return False
            if (response.getheader("Content-Encoding") or "").lower() not in ("", "identity"):
                return False
            first = response.read(64)
            return (first.startswith(b"\x89PNG\r\n\x1a\n")
                    or first.startswith(b"\xff\xd8\xff")
                    or first.startswith((b"GIF87a", b"GIF89a"))
                    or (first.startswith(b"RIFF") and first[8:12] == b"WEBP")
                    or (len(first) > 12 and first[4:12] in
                        (b"ftypavif", b"ftypavis")))
        except (OSError, TimeoutError, ValueError):
            return False
        finally:
            if connection:
                connection.close()
    return False


def image_candidates_from_discord_starter(api, tid, page_url):
    """Réutiliser les images de Discord liées au même domaine si accessibles."""
    try:
        starter = api.call("GET", f"/channels/{tid}/messages/{tid}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return []
        raise
    images = []
    for embed in starter.get("embeds") or []:
        source = clean_url(embed.get("url"))
        if not source or domain(source) != domain(page_url):
            continue
        for kind in ("image", "thumbnail"):
            media = embed.get(kind) or {}
            candidate = safe_preview_image(media.get("url"), page_url)
            if candidate and candidate not in images:
                images.append(candidate)
    return images


def image_from_discord_starter(api, tid, page_url):
    candidates = image_candidates_from_discord_starter(api, tid, page_url)
    return candidates[0] if candidates else None


def cached_site_preview(api, tid, page_url, tracking, reference_url=None):
    """Toujours revalider une image : un lien mis en cache peut expirer.

    Repli sur l'image OpenGraph du site de référence lorsqu'elle existe.
    Ne jamais créer de second message uniquement pour l'aperçu.
    """
    now = datetime.now(timezone.utc)
    saved = (tracking.get("preview_image_url")
             if tracking.get("preview_image_source") == page_url else None)
    if saved and preview_image_works(saved):
        return saved

    sources = [page_url]
    reference = clean_url(reference_url)
    if reference and reference != page_url:
        sources.append(reference)
    seen = set()
    for source in sources:
        candidates = image_candidates_from_discord_starter(api, tid, source)
        try:
            page = fetch_html_page(source, timeout=4)
        except (OSError, TimeoutError, ValueError, UnicodeError):
            page = None
        if page:
            candidates.extend(image_candidates_from_html(
                page["html"], page.get("final_url") or source
            ))
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if preview_image_works(candidate):
                tracking["preview_image_source"] = page_url
                tracking["preview_image_url"] = candidate
                tracking["preview_image_checked_at"] = now.isoformat(timespec="seconds")
                return candidate
    tracking["preview_image_source"] = page_url
    tracking["preview_image_url"] = None
    tracking["preview_image_checked_at"] = now.isoformat(timespec="seconds")
    return None


def is_preview_message(message, bot_id):
    """Ancien message du bot contenant uniquement un lien, sans carte."""
    return (str((message.get("author") or {}).get("id")) == bot_id
            and not (message.get("components") or [])
            and bool(re.fullmatch(r"https?://[^\s]+",
                                  str(message.get("content") or "").strip())))


def remove_duplicate_bot_links(api, tid, bot_id, tracking):
    """Supprimer les anciens messages URL du BOT, jamais ceux des membres.

    Cherche aussi quand le fichier de suivi a perdu l'identifiant historique.
    Ne supprime aucun message avec d'autres contenus ou composants.
    """
    known = tracking.get("preview_message_id")
    removed = 0
    inspected = set()
    if known:
        try:
            old = api.call("GET", f"/channels/{tid}/messages/{known}")
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
        else:
            if is_preview_message(old, bot_id):
                api.call("DELETE", f"/channels/{tid}/messages/{known}")
                removed += 1
                inspected.add(str(known))
    before = None
    for _ in range(10):
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = api.call("GET", f"/channels/{tid}/messages", params=params)
        if not batch:
            break
        for msg in batch:
            message_id = str(msg.get("id"))
            if message_id not in inspected and is_preview_message(msg, bot_id):
                api.call("DELETE", f"/channels/{tid}/messages/{message_id}")
                removed += 1
                inspected.add(message_id)
        if len(batch) < 100:
            break
        next_before = str(batch[-1]["id"])
        if next_before == before:
            break
        before = next_before
    tracking.pop("preview_message_id", None)
    return removed



def records_for_forum(state, thread_id):
    """Réunir les liens publics du fil et son adresse principale historique."""
    forum = {thread_id}
    found = [
        item for item in state.get("server_links", {}).values()
        if thread_id in forum_sources(item, forum) and item.get("active", True)
    ]
    primary = state.get("records", {}).get(thread_id, {})
    link = clean_url(primary.get("site_url"))
    if link and not any(domain(x.get("site_url")) == domain(link) for x in found):
        found.insert(0, {
            "site_url": link,
            "status": primary.get("status", "pending"),
            "active": True,
        })
    return found


def render_forum_update(title, records):
    groups = site_groups(records, title)
    heading = domain(groups[0]["url"]) if groups else safe_label(title)
    lines = ["### 🔄 Adresses mises à jour · " + heading, ""]
    for group in groups:
        name = domain(group["url"])
        lines.append(f"**✦ [{name}]({group['url']})**")
        if group["older"]:
            lines.append("-# ↳ Ancien : " + ", ".join(group["older"]))
        if group["announced"]:
            suffix = (" · Page HTML vérifiée" if group.get("verified")
                      else " · Intégrité du site à confirmer")
            lines.append("-# 🔄 Nouveau domaine annoncé par le site" + suffix)
        lines.append("")
    if not groups:
        lines.append("Aucune adresse actuelle lisible dans les messages du fil.")
    lines.extend([
        "-# Message actualisé automatiquement ; les publications d'origine restent intactes.",
        "||" + MARKER + "||",
    ])
    message = "\n".join(lines)
    if len(message) > 1900:
        raise ValueError("Liste trop longue : refuse de masquer des adresses.")
    return message


def own_message(msg, bot_id):
    return is_own_card(msg, bot_id)


def find_existing(api, tid, bot_id, known=None):
    if known:
        try:
            msg = api.call("GET", f"/channels/{tid}/messages/{known}")
            if own_message(msg, bot_id):
                return msg
            raise RuntimeError("Message suivi incompatible avec le bot")
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
    before = None
    for _ in range(10):
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = api.call("GET", f"/channels/{tid}/messages", params=params)
        for msg in batch:
            if own_message(msg, bot_id):
                return msg
        if len(batch) < 100:
            break
        before = str(batch[-1]["id"])
    return None


def upsert_forum_update(api, tid, bot_id, components, tracking):
    """Éditer une seule publication du bot, jamais celle du membre."""
    old = find_existing(api, tid, bot_id, tracking.get("message_id"))
    payload = {
        "content": None,
        "embeds": [],
        "flags": V2_FLAG,
        "components": components,
        "allowed_mentions": {"parse": []},
    }
    if old:
        if normalize(old.get("components")) != normalize(components):
            api.call("PATCH", f"/channels/{tid}/messages/{old['id']}", json=payload)
            return str(old["id"]), "updated"
        return str(old["id"]), "unchanged"
    posted = api.call("POST", f"/channels/{tid}/messages", json=payload)
    return str(posted["id"]), "created"


def is_preview_message(message, bot_id):
    """Lien seul, publié par ce bot sans Components V2."""
    return (str((message.get("author") or {}).get("id")) == bot_id
            and not (message.get("components") or [])
            and bool(re.fullmatch(r"https?://[^\s]+",
                                  str(message.get("content") or "").strip())))


def find_preview(api, tid, bot_id, known=None, urls=()):
    """Réutiliser le message d'aperçu, y compris après perte de l'état."""
    if known:
        try:
            old = api.call("GET", f"/channels/{tid}/messages/{known}")
            if is_preview_message(old, bot_id):
                return old
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
    before = None
    for _ in range(10):
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = api.call("GET", f"/channels/{tid}/messages", params=params)
        for msg in batch:
            if is_preview_message(msg, bot_id) and (
                    str(msg.get("content") or "").strip() in urls):
                return msg
        if len(batch) < 100:
            break
        before = str(batch[-1]["id"])
    return None


def upsert_preview(api, tid, bot_id, url, tracking, candidates=()):
    """L'aperçu natif nécessite un autre message : V2 interdit content/embeds."""
    old = find_preview(api, tid, bot_id, tracking.get("preview_message_id"),
                       (url, *candidates))
    if old:
        if str(old.get("content") or "").strip() != url:
            api.call("PATCH", f"/channels/{tid}/messages/{old['id']}",
                     json={"content": url, "allowed_mentions": {"parse": []}})
            return str(old["id"]), "updated"
        return str(old["id"]), "unchanged"
    posted = api.call("POST", f"/channels/{tid}/messages",
                      json={"content": url, "allowed_mentions": {"parse": []}})
    return str(posted["id"]), "created"


def authorized_teamflix_credentials(thread_id, forum_title):
    """Read a dedicated, non-versioned GitHub Actions secret for Teamflix.

    Never write credentials into monitor_state.json or GitHub history.
    They are only forwarded to the intended Discord forum card.
    """
    if str(thread_id) != TEAMFLIX_THREAD or str(forum_title or "").casefold() != "teamflix":
        return None
    username = os.environ.get("TEAMFLIX_LOGIN_USERNAME", "").strip()
    password = os.environ.get("TEAMFLIX_LOGIN_PASSWORD", "").strip()
    if not username or not password:
        return None
    if len(username) > 150 or len(password) > 200 or any(
        x in password for x in ("\\r", "\\n", "||")
    ):
        # Refuse malformed values instead of leaking them into Discord Markdown.
        return None
    return {"user": username, "password": password}


def sync_forum_updates(api, state, changed_events=()):
    """Initialiser tous les forums publics, puis synchroniser les changements.

    Les descriptions ne sont jamais sauvées dans le dépôt : on relit le
    message original au moment de publier/éditer la carte.
    """
    forums = {
        str(tid): rec for tid, rec in state.get("records", {}).items()
        if str(tid) != MIRROR_THREAD
    }
    tracked = state.setdefault("forum_updates", {})
    result = {
        "created": 0, "updated": 0, "unchanged": 0, "skipped": 0,
        "descriptions_found": 0, "description_missing": 0,
        "previews_created": 0, "previews_updated": 0,
        "duplicate_links_removed": 0, "errors": [],
    }
    bot_id = str(api.call("GET", "/users/@me")["id"])
    for tid, info in sorted(forums.items()):
        linked = records_for_forum(state, tid)
        if not linked:
            result["skipped"] += 1
            continue
        try:
            original = source_description(api, tid, bot_id)
            if original.strip():
                result["descriptions_found"] += 1
            else:
                result["description_missing"] += 1
            kind = directory_kind(info.get("title"))
            groups = (
                validated_movix_groups(linked) if is_movix_forum(info.get("title"))
                else validated_directory_groups(linked, kind) if kind
                else site_groups(linked, info.get("title") or "Site")
            )
            credentials = credentials_from_author_posts(api, tid, bot_id)
            secure_teamflix = authorized_teamflix_credentials(tid, info.get("title"))
            if secure_teamflix:
                credentials = {**(credentials or {}), **secure_teamflix}
            previous = tracked.get(tid, {})
            preview_url = clean_url(groups[0]["url"])
            image = (cached_site_preview(
                api, tid, preview_url, previous,
                reference_url=groups[0].get("reference_url"),
            ) if preview_url and not groups[0].get("no_active") and
                     groups[0].get("status") not in
                     ("offline", "suspect", "empty_index", "parked",
                      "interstitial", "blocked") else None)
            components = build_card(
                groups, original, info.get("title") or "Site",
                credentials=credentials, preview_image_url=image,
            )
            if not components:
                result["skipped"] += 1
                continue
            try:
                message_id, outcome = upsert_forum_update(
                    api, tid, bot_id, components, previous
                )
            except RuntimeError:
                if not image:
                    raise
                # Si Discord refuse l'URL image, garder la fiche texte
                # et utiliser le lien classique comme avant.
                previous["preview_image_url"] = None
                image = None
                components = build_card(
                    groups, original, info.get("title") or "Site",
                    credentials=credentials,
                )
                message_id, outcome = upsert_forum_update(
                    api, tid, bot_id, components, previous
                )
            # Enregistrer le message immédiatement pour éviter une seconde
            # carte si Discord refuse temporairement l'aperçu.
            # Ne pas conserver même une empreinte des mots de passe
            # dans l'historique public GitHub.
            safe_tracking = {k: v for k, v in previous.items() if k != "digest"}
            selected = groups[0]
            tracked[tid] = {
                **safe_tracking, "message_id": message_id,
                # Seules des URLs déjà publiées dans la fiche sont suivies.
                # Aucun texte original ou identifiant n'est conservé ici.
                "reference_url": selected.get("reference_url"),
                "active_url": (None if selected.get("no_active")
                               else selected.get("url")),
                "active_verified": bool(selected.get("verified")),
                "active_status": selected.get("status"),
                "suggested_url": selected.get("suggested_url"),
            }
            result[outcome] += 1
            # Une seule publication par forum : le lien se trouve deja
            # dans la carte, meme lorsqu'aucune image valide n'existe.
            result["duplicate_links_removed"] += remove_duplicate_bot_links(
                api, tid, bot_id, tracked[tid]
            )
        except (RuntimeError, ValueError) as exc:
            result["errors"].append({
                "thread_id": tid,
                "reason": str(exc).split("HTTP ")[-1][:85],
            })
    print("FORUMS : fiches stylisées et descriptions d'origine :", result)
    return result
