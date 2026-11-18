"""Republier les adresses actualisees dans les fils de forum Discord publics.

Le bot ne modifie jamais les messages de membres. Il gere une reponse
par fil et reutilise son identifiant, meme si une synchronisation echoue.
"""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from site_probe import clean_url, check_site
from name_probe import fetch_html_page
from forum_cards import (source_description, build_card, all_text, normalize,
                         is_own_card, card_digest, V2_FLAG)

MARKER = "[elikto-forum-suivi:v1]"
MIRROR_THREAD = "1430988094374547559"
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


def site_groups(records, forum_title):
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
        if key not in groups:
            groups[key] = {
                "url": url, "name": domain(url),
                "older": [], "announced": bool(successor),
                "status": rec.get("announced_status") if successor else rec.get("status"),
            }
        entry = groups[key]
        if successor:
            entry["url"] = successor
            entry["announced"] = True
            entry["status"] = rec.get("announced_status")
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
                    target = proposed
                    target_status = "online"
                    break
    # Si la destination est non vérifiée, montrer la page de référence,
    # jamais l'ancienne URL 404 comme si elle était encore valide.
    current = target or MOVIX_HUB
    return [{
        "url": current,
        "name": domain(current),
        "older": previous,
        "announced": bool(target),
        "status": target_status if target else hub_status,
        "directory_url": MOVIX_HUB if target else None,
    }]


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
            suffix = (" · Répond au contrôle HTTP" if group["status"] == "online"
                      else " · Accessibilité à confirmer")
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
        "previews_created": 0, "previews_updated": 0, "errors": [],
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
            groups = (validated_movix_groups(linked)
                      if is_movix_forum(info.get("title"))
                      else site_groups(linked, info.get("title") or "Site"))
            components = build_card(groups, original, info.get("title") or "Site")
            if not components:
                result["skipped"] += 1
                continue
            digest = card_digest(components)
            previous = tracked.get(tid, {})
            message_id, outcome = upsert_forum_update(
                api, tid, bot_id, components, previous
            )
            # Enregistrer le message immédiatement pour éviter une seconde
            # carte si Discord refuse temporairement l'aperçu.
            tracked[tid] = {**previous, "message_id": message_id, "digest": digest}
            result[outcome] += 1
            preview_url = clean_url(groups[0]["url"])
            if preview_url:
                # Le contenu URL seul déclenche l'aperçu natif de Discord
                # lorsque le site autorise son robot d'indexation.
                candidates = tuple(
                    clean_url(item.get("site_url"))
                    for item in linked if item.get("site_url")
                )
                preview_id, preview_outcome = upsert_preview(
                    api, tid, bot_id, preview_url, tracked[tid], candidates
                )
                tracked[tid]["preview_message_id"] = preview_id
                if preview_outcome != "unchanged":
                    result["previews_" + preview_outcome] += 1
        except (RuntimeError, ValueError) as exc:
            result["errors"].append({
                "thread_id": tid,
                "reason": str(exc).split("HTTP ")[-1][:85],
            })
    print("FORUMS : fiches stylisées et descriptions d'origine :", result)
    return result
