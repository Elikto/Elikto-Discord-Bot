"""Dupliquer les fiches publiques des forums en salons textuels, sans migration destructive.

Source : catalogue Discord public produit au début du workflow quotidien.
Les forums et messages d'origine ne sont jamais modifiés ou supprimés.
Les nouveaux salons sont identifiés par un marqueur dans leur sujet, afin que
les prochains passages réutilisent les mêmes salons et les mêmes cartes.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from sync_catalogue import (
    DiscordAPI, GUILD_ID, CHANNEL_CATEGORY, CHANNEL_FORUM,
    CHANNEL_TEXT, VIEW_CHANNEL, fold, public_channel,
)
from forum_cards import (
    build_card, source_description, safe_title, credential_fields,
    credentials_from_published_forum_card, V2_FLAG,
)
from forum_updates import (
    latest_check, records_for_forum, site_groups, upsert_forum_update,
)
from site_monitor import load_state, STATE_PATH
from site_probe import clean_url
from site_credentials import tr4ker_owner_post_credentials

PREFIX = "[elikto:site-channel:v1:"
CHANNEL_MARKER = re.compile(r"\[elikto:site-channel:v1:(\d{16,22})\]")
CATEGORY_SUFFIX = " · Sites"
DOWNLOAD_CATEGORIES = {
    "torrent": "📥 Téléchargement · Torrents",
    "jeux-video": "🎮 Téléchargement · Jeux vidéo",
    "livres": "📚 Téléchargement · Livres",
}
STYLED_CATEGORIES = {
    "streaming": ("🎬 Streaming · Sites", "🎬", "Streaming", 0x8B5CF6),
    "anti-pub": ("🛡️ Anti-pub · Sites", "🛡️", "Protection et anti-pub", 0x06B6D4),
    "ressource": ("🧰 Ressources · Sites", "🧰", "Outils et ressources", 0x64748B),
    "achat-shopping": ("🛍️ Shopping · Sites", "🛍️", "Shopping et comparateurs", 0xEC4899),
}
DOWNLOAD_STYLE = {
    "torrent": ("📥", "Torrents", 0x6366F1),
    "jeux-video": ("🎮", "Jeux vidéo", 0x0EA5E9),
    "livres": ("📚", "Livres et ebooks", 0xF59E0B),
}
FALLBACK_STYLE = ("🌐", "Sites référencés", 0x8271D8)
MAX_PER_CATEGORY = 45  # Marge sous la limite Discord de 50 salons.
MAX_CREATED = 250

GENERIC_TR4KER = "Tracker BitTorrent francophone proposant un catalogue de torrents"
SENSITIVE_HEADING = re.compile(
    r"(?i)^\s*(?:[🔐🔑👤📧]\s*)?(?:\*{0,2})"
    r"(?:identifiants?|mots?\s+de\s+passe|password|"
    r"utilisateurs?|username|login|adresse\s+e?-?mail)"
    r"(?:\*{0,2})\s*(?::|$)"
)
SOURCE_URL_LINE = re.compile(
    r"(?i)^\s*(?:🌐\s*)?(?:\*{0,2})"
    r"(?:site|adresse|url)\s*:?\s*(?:\*{0,2})"
    r"\s*https?://\S+\s*$"
)
BOILERPLATE = re.compile(
    r"(?i)^\s*(?:[✦📝🔄]\s*)?(?:\*{0,2})"
    r"(?:description|adresse contrôlée automatiquement chaque jour par le bot)"
    r"(?:\*{0,2})[.:]?\s*$"
)


def mirror_category_name(name, part=1):
    base = str(name).strip() or "Sans catégorie"
    suffix = CATEGORY_SUFFIX + (f" {part}" if part > 1 else "")
    return base[:100 - len(suffix)] + suffix


def original_forum_group(item):
    """Use the actual source forum, not keywords from a site's title."""
    if "telecharg" not in fold(item.get("source_category_name", "")).lower():
        return None
    original = fold(item.get("source_forum_name", "")).lower()
    original = re.sub(r"[^a-z0-9]+", "-", original).strip("-")
    return original if original in DOWNLOAD_CATEGORIES else None


def mirror_style(item):
    group = original_forum_group(item)
    if group:
        return DOWNLOAD_STYLE[group]
    category = fold(item.get("source_category_name", "")).lower().strip()
    if category in STYLED_CATEGORIES:
        return STYLED_CATEGORIES[category][1:]
    return FALLBACK_STYLE


