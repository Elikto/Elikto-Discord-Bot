"""Suivi quotidien de toutes les URLs du fil Streaming HD miroir."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import os
import re

from site_probe import check_site, extract_candidate_urls, other_domain, clean_url
from name_probe import probe_site_name, announced_rename
from site_discovery import candidate_groups
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


def grouped_current_sites(items):
    """Un site actuel par domaine annoncé, même si plusieurs anciens y mènent.

    Les six URLs sources restent suivies séparément dans url_states. Cette
    projection ne concerne que la liste publique du bot et du site web.
    """
    groups = {}
    for item in items:
        proposed_url = clean_url(item.get("announced_url")) if item.get("announced_url") else None
        current_url = proposed_url or item["url"]
        # www et non-www ne créent pas deux entrées pour le même site.
        parsed = urlsplit(current_url)
        host = (parsed.hostname or "").removeprefix("www.")
        key = (host, parsed.path.rstrip("/") or "/")
        displayed_name = (
            item.get("pending_name") or item.get("site_name")
            or item.get("label") or host
        )
        old_name = item.get("site_name") or item.get("label") or urlsplit(item["url"]).hostname
        if key not in groups:
            groups[key] = {
                "url": current_url,
                "name": str(displayed_name),
                "previous_names": [],
                "source_urls": [],
                "announced": bool(proposed_url),
                "pending": bool(item.get("pending_name")),
                "status": item.get("status", "pending"),
                "last_checked": item.get("last_checked"),
                "http_status": item.get("http_status"),
                "redirect_url": item.get("redirect_url"),
            }
        group = groups[key]
        # Une annonce plus récente a priorité sur un simple libellé initial.
        if item.get("pending_name"):
            group["name"] = str(item["pending_name"])
        group["announced"] = group["announced"] or bool(proposed_url)
        group["pending"] = group["pending"] or bool(item.get("pending_name"))
        if item["url"] not in group["source_urls"]:
            group["source_urls"].append(item["url"])
        if old_name and identity_key(old_name) != identity_key(group["name"]):
            if identity_key(old_name) not in {identity_key(n) for n in group["previous_names"]}:
                group["previous_names"].append(str(old_name))
    return list(groups.values())


def decorate_destinations(groups, source_items, target_states, now, state):
    """Contrôler les nouveaux domaines, y compris leurs renommages ultérieurs.

    Chaque cible est vérifiée une seule fois par jour. Un second changement
    annoncé dans le HTML suit la même procédure, jusqu'à trois transitions
    pour empêcher les cycles et les redirections infinies.
    """
    source_index = {item["url"]: item for item in source_items}
    required = set()
    projected = {}

    for initial in groups:
        group = {
            **initial,
            "previous_names": list(initial.get("previous_names", [])),
            "source_urls": list(initial.get("source_urls", [])),
        }
        visited = set()
        for _hop in range(4):
            target = group["url"]
            if target in visited:
                break
            visited.add(target)
            source = source_index.get(target)
            if source is not None:
                observation = source
            else:
                required.add(target)
                observation = target_states.get(target)
                is_new = observation is None
                if is_new:
                    observation = {
                        "url": target, "label": group["name"],
                        "site_name": group["name"], "status": "pending",
                        "last_checked": None, "http_status": None, "failures": 0,
                        "reason": None, "redirect_url": None, "final_url": None,
                    }
                    target_states[target] = observation
                if not is_checked_today(observation.get("last_checked"), now):
                    update_probe(
                        observation, check_site(target), now, state, first_run=is_new
                    )
                if (observation.get("status") == "online"
                        and not is_checked_today(observation.get("name_attempted_at"), now)):
                    observation["name_attempted_at"] = now.isoformat(timespec="seconds")
                    update_name(
                        observation, probe_site_name(target), now, state,
                        first_run=is_new,
                    )

            # Une page qui annonce "NANDOV devient X.com" est une preuve
            # d'annonce, pas encore la confirmation de son identité.
            successor = clean_url(observation.get("announced_url")) if observation.get("announced_url") else None
            successor_name = observation.get("pending_name")
            if (successor and successor != target and successor not in visited
                    and successor_name):
                current_name = group["name"]
                if identity_key(current_name) != identity_key(successor_name):
                    if identity_key(current_name) not in {
                        identity_key(name) for name in group["previous_names"]
                    }:
                        group["previous_names"].append(current_name)
                group["url"] = successor
                group["name"] = str(successor_name)
                group["pending"] = True
                group["announced"] = True
                continue

            for field in ("status", "last_checked", "http_status", "redirect_url"):
                group[field] = observation.get(field)
            break

        url = urlsplit(group["url"])
        key = ((url.hostname or "").removeprefix("www."), url.path.rstrip("/") or "/")
        if key not in projected:
            projected[key] = group
        else:
            merged = projected[key]
            for field in ("source_urls", "previous_names"):
                for val in group.get(field, []):
                    if val not in merged[field]:
                        merged[field].append(val)
            merged["pending"] = merged["pending"] or group.get("pending", False)
            merged["announced"] = merged["announced"] or group.get("announced", False)

    for url in set(target_states) - required:
        del target_states[url]
    return list(projected.values())


def summary_message(items, thread_name):
    """Liste Discord épurée : un nouveau nom cliquable, puis ses anciens noms.

    Les informations techniques (HTTP, dates, confirmations) restent dans le
    monitoring et l'historique, mais ne polluent plus le message public.
    """
    lines = [
        "🎬 Dernière sortie cinéma en HD **sans pub**, mais petit catalogue.",
        "✨ **Gratuit - Illimité - Ajouts quotidiens - Zéro pub**",
        "",
        "## ✦ SITES ACTUELS",
        "",
    ]
    for item in items:
        name = str(item.get("name") or urlsplit(item["url"]).hostname)
        name = name.replace("@", "@\u200b").replace("]", "")
        # Les URL sont déjà nettoyées et contrôlées via clean_url.
        url = clean_url(item.get("url"))
        if not url:
            continue
        lines.append(f"### ✦ [{name[:48]}]({url})")
        old_names = item.get("previous_names") or []
        if old_names:
            former = ", ".join(str(old) for old in old_names).replace("@", "@\u200b")
            lines.append(f"-# ↳ Ancien : {former}")
        lines.append("")

    content = "\n".join(lines).rstrip()
    if len(content) > 1950:
        raise ValueError("La liste des noms actuels dépasse Discord : aucun site masqué.")
    return content


COMPONENTS_V2 = 32768
CARD_ACCENT = 0x6174C6
HEADER_ACCENT = 0xA17AFF


def render_site_cards(items, thread_name):
    """Cartes Discord V2 : bordure colorée et liens bleus cliquables.

    Un contenant par site tant que le budget Discord le permet ; au-delà,
    plusieurs fiches partagent une carte afin de ne rien masquer.
    """
    header = (
        "## 🎬 CINÉMA HD · SITES ACTUELS\n"
        "Dernière sortie cinéma en HD **sans pub**, mais petit catalogue.\n"
        "✨ **Gratuit - Illimité - Ajouts quotidiens - Zéro pub**"
    )
    cards = []
    for site in items:
        url = clean_url(site.get("url"))
        if not url:
            continue
        name = str(site.get("name") or urlsplit(url).hostname).strip()
        name = name.replace("@", "@\u200b").replace("]", "").replace("[", "")[:48]
        old_names = []
        for raw in site.get("previous_names", []):
            old = str(raw).replace("@", "@\u200b")[:40]
            if old and old not in old_names:
                old_names.append(old)
        content = f"### ✦ [{name}]({url})"
        if old_names:
            content += "\n-# ↳ Ancien : " + ", ".join(old_names)
        cards.append(content)

    card_groups = (
        [[content] for content in cards] if len(cards) <= 18
        else [cards[i:i + 3] for i in range(0, len(cards), 3)]
    )
    components = [
        {"type": 17, "accent_color": HEADER_ACCENT,
         "components": [{"type": 10, "content": header}]}
    ]
    for group in card_groups:
        components.append({
            "type": 17, "accent_color": CARD_ACCENT,
            "components": [{"type": 10, "content": "\n\n".join(group)}],
        })
    total = len(components) * 2
    if total > 40:
        raise ValueError("Trop de composants Discord : la liste ne sera pas tronquée.")
    if sum(len(card) for card in cards) + len(header) > 3900:
        raise ValueError("La liste dépasse la limite de texte Discord : aucune fiche masquée.")
    return components


def component_signature(components):
    """Comparer le rendu, en ignorant les identifiants ajoutés par Discord."""
    return tuple(
        (entry.get("type"), entry.get("accent_color"),
         entry.get("content"),
         component_signature(entry.get("components") or []))
        for entry in (components or [])
    )


def component_text(components):
    texts = []
    for part in components or []:
        if part.get("type") == 10:
            texts.append(str(part.get("content") or ""))
        texts.extend(component_text(part.get("components") or []))
    return texts


def upsert_bot_message(api, tracked, bot_id, content):
    """Migrer l'ancien message texte vers les cartes, puis éditer sans doublon."""
    is_v2 = isinstance(content, list)
    if is_v2:
        payload = {
            "content": None, "embeds": [], "flags": COMPONENTS_V2,
            "components": content, "allowed_mentions": {"parse": []},
        }
    else:
        payload = {"content": content, "allowed_mentions": {"parse": []}}

    def update_if_needed(message):
        if is_v2:
            existing = component_signature(message.get("components") or [])
            expected = component_signature(content)
            if existing != expected:
                api.call(
                    "PATCH", f"/channels/{THREAD_ID}/messages/{message['id']}",
                    json=payload,
                )
        elif (message.get("content") or "") != content:
            api.call(
                "PATCH", f"/channels/{THREAD_ID}/messages/{message['id']}",
                json=payload,
            )
        return str(message["id"])

    known = tracked.get("message_id")
    if known:
        old = api.call("GET", f"/channels/{THREAD_ID}/messages/{known}")
        if str(old.get("author", {}).get("id")) == bot_id:
            return update_if_needed(old)
        raise ValueError("Le message connu n'appartient pas au bot")

    messages = api.call("GET", f"/channels/{THREAD_ID}/messages", params={"limit": 100})
    for msg in messages:
        if str(msg.get("author", {}).get("id")) != bot_id:
            continue
        prior = msg.get("content") or ""
        v2_text = "\n".join(component_text(msg.get("components") or []))
        if (
            MARKER in prior or "[elikto-suivi-fil:v1]" in prior
            or prior.startswith("## ✦ SITES ACTUELS")
            or prior.startswith("🎬 Dernière sortie cinéma")
            or "CINÉMA HD · SITES ACTUELS" in v2_text
        ):
            return update_if_needed(msg)

    created = api.call("POST", f"/channels/{THREAD_ID}/messages", json=payload)
    return str(created["id"])


