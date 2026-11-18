"""Detecter prudemment le nom affiche par un site HTML public.

Valide DNS et adresses IP pour chaque redirection, lecture bornee, sans script.
"""
from __future__ import annotations

import html
import http.client
from html.parser import HTMLParser
import re
import unicodedata
import zlib
from urllib.parse import urljoin, urlsplit, urlunsplit

from site_probe import (
    clean_url, resolve_public_ip, PublicHTTPConnection, PublicHTTPSConnection,
    USER_AGENT,
)

MAX_HTML = 96 * 1024
BAD_TITLES = (
    "just a moment", "attention required", "checking your browser", "please wait",
    "access denied", "forbidden", "security check", "robot check",
    "cloudflare", "captcha", "verify you are human", "page not found",
    "service unavailable", "502 bad gateway", "403 forbidden",
    "acces refuse", "un instant", "chargement", "loading", "error",
    "erreur", "not found", "index of /",
)
GENERIC_NAMES = {
    "home", "accueil", "login", "connexion", "sign in", "website",
    "site internet", "streaming", "films", "series", "séries", "movies",
    "films et series", "films et séries", "official website",
    "welcome", "bienvenue", "watch online",
}


class BrandHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.in_title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower().strip()
            content = attrs.get("content") or ""
            if key in ("og:site_name", "application-name", "og:title", "twitter:title"):
                self.meta.setdefault(key, content)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, value):
        if self.in_title:
            self.title_parts.append(value)


def sanitized_name(value):
    """Nom utilisable dans Discord, sans slogans, mentions ni faux anti-bot."""
    value = html.unescape(str(value or ""))
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().strip("|:—–-•·*_# ")
    if not value or re.search(r"https?://|<@|@everyone|@here", value, re.I):
        return None
    value = re.split(r"\s+[|—–]\s+|\s+-\s+|\s+:\s+", value, maxsplit=1)[0]
    value = re.sub(r"\s+", " ", value).strip().strip("|:—–-•·*_# ")
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    if not 3 <= len(value) <= 58:
        return None
    if normalized in GENERIC_NAMES:
        return None
    if any(phrase in normalized for phrase in BAD_TITLES):
        return None
    if not re.search(r"[^\W_]", value, re.UNICODE):
        return None
    return value


def announced_rename(raw):
    """Décoder les titres du type 'momdax devient nandov.com'.

    Ce sont des annonces affichées sur le site, pas une preuve que
    l'adresse annoncée est contrôlée par le même exploitant.
    """
    text = html.unescape(str(raw or "")).strip()
    match = re.search(
        r"(?i)(?<![\w-])(?P<old>[a-z][a-z0-9-]{2,35})(?:\.[a-z]{2,10})?"
        r"\s+(?:devient|est devenu|becomes|is now|renomm[ée] en)"
        r"\s+(?P<new>[a-z][a-z0-9-]{2,35})(?:\.(?P<tld>[a-z]{2,10}))?(?![\w-])",
        text,
    )
    if not match:
        return None
    old = match.group("old").upper()
    new = match.group("new").upper()
    if old == new:
        return None
    tld = match.group("tld")
    announced = ("https://" + match.group("new").lower() + "." + tld.lower() + "/") if tld else None
    return {
        "name": new,
        "previous_name": old,
        "announced_url": announced,
        "evidence": "title:rename",
        "confidence": "strong",
    }


# Les adresses annoncées dans les bannières ne sont pas des redirections
# HTTP. Elles peuvent être insérées dans du HTML ou une chaîne JS.
NOTICE_PATTERN = re.compile(
    r"(?i)(?:passe(?:r|z)?\s+sur|utiliser|continue(?:r|z)?\s+sur|"
    r"nouvelle?\s+adresse(?:\s+officielle)?\s*[:=\-]\s*|"
    r"migr(?:er|ez|ation)\s+(?:vers|sur))"
    r"\s+(?:https?://)?(?:www\.)?"
    r"(?P<domain>[a-z0-9][a-z0-9-]{1,61}(?:\.[a-z0-9-]{2,24})+)",
    flags=re.I,
)


OFFICIAL_LINK_CUE = re.compile(
    r"(?:url ephemere du moment|adresse officielle active|"
    r"(?:la )?nouvelle adresse(?: officielle)?|adresse actuelle|"
    r"adresse du moment|nouvelle url|nouveau domaine|"
    r"lien officiel du moment|adresse du site(?: actuel)?|"
    r"site officiel actuel|nouveau site officiel)"
    r"\s*[:\-—]?\s*$",
    flags=re.I,
)
OFFICIAL_LINK_LABEL = re.compile(
    r"(?:nouvelle adresse|adresse officielle|nouveau site officiel|"
    r"ouvrir le nouveau site|acceder au nouveau site)",
    flags=re.I,
)