def destination_category_name(item, part=1):
    group = original_forum_group(item)
    category = fold(item.get("source_category_name", "")).lower().strip()
    if group:
        base = DOWNLOAD_CATEGORIES[group]
    elif category in STYLED_CATEGORIES:
        base = STYLED_CATEGORIES[category][0]
    else:
        base = mirror_category_name(item["source_category_name"])
    return base + (f" {part}" if part > 1 else "")


def channel_topic(source):
    icon, kind, _ = mirror_style(source)
    name = safe_title(source.get("title")) or "Site"
    return (
        f"✦ {icon} {name} · {kind}\n"
        "Adresse, statut et dernier contrôle actualisés par le bot.\n"
        f"{PREFIX}{source['tid']}]"
    )[:1024]


def polished_status(group):
    status = group.get("status")
    if status in ("offline", "parked", "interstitial", "empty_index"):
        return "🔴 Adresse indisponible ou invalide", 0xEF4444
    if group.get("no_active"):
        return "🟡 Aucune adresse confirmée", 0xD97706
    if status == "restricted" and not group.get("verified"):
        return "🟠 Accès restreint · contenu non confirmé", 0xF97316
    if group.get("verified"):
        return "🟢 Site vérifié", None
    return "🟡 Vérification en attente", 0xD97706


def polish_mirror_components(components, source, groups):
    """Habiller UNIQUEMENT les salons miroirs, sans altérer les forums source.

    Conserve les URL, dates, preuves de statut et médias de build_card.
    Une couleur d'alerte prime sur la couleur de la rubrique.
    """
    icon, kind, theme_color = mirror_style(source)
    card = components[0]
    original_parts = list(card["components"])
    first = original_parts.pop(0)
    first["content"] = "## ✦ " + icon + " " + safe_title(source["title"]) + " ✦"
    top = [
        first,
        {"type": 10, "content": "-# " + kind + "  •  Fiche du catalogue"},
        {"type": 14, "divider": True, "spacing": 1},
    ]
    if groups:
        status, color = polished_status(groups[0])
        card["accent_color"] = color if color is not None else theme_color
        top.append({"type": 10, "content": "**" + status + "**"})
    else:
        card["accent_color"] = 0x94A3B8
        top.append({"type": 10, "content": "**🟡 Adresse en cours de vérification**"})

    sites = []
    reference = []
    descriptions = []
    media = []
    for part in original_parts:
        if part.get("type") == 12:
            media.append(part)
            continue
        content = part.get("content", "")
        if ("**🌐 Site actif**" in content
                or "**🌐 Site (accès restreint)**" in content):
            if groups and groups[0].get("no_active"):
                label = "**🌐 Adresse non confirmée**"
            elif groups and groups[0].get("status") in ("offline", "parked", "empty_index", "interstitial"):
                label = "**🌐 Dernière adresse connue**"
            elif groups and groups[0].get("verified"):
                label = "**🌐 Adresse actuelle**"
            else:
                label = "**🌐 Adresse à confirmer**"
            content = content.replace("**🌐 Site actif**", label).replace(
                "**🌐 Site (accès restreint)**", label
            )
            sites.append({**part, "content": content})
        elif content.startswith("**🔎 Site de référence**"):
            reference.append(part)
        else:
            descriptions.append(part)

    top.extend(sites)
    top.extend(reference)
    if descriptions:
        top.append({"type": 14, "divider": True, "spacing": 1})
        top.extend(descriptions)
    top.extend(media)
    card["components"] = top
    return components


def destination_group_key(item):
    return item["source_category"], original_forum_group(item) or ""


def channel_slug(title):
    name = fold(str(title or "")).lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return (name[:65].strip("-") or "site")


def catalogue_threads(catalogue):
    """Seules les fiches de VRAIS forums publics, sans les nouveaux salons."""
    result = []
    for category in catalogue.get("categories") or []:
        cid = str(category.get("id") or "")
        if (category.get("name", "").endswith(CATEGORY_SUFFIX)
                or category.get("name", "") in DOWNLOAD_CATEGORIES.values()):
            continue
        for parent in category.get("channels") or []:
            if parent.get("kind") != "forum":
                continue
            original_forum = str(parent.get("url") or "").rstrip("/").split("/")[-1]
            if not original_forum.isdigit():
                continue
            for item in parent.get("items") or []:
                tid = str(item.get("url") or "").rstrip("/").split("/")[-1]
                if not tid.isdigit():
                    continue
                result.append({
                    "tid": tid, "title": str(item.get("title") or "Site"),
                    "source_category": cid,
                    "source_category_name": str(category.get("name") or "Sans catégorie"),
                    "source_forum": original_forum,
                    "source_forum_name": str(parent.get("name") or ""),
                    "source_url": f"https://discord.com/channels/{GUILD_ID}/{tid}",
                })
    by_tid = {}
    for item in result:
        if item["tid"] in by_tid:
            raise RuntimeError("Publication source répertoriée deux fois : " + item["tid"])
        by_tid[item["tid"]] = item
    return sorted(by_tid.values(), key=lambda t: (
        t["source_category"], fold(t["title"]), t["tid"]
    ))


