"""Tests du changement de pseudonyme sans API Discord réelle."""
import unittest
from unittest.mock import patch

from bot_identity import BOT_NICKNAME, ensure_bot_nickname

GUILD_ID = "1430984645012357263"


class FakeDiscord:
    def __init__(self, nick):
        self.nick = nick
        self.calls = []

    def call(self, method, path, *, json=None):
        self.calls.append((method, path, json))
        if method == "GET" and path == "/users/@me":
            return {"id": "999", "username": "scan stream"}
        if method == "GET" and path == f"/guilds/{GUILD_ID}/members/999":
            return {"nick": self.nick, "user": {"id": "999"}}
        if method == "PATCH" and path == f"/guilds/{GUILD_ID}/members/@me":
            self.nick = json["nick"]
            return {"nick": self.nick}
        raise AssertionError("Unexpected Discord API call")


class BotIdentityTests(unittest.TestCase):
    def test_renames_server_nickname_without_changing_account_or_avatar(self):
        api = FakeDiscord("scan stream")
        self.assertTrue(ensure_bot_nickname(api))
        self.assertEqual(api.nick, BOT_NICKNAME)
        self.assertEqual(len(api.calls), 3)
        self.assertEqual(api.calls[0][:2], ("GET", "/users/@me"))
        self.assertEqual(api.calls[1][:2], (
            "GET", f"/guilds/{GUILD_ID}/members/999"
        ))
        self.assertEqual(api.calls[2], (
            "PATCH", f"/guilds/{GUILD_ID}/members/@me",
            {"nick": "checker link"},
        ))

    def test_idempotent_when_already_renamed(self):
        api = FakeDiscord("checker link")
        self.assertFalse(ensure_bot_nickname(api))
        self.assertEqual(len(api.calls), 2)

    def test_refuses_unconfirmed_nickname(self):
        api = FakeDiscord("scan stream")

        def no_confirmation(method, path, *, json=None):
            if path == "/users/@me":
                return {"id": "999"}
            return {"nick": "scan stream"}

        api.call = no_confirmation
        with self.assertRaisesRegex(RuntimeError, "pas confirmé"):
            ensure_bot_nickname(api)


if __name__ == "__main__":
    unittest.main()
