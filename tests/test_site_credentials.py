"""Tr4ker credentials may be taken ONLY from one public guild-owner reply."""
import unittest

from site_credentials import (
    tr4ker_owner_post_credentials, TR4KER_THREAD_ID,
)

GUILD_ID = "1430984645012357263"
OWNER_ID = "111111111111111111"


class FakeDiscord:
    def __init__(self, messages, owner_id=OWNER_ID):
        self.messages = messages
        self.owner_id = owner_id
        self.calls = []

    def call(self, method, path, *, params=None):
        self.calls.append((method, path, params))
        if method == "GET" and path == f"/guilds/{GUILD_ID}":
            return {"owner_id": self.owner_id}
        if method == "GET" and path == f"/channels/{TR4KER_THREAD_ID}/messages":
            return self.messages
        raise AssertionError(f"Unexpected request: {method} {path}")


def reply(content, author=OWNER_ID, *, bot=False):
    return {
        "id": "1700000000000000000",
        "author": {"id": author, "bot": bot},
        "content": content,
    }


class Tr4kerCredentialsTests(unittest.TestCase):
    def test_guild_owner_can_provide_both_credentials_in_one_reply(self):
        api = FakeDiscord([reply(
            "👤 Utilisateur : login-de-test@example.org\n"
            "🔑 Mot de passe : pass-for-tests-only"
        )])
        value = tr4ker_owner_post_credentials(
            api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID
        )
        self.assertEqual(value, {
            "user": "login-de-test@example.org",
            "password": "pass-for-tests-only",
        })
        self.assertEqual(len(api.calls), 2)

    def test_other_member_cannot_replace_guild_owner_credentials(self):
        api = FakeDiscord([
            reply("Utilisateur : faux\nMot de passe : faux", "222222222222222222"),
            reply("Utilisateur : ok\nMot de passe : real", OWNER_ID),
        ])
        self.assertEqual(
            tr4ker_owner_post_credentials(
                api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID
            )["user"],
            "ok",
        )

    def test_bot_messages_and_split_replies_are_not_accepted(self):
        api = FakeDiscord([
            reply("Utilisateur : bot\nMot de passe : bot", OWNER_ID, bot=True),
            reply("Utilisateur : foo"),
            reply("Mot de passe : bar"),
        ])
        self.assertIsNone(tr4ker_owner_post_credentials(
            api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID
        ))

    def test_other_threads_cannot_access_owner_login(self):
        api = FakeDiscord([reply("Utilisateur : foo\nMot de passe : bar")])
        self.assertIsNone(tr4ker_owner_post_credentials(
            api, "1650000000000000000", "C411", GUILD_ID
        ))
        self.assertEqual(api.calls, [])

    def test_missing_owner_id_is_ignored(self):
        api = FakeDiscord([reply("Utilisateur : a\nMot de passe : b")], owner_id="")
        self.assertIsNone(tr4ker_owner_post_credentials(
            api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID
        ))


if __name__ == "__main__":
    unittest.main()