def safe_source_description(api, source, bot_id):
    """Copier seulement une vraie description, jamais un mot de passe."""
    raw = source_description(api, source["tid"], bot_id)
    if source["tid"] == "1572752361967853713" or (
        fold(source["title"]).strip() == "tr4ker" and GENERIC_TR4KER in raw
    ):
        return ""  # Ancien texte d'amorçage générique : pas une description.
    kept = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if kept and kept[-1]:
                kept.append("")
            continue
        if (
            BOILERPLATE.fullmatch(stripped)
            or SOURCE_URL_LINE.fullmatch(stripped)
            or re.search(r"https?://(?:www\.)?(?:discord(?:app)?\.com)/channels/\d+/\d+",
                         stripped, re.I)
            or SENSITIVE_HEADING.match(stripped)
            or credential_fields(stripped)[0]
            or stripped.strip("*✦ ").casefold() == source["title"].casefold()
        ):
            continue
        kept.append(line)
    return "\n".join(kept).strip()[:3500]


def groups_for_mirror(state, source):
    """Même adresse sélectionnée que la fiche forum, sans rescanner le site."""
    tid = source["tid"]
    groups = site_groups(records_for_forum(state, tid), source["title"])
    tracking = (state.get("forum_updates") or {}).get(tid, {})
    active = clean_url(tracking.get("active_url"))
    suggested = clean_url(tracking.get("suggested_url"))
    reference = clean_url(tracking.get("reference_url"))
    if active:
        chosen = next((g for g in groups if clean_url(g["url"]) == active), None)
        selected = dict(chosen or groups[0] if groups else {})
        selected.update({
            "url": active,
            "name": urlsplit(active).hostname or "",
            "verified": bool(tracking.get("active_verified")),
            "status": tracking.get("active_status") or selected.get("status") or "unknown",
            "reference_url": reference,
        })
        selected.setdefault("older", [])
        selected["checked_at"] = selected.get("checked_at") or latest_check(
            (state.get("records") or {}).get(tid, {}).get("last_checked")
        )
        return [selected]
    if suggested or reference:
        return [{
            "url": reference or suggested,
            "reference_url": reference,
            "suggested_url": suggested,
            "no_active": True, "status": "unknown", "verified": False,
            "older": [], "checked_at": (
                groups[0].get("checked_at") if groups else None
            ),
        }]
    return groups[:1]


def mirror_card(api, state, source, bot_id):
    groups = groups_for_mirror(state, source)
    original = safe_source_description(api, source, bot_id)
    tracking = (state.get("forum_updates") or {}).get(source["tid"], {})
    if groups:
        # Only reproduce credentials ALREADY publicly displayed by the
        # original, bot-authored forum card. Never read from a private forum,
        # and never persist the login in the JSON state or the repository.
        credentials = credentials_from_published_forum_card(
            api, source["tid"], bot_id, tracking.get("message_id")
        ) or tr4ker_owner_post_credentials(
            api, source["tid"], source["title"], GUILD_ID
        ) or {}
        components = build_card(
            groups, original, source["title"],
            credentials=credentials, preview_image_url=tracking.get("preview_image_url"),
        )
    else:
        components = [{
            "type": 17, "accent_color": 0x8271D8,
            "components": [
                {"type": 10, "content": "## ✦ " + safe_title(source["title"]) + " ✦"},
                {"type": 10, "content": (
                    (original + "\n" if original else "")
                    + "🟡 Adresse en cours de vérification."
                )},
            ],
        }]
    return polish_mirror_components(components, source, groups)


