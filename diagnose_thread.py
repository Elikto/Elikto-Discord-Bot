"""Diagnostic ponctuel de la fiche Movix. Ne lit ni contenu ni secrets des messages."""
import os
from sync_catalogue import DiscordAPI, GUILD_ID, public_channel, CHANNEL_CATEGORY

THREAD_ID = "1430991985572839547"

def main():
    api = DiscordAPI(os.environ["DISCORD_BOT_TOKEN"])
    guild = api.call("GET", f"/guilds/{GUILD_ID}")
    channels = api.call("GET", f"/guilds/{GUILD_ID}/channels")
    cats = {str(c["id"]): c for c in channels if c["type"] == CHANNEL_CATEGORY}
    everyone = next(r for r in guild["roles"] if str(r["id"]) == GUILD_ID)
    permission = int(everyone["permissions"])
    print("DIAG thread_id =", THREAD_ID)
    try:
        thread = api.call("GET", f"/channels/{THREAD_ID}")
    except RuntimeError as e:
        print("DIAG thread_lookup =", str(e).split(" : HTTP ")[-1][:80])
        return
    keys = ["id", "name", "type", "parent_id", "guild_id", "owner_id"]
    print("DIAG thread =", {k: thread.get(k) for k in keys if k != "owner_id"})
    print("DIAG metadata =", {k: thread.get("thread_metadata", {}).get(k)
                                 for k in ("archived", "locked", "archive_timestamp", "invitable")})
    parent = next((c for c in channels if str(c["id"]) == str(thread.get("parent_id"))), None)
    if not parent:
        print("DIAG parent = ABSENT parmi les salons du serveur")
        return
    print("DIAG parent =", {k: parent.get(k) for k in ("id", "name", "type", "parent_id", "nsfw")})
    print("DIAG parent_public =", public_channel(parent, permission, cats, GUILD_ID))
    cat = cats.get(str(parent.get("parent_id")))
    print("DIAG parent_category =", cat.get("name") if cat else None)
    active = api.call("GET", f"/guilds/{GUILD_ID}/threads/active")
    print("DIAG active_listed =", any(str(t.get("id")) == THREAD_ID for t in active.get("threads", [])))
    if parent.get("type") == 15:
        try:
            in_archive = any(str(t["id"]) == THREAD_ID for t in api.archived(str(parent["id"])))
            print("DIAG public_archives_listed =", in_archive)
        except RuntimeError as e:
            print("DIAG archive_error =", type(e).__name__)
    print("DIAG currently_discarded_if_private =", thread.get("type") == 12)

if __name__ == "__main__":
    main()
