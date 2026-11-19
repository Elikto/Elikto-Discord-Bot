"""Tr4ker credentials may be taken ONLY from one public guild-owner reply."""
import unittest

from site_credentials import (
    tr4ker_owner_post_credentials, teamflix_owner_post_credentials,
    TR4KER_THREAD_ID, TEAMFLIX_THREAD_ID,
)

GUILD_ID = "1430984645012357263"
OWNER_ID = "111111111111111111"


class FakeDiscord:
    def __init__(self, messages, owner_id=OWNER_ID, mirror_messages=None):
        self.messages = messages
        self.mirror_messages = mirror_messages
        self.owner_id = owner_id
        self.calls = []

    def call(self, method, path, *, params=None):
        self.calls.append((method, path, params))
        if method == "GET" and path == f"/guilds/{GUILD_ID}":
            return {"owner_id": self.owner_id}
        if method == "GET" and path in (
            f"/channels/{TR4KER_THREAD_ID}/messages",
            f"/channels/{TEAMFLIX_THREAD_ID}/messages"
        ):
            return self.messages
        if method == "GET" and path == "/channels/1572759788167766058/messages":
            return self.mirror_messages or []
        raise AssertionError(f"Unexpected request: {method} {path}")


def reply(content, author=OWNER_ID, *, bot=False):
    return {
        "id": "1700000000000000000",
        "author": {"id": author, "bot": bot},
        "content": content,
    }


class Tr4kerCredentialsTests(unittest.TestCase):
    def test_teamflix_owner_post_is_supported_only_for_teamflix(self):
        api = FakeDiscord([reply(
            "Utilisateur : test-owner\nMot de passe : test-only"
        )])
        value = teamflix_owner_post_credentials(
            api, TEAMFLIX_THREAD_ID, "Teamflix", GUILD_ID
        )
        self.assertEqual(value, {"user": "test-owner", "password": "test-only"})
        self.assertIsNone(teamflix_owner_post_credentials(
            api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID
        ))

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

    def test_owner_can_hide_password_in_spoiler_and_card_adds_only_one(self):
        from forum_cards import build_card, all_text
        api = FakeDiscord([reply(
            "Utilisateur : test@example.org\n"
            "Mot de passe : ||hidden-only-in-discord||"
        )])
        found = tr4ker_owner_post_credentials(
            api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID
        )
        self.assertEqual(found["password"], "hidden-only-in-discord")
        card = build_card([{"url": "https://tr4ker.net/", "verified": True,
                            "status": "online"}], "", "Tr4ker",
                          credentials=found)
        self.assertIn(
            "||hidden-only-in-discord||",
            "\n".join(all_text(card)),
        )
        self.assertNotIn(
            "||||hidden-only-in-discord||||",
            "\n".join(all_text(card)),
        )

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

    def test_only_forum_messages_can_supply_credentials(self):
        api = FakeDiscord(
            [reply("Utilisateur : bon\\nMot de passe : original")],
            mirror_messages=[{
                "id": "1800000000000000000",
                "author": {"id": OWNER_ID},
                "content": "Utilisateur : mauvais\\nMot de passe : miroir",
            }]
        )
        found = tr4ker_owner_post_credentials(
            api, TR4KER_THREAD_ID, "Tr4ker", GUILD_ID,
            mirror_channel_id="1572759788167766058",
        )
        self.assertEqual(found, {"user": "bon", "password": "original"})
        self.assertFalse(any(
            "/1572759788167766058/messages" in path
            for _, path, _ in api.calls
        ))

    def test_teamflix_credentials_must_come_from_own_forum(self):
        api = FakeDiscord([
            reply("Utilisateur : team-test\\nMot de passe : pass-test")
        ])
        self.assertEqual(teamflix_owner_post_credentials(
            api, TEAMFLIX_THREAD_ID, "Teamflix", GUILD_ID
        ), {"user": "team-test", "password": "pass-test"})
        self.assertIsNone(teamflix_owner_post_credentials(
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
