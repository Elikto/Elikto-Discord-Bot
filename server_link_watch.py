"""Contrôle quotidien de TOUS les sites liés dans les espaces publics du serveur.

Parcourt les messages paginés des salons textuels et d'annonces, les forums,
les médias, tous les fils publics actifs et archives. Chaque URL est dédupliquée
sur l'ensemble du serveur, vérifiée une fois par jour et historisée.

Ne publie aucune donnée provenant de canaux privés, NSFW ou messages de bots.
Ne copie jamais le texte des messages, les tokens ou les paramètres d'URL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo
from collections import defaultdict
import json
import os
import re

from site_probe import RE_URL, clean_url, check_site
from name_probe import probe_site_name, probe_site_migration
from forum_updates import sync_forum_updates
from site_monitor import (
    STATE_PATH, load_state, event, record_check, notify_discord, stamp,
)
from sync_catalogue import (
    DiscordAPI, GUILD_ID, CHANNEL_CATEGORY, VIEW_CHANNEL, public_channel,
)

PARIS = ZoneInfo("Europe/Paris")
CHANNEL_TEXT = 0
CHANNEL_ANNOUNCEMENT = 5
CHANNEL_FORUM = 15
CHANNEL_MEDIA = 16
PUBLIC_THREAD_TYPES = {10, 11}
PUBLIC_CHANNEL_TYPES = {CHANNEL_TEXT, CHANNEL_ANNOUNCEMENT, CHANNEL_FORUM, CHANNEL_MEDIA}
BOT_CATALOGUE = "1572066782041084045"
INTERNAL_HOSTS = {
    "discord.com", "discord.gg", "discordapp.com",
    "cdn.discordapp.com", "media.discordapp.net", "discord.new",
}
EXCLUDE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".mp4", ".mov", ".mp3", ".webm", ".pdf", ".zip",
)
# Naked domains are supported too, even without https://.
BARE_DOMAIN = re.compile(
    r"(?<![@\w./:-])(?:[a-z0-9][a-z0-9-]{0,62}\.)+"
    r"[a-z]{2,24}(?:/[\w.%~!$&'()+,;=:@/-]*)?(?![\w.-])",
    re.I,
)


# Observations fournies par des captures d'écran (17/11/2026).
# Ces annonces ne prouvent ni l'identité juridique ni la sécurité du domaine.
# La détection HTML prend le relais lors des prochains changements.
OBSERVED_MIGRATIONS = {
    "https://webflix.art/": {
        "announced_url": "https://webflix.surf/",
        "evidence": "captures:2026-11-17",
        "other_previous": ["https://webflix.bio/"],
    },
    "https://webflix.bio/": {
        "announced_url": "https://webflix.surf/",
        "evidence": "captures:2026-11-17",
    },
}


def record_migration(state, rec, observation, now, *, first_run):
    """Historiser un changement annoncé sans remplacer l'ancienne URL source."""
    announced = clean_url((observation or {}).get("announced_url"))
    if not announced or announced == rec["site_url"]:
        return False
    previous = rec.get("announced_url")
    if previous == announced:
        return False
    rec["announced_url"] = announced
    rec["migration_evidence"] = observation.get("evidence", "html:migration_notice")
    rec["migration_observed_at"] = stamp(now)
    if observation.get("other_previous"):
        rec["other_previous_urls"] = observation["other_previous"]
    # Une annonce se rapporte au site, pas à une redirection HTTP prouvée.
    event(state, "server_migration_announced", rec, now,
          previous=previous or rec["site_url"], current=announced,
          note="Nouvelle adresse affichée sur le site, à vérifier indépendamment.",
          alert=not first_run)
    return True


def due_daily(last: str | None, now: datetime) -> bool:
    """Un contrôle par jour civil (France), avec 20 h minimum entre mesures."""
    if not last:
        return True
    try:
        then = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if then.tzinfo is None:
            return True
        if now - then < timedelta(hours=20):
            return False
        return then.astimezone(PARIS).date() != now.astimezone(PARIS).date()
    except (ValueError, TypeError):
        return True


