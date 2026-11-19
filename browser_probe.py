"""Repli navigateur gratuit pour les sites dont la sonde HTTP est bloquée.

Utilise Playwright/Chromium uniquement dans le workflow GitHub Actions.
Ne résout pas les CAPTCHA, ne manipule pas de cookies de membres et ne
considère pas un simple HTTP 200 comme preuve du bon site.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from name_probe import (
    MAX_HTML, _expected_brand, _matching_brand_identity,
    detect_wrong_page, is_empty_directory_index, parse_site_name,
)
from site_probe import clean_url, resolve_public_ip

MAX_BROWSER_REQUESTS = 100
BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}


def browser_page_identity(html, url, *, body_text="", navigation_count=0):
    """Validation de l'identité à partir du DOM final réellement rendu."""
    if is_empty_directory_index(html) or detect_wrong_page(html):
        return None
    final = clean_url(url)
    if not final or len(body_text.strip()) < 40 or navigation_count < 2:
        # Une page vide, un interstitiel ou un titre seul ne suffisent pas.
        return None
    identity = _matching_brand_identity(
        html, parse_site_name(html), _expected_brand(final)
    )
    if not identity:
        return None
    return {
        **identity, "page_health": "content",
        "final_url": final, "verification_method": "chromium",
    }


def probe_browser_site(url, *, timeout_ms=12000, include_html=False):
    """Essai secondaire avec un navigateur réel, jamais une promesse de bypass.

    La vérification réseau est bornée, sans identifiants, sans téléchargement,
    sans plugins anti-CAPTCHA et avec validation DNS/IP des URL demandées.
    Une protection 403 qui bloque aussi Chromium reste indéterminée.
    """
    initial = clean_url(url)
    if not initial:
        return None
    hostname = (urlsplit(initial).hostname or "").removeprefix("www.")
    if not hostname:
        return None
    try:
        resolve_public_ip(urlsplit(initial).hostname, 443 if initial.startswith("https:") else 80)
        from playwright.sync_api import sync_playwright
    except (ImportError, OSError, TimeoutError, ValueError):
        return None

    # Ne pas exposer le token Discord au sous-processus Chromium.
    browser_env = {
        key: value for key, value in os.environ.items()
        if key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "XDG_RUNTIME_DIR")
    }
    if "PATH" not in browser_env:
        browser_env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    allowed_hosts = {}
    count = [0]

    def allow_public_request(route, request):
        count[0] += 1
        candidate = clean_url(request.url)
        if count[0] > MAX_BROWSER_REQUESTS or not candidate:
            route.abort()
            return
        if request.resource_type in BLOCKED_RESOURCES:
            route.abort()
            return
        parsed = urlsplit(candidate)
        candidate_host = parsed.hostname
        # Une navigation hors du domaine initial est refusée. Les CDN et
        # scripts de domaines publics restent accessibles après contrôle DNS.
        if (request.is_navigation_request()
                and candidate_host.removeprefix("www.") != hostname):
            route.abort()
            return
        port = 443 if parsed.scheme == "https" else 80
        try:
            if (candidate_host, port) not in allowed_hosts:
                allowed_hosts[(candidate_host, port)] = resolve_public_ip(candidate_host, port)
        except (OSError, TimeoutError, ValueError):
            route.abort()
            return
        route.continue_()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                env=browser_env,
                args=["--disable-background-networking", "--disable-extensions"],
            )
            try:
                context = browser.new_context(
                    service_workers="block",
                    accept_downloads=False,
                    ignore_https_errors=False,
                    locale="fr-FR",
                )
                context.route("**/*", allow_public_request)
                page = context.new_page()
                response = page.goto(
                    initial, wait_until="domcontentloaded", timeout=timeout_ms
                )
                if response is None or response.status not in (200, 203, 206):
                    return None
                # Laisser les SPA hydrater leur contenu sans attendre toutes
                # leurs ressources externes, parfois constamment actives.
                page.wait_for_timeout(900)
                final_url = clean_url(page.url)
                if not final_url or (
                    (urlsplit(final_url).hostname or "").removeprefix("www.") != hostname
                ):
                    return None
                rendered_html = page.content()[:MAX_HTML]
                text = page.locator("body").inner_text(timeout=1500)[:4000]
                elements = page.locator("a, button, article, [role='button']").count()
                result = browser_page_identity(
                    rendered_html, final_url,
                    body_text=text, navigation_count=elements,
                )
                if result and include_html:
                    result["html"] = rendered_html
                return result
            finally:
                browser.close()
    except Exception as exc:
        # La disponibilité du navigateur n'est jamais nécessaire à
        # la synchronisation Discord. Le site reste orange si le test échoue.
        print(f"NAVIGATEUR : {hostname} indéterminé ({type(exc).__name__})")
        return None
