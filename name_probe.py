"""Detecter prudemment le nom affiche par un site HTML public.

Valide DNS et adresses IP pour chaque redirection, lecture bornee, sans script.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

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


def parse_site_name(html_text):
    try:
        parser = BrandHTMLParser()
        parser.feed(html_text[:MAX_HTML])
    except (ValueError, AssertionError):
        return None
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


def fetch_html_page(url, timeout=7):
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
            connection.request("GET", parsed.path or "/", headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9",
                "Accept-Encoding": "identity",
                "Range": f"bytes=0-{MAX_HTML - 1}",
            })
            response = connection.getresponse()
            status = response.status
            location = response.getheader("Location")
            if status in (301, 302, 303, 307, 308) and location:
                nxt = clean_url(urljoin(current, location))
                if not nxt:
                    return None
                current = nxt
                continue
            if status not in (200, 206):
                return None
            content_type = (response.getheader("Content-Type") or "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return None
            if (response.getheader("Content-Encoding") or "identity").lower() not in ("identity", ""):
                return None
            body = response.read(MAX_HTML + 1)[:MAX_HTML]
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


def probe_site_name(url):
    """Resultat structure ou None ; un seul GET borne par site et par jour."""
    try:
        page = fetch_html_page(url)
    except (OSError, ValueError, TimeoutError, UnicodeError):
        return None
    if not page:
        return None
    result = parse_site_name(page["html"])
    if not result:
        return None
    result["final_url"] = page["final_url"]
    return result
