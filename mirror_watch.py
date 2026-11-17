"""Suivi quotidien de toutes les URLs du fil Streaming HD miroir."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import os
import re

from site_probe import check_site, extract_candidate_urls, other_domain
from name_probe import probe_site_name, announced_rename
from site_monitor import event

THREAD_ID = "1430988094374547559"
GUILD_ID = "1430984645012357263"
MARKER = "[elikto-suivi-miroirs:v2]"
STATE_PATH = Path("data/monitor_state.json")
PARIS = ZoneInfo("Europe/Paris")

STATUS = {
    "online": "🟢 Accessible",
    "restricted": "🟠 Accès limité",
    "suspect": "🟡 À vérifier",
    "offline": "🔴 Inaccessible",
    "unknown": "⚪ Indéterminé",
    "pending": "⚪ En attente",
}
THREAD_TITLE = re.compile(r"(?im)^\s*(?:nouveau nom du fil|nouveau titre du fil)\s*[:=-]\s*(.+)$")


def is_checked_today(last, now):
    if not last:
        return False
    try:
        previous = datetime.fromisoformat(last.replace("Z", "+00:00"))
        # Deux jours calendaires peuvent se suivre à moins de deux heures.
        # Éviter de compter ces deux contrôles rapprochés comme indépendants.
        if now - previous < timedelta(hours=20):
            return True
        return previous.astimezone(PARIS).date() == now.astimezone(PARIS).date()
    except (TypeError, ValueError):
        return False


def label_from_source(message, url):
    for line in (message.get("content") or "").splitlines():
        if url not in extract_candidate_urls({"content": line}):
            continue
        prefix = re.split(r"https?://", line, maxsplit=1, flags=re.I)[0]
        prefix = prefix.strip(" \t\r*#_:•-–→|[]()")
        prefix = re.sub(r"^\d+[.)]\s*", "", prefix)
        if 2 <= len(prefix) <= 45 and not prefix.lower().startswith(
            ("adresse", "lien", "url", "nouveau domaine")
        ):
            return prefix
    return (urlsplit(url).hostname or url).removeprefix("www.")


def find_urls(messages, bot_id):
    """Inclut toutes les URLs des messages humains du fil, pas celles du bot."""
    links = {}
    for message in sorted(messages, key=lambda m: int(m["id"])):
        author = message.get("author") or {}
        if (str(author.get("id")) == bot_id or author.get("bot") or
                message.get("webhook_id")):
            continue
        for url in extract_candidate_urls(message):
            if url not in links:
                links[url] = {"url": url, "label": label_from_source(message, url)}
    return list(links.values())


def change_event(state, kind, item, now, before=None, after=None, detail=None):
    record = {"thread_id": THREAD_ID, "title": item["label"],
              "discord_url": f"https://discord.com/channels/{GUILD_ID}/{THREAD_ID}"}
    event(state, kind, record, now, previous=before,
          current=after, note=detail, alert=True)


def update_probe(item, observation, now, state, first_run=False):
    previous = item.get("status", "pending")
    previous_redirect = item.get("redirect_url")
    result = observation.get("result", "unknown")
    if result in ("online", "restricted"):
        item["status"] = result
        item["failures"] = 0
    elif result == "failed":
        item["failures"] = int(item.get("failures", 0)) + 1
        item["status"] = "offline" if item["failures"] >= 2 else "suspect"
    else:
        item["status"], item["failures"] = "unknown", 0
    item["http_status"] = observation.get("http_status")
    item["last_checked"] = now.isoformat(timespec="seconds")
    item["reason"] = observation.get("reason")
    item["final_url"] = observation.get("final_url")
    final = observation.get("final_url")
    item["redirect_url"] = final if other_domain(item["url"], final) else None

    if first_run:
        return
    if item["redirect_url"] and item["redirect_url"] != previous_redirect:
        change_event(state, "mirror_redirect", item, now,
                     item["url"], item["redirect_url"],
                     "Domaine final différent ; ne pas remplacer sans confirmation.")
    if previous != "offline" and item["status"] == "offline":
        change_event(state, "mirror_offline", item, now,
                     previous, item["url"], "Deux vérifications sur des jours différents ont échoué.")
    if previous == "offline" and item["status"] in ("online", "restricted"):
        change_event(state, "mirror_recovered", item, now,
                     previous, item["status"], "Le serveur répond à nouveau.")


def identity_key(name):
    """Comparer le nom sans majuscules, accents ou ponctuation."""
    import unicodedata
    value = unicodedata.normalize("NFKD", name or "")
    return re.sub(r"[\W_]+", "", "".join(
        ch for ch in value if not unicodedata.combining(ch)
    ).casefold())


def migrate_legacy_title(item, now, state, first_run=False):
    """Corriger les noms déjà enregistrés comme phrase 'X devient Y.com'.

    Ne pas considérer toute la phrase comme un nom : enregistrer X comme
    ancien nom connu et Y comme nouveau nom annoncé à confirmer.
    """
    announcement = announced_rename(item.get("site_name"))
    if not announcement:
        return False
    previous = announcement["previous_name"]
    proposed = announcement["name"]
    item["site_name"] = previous
    item["pending_name"] = proposed
    item["pending_seen_at"] = now.isoformat(timespec="seconds")
    item["pending_count"] = 1
    item["announced_url"] = announcement.get("announced_url")
    item["name_evidence"] = announcement["evidence"]
    if not first_run:
        change_event(state, "mirror_name_suspected", item, now,
                     before=previous, after=proposed,
                     detail="La page du site annonce un nouveau nom ou domaine ; à confirmer.")
    return True


def update_name(item, identity, now, state, first_run=False):
    """Une différence est suspecte au premier jour, confirmée au deuxième.

    Si la page est inaccessible, protégée ou ne fournit aucun nom crédible,
    le dernier nom fiable reste inchangé.
    """
    if not identity or not identity.get("name"):
        return
    detected = identity["name"][:58]
    item["name_last_checked"] = now.isoformat(timespec="seconds")
    item["name_evidence"] = identity.get("evidence", "html")
    if identity.get("announced_url"):
        item["announced_url"] = identity["announced_url"]
    official = item.get("site_name")
    if not official:
        if identity.get("previous_name"):
            previous = identity["previous_name"]
            item["site_name"] = previous
            item["pending_name"] = detected
            item["pending_seen_at"] = now.isoformat(timespec="seconds")
            item["pending_count"] = 1
            if not first_run:
                change_event(state, "mirror_name_suspected", item, now,
                             before=previous, after=detected,
                             detail="Le site annonce explicitement un changement de nom ; à confirmer.")
        else:
            item["site_name"] = detected
            item["pending_name"] = None
            item["pending_seen_at"] = None
            item["pending_count"] = 0
        return
    if identity_key(detected) == identity_key(official):
        item["pending_name"] = None
        item["pending_seen_at"] = None
        item["pending_count"] = 0
        return

    old_candidate = item.get("pending_name")
    candidate_same = identity_key(old_candidate) == identity_key(detected)
    previous_at = item.get("pending_seen_at")
    distinct_day = not is_checked_today(previous_at, now)

    if not candidate_same:
        item["pending_name"] = detected
        item["pending_seen_at"] = now.isoformat(timespec="seconds")
        item["pending_count"] = 1
        if not first_run:
            change_event(state, "mirror_name_suspected", item, now,
                         before=official, after=detected,
                         detail="Autre nom détecté sur le site : confirmation nécessaire au prochain contrôle.")
        return

    if not distinct_day:
        return
    item["pending_seen_at"] = now.isoformat(timespec="seconds")
    item["pending_count"] = int(item.get("pending_count") or 1) + 1
    if item["pending_count"] < 2:
        return

    item["site_name"] = detected
    item["pending_name"] = None
    item["pending_seen_at"] = None
    item["pending_count"] = 0
    if not first_run:
        change_event(state, "mirror_name_changed", item, now,
                     before=official, after=detected,
                     detail="Nouveau nom confirmé par deux contrôles indépendants du site.")


def summary_message(items, thread_name):
    lines = [
        f"🔎 **SUIVI DES SITES — {thread_name[:60].upper()}**",
        MARKER,
        f"**{len(items)} adresses** · vérification **quotidienne** · "
        f"**{sum(bool(i.get('site_name')) for i in items)} noms reconnus**",
        "",
    ]
    last = []
    for index, item in enumerate(items, 1):
        label = str(item.get("site_name") or item.get("label") or urlsplit(item["url"]).hostname)
        label = label.replace("@", "@\u200b")
        label_status = STATUS.get(item.get("status"), STATUS["unknown"])
        lines.append(f"**{index}. {label[:48]}** — {label_status}")
        code = f" · HTTP {item['http_status']}" if item.get("http_status") else ""
        checked = item.get("last_checked")
        moment = (f" · <t:{int(datetime.fromisoformat(checked).timestamp())}:R>"
                  if checked else " · non contrôlée")
        lines.append(f"   {item['url']}{code}{moment}")
        if checked:
            last.append(datetime.fromisoformat(checked).timestamp())
        if item.get("pending_name"):
            suspected = str(item["pending_name"]).replace("@", "@\u200b")
            lines.append(f"   🟡 Nouveau nom annoncé : {suspected[:50]} (à confirmer)")
            if item.get("announced_url"):
                lines.append(f"   🌐 Domaine annoncé : {item['announced_url']}")
        if item.get("redirect_url"):
            lines.append(f"   ↪ {item['redirect_url']} (changement à confirmer)")
    if last:
        lines.extend(["", f"🕒 Dernier contrôle : <t:{int(max(last))}:f>"])
    lines.append("ℹ️ Une réponse HTTP ne garantit pas que le site fonctionne entièrement.")
    content = "\n".join(lines)
    if len(content) > 1950:
        raise ValueError("Trop de liens pour un message Discord ; aucune URL ne sera masquée.")
    return content


def upsert_bot_message(api, tracked, bot_id, content):
    known = tracked.get("message_id")
    if known:
        try:
            old = api.call("GET", f"/channels/{THREAD_ID}/messages/{known}")
            if str(old.get("author", {}).get("id")) == bot_id:
                if old.get("content") != content:
                    api.call("PATCH", f"/channels/{THREAD_ID}/messages/{known}",
                             json={"content": content, "allowed_mentions": {"parse": []}})
                return str(known)
        except RuntimeError:
            pass
    messages = api.call("GET", f"/channels/{THREAD_ID}/messages", params={"limit": 100})
    for msg in messages:
        if str(msg.get("author", {}).get("id")) != bot_id:
            continue
        if (MARKER in (msg.get("content") or "") or
                "[elikto-suivi-fil:v1]" in (msg.get("content") or "")):
            if msg.get("content") != content:
                api.call("PATCH", f"/channels/{THREAD_ID}/messages/{msg['id']}",
                         json={"content": content, "allowed_mentions": {"parse": []}})
            return str(msg["id"])
    created = api.call("POST", f"/channels/{THREAD_ID}/messages",
                       json={"content": content, "allowed_mentions": {"parse": []}})
    return str(created["id"])


def sync_thread(api, state, now=None):
    now = now or datetime.now(timezone.utc)
    thread = api.call("GET", f"/channels/{THREAD_ID}")
    if str(thread.get("guild_id")) != GUILD_ID or thread.get("type") != 11:
        raise ValueError("Fil public attendu sur le serveur")
    bot_id = str(api.call("GET", "/users/@me")["id"])
    messages = api.call("GET", f"/channels/{THREAD_ID}/messages", params={"limit": 100})
    if not any(m.get("content") or m.get("embeds") for m in messages
               if str(m.get("author", {}).get("id")) != bot_id):
        print("MIROIRS : contenu source inaccessible.")
        return "blocked"
    sources = find_urls(messages, bot_id)
    print("MIROIRS : liens distincts détectés =", len(sources))
    if not sources:
        return "no_urls"

    tracked = state.setdefault("tracked_threads", {}).setdefault(THREAD_ID, {})
    entries = tracked.setdefault("url_states", {})
    is_first = not entries
    before = set(entries)
    observed = {s["url"] for s in sources}

    for source in sources:
        url = source["url"]
        if url not in entries:
            entries[url] = {
                "url": url, "label": source["label"],
                "status": "pending", "last_checked": None,
                "http_status": None, "failures": 0,
                "reason": None, "redirect_url": None, "final_url": None,
            }
            if not is_first:
                change_event(state, "mirror_added", entries[url], now,
                             after=url, detail="Nouvelle URL détectée dans le fil.")
        elif source["label"] != entries[url].get("label"):
            old_label = entries[url].get("label")
            entries[url]["label"] = source["label"]
            if old_label and not is_first:
                change_event(state, "mirror_renamed", entries[url], now,
                             before=old_label, after=source["label"],
                             detail="Libellé de l'adresse modifié dans le fil.")
    for removed in before - observed:
        old = entries.pop(removed)
        if not is_first:
            change_event(state, "mirror_removed", old, now,
                         before=removed, after="retirée",
                         detail="URL absente des messages du fil.")

    checked = 0
    names_checked = 0
    for source in sources:
        item = entries[source["url"]]
        migrate_legacy_title(item, now, state, first_run=is_first)
        if not is_checked_today(item.get("last_checked"), now):
            update_probe(item, check_site(item["url"]), now, state, first_run=is_first)
            checked += 1
        # Analyser les noms dès l'installation sans refaire les tests HTTP
        # déjà effectués dans la journée.
        if (item.get("status") == "online" and
                not is_checked_today(item.get("name_attempted_at"), now)):
            item["name_attempted_at"] = now.isoformat(timespec="seconds")
            identity = probe_site_name(item["url"])
            update_name(item, identity, now, state, first_run=is_first)
            names_checked += 1

    owner = str(thread.get("owner_id") or "")
    proposed_names = []
    for msg in sorted(messages, key=lambda m: int(m["id"])):
        if str(msg.get("author", {}).get("id")) == owner:
            proposed_names.extend(THREAD_TITLE.findall(msg.get("content") or ""))
    thread_name = str(thread.get("name") or "Streaming HD miroir")
    if proposed_names:
        proposed = proposed_names[-1].strip(" *:'-")
        if (2 <= len(proposed) <= 80 and
                not any(term in proposed.lower() for term in ("http:", "https:", "@everyone", "<@"))):
            if proposed != thread_name:
                try:
                    api.call("PATCH", f"/channels/{THREAD_ID}", json={"name": proposed})
                    thread_name = proposed
                except RuntimeError:
                    print("MIROIRS : impossible de renommer le fil (permission requise).")

    summary = summary_message([entries[source["url"]] for source in sources], thread_name)
    try:
        message_id = upsert_bot_message(api, tracked, bot_id, summary)
    except RuntimeError:
        print("MIROIRS : autorisation d'écrire dans le fil manquante.")
        return "permission_denied"
    tracked.update({
        "name": thread_name,
        "message_id": message_id,
        "urls": [source["url"] for source in sources],
        "last_synced": now.isoformat(timespec="seconds"),
    })
    known_names = sum(bool(entries[source["url"]].get("site_name")) for source in sources)
    suspected_names = sum(bool(entries[source["url"]].get("pending_name")) for source in sources)
    print("MIROIRS :", len(sources), "URL visibles ;", checked,
          "vérifications HTTP ;", names_checked, "recherches de nom ;",
          known_names, "noms identifiés ;",
          suspected_names, "changements de nom suspects.")
    return "updated"


def main():
    from sync_catalogue import DiscordAPI
    from site_monitor import notify_discord
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    before = len(state.get("events", []))
    api = DiscordAPI(os.environ["DISCORD_BOT_TOKEN"])
    result = sync_thread(api, state)
    if result == "updated":
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        alerts = [e for e in state.get("events", [])[before:] if e.get("alert")]
        if alerts:
            notify_discord(api, GUILD_ID, alerts)
    print("MIROIRS : suivi =", result)


if __name__ == "__main__":
    main()
