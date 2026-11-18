"""Catalogue global du serveur Discord, lance ponctuellement par GitHub Actions.

Indexe les salons textuels et les publications actives ou archivees des forums
visibles par @everyone. Ne modifie pas les fiches d'origine.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import os
import time
import unicodedata

import requests

API = "https://discord.com/api/v10"
VIEW_CHANNEL = 1 << 10
CHANNEL_TEXT = 0
CHANNEL_CATEGORY = 4
CHANNEL_FORUM = 15
MARKER = "elikto:catalog:"
CATALOG_TOPIC_MARKER = "[elikto-catalogue:v1]"
SITE_MIRROR_TOPIC_MARKER = "[elikto:site-channel:v1:"
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "1430984645012357263")
CHANNEL_ID = os.getenv("CATALOG_CHANNEL_ID", "")
EXCLUDE = {x.strip() for x in os.getenv("EXCLUDED_CHANNEL_IDS", "").split(",") if x.strip()}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def escape(s: str) -> str:
    special = set("\\*_~|>[]()" + chr(96))
    return "".join("\\" + c if c in special else c for c in str(s).replace("\n", " "))


def public_channel(channel: dict, everyone_permissions: int, categories: dict, guild_id: str) -> bool:
    """Index public seulement : @everyone doit voir le salon et sa categorie."""
    if channel.get("nsfw") or channel.get("type") not in (CHANNEL_TEXT, CHANNEL_FORUM):
        return False

    def apply(permissions: int, item: dict) -> int:
        for overwrite in item.get("permission_overwrites", []):
            if str(overwrite.get("id")) == guild_id and overwrite.get("type") in (0, "0", "role"):
                permissions = (permissions & ~int(overwrite.get("deny", "0"))) | int(overwrite.get("allow", "0"))
        return permissions

    parent = categories.get(str(channel.get("parent_id", "")))
    if parent and not (apply(everyone_permissions, parent) & VIEW_CHANNEL):
        return False
    return bool(apply(everyone_permissions, channel) & VIEW_CHANNEL)


def chunks(lines: list[str], max_chars: int = 3400) -> list[str]:
    result, current = [], ""
    for line in lines:
        line = line[:max_chars - 2]
        if len(current) + len(line) + 1 > max_chars and current:
            result.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current or not result:
        result.append(current.rstrip() or "Aucune fiche.")
    return result


class DiscordAPI:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bot " + token,
            "User-Agent": "EliktoCatalogue/1.0",
        })

    def call(self, method: str, path: str, *, params=None, json=None):
        for attempt in range(8):
            response = self.session.request(method, API + path, params=params, json=json, timeout=30)
            if response.status_code == 429:
                try:
                    delay = float(response.json().get("retry_after", 2))
                except (ValueError, TypeError):
                    delay = 2
                time.sleep(min(max(delay, 0), 30) + .2)
                continue
            if response.status_code in (500, 502, 503, 504) and attempt < 7:
                time.sleep(min(2 ** attempt, 12))
                continue
            if not response.ok:
                # Ne jamais imprimer les en-tetes Authorization.
                raise RuntimeError(f"Discord {method} {path} : HTTP {response.status_code} ({response.text[:200]})")
            return response.json() if response.content else {}
        raise RuntimeError(f"Discord API : echec repete pour {path}")

    def archived(self, forum_id: str):
        before, seen = None, set()
        for _ in range(500):
            params = {"limit": 100}
            if before:
                params["before"] = before
            page = self.call("GET", f"/channels/{forum_id}/threads/archived/public", params=params)
            batch = page.get("threads", [])
            for thread in batch:
                if thread["id"] not in seen:
                    seen.add(thread["id"])
                    yield thread
            if not page.get("has_more"):
                return
            if not batch:
                raise RuntimeError("Archives incompletes : page vide malgre has_more")
            next_before = batch[-1].get("thread_metadata", {}).get("archive_timestamp")
            if not next_before or next_before == before:
                raise RuntimeError("Pagination des archives impossible : " + forum_id)
            before = next_before
        raise RuntimeError("Limite de pagination d'archives atteinte : " + forum_id)

    def own_messages(self, channel_id: str, me: str):
        before, found = None, {}
        for _ in range(20):
            params = {"limit": 100}
            if before:
                params["before"] = before
            page = self.call("GET", f"/channels/{channel_id}/messages", params=params)
            if not page:
                break
            for message in page:
                if str(message.get("author", {}).get("id")) != me:
                    continue
                for embed in message.get("embeds", []):
                    footer = embed.get("footer", {}).get("text", "")
                    if MARKER in footer:
                        marker = footer[footer.index(MARKER):].split()[0]
                        found[marker] = message
                for component in message.get("components", []):
                    if component.get("type") == 17 and component.get("id") is not None:
                        found[f"v2id:{component['id']}"] = message
            before = page[-1]["id"]
        return found



def ensure_catalog_channel(api: DiscordAPI) -> str:
    """Reutilise ou cree #catalogue. Jamais de doublon ni modification de salon existant."""
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    if CHANNEL_ID:
        matches = [c for c in channels if str(c["id"]) == CHANNEL_ID]
        if len(matches) != 1 or matches[0]["type"] != CHANNEL_TEXT:
            raise RuntimeError("CATALOG_CHANNEL_ID ne correspond pas a un salon textuel accessible")
        return CHANNEL_ID

    # L'identification par marqueur garantit la reutilisation du salon entre
    # les executions ephemeres de GitHub Actions.
    existing = [c for c in channels
                if c.get("type") == CHANNEL_TEXT
                and CATALOG_TOPIC_MARKER in (c.get("topic") or "")]
    if len(existing) == 1:
        print("Salon catalogue existant :", existing[0]["id"])
        return str(existing[0]["id"])
    if len(existing) > 1:
        raise RuntimeError("Plusieurs salons de catalogue identifies : correction manuelle necessaire")

    if any(c.get("name") == "catalogue" for c in channels):
        raise RuntimeError(
            "Un salon #catalogue existe deja sans marqueur du bot. "
            "Indiquer son ID dans la variable CATALOG_CHANNEL_ID pour le reutiliser."
        )
    payload = {
        "name": "catalogue",
        "type": CHANNEL_TEXT,
        "topic": "Catalogue general automatique des forums publics — " + CATALOG_TOPIC_MARKER,
    }
    try:
        created = api.call("POST", f"/guilds/{GUILD_ID}/channels", json=payload)
    except RuntimeError as exc:
        raise RuntimeError(
            "Creation de #catalogue impossible. Verifier que le bot possede "
            "la permission Discord 'Gerer les salons' et son jeton est valide."
        ) from exc
    print("Salon #catalogue cree :", created["id"])
    return str(created["id"])


