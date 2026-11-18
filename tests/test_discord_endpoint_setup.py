import unittest
from unittest.mock import Mock

from discord_endpoint_setup import configure

KEY = "a" * 64
URL = "https://example.vercel.app/api/discord-interactions"


class DiscordEndpointTests(unittest.TestCase):
    def app(self, key=KEY, endpoint=None):
        return Mock(status_code=200, json=lambda: {
            "verify_key": key,
            "interactions_endpoint_url": endpoint,
        })

    def test_configures_matching_application_once(self):
        http = Mock()
        http.get.return_value = self.app()
        http.patch.return_value = Mock(
            status_code=200,
            json=lambda: {"interactions_endpoint_url": URL},
        )
        self.assertTrue(configure("fake-test-token", KEY, URL, http=http))
        self.assertEqual(http.patch.call_args.kwargs["json"],
                         {"interactions_endpoint_url": URL})

    def test_does_not_replace_existing_endpoint(self):
        http = Mock()
        http.get.return_value = self.app(endpoint="https://existing.example.org/")
        self.assertFalse(configure("test", KEY, URL, http=http))
        http.patch.assert_not_called()

    def test_does_not_modify_different_application(self):
        http = Mock()
        http.get.return_value = self.app(key="b" * 64)
        self.assertFalse(configure("test", KEY, URL, http=http))
        http.patch.assert_not_called()

    def test_existing_endpoint_avoids_unnecessary_patch(self):
        http = Mock()
        http.get.return_value = self.app(endpoint=URL)
        self.assertTrue(configure("test", KEY, URL, http=http))
        http.patch.assert_not_called()

    def test_failed_ping_or_invalid_credential_keeps_fallback(self):
        http = Mock()
        http.get.return_value = self.app()
        http.patch.return_value = Mock(status_code=400)
        self.assertFalse(configure("test", KEY, URL, http=http))
        self.assertFalse(configure(None, KEY, URL, http=http))

    def test_discord_error_keeps_fallback(self):
        http = Mock()
        http.get.return_value = Mock(status_code=401)
        self.assertFalse(configure("test", KEY, URL, http=http))
        http.patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