def _fold_notice_text(value):
    value = unicodedata.normalize("NFKD", html.unescape(str(value or "")))
    return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()


class OfficialAddressLinkParser(HTMLParser):
    """Liens associés à une annonce explicite sur le site lui-même.

    « URL Éphémère Du moment » suivi d'un lien (ex. fstream.info),
    « Nouvelle adresse officielle » ou « Adresse actuelle ».
    Ne pas assimiler un lien IPTV/publicité/contact à une nouvelle adresse.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.context = ""
        self.current = None
        self.links = []
        self.meta_refresh = []

    def handle_starttag(self, tag, attrs):
        if tag == "meta" and dict(attrs).get("http-equiv", "").casefold() == "refresh":
            self.meta_refresh.append(dict(attrs).get("content") or "")
        if tag == "a":
            href = dict(attrs).get("href")
            self.current = {
                "href": href,
                "before": self.context[-160:],
                "parts": [],
            }

    def handle_data(self, data):
        data = re.sub(r"\s+", " ", data)
        if self.current is not None:
            self.current["parts"].append(data)
        else:
            self.context = (self.context + " " + data)[-400:]

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            info = self.current
            label = " ".join(info["parts"]).strip()
            self.links.append((info["href"], info["before"], label))
            self.context = (self.context + " " + label)[-400:]
            self.current = None


def announced_url_from_official_link(html_text, source):
    try:
        parser = OfficialAddressLinkParser()
        parser.feed(str(html_text or "")[:MAX_HTML])
    except (ValueError, AssertionError):
        return None
    source_host = (urlsplit(source).hostname or "").removeprefix("www.")
    for href, preceding, label in parser.links:
        if not href:
            continue
        cue = OFFICIAL_LINK_CUE.search(_fold_notice_text(preceding)[-110:])
        direct = OFFICIAL_LINK_LABEL.search(_fold_notice_text(label))
        if not (cue or direct):
            continue
        target = clean_url(urljoin(source, href))
        if not target:
            continue
        target_host = (urlsplit(target).hostname or "").removeprefix("www.")
        if target_host == source_host:
            continue
        return {
            "announced_url": target,
            "previous_url": source,
            "evidence": "html:official_address_link",
            "confidence": "announced",
        }
    # Certaines pages migrées ne montrent aucun lien, mais utilisent
    # <meta http-equiv="refresh" content="0; URL=https://nouveau.site/">.
    # Ne suivre que les redirections rapides et vers une autre adresse publique.
    for refresh in parser.meta_refresh:
        match = re.match(
            r"""(?i)^\s*(?P<delay>\d{1,2})\s*;\s*url\s*=\s*['"]?(?P<url>[^'"\s;]+)""",
            refresh,
        )
        if not match or int(match.group("delay")) > 10:
            continue
        target = clean_url(urljoin(source, match.group("url")))
        if not target:
            continue
        target_host = (urlsplit(target).hostname or "").removeprefix("www.")
        if target_host != source_host:
            return {
                "announced_url": target,
                "previous_url": source,
                "evidence": "html:meta_refresh",
                "confidence": "announced",
            }
    return None



OFFICIAL_VISIBLE_DOMAIN = re.compile(
    r"(?:(?:la )?nouvelle adresse(?: officielle)?|url ephemere du moment|"
    r"adresse actuelle|adresse officielle active|adresse du moment|"
    r"nouveau domaine|nouvelle url|site officiel actuel)"
    r"\s*[:\-—]?\s*(?P<domain>(?:https?://)?(?:www\.)?"
    r"[a-z0-9][a-z0-9-]{0,61}(?:\.[a-z0-9-]{2,24})+)",
    re.I,
)


def announced_url_from_visible_text(html_text, source):
    """Nom de domaine écrit comme texte sous une étiquette officielle.

    Sur certains portails (ex. French Stream), l'URL éphémère est affichée
    dans une grande zone non cliquable ou mise en place par JavaScript. Le
    serveur peut donc renvoyer la valeur visible sans élément <a href>.
    Exiger une annonce immédiatement suivie du domaine, jamais une recherche
    d'URL arbitraire dans le reste de la page.
    """
    raw = str(html_text or "")[:MAX_HTML]
    raw = re.sub(r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>",
                 " ", raw)
    visible = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    visible = re.sub(r"\s+", " ", visible)
    folded = _fold_notice_text(visible)
    source_host = (urlsplit(source).hostname or "").removeprefix("www.")
    for match in OFFICIAL_VISIBLE_DOMAIN.finditer(folded):
        value = match.group("domain")
        target = clean_url(value if value.startswith(("https://", "http://"))
                           else "https://" + value)
        if target and (urlsplit(target).hostname or "").removeprefix("www.") != source_host:
            return {
                "announced_url": target,
                "previous_url": source,
                "evidence": "html:official_address_text",
                "confidence": "announced",
            }
    return None


