"""Republier les adresses actualisees dans les fils de forum Discord publics.

Le bot ne modifie jamais les messages de membres. Il gere une reponse
par fil et reutilise son identifiant, meme si une synchronisation echoue.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit
from site_probe import clean_url

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
                "url": url, "name": safe_label(rec.get("site_name") or forum_title)
                if successor else safe_label(rec.get("site_name") or domain(original)),
                "older": [], "announced": bool(successor),
                "status": rec.get("announced_status") if successor else rec.get("status"),
            }
        entry = groups[key]
        if successor:
            entry["url"] = successor
            entry["announced"] = True
            entry["status"] = rec.get("announced_status")
            entry["name"] = safe_label(rec.get("site_name") or forum_title)
            for old in [original, *(rec.get("other_previous_urls") or [])]:
                older = domain(old)
                if older and older != key and older not in entry["older"]:
                    entry["older"].append(older)
    return sorted(groups.values(), key=lambda item: (not item["announced"], item["url"]))


def render_forum_update(title, records):
    groups = site_groups(records, title)
    lines = ["### 🔄 Adresses mises à jour · " + safe_label(title), ""]
    for group in groups:
        name = group["name"] or domain(group["url"]).upper()
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
    return str((msg.get("author") or {}).get("id")) == bot_id and MARKER in (
        msg.get("content") or ""
    )


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


def upsert_forum_update(api, tid, bot_id, content, tracking):
    old = find_existing(api, tid, bot_id, tracking.get("message_id"))
    if old:
        if old.get("content") != content:
            api.call("PATCH", f"/channels/{tid}/messages/{old['id']}",
                     json={"content": content, "allowed_mentions": {"parse": []}})
            return str(old["id"]), "updated"
        return str(old["id"]), "unchanged"
    msg = api.call("POST", f"/channels/{tid}/messages",
                   json={"content": content, "allowed_mentions": {"parse": []}})
    return str(msg["id"]), "created"


def sync_forum_updates(api, state, changed_events=()):
    """Mettre a jour uniquement les forums publics touches par un changement."""
    forums = {str(i): v for i, v in state.get("records", {}).items()
              if str(i) != MIRROR_THREAD}
    by_forum = {}
    for record in state.get("server_links", {}).values():
        for tid in forum_sources(record, forums):
            by_forum.setdefault(tid, []).append(record)

    changed = {str(e.get("thread_id")) for e in changed_events
               if e.get("type") in CHANGE_EVENTS}
    tracked = state.setdefault("forum_updates", {})
    targets = {
        tid for tid, records in by_forum.items()
        if any(rec.get("announced_url") for rec in records)
        or tid in changed or tid in tracked
    }
    if not targets:
        return {"created": 0, "updated": 0, "unchanged": 0, "errors": []}
    bot_id = str(api.call("GET", "/users/@me")["id"])
    results = {"created": 0, "updated": 0, "unchanged": 0, "errors": []}
    for tid in sorted(targets):
        message = render_forum_update(forums[tid].get("title") or "Site",
                                      by_forum.get(tid, []))
        previous = tracked.get(tid, {})
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
        if previous.get("digest") == digest and previous.get("message_id"):
            results["unchanged"] += 1
            continue
        try:
            mid, outcome = upsert_forum_update(
                api, tid, bot_id, message, previous
            )
        except RuntimeError as exc:
            results["errors"].append({
                "thread_id": tid, "reason": str(exc).split("HTTP ")[-1][:55]
            })
            continue
        tracked[tid] = {"message_id": mid, "digest": digest}
        results[outcome] += 1
    print("FORUMS : resultats des messages actualises :", results)
    return results
