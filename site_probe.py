"""Surveillance HTTP prudente des adresses publiques issues de Discord.

La cible n'est jamais une adresse locale, privee ou reservee. Chaque redirection
est revalidee avant de se connecter; la connexion TCP utilise l'adresse IP deja
validee et TLS verifie le nom d'hote d'origine.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit


RE_URL = re.compile(r'https?://[^\s<>"\'\]\)\}]+', flags=re.I)
SKIP_HOSTS = {
    "discord.com", "discord.gg", "cdn.discordapp.com", "media.discordapp.net",
    "discordapp.com", "github.com", "www.github.com",
    "youtube.com", "youtu.be", "twitter.com", "x.com", "t.me",
    "instagram.com", "facebook.com", "tiktok.com",
}
USER_AGENT = "EliktoSiteMonitor/1.0 (+https://elikto.github.io/Elikto-Discord-Bot/)"


def clean_url(value: str) -> str | None:
    """Elimine identifiants, fragments et queries, jamais publies dans l'historique."""
    try:
        raw = str(value or "").strip().strip(".,;!?>")
        obj = urlsplit(raw)
        if obj.scheme.lower() not in {"http", "https"}:
            return None
        if obj.username or obj.password or obj.port not in (None, 80, 443):
            return None
        hostname = (obj.hostname or "").rstrip(".").lower()
        if not hostname or len(hostname) > 253:
            return None
        if hostname.endswith((".localhost", ".local", ".internal", ".test", ".invalid", ".example")):
            return None
        if hostname in {"localhost", "metadata.google.internal"}:
            return None
        try:
            ip = ipaddress.ip_address(hostname.strip("[]"))
            if not ip.is_global:
                return None
        except ValueError:
            if "." not in hostname or any(not x for x in hostname.split(".")):
                return None
            if not re.fullmatch(r"[a-z0-9._-]+", hostname):
                return None
        # Conserver le chemin pour pouvoir observer les 404 des pages de liens.
        path = obj.path if obj.path.startswith("/") else "/"
        path = path or "/"
        return urlunsplit((obj.scheme.lower(), hostname, path, "", ""))
    except (ValueError, TypeError):
        return None


def extract_candidate_urls(message: dict) -> list[str]:
    """Uniquement contenu visible du premier message du fil (pas les autres auteurs)."""
    fragments = [str(message.get("content") or "")]
    for embed in message.get("embeds") or []:
        for key in ("url", "description"):
            if embed.get(key):
                fragments.append(str(embed[key]))
        for field in embed.get("fields", []):
            fragments.append(str(field.get("value") or ""))
    seen, links = set(), []
    for text in fragments:
        for found in RE_URL.findall(text):
            url = clean_url(found)
            if not url:
                continue
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            if host in SKIP_HOSTS or host.endswith(".discord.com"):
                continue
            if url in seen:
                continue
            seen.add(url)
            links.append(url)
    return links


def resolve_public_ip(hostname: str, port: int) -> str:
    """Refuse si une seule adresse DNS est non publique (mitigation SSRF)."""
    records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    if not records:
        raise ValueError("DNS sans adresse")
    addresses = sorted({rec[4][0] for rec in records})
    if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Adresse non publique interdite")
    return addresses[0]


class PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self.pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection(
            (self.pinned_ip, self.port), timeout=self.timeout
        )


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_ip = ip

    def connect(self):
        sock = socket.create_connection(
            (self.pinned_ip, self.port), timeout=self.timeout
        )
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def single_request(url: str, method: str, timeout: float = 7) -> tuple[int, str | None]:
    parsed = urlsplit(url)
    port = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname
    if not host:
        raise ValueError("Hote absent")
    ip = resolve_public_ip(host, port)
    connection = (
        PublicHTTPSConnection(host, ip, port, timeout)
        if parsed.scheme == "https"
        else PublicHTTPConnection(host, ip, port, timeout)
    )
    path = parsed.path or "/"
    try:
        # Pas de cookies ni de headers d'authentification et aucun proxy implicite.
        connection.request(method, path, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "identity",
            "Range": "bytes=0-1024" if method == "GET" else "bytes=0-0",
        })
        response = connection.getresponse()
        return response.status, response.getheader("Location")
    finally:
        connection.close()


def check_site(url: str) -> dict:
    """Reponse reelle du serveur, sans interpretation de son contenu."""
    initial = clean_url(url)
    if not initial:
        return {"result": "unknown", "http_status": None, "final_url": None,
                "reason": "URL non autorisee"}
    current = initial
    try:
        for hops in range(5):
            code, location = single_request(current, "HEAD")
            if code in (405, 501):
                code, location = single_request(current, "GET")
            if code in (301, 302, 303, 307, 308) and location:
                destination = clean_url(urljoin(current, location))
                if not destination:
                    return {"result": "unknown", "http_status": code,
                            "final_url": current, "reason": "Redirection non publique"}
                current = destination
                continue
            if code in (401, 403, 429):
                result = "restricted"
            elif 200 <= code < 400:
                result = "online"
            elif code in (404, 410) or code >= 500:
                result = "failed"
            else:
                result = "unknown"
            return {"result": result, "http_status": code,
                    "final_url": current, "reason": None}
        return {"result": "unknown", "http_status": None,
                "final_url": current, "reason": "Trop de redirections"}
    except (OSError, TimeoutError, ValueError, ssl.SSLError, http.client.HTTPException) as exc:
        return {
            "result": "failed",
            "http_status": None,
            "final_url": current,
            "reason": type(exc).__name__[:55],
        }


def other_domain(url1: str | None, url2: str | None) -> bool:
    if not url1 or not url2:
        return False
    host1 = (urlsplit(url1).hostname or "").removeprefix("www.")
    host2 = (urlsplit(url2).hostname or "").removeprefix("www.")
    return host1 != host2