def get_items(api: DiscordAPI):
    guild = api.call("GET", f"/guilds/{GUILD_ID}")
    everyone = next((r for r in guild["roles"] if str(r["id"]) == GUILD_ID), None)
    if everyone is None:
        raise RuntimeError("Role @everyone introuvable")
    permissions = int(everyone["permissions"])
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    categories = {str(ch["id"]): ch for ch in channels if ch["type"] == CHANNEL_CATEGORY}
    active = api.call("GET", f"/guilds/{GUILD_ID}/threads/active").get("threads", [])
    by_parent = defaultdict(dict)
    for t in active:
        by_parent[str(t.get("parent_id"))][str(t["id"])] = t

    grouped = defaultdict(lambda: defaultdict(list))
    for channel in channels:
        if str(channel["id"]) in EXCLUDE | {CHANNEL_ID}:
            continue
        # Les copies textuelles sont déjà représentées par les publications
        # originales des forums. Les réindexer doublerait 30 sites et ferait
        # dépasser au catalogue central la limite Discord Components V2.
        # Elles restent visibles normalement dans la barre latérale.
        if (channel.get("type") == CHANNEL_TEXT
                and SITE_MIRROR_TOPIC_MARKER in (channel.get("topic") or "")):
            continue
        if not public_channel(channel, permissions, categories, GUILD_ID):
            continue
        category = categories.get(str(channel.get("parent_id")))
        cat_id, cat_name = ((str(category["id"]), category["name"]) if category
                            else ("uncategorized", "Sans categorie"))
        name = channel["name"]
        link = f"https://discord.com/channels/{GUILD_ID}/{channel['id']}"
        if channel["type"] == CHANNEL_TEXT:
            grouped[(cat_id, cat_name)][name].append(("Ouvrir le salon", link))
            continue
        threads = dict(by_parent.get(str(channel["id"]), {}))
        # Une erreur de lecture des archives bloque toute publication plutot
        # que de publier une liste tronquee.
        for t in api.archived(str(channel["id"])):
            threads[str(t["id"])] = t
        entries = grouped[(cat_id, cat_name)][name]
        entries.append(("Ouvrir le forum", link))
        for t in threads.values():
            if t.get("type") == 12:
                continue
            entries.append((t["name"], f"https://discord.com/channels/{GUILD_ID}/{t['id']}"))
    return grouped



