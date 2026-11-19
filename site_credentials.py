"""Read Tr4ker's credentials only from a verified Discord server owner post.

The original Tr4ker forum starter was written by the bot and contains no
credentials. The owner can provide them in ONE message in the original thread or mirror.
No values are written to source code, GitHub Actions logs or monitoring JSON.
"""
from forum_cards import credential_fields

TR4KER_THREAD_ID = "1572752361967853713"


def tr4ker_owner_post_credentials(
    api, thread_id, forum_title, guild_id, mirror_channel_id=None
):
    """Read one complete owner-authored credential post from Tr4ker.

    Accepts the original forum and the NEW text channel. This lets the owner
    post directly in #tr4ker without returning to archived forum threads.
    The latest legitimate post wins. Never persist or log credential values.
    """
    if (str(thread_id) != TR4KER_THREAD_ID
            or str(forum_title or "").casefold().strip() != "tr4ker"):
        return None
    try:
        guild = api.call("GET", f"/guilds/{guild_id}")
        owner_id = str(guild.get("owner_id") or "")
        if not owner_id.isdigit():
            return None
    except (RuntimeError, AssertionError, ValueError, TypeError):
        return None

    candidates = []
    channels = [str(thread_id)]
    mirror_id = str(mirror_channel_id or "")
    if mirror_id.isdigit() and mirror_id not in channels:
        channels.insert(0, mirror_id)

    for channel_id in channels:
        try:
            messages = api.call(
                "GET", f"/channels/{channel_id}/messages",
                params={"limit": 100}
            )
        except (RuntimeError, AssertionError, ValueError, TypeError):
            continue
        for message in messages:
            author = message.get("author") or {}
            if (str(author.get("id")) != owner_id or author.get("bot")
                    or message.get("webhook_id")):
                continue
            fields, _ = credential_fields(message.get("content") or "")
            if {"user", "password"}.issubset(fields):
                # Allow an owner to hide the original Discord post with
                # spoiler syntax too. build_card will apply a single spoiler.
                secret = fields["password"].strip()
                if secret.startswith("||") and secret.endswith("||"):
                    secret = secret[2:-2]
                if not secret or len(secret) > 200:
                    continue
                fields["password"] = secret
                candidates.append((int(message.get("id") or 0), fields))
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry[0])[1]