def public_source(channel, everyone_permissions, categories):
    if channel.get("type") not in PUBLIC_CHANNEL_TYPES:
        return False
    copy = dict(channel)
    # La fonction centralisée calcule les permissions des salons texte/forum.
    if copy["type"] == CHANNEL_ANNOUNCEMENT:
        copy["type"] = CHANNEL_TEXT
    elif copy["type"] == CHANNEL_MEDIA:
        copy["type"] = CHANNEL_FORUM
    return public_channel(copy, everyone_permissions, categories, GUILD_ID)


def public_thread_locations(api):
    """Tous les parents publics et leurs fils, archives incluses."""
    guild = api.call("GET", f"/guilds/{GUILD_ID}")
    everyone = next(r for r in guild["roles"] if str(r["id"]) == GUILD_ID)
    permissions = int(everyone["permissions"])
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    categories = {str(c["id"]): c for c in channels if c["type"] == CHANNEL_CATEGORY}
    excluded = {
        BOT_CATALOGUE,
        *(s.strip() for s in os.getenv("EXCLUDED_CHANNEL_IDS", "").split(",") if s.strip()),
    }
    parents = {
        str(c["id"]): c for c in channels
        if str(c["id"]) not in excluded
        and public_source(c, permissions, categories)
    }
    active = api.call("GET", f"/guilds/{GUILD_ID}/threads/active").get("threads", [])
    threads = {}
    failures = []
    for thread in active:
        tid = str(thread.get("id"))
        if (thread.get("type") in PUBLIC_THREAD_TYPES
                and str(thread.get("parent_id")) in parents
                and not thread.get("nsfw")):
            threads[tid] = thread
    for pid, parent in parents.items():
        try:
            archived = api.archived(pid)
            for thread in archived:
                if (thread.get("type") in PUBLIC_THREAD_TYPES
                        and not thread.get("nsfw")):
                    threads[str(thread["id"])] = thread
        except RuntimeError as exc:
            # L'absence de Manage Threads peut bloquer les archives.
            failures.append({
                "channel_id": pid, "reason": "archives_inaccessibles",
                "detail": str(exc).split("HTTP ")[-1][:45],
            })
    targets = [
        {"id": pid, "name": p["name"], "kind": "salon", "parent_id": pid}
        for pid, p in parents.items()
        if p["type"] in (CHANNEL_TEXT, CHANNEL_ANNOUNCEMENT)
    ]
    targets.extend({
        "id": tid, "name": t.get("name") or tid,
        "kind": "fil", "parent_id": str(t.get("parent_id")),
    } for tid, t in threads.items())
    return targets, failures


def all_messages(api, channel_id):
    """Lire toute l'histoire, pagination 100 messages jusqu'à épuisement."""
    before = None
    seen = set()
    for _ in range(3000):
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = api.call("GET", f"/channels/{channel_id}/messages", params=params)
        if not batch:
            return
        for msg in batch:
            mid = str(msg.get("id", ""))
            if mid not in seen:
                yield msg
                seen.add(mid)
        cursor = str(batch[-1].get("id"))
        if cursor in (before, "", "None"):
            raise RuntimeError("Pagination des messages bloquée dans " + channel_id)
        before = cursor
        if len(batch) < 100:
            return
    raise RuntimeError("Plus de 300000 messages dans " + channel_id + " : couverture incomplète")


def message_fragments(message):
    yield str(message.get("content") or "")
    for embed in message.get("embeds") or []:
        for k in ("url", "description", "title"):
            yield str(embed.get(k) or "")
        for field in embed.get("fields") or []:
            yield str(field.get("value") or "")
    def visit_component(part):
        if not isinstance(part, dict):
            return
        for field in ("url", "content"):
            if part.get(field):
                yield str(part[field])
        for nested in part.get("components") or []:
            yield from visit_component(nested)
    for comp in message.get("components") or []:
        yield from visit_component(comp)


def normalize_site_url(value):
    url = clean_url(value)
    if not url:
        return None
    p = urlsplit(url)
    hostname = (p.hostname or "").removeprefix("www.")
    return urlunsplit((p.scheme, hostname, p.path or "/", "", ""))