def public_sources(api, catalogue, channels):
    """Vérifier de nouveau les permissions juste avant chaque création."""
    guild = api.call("GET", f"/guilds/{GUILD_ID}")
    role = next((r for r in guild.get("roles", [])
                 if str(r["id"]) == GUILD_ID), None)
    if not role:
        raise RuntimeError("Rôle @everyone introuvable")
    permissions = int(role["permissions"])
    original_categories = {
        str(x["id"]): x for x in channels if x.get("type") == CHANNEL_CATEGORY
    }
    original_forums = {
        str(x["id"]): x for x in channels if x.get("type") in (CHANNEL_FORUM, 16)
    }
    sources = []
    for item in catalogue_threads(catalogue):
        forum = original_forums.get(item["source_forum"])
        if not forum or forum.get("nsfw") or str(forum.get("parent_id")) != item["source_category"]:
            continue
        check = dict(forum)
        if check["type"] == 16:
            check["type"] = CHANNEL_FORUM
        if not public_channel(check, permissions, original_categories, GUILD_ID):
            continue
        sources.append(item)
    return sources


def create_structure(api, sources):
    """Split download mirror channels by original forum, preserving channel IDs.

    Discord cannot nest categories. Prefixes make the three top-level
    categories appear together, and existing channels are MOVED (not copied).
    Never move or rename the original forums/categories.
    """
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    categories = {str(c["id"]): c for c in channels
                  if c.get("type") == CHANNEL_CATEGORY}
    copies = {}
    for channel in channels:
        if channel.get("type") != CHANNEL_TEXT:
            continue
        marker = CHANNEL_MARKER.search(channel.get("topic") or "")
        if not marker:
            continue
        tid = marker.group(1)
        if tid in copies:
            raise RuntimeError("Plusieurs salons miroirs pour la même fiche " + tid)
        copies[tid] = channel

    categories_renamed = 0
    moved_channels = 0
    created_categories = 0
    created_channels = 0

    # Give a consistent aesthetic to *managed* categories. Keep IDs and
    # never rename an unrelated or user-customized category.
    categories_to_style = {}
    for entry in sources:
        if original_forum_group(entry) not in (None, "torrent"):
            continue  # The old mixed folder is reused for Torrents only.
        cid = entry["source_category"]
        new_name = destination_category_name(entry)
        old_name = mirror_category_name(entry["source_category_name"])
        if old_name != new_name:
            categories_to_style[(cid, new_name)] = old_name
    for (cid, new_name), old_name in categories_to_style.items():
        legacy = next((c for c in categories.values()
                       if fold(c.get("name", "")) == fold(old_name)), None)
        existing_new = any(c.get("name") == new_name
                           for c in categories.values())
        if legacy and not existing_new:
            children = [x for x in channels
                        if str(x.get("parent_id")) == str(legacy["id"])]
            # Never rename a category that contains user's own channels.
            if children and all(
                x.get("type") == CHANNEL_TEXT
                and CHANNEL_MARKER.search(x.get("topic") or "")
                for x in children
            ):
                api.call("PATCH", f"/channels/{legacy['id']}",
                         json={"name": new_name})
                legacy["name"] = new_name
                categories_renamed += 1
                print("SALONS : catégorie miroir renommée",
                      old_name, "→", new_name)

    # Categories remain top-level (Discord does not support nesting).
    # The name is stable across daily runs; ' 2' only when 45 slots are full.
    def managed_category_names(item):
        names = {
            fold(mirror_category_name(item["source_category_name"])),
            fold(destination_category_name(item)),
        }
        if original_forum_group(item):
            names.update(fold(label) for label in DOWNLOAD_CATEGORIES.values())
        return names

    def desired_slots(item):
        base = destination_category_name(item)
        return [c for c in categories.values() if (
            c.get("name") == base
            or re.fullmatch(re.escape(base) + r" [2-9][0-9]*",
                            c.get("name", ""))
        )]

    def choose_category(item, current=None):
        nonlocal created_categories
        slots = desired_slots(item)
        if current and any(str(c["id"]) == str(current.get("parent_id"))
                           for c in slots):
            return categories[str(current["parent_id"])]
        available = next((c for c in slots if sum(
            1 for ch in channels if str(ch.get("parent_id")) == str(c["id"])
        ) < MAX_PER_CATEGORY), None)
        if available:
            return available
        part = len(slots) + 1
        label = destination_category_name(item, part)
        while any(c.get("name") == label for c in categories.values()):
            part += 1
            label = destination_category_name(item, part)
        if created_categories + created_channels >= MAX_CREATED:
            raise RuntimeError("Limite de sécurité des créations atteinte")
        original_parent = categories.get(item["source_category"], {})
        new = api.call("POST", f"/guilds/{GUILD_ID}/channels", json={
            "name": label,
            "type": CHANNEL_CATEGORY,
            "permission_overwrites": original_parent.get("permission_overwrites", []),
        })
        channels.append(new)
        categories[str(new["id"])] = new
        created_categories += 1
        print("SALONS : catégorie créée", label, new["id"])
        return new

    for item in sources:
        tid = item["tid"]
        existing = copies.get(tid)
        target = choose_category(item, existing)
        if existing:
            # Update only original bot-written topics. A user-customized topic
            # is never overwritten, and the channel/message IDs remain stable.
            old_topic = str(existing.get("topic") or "")
            if (old_topic.startswith("Fiche miroir · ancienne publication :")
                    or old_topic.startswith("✦ ")):
                new_topic = channel_topic(item)
                if new_topic != old_topic:
                    api.call("PATCH", f"/channels/{existing['id']}",
                             json={"topic": new_topic})
                    existing["topic"] = new_topic
                    print("SALONS : sujet enrichi", existing["name"])
            if str(existing.get("parent_id")) != str(target["id"]):
                current_category = categories.get(str(existing.get("parent_id")))
                # Only migrate from one of the previously bot-managed mirror
                # categories; preserve any manual move to an unrelated category.
                if current_category and fold(current_category.get("name", "")) in managed_category_names(item):
                    api.call("PATCH", f"/channels/{existing['id']}",
                             json={"parent_id": str(target["id"])})
                    print("SALONS : déplacé", existing["name"],
                          "→", target["name"])
                    existing["parent_id"] = str(target["id"])
                    moved_channels += 1
                else:
                    print("SALONS : emplacement personnalisé conservé",
                          existing["name"])
            continue

        slug = channel_slug(item["title"])
        siblings = {c["name"] for c in channels
                    if str(c.get("parent_id")) == str(target["id"])}
        if slug in siblings:
            slug = f"{slug[:60]}-{tid[-5:]}"
        if created_categories + created_channels >= MAX_CREATED:
            raise RuntimeError("Limite de sécurité des créations atteinte")
        created = api.call("POST", f"/guilds/{GUILD_ID}/channels", json={
            "name": slug, "type": CHANNEL_TEXT, "parent_id": target["id"],
            "topic": channel_topic(item),
        })
        channels.append(created)
        copies[tid] = created
        created_channels += 1
        print("SALONS : créé", slug, created["id"], "←", item["title"])
    return copies, created_categories, created_channels, categories_renamed, moved_channels


