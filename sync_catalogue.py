"""Catalogue global du serveur Discord, lance ponctuellement par GitHub Actions.

Indexe les salons textuels et les publications actives ou archivees des forums
visibles par @everyone. Ne modifie pas les fiches d'origine.
"""
from __future__ import annotations

from collections import defaultdict
import os
import sys
import time
import unicodedata

import requests

API = "https://discord.com/api/v10"
VIEW_CHANNEL = 1 << 10
CHANNEL_TEXT = 0
CHANNEL_CATEGORY = 4
CHANNEL_FORUM = 15
MARKER = "elikto:catalog:"
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
                    marker = embed.get("footer", {}).get("text", "")
                    if marker.startswith(MARKER):
                        found[marker] = message
            before = page[-1]["id"]
        return found


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
        if not public_channel(channel, permissions, categories, GUILD_ID):
            continue
        category = categories.get(str(channel.get("parent_id")))
        cat_id, cat_name = ((str(category["id"]), category["name"]) if category
                            else ("uncategorized", "Sans categorie"))
        name = channel["name"]
        link = f"https://discord.com/channels/{GUILD_ID}/{channel['id']}"
        if channel["type"] == CHANNEL_TEXT:
            grouped[(cat_id, cat_name)][name].append(("#" + name, link))
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


def render(grouped):
    rendered = []
    categories_summary = []
    total = 0
    for (cat_id, cat_name), forums in sorted(grouped.items(), key=lambda e: fold(e[0][1])):
        lines, count = [], 0
        for name, entries in sorted(forums.items(), key=lambda e: fold(e[0])):
            lines.append("\n**" + escape(name) + "**")
            for title, url in sorted(entries, key=lambda e: (e[0] != "Ouvrir le forum", fold(e[0]))):
                if title != "Ouvrir le forum" and not title.startswith("#"):
                    count += 1
                lines.append("• [" + escape(title[:95]) + "](" + url + ")")
        total += count
        categories_summary.append("• **" + escape(cat_name) + f"** — {count} fiche(s)")
        pages = chunks(lines)
        for i, body in enumerate(pages, 1):
            marker = f"{MARKER}cat:{cat_id}:{i}"
            embed = {
                "title": f"📁 {cat_name}" + (f" · {i}/{len(pages)}" if len(pages) > 1 else ""),
                "description": body,
                "color": 5793266,
                "footer": {"text": marker},
            }
            rendered.append((marker, f"📂 {cat_name} · {i}/{len(pages)}", embed))
    description = "\n".join(categories_summary)[:3700] or "Aucun salon public indexable."
    description += f"\n\n**{total} fiches** · synchronisation automatique toutes les 6 heures."
    summary = {
        "title": "📚 Les notes d'Elikto — Catalogue general",
        "description": description,
        "color": 5793266,
        "footer": {"text": f"{MARKER}index"},
    }
    return [(f"{MARKER}index", "📚 Catalogue general — Les notes d'Elikto", summary)] + rendered


def publish(api: DiscordAPI, grouped):
    me = str(api.call("GET", "/users/@me")["id"])
    existing = api.own_messages(CHANNEL_ID, me)
    rendered = render(grouped)
    wanted = set()
    for marker, content, embed in rendered:
        wanted.add(marker)
        payload = {"content": content, "embeds": [embed], "allowed_mentions": {"parse": []}}
        if marker in existing:
            message = existing[marker]
            old_embed = next((e for e in message.get("embeds", []) if e.get("footer", {}).get("text") == marker), {})
            if (message.get("content") != content or
                old_embed.get("description") != embed["description"] or
                old_embed.get("title") != embed["title"]):
                api.call("PATCH", f"/channels/{CHANNEL_ID}/messages/{message['id']}", json=payload)
                print("Actualise", marker)
        else:
            api.call("POST", f"/channels/{CHANNEL_ID}/messages", json=payload)
            print("Cree", marker)
    for marker, message in existing.items():
        if marker not in wanted:
            # Ne supprime que les pages d'index du bot devenues inutiles.
            api.call("DELETE", f"/channels/{CHANNEL_ID}/messages/{message['id']}")
            print("Ancienne page d'index retiree", marker)
    print(f"Termine : {len(rendered)} messages dans {len(grouped)} categories.")


def main():
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or not GUILD_ID.isdigit() or not CHANNEL_ID.isdigit():
        raise SystemExit("Configurer DISCORD_BOT_TOKEN et CATALOG_CHANNEL_ID dans GitHub Actions")
    api = DiscordAPI(token)
    grouped = get_items(api)
    publish(api, grouped)


if __name__ == "__main__":
    main()
