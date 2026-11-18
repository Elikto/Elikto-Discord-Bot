"""Créer la fiche publique Tr4ker dans le forum Discord Torrent une seule fois.

Aucun compte privé ni mot de passe n'est nécessaire à la surveillance d'URL.
Les identifiants fournis par un utilisateur ne sont jamais publiés ni stockés.
"""
from __future__ import annotations

import os

from sync_catalogue import DiscordAPI, GUILD_ID, fold

SEED_MARKER = "[elikto-site-seed:tr4ker:v1]"
SITE_URL = "https://tr4ker.net/"
THREAD_TITLE = "Tr4ker"
STARTER_CONTENT = (
    "✦ **Tr4ker** ✦\n\n"
    "📝 **Description**\n"
    "Tracker BitTorrent francophone proposant un catalogue de torrents "
    "(films, séries et autres catégories). Certaines fonctions peuvent "
    "nécessiter un compte.\n\n"
    "🌐 **Site :** " + SITE_URL + "\n\n"
    "🔄 Adresse contrôlée automatiquement chaque jour par le bot.\n"
    + SEED_MARKER
)


def find_torrent_forum(api):
    """Trouver exactement un vrai forum Torrent, pas un salon texte homonyme."""
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    matches = [ch for ch in channels
               if ch.get("type") == 15 and fold(ch.get("name", "")) == "torrent"]
    if len(matches) != 1:
        raise RuntimeError("Forum Torrent absent ou ambigu : aucune publication")
    return str(matches[0]["id"])


def existing_tr4ker(api, forum_id):
    """Contrôler actifs ET archives afin de ne jamais créer deux fils."""
    active = api.call("GET", f"/guilds/{GUILD_ID}/threads/active").get("threads", [])
    threads = [t for t in active if str(t.get("parent_id")) == forum_id]
    threads.extend(api.archived(forum_id))
    for thread in threads:
        title = fold(str(thread.get("name") or "")).strip()
        if title == "tr4ker" or title.startswith("tr4ker -") or title.startswith("tr4ker |"):
            return str(thread["id"])
    return None


def ensure_tr4ker_forum(api):
    """Créer une seule fiche. Sans droits Discord, ne modifier aucun autre fil."""
    forum_id = find_torrent_forum(api)
    existing = existing_tr4ker(api, forum_id)
    if existing:
        print("TR4KER : fiche Torrent déjà présente, réutilisée :", existing)
        return existing, False

    payload = {
        "name": THREAD_TITLE,
        "auto_archive_duration": 10080,
        "message": {
            "content": STARTER_CONTENT,
            "allowed_mentions": {"parse": []},
        },
    }
    created = api.call("POST", f"/channels/{forum_id}/threads", json=payload)
    thread_id = str(created["id"])
    print("TR4KER : nouvelle fiche créée dans Torrent :", thread_id)
    return thread_id, True


def main():
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token or not GUILD_ID.isdigit():
        raise SystemExit("Jeton du bot Discord non configuré")
    ensure_tr4ker_forum(DiscordAPI(token))


if __name__ == "__main__":
    main()
