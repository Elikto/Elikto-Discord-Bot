"""Configure l'endpoint Discord si le bot GitHub correspond à la clé publique.

Ce script utilise uniquement DISCORD_BOT_TOKEN depuis GitHub Actions.
Il ne révèle ni le jeton ni d'autres champs de la réponse API.
Le menu est désactivé si l'application est différente, si un autre endpoint
est configuré, ou si Discord ne vérifie pas le nouveau service HTTPS.
"""
import os
import re
import requests

API = "https://discord.com/api/v10"
HTTP_TIMEOUT = 12


def configure(bot_token, expected_public_key, endpoint, http=requests):
    """Retourne True seulement si l'application Discord accepte cet endpoint."""
    if not bot_token or not re.fullmatch(r"[a-f0-9]{64}", expected_public_key or "", re.I):
        print("INTERACTIONS : secret bot ou clé publique absent.")
        return False
    if not endpoint.startswith("https://") or not endpoint.endswith("/api/discord-interactions"):
        print("INTERACTIONS : URL de service incorrecte.")
        return False

    headers = {
        "Authorization": "Bot " + bot_token,
        "Content-Type": "application/json",
        "User-Agent": "EliktoDiscordBot (mirror-menu/1.0)",
    }
    try:
        r = http.get(API + "/applications/@me", headers=headers, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print(f"INTERACTIONS : lecture application impossible (HTTP {r.status_code}).")
            return False
        app = r.json()
        if str(app.get("verify_key") or "").casefold() != expected_public_key.casefold():
            print("INTERACTIONS : clé publique différente. Aucune modification Discord.")
            return False
        old = app.get("interactions_endpoint_url")
        if old and old != endpoint:
            print("INTERACTIONS : autre endpoint déjà configuré. Aucun remplacement.")
            return False
        if old == endpoint:
            print("INTERACTIONS : endpoint Discord déjà configuré.")
            return True

        r = http.patch(
            API + "/applications/@me",
            headers=headers,
            json={"interactions_endpoint_url": endpoint},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code not in (200, 201):
            print(f"INTERACTIONS : Discord a refusé la validation (HTTP {r.status_code}).")
            return False
        if r.json().get("interactions_endpoint_url") != endpoint:
            print("INTERACTIONS : Discord n'a pas confirmé l'adresse.")
            return False
        print("INTERACTIONS : Discord a validé l'endpoint. Menu activable.")
        return True
    except (requests.RequestException, ValueError, TypeError):
        print("INTERACTIONS : connexion ou réponse Discord invalide.")
        return False


def main():
    ready = configure(
        os.getenv("DISCORD_BOT_TOKEN"),
        os.getenv("DISCORD_PUBLIC_KEY_EXPECTED"),
        os.getenv("DISCORD_INTERACTIONS_ENDPOINT") or "",
    )
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write("enabled=" + ("1" if ready else "0") + "\n")


if __name__ == "__main__":
    main()
