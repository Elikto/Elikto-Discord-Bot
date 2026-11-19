"""Read Tr4ker's credentials only from a verified Discord server owner post.

The original Tr4ker forum starter was written by the bot and contains no
credentials. The owner can provide them in ONE message in the original forum thread only.
No values are written to source code, GitHub Actions logs or monitoring JSON.
"""
from forum_cards import credential_fields

TR4KER_THREAD_ID = "1572752361967853713"
TEAMFLIX_THREAD_ID = "1565898044518568187"


def _owner_post_credentials(
    api, thread_id, forum_title, guild_id, mirror_channel_id=None
):
    """Read the latest complete owner-authored post from this source forum.

    Never inspect the mirror channel or use GitHub secrets as a substitute.
    """
    permitted = {TR4KER_THREAD_ID: "tr4ker", TEAMFLIX_THREAD_ID: "teamflix"}
    if permitted.get(str(thread_id)) != str(forum_title or "").casefold().strip():
        return None
    try:
        guild = api.call("GET", f"/guilds/{guild_id}")
        owner_id = str(guild.get("owner_id") or "")
        if not owner_id.isdigit():
            return None
    except (RuntimeError, AssertionError, ValueError, TypeError):
        return None

    candidates = []
    before = None
    for _ in range(20):
        params = {"limit": 100}
        if before:
            params["before"] = before
        try:
            messages = api.call(
                "GET", f"/channels/{thread_id}/messages",
                params=params
            )
        except (RuntimeError, AssertionError, ValueError, TypeError):
            break
        if not messages:
            break
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
                try:
                    candidates.append((int(message.get("id") or 0), fields))
                except (ValueError, TypeError):
                    continue
        if len(messages) < 100:
            break
        try:
            oldest = str(min(int(item["id"]) for item in messages))
        except (TypeError, ValueError, KeyError):
            break
        if oldest == before:
            break
        before = oldest
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry[0])[1]


def tr4ker_owner_post_credentials(
    api, thread_id, forum_title, guild_id, mirror_channel_id=None
):
    if str(thread_id) != TR4KER_THREAD_ID:
        return None
    return _owner_post_credentials(
        api, thread_id, forum_title, guild_id, mirror_channel_id
    )


def teamflix_owner_post_credentials(
    api, thread_id, forum_title, guild_id, mirror_channel_id=None
):
    """Only an owner-authored post in the source Teamflix forum qualifies."""
    if str(thread_id) != TEAMFLIX_THREAD_ID:
        return None
    return _owner_post_credentials(
        api, thread_id, forum_title, guild_id, mirror_channel_id
    )
