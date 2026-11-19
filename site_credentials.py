"""Read Tr4ker's credentials only from a verified Discord server owner post.

The original Tr4ker forum starter was written by the bot and contains no
credentials. The owner can provide them in ONE reply in that public thread.
No values are written to source code, GitHub Actions logs or monitoring JSON.
"""
from forum_cards import credential_fields

TR4KER_THREAD_ID = "1572752361967853713"


def tr4ker_owner_post_credentials(api, thread_id, forum_title, guild_id):
    if (str(thread_id) != TR4KER_THREAD_ID
            or str(forum_title or "").casefold().strip() != "tr4ker"):
        return None
    try:
        guild = api.call("GET", f"/guilds/{guild_id}")
        owner_id = str(guild.get("owner_id") or "")
        if not owner_id.isdigit():
            return None
        messages = api.call(
            "GET", f"/channels/{thread_id}/messages", params={"limit": 100}
        )
    except (RuntimeError, AssertionError, ValueError, TypeError):
        return None

    # Only the guild owner may supply a pair. Both fields must occur in a
    # single message to prevent unrelated posts from being combined.
    for message in messages:
        author = message.get("author") or {}
        if (str(author.get("id")) != owner_id or author.get("bot")
                or message.get("webhook_id")):
            continue
        fields, _ = credential_fields(message.get("content") or "")
        if {"user", "password"}.issubset(fields):
            return fields
    return None
