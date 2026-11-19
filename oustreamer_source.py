"""Read explicit address-change hints from the public OùStreamer trends page.

This is third-party evidence, NOT proof that the new destination is safe or
working. Never infer a URL solely from a site's displayed name or status.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
import html
import json
import re

from site_probe import clean_url
from name_probe import fetch_html_page

SOURCE_URL = "https://app.oustreamer.com/tendances"
MAX_BYTES = 200_000
MAX_CHANGES = 60
DOMAIN = r"(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9][a-z0-9-]{0,62})*\.[a-z]{2,24}(?:/[a-z0-9._~%/-]*)?"
EXPLICIT_CHANGE = re.compile(
    rf"(?i)(?<![\w.])(?P<old>{DOMAIN})\s*"
    rf"(?:→|➡️?|->|devient|redirig(?:e|é|ée)?\s+vers|"
    rf"(?:nouvelle|actuelle)\s+adresse\s*[:：]?)\s*"
    rf"(?P<new>{DOMAIN})(?![\w.])"
)

OLD_KEYS = ("old_url", "oldUrl", "previous_url", "previousUrl", "original_url")
NEW_KEYS = ("new_url", "newUrl", "current_url", "currentUrl", "redirect_url", "redirectUrl", "target_url")
REDIRECT_STATES = ("redirected", "redirection", "moved", "migrated")
IGNORE_HOSTS = {"oustreamer.com", "app.oustreamer.com", "example.com", "localhost"}


def normalized_site(raw):
    if not isinstance(raw, str):
        return None
    raw = html.unescape(raw).strip().strip("<>\"'").rstrip(".,!?")
    if len(raw) > 200:
        return None
    url = clean_url(raw if raw.startswith(("https://", "http://")) else "https://" + raw)
    if not url:
        return None
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if host in IGNORE_HOSTS or host.endswith(".oustreamer.com"):
        return None
    return url


class PageText(HTMLParser):
    """Text and JSON data, without script noise masquerading as visible text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.visible = []
        self.scripts = []
        self.skipping = 0
        self.script_json = False
        self.current_script = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script_json = (
                "json" in (attrs.get("type") or "").lower()
                or attrs.get("id") == "__NEXT_DATA__"
            )
            self.current_script = []
            self.skipping += 1
        elif tag in ("style", "noscript", "svg"):
            self.skipping += 1
        elif tag in ("p", "li", "tr", "td", "section", "div", "a", "br", "h2", "h3"):
            self.visible.append(" ")

    def handle_endtag(self, tag):
        if tag == "script":
            if self.script_json:
                self.scripts.append("".join(self.current_script))
            self.script_json = False
            self.current_script = []
            self.skipping = max(0, self.skipping - 1)
        elif tag in ("style", "noscript", "svg"):
            self.skipping = max(0, self.skipping - 1)
        elif tag in ("p", "li", "tr", "td", "section", "div", "a", "br"):
            self.visible.append(" ")

    def handle_data(self, data):
        if self.script_json:
            self.current_script.append(data)
        elif not self.skipping:
            self.visible.append(data)


def parse_oustreamer_changes(page_html):
    """Only explicit old→new pairs, never inferred from Active/Blocked badges."""
    parser = PageText()
    try:
        parser.feed(str(page_html or "")[:MAX_BYTES])
    except (ValueError, AssertionError):
        return []

    matches = {}

    def add_pair(old, new):
        old_url, new_url = normalized_site(old), normalized_site(new)
        if not old_url or not new_url:
            return
        old_host = (urlsplit(old_url).hostname or "").removeprefix("www.")
        new_host = (urlsplit(new_url).hostname or "").removeprefix("www.")
        if old_host == new_host:
            return
        if len(matches) >= MAX_CHANGES and old_host not in matches:
            return
        matches[old_host] = {
            "old_url": old_url, "url": new_url,
            "name": new_host.split(".")[0].upper(),
            "previous_names": [old_host.split(".")[0].upper()],
            "evidence": "oustreamer:explicit_change",
        }

    visible_text = " ".join(parser.visible)
    for match in EXPLICIT_CHANGE.finditer(visible_text):
        add_pair(match.group("old"), match.group("new"))

    def traverse(obj, depth=0):
        if depth > 9:
            return
        if isinstance(obj, list):
            for child in obj[:500]:
                traverse(child, depth + 1)
        elif isinstance(obj, dict):
            older = next((obj[key] for key in OLD_KEYS
                          if isinstance(obj.get(key), str)), None)
            newer = next((obj[key] for key in NEW_KEYS
                          if isinstance(obj.get(key), str)), None)
            status = str(obj.get("status") or "").casefold()
            if not older and status in REDIRECT_STATES and newer:
                older = obj.get("url") or obj.get("domain")
            if older and newer:
                add_pair(older, newer)
            for child in list(obj.values())[:150]:
                if isinstance(child, (list, dict)):
                    traverse(child, depth + 1)

    for block in parser.scripts[:10]:
        try:
            traverse(json.loads(block))
        except (TypeError, ValueError, RecursionError):
            continue
    return list(matches.values())


def read_changes(state, now, *, fetcher=fetch_html_page, force=False):
    """Read once per 20 hours by default; failures never erase scan history."""
    cached = state.get("oustreamer_observations") or {}
    try:
        checked = datetime.fromisoformat(
            str(cached.get("checked_at") or "").replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        checked = None
    if (not force and checked and checked.tzinfo
            and timedelta(0) <= now - checked < timedelta(hours=20)):
        return list(cached.get("changes") or [])

    try:
        page = fetcher(SOURCE_URL)
    except (OSError, ValueError, TimeoutError, UnicodeError):
        page = None
    changes = parse_oustreamer_changes(page["html"]) if page and page.get("html") else []
    state["oustreamer_observations"] = {
        "source": SOURCE_URL,
        "checked_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "status": "read" if page and page.get("html") else "unavailable",
        "changes": changes,
    }
    print("OÙSTREAMER :", len(changes), "changements explicites ;",
          state["oustreamer_observations"]["status"])
    return changes