def sync_mirrors(api, catalogue, state):
    guild_channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    sources = public_sources(api, catalogue, guild_channels)
    copies, new_categories, new_channels, renamed, moved = create_structure(api, sources)
    bot_id = str(api.call("GET", "/users/@me")["id"])
    tracking = state.setdefault("site_channel_mirrors", {})
    result = {
        "categories_created": new_categories, "channels_created": new_channels,
        "categories_renamed": renamed, "channels_moved": moved,
        "cards_created": 0, "cards_updated": 0, "cards_unchanged": 0, "errors": [],
    }
    for source in sources:
        tid = source["tid"]
        copy = copies.get(tid)
        if not copy:
            continue
        cid = str(copy["id"])
        try:
            card = mirror_card(api, state, source, bot_id)
            known = tracking.get(tid, {})
            previous = {
                "message_id": known.get("message_id")
                if known.get("channel_id") == cid else None
            }
            try:
                mid, outcome = upsert_forum_update(
                    api, cid, bot_id, card, previous
                )
            except RuntimeError:
                # Un aperçu inaccessible ne doit pas bloquer la carte texte.
                if not any(p.get("type") == 12
                           for p in card[0].get("components", [])):
                    raise
                card[0]["components"] = [
                    p for p in card[0]["components"] if p.get("type") != 12
                ]
                mid, outcome = upsert_forum_update(
                    api, cid, bot_id, card, previous
                )
            tracking[tid] = {
                "channel_id": cid, "message_id": mid,
                "source_thread_id": tid,
            }
            result["cards_" + outcome] += 1
        except (RuntimeError, ValueError, AssertionError) as exc:
            result["errors"].append({
                "source": tid, "reason": str(exc).split("HTTP ")[-1][:100]
            })
    print("SALONS : résultat", result)
    if result["errors"]:
        raise RuntimeError(
            "Échec partiel des cartes (salons conservés pour le prochain passage)"
        )
    return result


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN absent")
    path = Path("site/catalogue.json")
    catalogue = json.loads(path.read_text(encoding="utf-8"))
    if catalogue.get("visibility") != "public_everyone_only":
        raise RuntimeError("Catalogue public requis ; création annulée")
    state = load_state()
    result = sync_mirrors(DiscordAPI(token), catalogue, state)
    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_PATH).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("SALONS : synchronisation quotidienne activée", result)


if __name__ == "__main__":
    main()
