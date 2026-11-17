"""Suivi du fil Streaming HD miroir, sans editer les messages de tiers.

Un message unique du bot affiche le nom et l'adresse verifies dans les
messages de l'auteur du fil. Les changements explicites de nom permettent
un renommage du fil si le bot dispose de MANAGE_THREADS.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from site_probe import extract_candidate_urls, other_domain

THREAD_ID = "1430988094374547559"
GUILD_ID = "1430984645012357263"
STATE_PATH = Path("data/monitor_state.json")
MARKER = "[elikto-suivi-fil:v1]"
NAME_PATTERN = re.compile(
    r"(?im)^\s*(?:\*{0,2})(?:nouveau\s+nom|nouveau\s+titre|nom\s+actuel|renomm[ée]\s+en)"
    r"(?:\*{0,2})\s*[:=-]\s*(.+)$"
)
ADDRESS_PATTERN = re.compile(
    r"(?im)^\s*(?:\*{0,2})(?:nouvelle?\s+(?:adresse|url|lien)|"
    r"adresse\s+actuelle|nouveau\s+domaine)(?:\*{0,2})\s*[:=-]\s*(.+)$"
)


def safe_name(content):
    matches = NAME_PATTERN.findall(content or "")
    if not matches:
        return None
    title = matches[-1].strip(" *_'\":-")
    if not (2 <= len(title) <= 85) or any(x in title.lower() for x in (
        "http://", "https://", "@everyone", "@here", "<@", "\n")):
        return None
    return title


def source_info(messages):
    """Ne choisir automatiquement qu'un lien explicite ou un domaine unique."""
    all_links, declared_links, declared_names = [], [], []
    for message in sorted(messages, key=lambda m: int(m["id"])):
        content = message.get("content") or ""
        all_links.extend(extract_candidate_urls(message))
        name = safe_name(content)
        if name:
            declared_names.append(name)
        for line in ADDRESS_PATTERN.findall(content):
            declared_links.extend(extract_candidate_urls({"content": line}))
    title = declared_names[-1] if declared_names else None
    if declared_links:
        return title, declared_links[-1], "annonce explicite de l'auteur"
    unique = list(dict.fromkeys(all_links))
    if len(unique) == 1:
        return title, unique[0], "adresse unique du fil"
    if len(unique) > 1 and len({
        (urlsplit(x).hostname or "").removeprefix("www.") for x in unique
    }) == 1:
        return title, unique[-1], "domaine unique du fil"
    return title, None, "plusieurs adresses ambiguës" if unique else "aucune adresse lisible"


def render_message(name, url, source, *, redirect=None, now=None):
    now = now or datetime.now(timezone.utc)
    lines = [
        "🔄 **SUIVI AUTOMATIQUE — STREAMING HD MIROIR**",
        MARKER,
        "**Nom actuel :** " + name,
        "**Adresse actuelle :** " + url,
        "**Source :** " + source,
    ]
    if redirect and other_domain(url, redirect):
        lines.append(
            "🔀 **Redirection vers un autre domaine :** " + redirect +
            " — à vérifier avant de remplacer l'adresse officielle."
        )
    lines.extend([
        "**Dernière modification :** <t:" + str(int(now.timestamp())) + ":f>",
        "Ce message est actualisé par le bot. Les messages des membres restent inchangés.",
    ])
    return "\n".join(lines)[:1900]


def upsert_bot_message(api, state, bot_id, content):
    mid = state.get("message_id")
    if mid:
        try:
            old = api.call("GET", f"/channels/{THREAD_ID}/messages/{mid}")
            if str(old.get("author", {}).get("id")) == bot_id:
                if old.get("content") != content:
                    api.call("PATCH", f"/channels/{THREAD_ID}/messages/{mid}",
                             json={"content": content, "allowed_mentions": {"parse": []}})
                return str(mid)
        except RuntimeError:
            pass

    # Deduplication au cas ou le commit de suivi a echoue apres le POST.
    messages = api.call("GET", f"/channels/{THREAD_ID}/messages", params={"limit": 100})
    for m in messages:
        if str(m.get("author", {}).get("id")) == bot_id and MARKER in (m.get("content") or ""):
            if m.get("content") != content:
                api.call("PATCH", f"/channels/{THREAD_ID}/messages/{m['id']}",
                         json={"content": content, "allowed_mentions": {"parse": []}})
            return str(m["id"])
    posted = api.call("POST", f"/channels/{THREAD_ID}/messages",
                      json={"content": content, "allowed_mentions": {"parse": []}})
    return str(posted["id"])