def parse_site_migration(html_text, source_url):
    """Extraire une annonce 'Passe sur Webflix.surf, même compte'.

    Ne pas prendre un lien quelconque, une publicité ou une image pour une
    preuve. On vérifie la formulation et la cohérence avec le domaine source.
    """
    source = clean_url(source_url)
    if not source:
        return None
    source_host = (urlsplit(source).hostname or "").removeprefix("www.")
    raw = html.unescape(str(html_text or "")[:MAX_HTML])
    # Échappements fréquents des bundles JS et du HTML embarqué.
    raw = (raw.replace(r"\u002e", ".").replace(r"\x2e", ".")
              .replace(r"\/", "/").replace("\\n", " "))
    raw = re.sub(r"<[^>]+>", " ", raw)
    for match in NOTICE_PATTERN.finditer(raw):
        domain = match.group("domain").rstrip(".,;:")
        target = clean_url("https://" + domain + "/")
        if not target:
            continue
        target_host = (urlsplit(target).hostname or "").removeprefix("www.")
        if target_host == source_host:
            continue
        surrounding = raw[max(0, match.start() - 180):min(len(raw), match.end() + 180)].casefold()
        brand = source_host.split(".")[0]
        new_brand = target_host.split(".")[0]
        # Un nom identique avec nouvelle extension est crédible, ou bien
        # la notice mentionne un compte commun / une adresse officielle.
        corroboration = (
            brand == new_brand
            or "même compte" in surrounding
            or "meme compte" in surrounding
            or "adresse officielle" in surrounding
            or "compte et listes synchronis" in surrounding
        )
        if not corroboration:
            continue
        return {
            "announced_url": target,
            "previous_url": source,
            "evidence": "html:migration_notice",
            "confidence": "announced",
        }
    return (announced_url_from_official_link(html_text, source)
            or announced_url_from_visible_text(html_text, source))


def probe_site_migration(url):
    """Détecter une adresse annoncée dans une page HTML, même si HTTP 451.

    Une page construite entièrement par JavaScript peut rester illisible.
    """
    try:
        page = fetch_html_page(url)
    except (OSError, ValueError, TimeoutError, UnicodeError):
        return None
    if not page:
        return None
    return parse_site_migration(page["html"], url)


def parse_site_name(html_text):
    try:
        parser = BrandHTMLParser()
        parser.feed(html_text[:MAX_HTML])
    except (ValueError, AssertionError):
        return None
    # Les titres de certains sites annoncent explicitement un nouveau
    # domaine (ex. MOMDAX devient NANDOV.com) : capturer les DEUX noms.
    raw_titles = [" ".join(parser.title_parts)]
    raw_titles.extend(parser.meta.get(key, "") for key in
                      ("og:title", "twitter:title", "og:site_name"))
    for raw in raw_titles:
        announcement = announced_rename(raw)
        if announcement:
            return announcement

    for key in ("og:site_name", "application-name"):
        value = sanitized_name(parser.meta.get(key))
        if value:
            return {"name": value, "evidence": key, "confidence": "strong"}
    for key, raw in (
        ("title", " ".join(parser.title_parts)),
        ("og:title", parser.meta.get("og:title")),
        ("twitter:title", parser.meta.get("twitter:title")),
    ):
        value = sanitized_name(raw)
        if value:
            return {"name": value, "evidence": key, "confidence": "medium"}
    return None


