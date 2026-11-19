"""Identifiants Tr4ker fournis explicitement par l'utilisateur.

Aucun identifiant ne doit se trouver dans le code, les fichiers du dépôt,
les états persistants, les messages de log ou les sujets des salons.
L'utilisateur a demandé leur affichage public sur Discord avec spoiler.
Discord spoiler n'est PAS une restriction d'accès.
"""
import os

TR4KER_THREAD_ID = "1572752361967853713"


def authorized_tr4ker_credentials(thread_id, forum_title):
    if (str(thread_id) != TR4KER_THREAD_ID
            or str(forum_title or "").casefold().strip() != "tr4ker"):
        return None
    username = os.environ.get("TR4KER_LOGIN_USERNAME", "").strip()
    password = os.environ.get("TR4KER_LOGIN_PASSWORD", "").strip()
    if not username or not password:
        return None
    if (len(username) > 150 or len(password) > 200
            or any(x in username + password for x in ("\r", "\n", "||", "@everyone", "@here"))):
        return None
    return {"user": username, "password": password}
