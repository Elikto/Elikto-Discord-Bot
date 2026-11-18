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
    V2_FLAG,
)
from forum_updates import (
    latest_check, records_for_forum, site_groups, upsert_forum_update,
)
from site_monitor import load_state, STATE_PATH
from site_probe import clean_url

PREFIX = "[elikto:site-channel:v1:"
CHANNEL_MARKER = re.compile(r"\[elikto:site-channel:v1:(\d{16,22})\]")
CATEGORY_SUFFIX = " · Sites"
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


def channel_slug(title):
    name = fold(str(title or "")).lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return (name[:65].strip("-") or "site")


def catalogue_threads(catalogue):
    """Seules les fiches de VRAIS forums publics, sans les nouveaux salons."""
    result = []
    for category in catalogue.get("categories") or []:
        cid = str(category.get("id") or "")
        if category.get("name", "").endswith(CATEGORY_SUFFIX):
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
        # Le lien de l'ancien fil est une ressource distincte, pas un
        # doublon d'adresse du site. Aucun identifiant n'est recopié.
        components = build_card(
            groups, original, source["title"],
            credentials={}, preview_image_url=tracking.get("preview_image_url"),
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
    components[0]["components"].append({
        "type": 9,
        "components": [{"type": 10, "content": "-# 📚 Publication d'origine conservée"}],
        "accessory": {
            "type": 2, "style": 5, "label": "Ancien forum",
            "url": source["source_url"],
        },
    })
    return components


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
    """Créer des catégories de copie identifiables, sans toucher aux anciennes."""
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    categories = {str(c["id"]): c for c in channels if c.get("type") == CHANNEL_CATEGORY}
    copies = {}
    for c in channels:
        if c.get("type") != CHANNEL_TEXT:
            continue
        marker = CHANNEL_MARKER.search(c.get("topic") or "")
        if not marker:
            continue
        tid = marker.group(1)
        if tid in copies:
            raise RuntimeError("Plusieurs salons miroirs pour la même fiche " + tid)
        copies[tid] = c
    original = sources
    source_cats = {}
    for entry in sources:
        source_cats[entry["source_category"]] = entry["source_category_name"]
    category_slots = {}
    for cid, name in source_cats.items():
        existing = [
            cat for cat in categories.values()
            if cat["name"] == mirror_category_name(name)
            or re.fullmatch(re.escape(mirror_category_name(name)) + r" \d+", cat["name"])
        ]
        category_slots[cid] = existing
    created_categories = 0
    created_channels = 0
    for item in original:
        tid = item["tid"]
        if tid in copies:
            continue
        cid = item["source_category"]
        slots = category_slots[cid]
        selected = next((c for c in slots if sum(
            1 for v in channels if str(v.get("parent_id")) == str(c["id"])
        ) < MAX_PER_CATEGORY), None)
        if not selected:
            index = len(slots) + 1
            while any(c.get("type") == CHANNEL_CATEGORY
                      and c.get("name") == mirror_category_name(source_cats[cid], index)
                      for c in channels):
                index += 1
            if created_categories + created_channels >= MAX_CREATED:
                raise RuntimeError("Limite de sécurité des créations atteinte")
            orig = categories.get(cid, {})
            selected = api.call("POST", f"/guilds/{GUILD_ID}/channels", json={
                "name": mirror_category_name(source_cats[cid], index),
                "type": CHANNEL_CATEGORY,
                "permission_overwrites": orig.get("permission_overwrites", []),
            })
            channels.append(selected)
            categories[str(selected["id"])] = selected
            slots.append(selected)
            created_categories += 1
            print("SALONS : catégorie créée", selected["name"], selected["id"])
        slug = channel_slug(item["title"])
        siblings = {c["name"] for c in channels
                    if str(c.get("parent_id")) == str(selected["id"])}
        if slug in siblings:
            slug = f"{slug[:60]}-{tid[-5:]}"
        if created_categories + created_channels >= MAX_CREATED:
            raise RuntimeError("Limite de sécurité des créations atteinte")
        payload = {
            "name": slug,
            "type": CHANNEL_TEXT,
            "parent_id": selected["id"],
            "topic": (
                "Fiche miroir · ancienne publication : " + item["source_url"]
                + " " + PREFIX + tid + "]"
            )[:1024],
        }
        created = api.call("POST", f"/guilds/{GUILD_ID}/channels", json=payload)
        channels.append(created)
        copies[tid] = created
        created_channels += 1
        print("SALONS : créé", slug, created["id"], "←", item["title"])
    return copies, created_categories, created_channels


def sync_mirrors(api, catalogue, state):
    guild_channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    sources = public_sources(api, catalogue, guild_channels)
    copies, new_categories, new_channels = create_structure(api, sources)
    bot_id = str(api.call("GET", "/users/@me")["id"])
    tracking = state.setdefault("site_channel_mirrors", {})
    result = {
        "categories_created": new_categories, "channels_created": new_channels,
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