def fetch_html_page(url, timeout=7, *, browser_compatible=False):
    """GET borne, suivi securise d'au plus quatre redirections."""
    current = clean_url(url)
    if not current:
        return None
    for _ in range(5):
        parsed = urlsplit(current)
        host = parsed.hostname
        port = 443 if parsed.scheme == "https" else 80
        ip = resolve_public_ip(host, port)
        connection = (
            PublicHTTPSConnection(host, ip, port, timeout)
            if parsed.scheme == "https" else
            PublicHTTPConnection(host, ip, port, timeout)
        )
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9",
                "Accept-Encoding": "identity",
            }
            if browser_compatible:
                # Une navigation ordinaire peut être acceptée alors que
                # l'agent de surveillance reçoit 403. Aucun cookie, JavaScript,
                # CAPTCHA, proxy ou contournement de challenge n'est utilisé.
                headers.update({
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                        "Gecko/20100101 Firefox/128.0"
                    ),
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,*/*;q=0.8"
                    ),
                    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7",
                    # Certains CDN diffèrent selon la négociation de
                    # compression : décoder gzip/deflate en restant borné.
                    "Accept-Encoding": "gzip, deflate",
                })
            # Pas de Range et lecture plafonnée à MAX_HTML octets.
            connection.request("GET", parsed.path or "/", headers=headers)
            response = connection.getresponse()
            status = response.status
            location = response.getheader("Location")
            if status in (301, 302, 303, 307, 308) and location:
                nxt = clean_url(urljoin(current, location))
                if not nxt:
                    return None
                current = nxt
                continue
            if status not in (200, 206, 451):
                return None
            content_type = (response.getheader("Content-Type") or "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return None
            content_encoding = (
                response.getheader("Content-Encoding") or "identity"
            ).lower().strip()
            if content_encoding not in ("identity", "", "gzip", "deflate"):
                return None
            # La taille lue ET la sortie décompressée restent plafonnées
            # pour éviter qu'une page arbitraire ne consomme trop de RAM.
            try:
                body = response.read(MAX_HTML + 1)[:MAX_HTML]
            except http.client.IncompleteRead:
                # Un CDN peut fermer un flux chunked avant la fin. Ce n'est
                # ni un site hors ligne ni une raison d'arrêter tout Discord.
                # Ne pas utiliser du HTML partiel comme preuve d'identité.
                return None
            if content_encoding in ("gzip", "deflate"):
                try:
                    inflater = zlib.decompressobj(
                        16 + zlib.MAX_WBITS if content_encoding == "gzip"
                        else zlib.MAX_WBITS
                    )
                    body = inflater.decompress(body, MAX_HTML + 1)[:MAX_HTML]
                except zlib.error:
                    return None
            if not body:
                return None
            encoding = "utf-8"
            charset = re.search(r"charset=([\w-]+)", content_type)
            if charset and charset.group(1).lower() in ("utf-8", "iso-8859-1", "windows-1252"):
                encoding = charset.group(1)
            return {"html": body.decode(encoding, errors="replace"), "final_url": current}
        finally:
            connection.close()
    return None


def is_empty_directory_index(html_text):
    """Reconnaître un répertoire HTTP auto-indexé, pas un catalogue de streaming.

    Le titre/entête doit être explicitement « Index of /... » (Apache/nginx)
    ou « Directory listing for /... » (serveur Python). Une application JS
    sans texte visible n'est PAS considérée vide : ne pas inventer une panne.
    """
    raw = html.unescape(str(html_text or "")[:MAX_HTML])
    headings = re.findall(
        r"<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)\s*>",
        raw, flags=re.I | re.S,
    )
    for heading in headings:
        plain = re.sub(r"<[^>]+>", " ", heading)
        plain = re.sub(r"\s+", " ", plain).strip()
        if re.match(r"(?i)^(?:index of|directory listing for|index de)\s+/", plain):
            return True
    return False


# Signaux forts du contenu final, indépendants du code HTTP ou du titre.
# On exclut scripts/styles : les mots de ces ressources ne décrivent pas la page.
PARKING_MARKERS = (
    r"\bdomain registration has expired\b",
    r"\bthis domain (?:name )?(?:has expired|is expired)\b",
    r"\b(?:ce )?nom de domaine (?:est )?expire\b",
    r"\bthis domain (?:name )?is (?:for sale|parked)\b",
    r"\b(?:website|account) (?:is )?suspended\b",
)
SHORTENER_MARKERS = (
    r"\bthe link has been removed\b",
    r"\bthis link (?:has been )?(?:removed|deleted|expired)\b",
    r"\bce lien (?:a ete )?(?:supprime|retire|expire)\b",
    r"\bplease wait \d{1,3} seconds? before viewing the link\b",
    r"\bplease wait\b.{0,90}\b(?:link|destination|redirect)\b",
    r"\byour link will be available in \d{1,3} seconds?\b",
    r"\b(?:unlock|continue to|access) (?:the |your )?link after\b",
    r"\byour link is (?:almost|nearly) ready\b",
    r"\bwait \d{1,3} seconds? to (?:access|continue|get).*link\b",
    r"\bskip (?:this )?ad(?:vertisement)?\b",
    r"\bacces au lien dans \d{1,3} secondes?\b",
    r"\bveuillez patienter \d{1,3} secondes? pour acceder au lien\b",
)
ANTI_BOT_MARKERS = (
    r"\bchecking your browser before accessing\b",
    r"\bverify you are human\b",
    r"\bjust a moment\b.{0,70}\bcloudflare\b",
)


def detect_wrong_page(html_text):
    """Classer les faux HTTP 200 sans interpréter un simple titre comme preuve.

    parked : domaine expiré / parqué, interstitial : monétisation de liens
    ou compte à rebours, blocked : protection anti-bot non concluante.
    Aucun JavaScript n'est exécuté, donc un site sans HTML clair reste inconnu.
    """
    source = str(html_text or "")[:MAX_HTML]
    source = re.sub(
        r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>",
        " ", source,
    )
    plain = re.sub(r"(?s)<[^>]+>", " ", html.unescape(source))
    plain = re.sub(r"\s+", " ", plain)[:MAX_HTML]
    folded = _fold_notice_text(plain)
    for kind, expressions in (
        ("parked", PARKING_MARKERS),
        ("interstitial", SHORTENER_MARKERS),
        ("blocked", ANTI_BOT_MARKERS),
    ):
        if any(re.search(pattern, folded, re.I) for pattern in expressions):
            return {"page_health": kind, "evidence": "html:" + kind}
    return None


def _brand_key(text):
    """Comparer les noms sans accents ni ponctuation, sans utiliser le corps HTML."""
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in value if char.isalnum()
                   and not unicodedata.combining(char))


def _expected_brand(url):
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    labels = host.split(".")
    # Prendre le nom de domaine, pas le sous-domaine régional (fr, en...).
    return labels[-2] if len(labels) >= 2 else ""


def _matching_brand_identity(html_text, identity, expected):
    """Exiger une identité cohérente pour valider un site protégé (403).

    Le nom peut être à la fin d'un titre long, comme
    'Films et Séries ... | CinezZz'. Le texte du corps seul ne suffit pas.
    """
    brand = _brand_key(expected)
    if len(brand) < 4:
        return None
    if identity and brand in _brand_key(identity.get("name")):
        return identity
    parser = BrandHTMLParser()
    try:
        parser.feed(str(html_text or "")[:MAX_HTML])
    except (ValueError, AssertionError):
        return None
    titles = [" ".join(parser.title_parts)]
    titles.extend(parser.meta.get(key, "") for key in
                  ("og:site_name", "application-name", "og:title", "twitter:title"))
    if any(brand in _brand_key(title) for title in titles):
        return {"name": expected, "evidence": "html:title_brand",
                "confidence": "medium"}
    return None


def probe_site_name(url, *, allow_www_fallback=False):
    """Lire l'identité HTML. Repli prudent pour les sites HTTP restreints.

    Jusqu'à trois GET publics si autorisé : sonde habituelle, requête HTML
    compatible navigateur, puis variante www/non-www. Une page tierce, une
    publicité ou un domaine parqué ne prouve jamais que le site fonctionne.
    """
    original = clean_url(url)
    if not original:
        return None
    attempts = [(original, False)]
    expected = _expected_brand(original)
    if allow_www_fallback:
        attempts.append((original, True))
        parts = urlsplit(original)
        host = parts.hostname or ""
        alternate = host[4:] if host.startswith("www.") else "www." + host
        if alternate and alternate != host:
            attempts.append((
                urlunsplit((parts.scheme, alternate, parts.path, "", "")), True
            ))
    inconclusive = None
    for candidate, browser_headers in attempts:
        try:
            page = (fetch_html_page(candidate, browser_compatible=True)
                    if browser_headers else fetch_html_page(candidate))
        except (OSError, ValueError, TimeoutError, UnicodeError):
            continue
        if not page:
            continue
        final = clean_url(page.get("final_url"))
        if not final:
            continue
        # Le repli www ne doit pas transformer une redirection publicitaire
        # ou vers un tout autre domaine en preuve de disponibilité.
        if (allow_www_fallback and
                (urlsplit(final).hostname or "").removeprefix("www.")
                != (urlsplit(original).hostname or "").removeprefix("www.")):
            continue
        if is_empty_directory_index(page["html"]):
            inconclusive = {"page_health": "empty_index",
                            "evidence": "html:directory_index",
                            "final_url": final}
            continue
        wrong = detect_wrong_page(page["html"])
        if wrong:
            wrong["final_url"] = final
            inconclusive = wrong
            continue
        result = parse_site_name(page["html"])
        if allow_www_fallback:
            result = _matching_brand_identity(page["html"], result, expected)
        if not result:
            continue
        result["final_url"] = final
        result["page_health"] = "content"
        return result
    return inconclusive