def category_style(name: str):
    """Ordre, emoji, couleur et courte description. Les noms reels restent intacts."""
    n = fold(name)
    if "stream" in n or "film" in n or "serie" in n or "cinema" in n:
        return (0, "🎬", 0x9C84F7, "Films, séries et plateformes")
    if "iptv" in n or "television" in n:
        return (1, "📺", 0x45B5D9, "Télévision et chaînes")
    if "anime" in n or "manga" in n:
        return (2, "🌸", 0xEC91C6, "Anime et animation")
    if "telecharg" in n or "download" in n or "torrent" in n:
        return (3, "📥", 0x5F9CF0, "Téléchargements et ressources")
    if "anti" in n and ("pub" in n or "publicit" in n):
        return (4, "🛡️", 0x52CDAA, "Bloqueurs de publicité et protection")
    if "android" in n or "mobile" in n or "application" in n:
        return (5, "📱", 0x8ACB73, "Applications et outils mobiles")
    if "jeu" in n or "gaming" in n or "game" in n:
        return (6, "🎮", 0xF0B96A, "Jeux et ressources")
    if "livre" in n or "comic" in n or "lecture" in n:
        return (7, "📖", 0xD5A1FC, "Livres, comics et lecture")
    return (100, "📁", 0x8493AA, "Ressources du serveur")


def forum_icon(name: str) -> str:
    n = fold(name)
    if "film" in n and "serie" in n:
        return "📺"
    if "film" in n:
        return "🎞️"
    if "serie" in n:
        return "📺"
    if "iptv" in n:
        return "📡"
    if "anime" in n or "manga" in n:
        return "🌸"
    if "jeu" in n or "gaming" in n:
        return "🎮"
    if "livre" in n or "comic" in n:
        return "📖"
    if "anti" in n and ("pub" in n or "publicit" in n):
        return "🛡️"
    if "android" in n:
        return "📱"
    return "📂"


def pretty(name: str) -> str:
    return " ".join(name.replace("-", " ").replace("_", " ").split()).upper()


def category_sort(item):
    category = item[0][1]
    return (category_style(category)[0], fold(category))


def category_counts(forums):
    return sum(1 for entries in forums.values()
               for title, _ in entries
               if title not in ("Ouvrir le forum", "Ouvrir le salon"))


def overview_embed(grouped, links=None):
    links = links or {}
    now = datetime.now(timezone.utc)
    count = sum(category_counts(forums) for forums in grouped.values())
    lines = []
    for (cat_id, cat_name), forums in sorted(grouped.items(), key=category_sort):
        icon = category_style(cat_name)[1]
        name = escape(cat_name)
        url = links.get(cat_id)
        label = f"[**{name}**]({url})" if url else f"**{name}**"
        line = f"{icon} {label}  ·  {category_counts(forums)} fiche(s)"
        if len("\n".join(lines)) + len(line) > 2700:
            lines.append("• D'autres rubriques sont détaillées plus bas.")
            break
        lines.append(line)
    index = "\n".join(lines) if lines else "Les rubriques publiques apparaîtront ici."
    description = (
        "> **Ton point d'entrée vers toutes les ressources du serveur.**\n"
        "> Fiches classées par catégorie et par forum, avec des liens directs.\n\n"
        "**🧭  EXPLORER LES CATÉGORIES**\n\n" + index +
        "\n\n━━━━━━━━━━━━━━━━\n"
        f"📚 **{count} fiches**  •  🗂️ **{len(grouped)} catégories**\n"
        "🔎 Sélectionne une rubrique pour ouvrir sa page.\n"
        f"🔄 Dernière synchronisation : <t:{int(now.timestamp())}:f>\n"
        "🔒 Salons privés exclus de l'index public."
    )
    return {
        "title": "✦ LES NOTES D'ELIKTO  |  CATALOGUE",
        "description": description,
        "color": 0x977AF4,
        "footer": {"text": f"Index général · mise à jour automatique · {MARKER}index"},
    }