def sync_thread(api, state, now=None):
    now = now or datetime.now(timezone.utc)
    thread = api.call("GET", f"/channels/{THREAD_ID}")
    if str(thread.get("guild_id")) != GUILD_ID or thread.get("type") != 11:
        raise ValueError("Cible invalide : le fil doit etre public et dans le bon serveur")
    owner_id = str(thread.get("owner_id") or "")
    if not owner_id:
        raise ValueError("Impossible d'identifier l'auteur du fil")
    messages = api.call("GET", f"/channels/{THREAD_ID}/messages", params={"limit": 100})
    trusted = sorted(
        [m for m in messages if str(m.get("author", {}).get("id")) == owner_id
         and not m.get("webhook_id")],
        key=lambda m: int(m["id"])
    )
    if not any(m.get("content") or m.get("embeds") for m in trusted):
        print("SUIVI CIBLE : contenu masque par Discord ; aucun changement publie.")
        return "blocked"

    suggested_name, address, source = source_info(trusted)
    if not address:
        print("SUIVI CIBLE : adresse absente ou ambiguë ; aucune correction.")
        return "ambiguous"

    tracked = state.setdefault("tracked_threads", {}).setdefault(THREAD_ID, {})
    original_name = str(thread.get("name") or "Streaming HD miroir")
    current_name = original_name
    if suggested_name and suggested_name != current_name:
        try:
            api.call("PATCH", f"/channels/{THREAD_ID}", json={"name": suggested_name})
            print("SUIVI CIBLE : nom du fil mis a jour depuis l'annonce de son auteur.")
        except RuntimeError:
            print("SUIVI CIBLE : Gérer les fils requis pour renommer ; message seulement.")
        current_name = suggested_name

    prior_name, prior_address = tracked.get("name"), tracked.get("address")
    changed_name = bool(prior_name and prior_name != current_name)
    changed_address = bool(prior_address and prior_address != address)
    last_modification = (
        now if (changed_name or changed_address or not tracked.get("last_modified"))
        else datetime.fromisoformat(tracked["last_modified"])
    )
    redirect = (state.get("records", {}).get(THREAD_ID) or {}).get("redirect_url")
    content = render_message(current_name, address, source, redirect=redirect,
                             now=last_modification)

    bot_id = str(api.call("GET", "/users/@me")["id"])
    try:
        mid = upsert_bot_message(api, tracked, bot_id, content)
    except RuntimeError:
        print("SUIVI CIBLE : ecriture impossible dans le fil ; verifier les permissions.")
        return "permission_denied"

    tracked.update({
        "message_id": mid, "name": current_name, "address": address,
        "last_modified": last_modification.isoformat(timespec="seconds"),
        "last_synced": now.isoformat(timespec="seconds"), "source": source,
    })

    # Journaliser seulement les modifications posteriores a la premiere lecture.
    if changed_name or changed_address:
        from site_monitor import event
        record = {
            "thread_id": THREAD_ID, "title": current_name,
            "discord_url": f"https://discord.com/channels/{GUILD_ID}/{THREAD_ID}"
        }
        if changed_name:
            event(state, "title_changed", record, now,
                  previous=prior_name, current=current_name,
                  note="Nom annonce par l'auteur du fil et reporte dans le suivi.", alert=True)
        if changed_address:
            event(state, "address_changed", record, now,
                  previous=prior_address, current=address,
                  note="Adresse annoncee par l'auteur du fil et reportee dans le suivi.", alert=True)
    print("SUIVI CIBLE : message gere par le bot ; changement =", changed_name or changed_address)
    return "updated"


def main():
    from sync_catalogue import DiscordAPI
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    outcome = sync_thread(DiscordAPI(os.environ["DISCORD_BOT_TOKEN"]), state)
    if outcome == "updated":
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print("Suivi du fil :", outcome)


if __name__ == "__main__":
    main()
