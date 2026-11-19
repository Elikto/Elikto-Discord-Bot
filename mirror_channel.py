"""A dedicated text channel with the same verified mirror dropdown as the forum.

The old forum remains intact. Mirrors are scanned ONCE by mirror_watch,
then the identical source data renders the new text channel's bot card.
No site URLs or logins are stored in channel topics or duplicated messages.
"""
from __future__ import annotations

import json
import os

from mirror_watch import (
    THREAD_ID, GUILD_ID, STATE_PATH, render_site_cards, upsert_bot_message,
)
from sync_catalogue import DiscordAPI, CHANNEL_TEXT, CHANNEL_CATEGORY

CATEGORY_NAMES = ("🎬 Streaming · Sites", "Streaming · Sites")
CHANNEL_NAME = "streaming-hd-miroirs"
TOPIC_MARKER = "[elikto:streaming-hd-mirrors:v1]"
CHANNEL_TOPIC = (
    "🎬 Streaming HD · adresses miroirs vérifiées à 7 h et 18 h (Paris). "
    "🟢 Vérifié · 🟡 À confirmer · 🔴 Indisponible. " + TOPIC_MARKER
)


def find_or_create_channel(api, state=None):
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    # The original Streaming HD site-channel already exists. Reuse it
    # rather than creating a duplicate streaming-hd-miroirs channel.
    known = ((state or {}).get("site_channel_mirrors") or {}).get(THREAD_ID, {})
    preferred_id = str(known.get("channel_id") or "")
    original_marker = f"[elikto:site-channel:v1:{THREAD_ID}]"
    originals = [
        c for c in channels
        if c.get("type") == CHANNEL_TEXT
        and (original_marker in (c.get("topic") or "")
             or (preferred_id and str(c.get("id")) == preferred_id))
    ]
    if len(originals) > 1:
        raise RuntimeError("Plusieurs salons associés à Streaming HD : doublon à examiner")
    if originals:
        return str(originals[0]["id"]), False
    found = [
        channel for channel in channels
        if channel.get("type") == CHANNEL_TEXT
        and TOPIC_MARKER in (channel.get("topic") or "")
    ]
    if len(found) > 1:
        raise RuntimeError("Plusieurs salons Streaming HD miroirs gérés par le bot")
    if found:
        return str(found[0]["id"]), False

    category = next(
        (c for name in CATEGORY_NAMES for c in channels
         if c.get("type") == CHANNEL_CATEGORY and c.get("name") == name),
        None,
    )
    if not category:
        raise RuntimeError("Catégorie Streaming · Sites absente ; ne pas créer à la racine")

    # Recover an exact preexisting, empty-topic channel in the correct
    # category; do not take over anyone's custom topic or other channels.
    preexisting = [
        c for c in channels if (
            c.get("type") == CHANNEL_TEXT
            and c.get("name") == CHANNEL_NAME
            and str(c.get("parent_id")) == str(category["id"])
            and not (c.get("topic") or "").strip()
        )
    ]
    if len(preexisting) == 1:
        channel_id = str(preexisting[0]["id"])
        api.call("PATCH", f"/channels/{channel_id}", json={"topic": CHANNEL_TOPIC})
        print("MIROIRS SALON : salon existant réutilisé", channel_id)
        return channel_id, False

    existing_names = {c.get("name") for c in channels
                      if str(c.get("parent_id")) == str(category["id"])}
    name = CHANNEL_NAME
    if name in existing_names:
        name = CHANNEL_NAME + "-suivi"
    if name in existing_names:
        raise RuntimeError("Nom de salon déjà utilisé ; abandon pour éviter un doublon")
    created = api.call("POST", f"/guilds/{GUILD_ID}/channels", json={
        "name": name,
        "type": CHANNEL_TEXT,
        "parent_id": str(category["id"]),
        "topic": CHANNEL_TOPIC,
    })
    channel_id = str(created["id"])
    print("MIROIRS SALON : créé", name, channel_id)
    return channel_id, True


def sync_mirror_channel(api, state, *, dropdown_enabled=False):
    source = (state.get("tracked_threads") or {}).get(THREAD_ID) or {}
    sites = source.get("display_sites") or []
    if not sites:
        raise RuntimeError("Aucun miroir contrôlé dans l'ancien forum ; salon non créé")

    # Keep the same selection IDs that the signed interaction endpoint
    # already handles in the old forum.
    cards = render_site_cards(
        sites, source.get("name") or "Streaming HD miroir",
        dropdown_enabled=dropdown_enabled, status_badges=True,
    )
    channel_id, created = find_or_create_channel(api, state)
    bot_id = str(api.call("GET", "/users/@me")["id"])
    tracked = state.setdefault("streaming_mirror_channel", {})
    if str(tracked.get("channel_id")) != channel_id:
        tracked.pop("message_id", None)
    tracked["channel_id"] = channel_id
    original = (state.get("site_channel_mirrors") or {}).get(THREAD_ID, {})
    if (str(original.get("channel_id")) == channel_id
            and original.get("message_id")):
        # Reuse and edit the card already posted in the requested channel.
        tracked["message_id"] = str(original["message_id"])
    message_id = upsert_bot_message(
        api, tracked, bot_id, cards, channel_id=channel_id,
    )
    tracked["message_id"] = message_id
    if str(original.get("channel_id")) == channel_id:
        original["message_id"] = message_id
    print(
        "MIROIRS SALON :",
        len(sites), "adresses dans le menu ; salon", channel_id,
        "; carte", message_id, "; nouveau salon =", created,
        "; menu interactif =", dropdown_enabled,
    )
    return channel_id, message_id


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN absent")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    channel_id, _ = sync_mirror_channel(
        DiscordAPI(token), state,
        dropdown_enabled=os.environ.get("DISCORD_MIRROR_DROPDOWN_ENABLED") == "1",
    )
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("MIROIRS SALON : synchronisation effectuée pour", channel_id)


if __name__ == "__main__":
    main()
