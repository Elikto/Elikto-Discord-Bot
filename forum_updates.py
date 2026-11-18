"""Republier les adresses actualisees dans les fils de forum Discord publics.

Le bot ne modifie jamais les messages de membres. Il gere une reponse
par fil et reutilise son identifiant, meme si une synchronisation echoue.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit
from site_probe import clean_url
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
        "descriptions_found": 0, "description_missing": 0, "errors": [],
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
            components = build_card(site_groups(linked, info.get("title") or "Site"), original)
            if not components:
                result["skipped"] += 1
                continue
            digest = card_digest(components)
            message_id, outcome = upsert_forum_update(
                api, tid, bot_id, components, tracked.get(tid, {})
            )
            tracked[tid] = {"message_id": message_id, "digest": digest}
            result[outcome] += 1
        except (RuntimeError, ValueError) as exc:
            result["errors"].append({
                "thread_id": tid,
                "reason": str(exc).split("HTTP ")[-1][:85],
            })
    print("FORUMS : fiches stylisées et descriptions d'origine :", result)
    return result
