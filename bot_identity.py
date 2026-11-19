"""Afficher le bot sous le nom « checker link » sur le serveur Discord.

Seul le pseudonyme de serveur est modifié : l'application, l'avatar,
l'identifiant et le jeton Discord restent inchangés.
"""
import os

from sync_catalogue import DiscordAPI, GUILD_ID

BOT_NICKNAME = "checker link"


def ensure_bot_nickname(api, guild_id=GUILD_ID):
    member = api.call("GET", f"/guilds/{guild_id}/members/@me")
    if str(member.get("nick") or "") == BOT_NICKNAME:
        print("BOT : pseudonyme déjà configuré")
        return False
    changed = api.call(
        "PATCH", f"/guilds/{guild_id}/members/@me",
        json={"nick": BOT_NICKNAME},
    )
    if str(changed.get("nick") or "") != BOT_NICKNAME:
        raise RuntimeError("Discord n'a pas confirmé le nouveau pseudonyme")
    print("BOT : renommé checker link sur le serveur")
    return True


def main():
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Jeton Discord absent")
    ensure_bot_nickname(DiscordAPI(token))


if __name__ == "__main__":
    main()