def sync_thread(api, state, now=None):
    now = now or datetime.now(timezone.utc)
    thread = api.call("GET", f"/channels/{THREAD_ID}")
    if str(thread.get("guild_id")) != GUILD_ID or thread.get("type") != 11:
        raise ValueError("Fil public attendu sur le serveur")
    bot_id = str(api.call("GET", "/users/@me")["id"])
    tracked = state.setdefault("tracked_threads", {}).setdefault(THREAD_ID, {})
    try:
        messages = api.call("GET", f"/channels/{THREAD_ID}/messages", params={"limit": 100})
    except RuntimeError:
        if tracked.get("url_states"):
            messages = []
            print("MIROIRS : lecture momentanément indisponible ; état conservé.")
        else:
            raise
    entries = tracked.setdefault("url_states", {})
    readable = any(
        m.get("content") or m.get("embeds")
        for m in messages if str(m.get("author", {}).get("id")) != bot_id
    )
    if not readable and not entries:
        print("MIROIRS : contenu source inaccessible et aucun historique conservé.")
        return "blocked"
    sources = find_urls(messages, bot_id)
    print("MIROIRS : liens distincts détectés =", len(sources))
    if not sources:
        # Si le message original n'affiche plus les anciennes URL, conserver
        # les sources déjà vérifiées. Le bot doit pouvoir rééditer son propre
        # récapitulatif même si les textes Discord ont été réorganisés.
        sources = [
            {"url": url, "label": entries[url].get("label", url)}
            for url in tracked.get("urls", list(entries))
            if url in entries
        ]
        if not sources:
            return "no_urls"
        print("MIROIRS : aucun lien lisible dans le fil ; réutilisation de",
              len(sources), "adresses historiques validées.")

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

    source_items = [entries[source["url"]] for source in sources]
    original_sites = grouped_current_sites(source_items)
    internet_sites = candidate_groups(tracked, now)
    target_states = tracked.setdefault("target_states", {})
    sites = decorate_destinations(
        original_sites + internet_sites, source_items, target_states, now, state
    )
    # Seuls les candidats supplémentaires réellement joignables sont
    # publiés. Les anciens sites suivis depuis Discord restent affichés.
    sites = [
        site for site in sites
        if site.get("source_urls") or site.get("status") == "online"
    ]
    cards = render_site_cards(sites, thread_name)
    try:
        message_id = upsert_bot_message(api, tracked, bot_id, cards)
    except RuntimeError:
        print("MIROIRS : autorisation d'écrire dans le fil manquante.")
        return "permission_denied"
    tracked.update({
        "name": thread_name,
        "message_id": message_id,
        "urls": [source["url"] for source in sources],
        "display_sites": sites,
        "last_synced": now.isoformat(timespec="seconds"),
    })
    known_names = sum(bool(entries[source["url"]].get("site_name")) for source in sources)
    suspected_names = sum(bool(entries[source["url"]].get("pending_name")) for source in sources)
    print("MIROIRS :", len(sources), "adresses source ;", len(sites),
          "sites affichés dont", len(internet_sites), "candidats Internet ;",
          checked, "vérifications HTTP sources ;",
          names_checked, "recherches de nom ;", known_names,
          "noms identifiés ;", suspected_names,
          "changements de nom suspects.")
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