def render(grouped):
    """Un message d'accueil et un embed par catégorie, paginés si nécessaire."""
    rendered = [(f"{MARKER}index", "📚  **LE CATALOGUE DES NOTES D'ELIKTO**",
                 overview_embed(grouped))]
    for (cat_id, cat_name), forums in sorted(grouped.items(), key=category_sort):
        _, icon, color, subtitle = category_style(cat_name)
        lines = []
        for name, entries in sorted(forums.items(), key=lambda e: fold(e[0])):
            thread_items = [(title, url) for title, url in entries
                            if title not in ("Ouvrir le forum", "Ouvrir le salon")]
            open_forum = next((url for title, url in entries
                               if title == "Ouvrir le forum"), None)
            open_text = next((url for title, url in entries if title == "Ouvrir le salon"), None)
            local_count = len(thread_items)
            kind = "fiche" if local_count == 1 else "fiches"
            lines.append(
                f"\n**{forum_icon(name)}  {escape(pretty(name))}**"
                + (f"  ·  {local_count} {kind}" if open_forum else "")
            )
            if open_forum:
                lines.append(f"↳ [Ouvrir le forum]({open_forum})")
            elif open_text:
                lines.append(f"↳ [Accéder au salon]({open_text})")
            for title, url in sorted(thread_items, key=lambda e: fold(e[0])):
                lines.append(f"• [{escape(title[:95])}]({url})")
            lines.append("")
        pages = chunks(lines, max_chars=3250)
        count = category_counts(forums)
        for i, body in enumerate(pages, 1):
            marker = f"{MARKER}cat:{cat_id}:{i}"
            description = (
                f"> {subtitle}\n"
                f"> **{count} fiches**  •  **{len(forums)} salons**\n"
                "\n━━━━━━━━━━━━━━━━\n"
                + body
            )
            embed = {
                "title": f"{icon}  {cat_name}"[:235]
                         + (f" · Page {i}/{len(pages)}" if len(pages) > 1 else ""),
                "description": description,
                "color": color,
                "footer": {"text": f"Catalogue · synchronisation 6 h · {marker}"},
            }
            rendered.append((marker, f"{icon}  **{cat_name.upper()}**"[:180], embed))
    return rendered


def publish(api: DiscordAPI, grouped):
    """Un seul message Discord : toutes les rubriques dans des cartes colorees."""
    from dashboard_unique import render_dashboard
    from catalogue_v2 import FLAG_V2, marker_id, normalize

    me = str(api.call("GET", "/users/@me")["id"])
    existing = api.own_messages(CHANNEL_ID, me)
    marker = f"{MARKER}index"
    components = render_dashboard(grouped)
    payload = {
        "flags": FLAG_V2,
        "components": components,
        "allowed_mentions": {"parse": []},
    }

    found = existing.get(f"v2id:{marker_id(marker)}") or existing.get(marker)
    if found:
        old_components = found.get("components", [])
        if (not (found.get("flags", 0) & FLAG_V2)
                or normalize(old_components) != normalize(components)):
            data = dict(payload)
            if not (found.get("flags", 0) & FLAG_V2):
                data.update({"content": None, "embeds": []})
            api.call("PATCH", f"/channels/{CHANNEL_ID}/messages/{found['id']}", json=data)
            print("Tableau de bord unique actualise")
        dashboard_id = str(found["id"])
    else:
        created = api.call("POST", f"/channels/{CHANNEL_ID}/messages", json=payload)
        dashboard_id = str(created["id"])
        print("Tableau de bord unique cree")

    # Nettoyage exclusivement apres creation/actualisation reussie :
    # les anciennes pages de categories appartiennent au bot, pas aux auteurs.
    # Les cartes V2 avaient un pied de page caracteristique.
    stale_ids = set()
    for key, message in existing.items():
        message_id = str(message["id"])
        if message_id == dashboard_id:
            continue
        old_embed = key.startswith(f"{MARKER}cat:")
        old_v2 = any(
            "Catalogue synchronisé" in child.get("content", "")
            for parent in message.get("components", [])
            for child in parent.get("components", [])
            if isinstance(child, dict)
        )
        if old_embed or old_v2:
            stale_ids.add(message_id)

    for message_id in stale_ids:
        api.call("DELETE", f"/channels/{CHANNEL_ID}/messages/{message_id}")
        print("Ancienne carte de rubrique retiree :", message_id)

    print(
        f"Termine : un seul message central, {len(grouped)} categories, "
        f"{len(stale_ids)} anciennes cartes retirees."
    )



def main():
    global CHANNEL_ID
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or not GUILD_ID.isdigit():
        raise SystemExit("Configurer DISCORD_BOT_TOKEN dans les secrets GitHub Actions")
    api = DiscordAPI(token)
    CHANNEL_ID = ensure_catalog_channel(api)
    grouped = get_items(api)
    from export_catalogue import write_catalogue
    write_catalogue(grouped)
    print("Rubriques publiques indexees :",
          [(name, list(forums)) for (_, name), forums in grouped.items()])
    publish(api, grouped)


if __name__ == "__main__":
    main()
