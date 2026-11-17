"""Surveiller les sites liés aux fiches publiques du serveur Discord.

Un état JSON versionné conserve les observations et l'historique. L'export
public ne contient jamais de jeton Discord ni le contenu brut des messages.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from site_probe import check_site, extract_candidate_urls, other_domain


INTERVAL = timedelta(hours=48)
STATE_PATH = Path("data/monitor_state.json")
PUBLIC_PATH = Path("site/monitoring.json")
CATALOGUE_PATH = Path("site/catalogue.json")
ALERTS_TOPIC = "[elikto-surveillance:v1]"
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_HISTORY = 1 << 16


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def due(last: str | None, now: datetime, interval=INTERVAL) -> bool:
    if not last:
        return True
    try:
        then = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if then.tzinfo is None:
            return True
        return now - then >= interval
    except (ValueError, TypeError):
        return True


def load_state(path=STATE_PATH) -> dict:
    if not Path(path).exists():
        return {"version": 1, "records": {}, "events": [], "event_sequence": 0}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw.get("records"), dict) or not isinstance(raw.get("events"), list):
        raise ValueError("Etat de surveillance invalide")
    return raw


def event(state, name, record, now, *, previous=None, current=None, note=None, alert=False):
    state["event_sequence"] = int(state.get("event_sequence", 0)) + 1
    item = {
        "id": state["event_sequence"],
        "type": name,
        "at": stamp(now),
        "thread_id": record["thread_id"],
        "title": record["title"],
        "discord_url": record["discord_url"],
        "previous": previous,
        "current": current,
        "detail": note,
        "alert": bool(alert),
    }
    state["events"].append(item)
    state["events"] = state["events"][-250:]
    return item


def read_starter_message(api, thread_id: str):
    try:
        return api.call("GET", f"/channels/{thread_id}/messages/{thread_id}")
    except RuntimeError:
        # Certains fils plus anciens ont un message de depart distinct.
        try:
            data = api.call("GET", f"/channels/{thread_id}/messages", params={"limit": 100})
            if not isinstance(data, list) or not data:
                return None
            for item in data:
                if str(item.get("id")) == thread_id:
                    return item
            return min(data, key=lambda item: int(item.get("id", "0")))
        except (RuntimeError, ValueError, TypeError):
            return None


DISCOVERY_COUNTS = {"unreadable": 0, "empty_message": 0, "readable": 0, "with_url": 0}


def pick_site_url(api, thread_id: str, title: str) -> str | None:
    # Le corps du message de depart est prioritaire. Ne pas scanner des
    # conversations d'autres membres pour eviter de prendre un lien parasite.
    message = read_starter_message(api, thread_id)
    if message is None:
        DISCOVERY_COUNTS["unreadable"] += 1
    elif not (message.get("content") or message.get("embeds")):
        DISCOVERY_COUNTS["empty_message"] += 1
    else:
        DISCOVERY_COUNTS["readable"] += 1
    choices = extract_candidate_urls(message or {})
    if not choices:
        choices = extract_candidate_urls({"content": title})
    if choices:
        DISCOVERY_COUNTS["with_url"] += 1
    return choices[0] if choices else None


def record_check(state: dict, record: dict, observation: dict, now: datetime) -> list[dict]:
    """Evite les fausses pannes : deux controles independants sont necessaires."""
    result = observation.get("result", "unknown")
    old_status = record.get("status", "pending")
    old_redirect = record.get("redirect_url")
    alerts = []
    failures = int(record.get("consecutive_failures", 0))

    if result == "online":
        record["status"] = "online"
        record["consecutive_failures"] = 0
    elif result == "restricted":
        record["status"] = "restricted"
        record["consecutive_failures"] = 0
    elif result == "failed":
        failures += 1
        record["consecutive_failures"] = failures
        record["status"] = "offline" if failures >= 2 else "suspect"
    else:
        # Une erreur de resolution SSRF ou redirection ne confirme rien.
        record["status"] = "unknown"
        record["consecutive_failures"] = 0

    record["last_checked"] = stamp(now)
    record["http_status"] = observation.get("http_status")
    record["reason"] = observation.get("reason")
    record["final_url"] = observation.get("final_url")

    redir = observation.get("final_url") if other_domain(
        record.get("site_url"), observation.get("final_url")
    ) else None
    record["redirect_url"] = redir
    if redir and redir != old_redirect:
        alerts.append(event(
            state, "redirect_detected", record, now,
            previous=record.get("site_url"), current=redir,
            note="Domaine final différent : correspondance à vérifier manuellement.",
            alert=True,
        ))

    if old_status != "offline" and record["status"] == "offline":
        alerts.append(event(
            state, "site_unreachable", record, now,
            previous=old_status, current="offline",
            note="Deux vérifications successives sans réponse exploitable.",
            alert=True,
        ))
    elif old_status == "offline" and record["status"] in ("online", "restricted"):
        alerts.append(event(
            state, "site_recovered", record, now,
            previous="offline", current=record["status"],
            note="Le serveur répond de nouveau à la vérification HTTP.",
            alert=True,
        ))
    return alerts


def public_mirrors(state: dict, thread_id: str) -> list[dict]:
    """Liste publique des adresses distinctes déjà vérifiées pour un fil."""
    tracked = state.get("tracked_threads", {}).get(str(thread_id), {})
    observed = tracked.get("url_states", {})
    return [
        {
            "url": url,
            "name": entry.get("label"),
            "site_name": entry.get("site_name"),
            "pending_name": entry.get("pending_name"),
            "announced_url": entry.get("announced_url"),
            "name_evidence": entry.get("name_evidence"),
            "name_last_checked": entry.get("name_last_checked"),
            "status": entry.get("status", "pending"),
            "last_checked": entry.get("last_checked"),
            "http_status": entry.get("http_status"),
            "redirect_url": entry.get("redirect_url"),
        }
        for url in tracked.get("urls", [])
        if (entry := observed.get(url)) is not None
    ]


def watch_catalogue(api, catalogue: dict, state: dict, now=None) -> tuple[dict, list[dict]]:
    now = now or utc_now()
    alerts = []
    existing = state["records"]
    initial_index = not existing
    visible = set()

    for category in catalogue.get("categories", []):
        for channel in category.get("channels", []):
            if channel.get("kind") != "forum":
                continue
            for item in channel.get("items", []):
                thread_id = str(item.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
                if not thread_id.isdigit():
                    continue
                visible.add(thread_id)
                record = existing.get(thread_id)
                if record is None:
                    record = {
                        "thread_id": thread_id,
                        "title": item["title"],
                        "discord_url": item["url"],
                        "site_url": None,
                        "status": "missing_url",
                        "last_checked": None,
                        "last_discovery": None,
                        "consecutive_failures": 0,
                        "http_status": None,
                        "reason": None,
                        "redirect_url": None,
                        "final_url": None,
                        "first_seen": stamp(now),
                    }
                    existing[thread_id] = record
                    if not initial_index:
                        alerts.append(event(
                            state, "new_resource", record, now,
                            current=item["title"], alert=True,
                            note="Nouvelle fiche publique ajoutée au serveur.",
                        ))
                elif record.get("title") != item["title"]:
                    previous = record["title"]
                    record["title"] = item["title"]
                    alerts.append(event(
                        state, "title_changed", record, now,
                        previous=previous, current=item["title"],
                        note="Nom de fiche changé sur Discord (sans preuve de changement du site).",
                        alert=True,
                    ))

                record["discord_url"] = item["url"]
                record["category"] = category["name"]
                record["forum"] = channel["name"]
                # Reessayer au prochain lancement si aucun site n'a ete extrait :
                # utile lorsque MESSAGE_CONTENT vient d'etre autorise.
                tracked_url = None
                if thread_id == "1430988094374547559":
                    tracked_url = state.get("tracked_threads", {}).get(thread_id, {}).get("address")
                needs_discovery = (
                    tracked_url != record.get("site_url") if tracked_url else (
                        not record.get("site_url") or due(record.get("last_discovery"), now)
                    )
                )
                if needs_discovery:
                    # Pour le fil suivi, le lien validé par l'auteur du fil
                    # prévaut sur l'ancien lien du message initial.
                    candidate = tracked_url or pick_site_url(api, thread_id, item["title"])
                    record["last_discovery"] = stamp(now)
                    if candidate and candidate != record.get("site_url"):
                        previous = record.get("site_url")
                        record["site_url"] = candidate
                        record["last_checked"] = None
                        record["consecutive_failures"] = 0
                        record["status"] = "pending"
                        if previous:
                            # Le suiveur peut déjà avoir enregistré le même
                            # changement dans l'historique de ce passage.
                            already_recorded = any(
                                ev.get("type") == "address_changed"
                                and ev.get("thread_id") == thread_id
                                and ev.get("current") == candidate
                                and not due(ev.get("at"), now, timedelta(hours=1))
                                for ev in state.get("events", [])[-30:]
                            )
                            if not already_recorded:
                                alerts.append(event(
                                    state, "address_changed", record, now,
                                    previous=previous, current=candidate,
                                    note="Adresse modifiée dans la fiche Discord.",
                                    alert=True,
                                ))

                if record.get("site_url") and due(record.get("last_checked"), now):
                    observation = check_site(record["site_url"])
                    alerts.extend(record_check(state, record, observation, now))
                elif not record.get("site_url"):
                    record["status"] = "missing_url"

                item["monitor"] = {
                    "site_url": record.get("site_url"),
                    "status": record["status"],
                    "last_checked": record.get("last_checked"),
                    "http_status": record.get("http_status"),
                    "redirect_url": record.get("redirect_url"),
                    "reason": record.get("reason"),
                    "mirrors": public_mirrors(state, thread_id),
                }

    # Ne plus exposer les fiches supprimees du catalogue, mais conserver leurs
    # evenements historiques avec leur titre original.
    state["records"] = {key: value for key, value in existing.items()
                        if key in visible}
    statuses = {}
    control_count = 0
    monitored_urls = 0
    for rec in state["records"].values():
        mirrors = public_mirrors(state, rec["thread_id"])
        if mirrors:
            for mirror in mirrors:
                key = mirror.get("status", "unknown")
                statuses[key] = statuses.get(key, 0) + 1
                control_count += 1
                monitored_urls += 1
        else:
            key = rec.get("status", "unknown")
            statuses[key] = statuses.get(key, 0) + 1
            control_count += 1
            monitored_urls += int(bool(rec.get("site_url")))

    events = state["events"][-120:]
    report = {
        "schema_version": 1,
        "updated_at": stamp(now),
        "next_check_policy": "48 hours after previous check (Github Actions cadence)",
        "status_counts": statuses,
        "total": control_count,
        "fiches": len(state["records"]),
        "monitored": monitored_urls,
        "events": list(reversed(events)),
        "sites": [{
            "thread_id": r["thread_id"],
            "title": r["title"],
            "discord_url": r["discord_url"],
            "category": r.get("category"),
            "forum": r.get("forum"),
            "url": r.get("site_url"),
            "status": r.get("status"),
            "last_checked": r.get("last_checked"),
            "http_status": r.get("http_status"),
            "redirect_url": r.get("redirect_url"),
            "reason": r.get("reason"),
            "mirrors": public_mirrors(state, r["thread_id"]),
        } for r in state["records"].values()],
    }
    return report, alerts


def ensure_private_alert_channel(api, guild_id: str) -> str | None:
    """Salon avec @everyone explicitement refuse, sans toucher aux salons existants."""
    channels = api.call("GET", f"/guilds/{guild_id}/channels")
    matched = [
        c for c in channels if c.get("type") == 0 and
        ALERTS_TOPIC in (c.get("topic") or "")
    ]
    if matched:
        return str(matched[0]["id"])
    if any(c.get("name") == "alertes-sites" for c in channels):
        print("Un salon #alertes-sites sans marqueur existe : aucune modification.")
        return None
    try:
        bot_id = str(api.call("GET", "/users/@me")["id"])
        payload = {
            "name": "alertes-sites", "type": 0,
            "topic": "Alertes automatiques de suivi des sites — " + ALERTS_TOPIC,
            "permission_overwrites": [
                {"id": guild_id, "type": 0, "allow": "0",
                 "deny": str(VIEW_CHANNEL)},
                {"id": bot_id, "type": 1,
                 "allow": str(VIEW_CHANNEL | SEND_MESSAGES | READ_HISTORY),
                 "deny": "0"},
            ],
        }
        response = api.call("POST", f"/guilds/{guild_id}/channels", json=payload)
        print("Salon d'alertes prive cree : ", response["id"])
        return str(response["id"])
    except RuntimeError as exc:
        print("Salon d'alertes prive indisponible :", type(exc).__name__)
        return None


def notify_discord(api, guild_id: str, alerts: list[dict]) -> int:
    channel_id = ensure_private_alert_channel(api, guild_id)
    if not channel_id or not alerts:
        return 0
    names = {
        "new_resource": "🆕 Nouvelle fiche",
        "title_changed": "✏️ Nom de fiche modifié",
        "address_changed": "🔄 Adresse modifiée",
        "redirect_detected": "🔀 Autre domaine détecté",
        "site_unreachable": "🔴 Site inaccessible (confirmé)",
        "site_recovered": "🟢 Site de nouveau joignable",
        "mirror_added": "🆕 Nouvelle adresse miroir",
        "mirror_removed": "➖ Adresse miroir retirée",
        "mirror_renamed": "✏️ Nom d'une adresse modifié",
        "mirror_redirect": "🔀 Redirection d'un miroir",
        "mirror_offline": "🔴 Adresse miroir inaccessible",
        "mirror_recovered": "🟢 Adresse miroir rétablie",
        "mirror_name_suspected": "🟡 Changement de nom détecté, à confirmer",
        "mirror_name_changed": "🔄 Nouveau nom du site confirmé",
    }
    count = 0
    # Une alerte par changement important, sans mention ni spam de masse.
    for entry in alerts[:15]:
        label = names.get(entry["type"], "📍 Changement détecté")
        body = (
            f"**{label}**\n"
            f"**Fiche :** {entry['title'][:120]}\n"
            f"**Événement :** {entry.get('detail') or entry['type']}\n"
            f"**Avant :** {str(entry.get('previous') or '—')[:190]}\n"
            f"**Après :** {str(entry.get('current') or '—')[:190]}\n"
            f"[Ouvrir la fiche Discord]({entry['discord_url']})"
        )
        try:
            api.call("POST", f"/channels/{channel_id}/messages", json={
                "content": body[:1850], "allowed_mentions": {"parse": []},
            })
            count += 1
        except RuntimeError as exc:
            print("Alerte Discord non envoyee :", type(exc).__name__)
            break
    return count


def main():
    from sync_catalogue import DiscordAPI, GUILD_ID

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Secret DISCORD_BOT_TOKEN absent")
    catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    state = load_state()
    api = DiscordAPI(token)
    report, alerts = watch_catalogue(api, catalogue, state)
    # Ecrire les deux exports avant le commit de l'etat, pour garantir
    # l'identite entre donnees publiees et historique.
    CATALOGUE_PATH.write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    PUBLIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    notified = notify_discord(api, GUILD_ID, alerts)
    print("Diagnostic lecture des fiches Discord (comptes uniquement) :", DISCOVERY_COUNTS)
    print(
        "Surveillance :",
        report["total"], "fiches,",
        report["monitored"], "sites a surveiller,",
        len(alerts), "changements,",
        notified, "alertes Discord.",
        "Statuts :", report["status_counts"],
    )


if __name__ == "__main__":
    main()