def link_from_text(fragment):
    found = set()
    for raw in RE_URL.findall(fragment):
        value = normalize_site_url(raw.rstrip(".,;!?<>)]}\"'"))
        if value:
            found.add(value)
            yield value
    remaining = RE_URL.sub(" ", fragment)
    for match in BARE_DOMAIN.finditer(remaining):
        value = normalize_site_url("https://" + match.group(0).rstrip(".,;!?<>)]}\"'"))
        if value and value not in found:
            found.add(value)
            yield value


def extract_links(message):
    """Tous les liens web, même les URLs sans schéma et les boutons d'URL."""
    seen = set()
    for fragment in message_fragments(message):
        for url in link_from_text(fragment):
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            if (host in INTERNAL_HOSTS or host.endswith(".discord.com")
                    or urlsplit(url).path.lower().endswith(EXCLUDE_EXTENSIONS)):
                continue
            if url not in seen:
                seen.add(url)
                yield url


def find_server_links(api, *, existing=None):
    """Une analyse exhaustive, sans cacher les erreurs de permissions."""
    bot_id = str(api.call("GET", "/users/@me")["id"])
    targets, failures = public_thread_locations(api)
    seen_urls = {}
    counts = defaultdict(int)
    for target in targets:
        cid = target["id"]
        try:
            for message in all_messages(api, cid):
                counts["messages_read"] += 1
                author = message.get("author") or {}
                if str(author.get("id")) == bot_id:
                    continue
                for url in extract_links(message):
                    rec = seen_urls.setdefault(url, {
                        "url": url, "source_count": 0, "sources": [],
                    })
                    rec["source_count"] += 1
                    if len(rec["sources"]) < 6:
                        rec["sources"].append({
                            "url": (
                                f"https://discord.com/channels/{GUILD_ID}/"
                                f"{cid}/{message['id']}"
                            ),
                            "channel": target["name"],
                            "kind": target["kind"],
                        })
                    counts["links_found"] += 1
            counts["locations_scanned"] += 1
        except RuntimeError as exc:
            failures.append({
                "channel_id": cid, "reason": "messages_inaccessibles",
                "detail": str(exc).split("HTTP ")[-1][:45],
            })
    counts["locations_discovered"] = len(targets)
    counts["unique_urls"] = len(seen_urls)
    counts["scan_failures"] = len(failures)
    return seen_urls, dict(counts), failures


def update_identity(state, record, observation, now, *, first_run):
    if not observation or not observation.get("name"):
        return
    name = str(observation["name"])[:58]
    previous = record.get("site_name")
    record["name_checked_at"] = stamp(now)
    record["name_evidence"] = observation.get("evidence")
    if observation.get("announced_url"):
        record["announced_url"] = observation["announced_url"]
    if not previous:
        record["site_name"] = observation.get("previous_name") or name
        previous = record["site_name"]
    if previous.casefold() == name.casefold():
        record["pending_name"] = None
        record["pending_name_at"] = None
        return
    if (record.get("pending_name") or "").casefold() != name.casefold():
        record["pending_name"] = name
        record["pending_name_at"] = stamp(now)
        if not first_run:
            event(state, "server_name_suspected", record, now,
                  previous=previous, current=name, alert=True,
                  note="Nouveau nom observé dans la page ; confirmation quotidienne nécessaire.")
        return
    if due_daily(record.get("pending_name_at"), now):
        record["site_name"] = name
        record["pending_name"] = None
        record["pending_name_at"] = None
        event(state, "server_name_changed", record, now,
              previous=previous, current=name, alert=True,
              note="Renommage confirmé sur deux journées de contrôle.")


def watch_server_links(api, state, now=None, *, discover=find_server_links):
    now = now or datetime.now(timezone.utc)
    started = len(state.get("events", []))
    discovered, scan_counts, errors = discover(api)
    records = state.setdefault("server_links", {})
    first_run = not records
    seen = set(discovered)

    for url, found in discovered.items():
        rec = records.get(url)
        if rec is None:
            rec = {
                "thread_id": found["sources"][0]["url"].split("/")[-2],
                "title": urlsplit(url).hostname or url,
                "discord_url": found["sources"][0]["url"],
                "site_url": url, "first_seen": stamp(now),
                "status": "pending", "last_checked": None,
                "consecutive_failures": 0,
            }
            records[url] = rec
            if not first_run:
                event(state, "server_site_added", rec, now,
                      current=url, alert=True,
                      note="Lien de site découvert dans un message public du serveur.")
        rec["source_count"] = found["source_count"]
        rec["sources"] = found["sources"]
        rec["last_seen"] = stamp(now)
        rec["active"] = True

    # Ne retirer un site de l'inventaire qu'après un scan entièrement réussi.
    if not errors:
        for url, rec in records.items():
            if url not in seen and rec.get("active"):
                rec["active"] = False
                event(state, "server_site_removed", rec, now,
                      previous=url, current="retiré", alert=True,
                      note="Site non retrouvé dans les messages publics du serveur.")
    # Si une lecture a échoué, préserver toutes les observations déjà connues.
    checked = 0
    identity_checks = 0
    migration_checks = 0
    new_address_checks = {}
    for rec in records.values():
        if not rec.get("active"):
            continue
        if due_daily(rec.get("last_checked"), now):
            observation = check_site(rec["site_url"])
            record_check(state, rec, observation, now)
            checked += 1
        if rec.get("status") == "online" and due_daily(rec.get("name_attempted_at"), now):
            rec["name_attempted_at"] = stamp(now)
            identity = probe_site_name(rec["site_url"])
            update_identity(state, rec, identity, now, first_run=first_run)
            identity_checks += 1

        # Une fenêtre "Passe sur ..." n'est ni une redirection HTTP ni
        # forcément le titre de la page. Examiner la notice même sur HTTP 451.
        if (rec.get("status") in ("online", "unknown", "restricted")
                and due_daily(rec.get("migration_attempted_at"), now)):
            rec["migration_attempted_at"] = stamp(now)
            notice = probe_site_migration(rec["site_url"])
            migration_checks += 1
            if notice:
                record_migration(state, rec, notice, now, first_run=first_run)
        # Garde-fou pour l'annonce Webflix documentée par deux captures :
        # utile si la notice est rendue côté JavaScript et absente du HTML.
        if rec["site_url"] in OBSERVED_MIGRATIONS and not rec.get("announced_url"):
            record_migration(
                state, rec, OBSERVED_MIGRATIONS[rec["site_url"]], now,
                first_run=first_run,
            )

        target = rec.get("announced_url")
        if target and due_daily(rec.get("announced_last_checked"), now):
            if target not in new_address_checks:
                new_address_checks[target] = check_site(target)
            checked_target = new_address_checks[target]
            rec["announced_status"] = checked_target.get("result", "unknown")
            rec["announced_http_status"] = checked_target.get("http_status")
            rec["announced_last_checked"] = stamp(now)

    state["server_scan"] = {
        "at": stamp(now), "counts": scan_counts,
        "errors": errors[:30],
        "all_public_sources_scanned": not errors,
        "new_http_checks": checked,
        "new_name_checks": identity_checks,
        "new_migration_checks": migration_checks,
        "new_announced_address_checks": len(new_address_checks),
    }
    active_records = [rec for rec in records.values() if rec.get("active")]
    report = {
        "checked": checked, "name_checked": identity_checks,
        "migration_checked": migration_checks,
        "new_addresses_checked": len(new_address_checks),
        "sites": len(active_records), "sources": scan_counts,
        "complete": not errors,
    }
    print("SURVEILLANCE GLOBALE :", report)
    return report, [
        ev for ev in state.get("events", [])[started:]
        if ev.get("alert")
    ]


def main():
    api = DiscordAPI(os.environ["DISCORD_BOT_TOKEN"])
    state = load_state()
    result, alerts = watch_server_links(api, state)
    # Les alertes et les nouvelles URLs declenchent aussi la mise a jour
    # du fil de forum d'origine (message du bot uniquement).
    forum_result = sync_forum_updates(api, state, alerts)
    state["server_scan"]["forum_updates"] = forum_result
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if alerts:
        notify_discord(api, GUILD_ID, alerts)
    print("Contrôle global terminé :", result)
    print("Mises à jour des forums :", forum_result)


if __name__ == "__main__":
    main()
