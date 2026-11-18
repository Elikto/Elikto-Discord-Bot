"""Cartes Discord V2 des fiches publiques.

Le titre du fil et la description du message d'ouverture sont conservés.
L'aperçu du lien est publié dans un second message Discord classique.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit

from site_probe import clean_url

V2_FLAG = 32768
COLOR = 0x8271D8
BRAND = "✦ "
LEGACY_BRAND = "✦ FICHE SITE"
LEGACY = "[elikto-forum-suivi:v1]"


def domain(url):
    return (urlsplit(url or "").hostname or "").removeprefix("www.")


def safe_title(value):
    title = str(value or "")
    return "".join(ch if ch not in "*[]()<>\n\r@" else " " for ch in title)[:90].strip()


def source_description(api, thread_id, bot_id):
    """Le message fondateur du fil est la seule source de description."""
    try:
        message = api.call("GET", f"/channels/{thread_id}/messages/{thread_id}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return ""
        raise
    if str((message.get("author") or {}).get("id")) == bot_id:
        return ""
    return str(message.get("content") or "")


def split_text(text, length=3850):
    return [text[i:i+length] for i in range(0, len(text), length)]


def strip_duplicate_source_url(description, groups):
    """Ôter seulement une URL finale déjà affichée dans la fiche.

    Conserve sans réécriture le corps de la description et ses liens internes.
    """
    known = set()
    for group in groups:
        known.add(domain(group.get("url")))
        known.update(group.get("older") or [])
    lines = str(description or "").rstrip().splitlines()
    while lines:
        last = lines[-1].strip()
        if not re.fullmatch(r"<?https?://[^\s<>]+>?", last):
            break
        host = domain(last.strip("<>").rstrip(".,!?"))
        if host not in known:
            break
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).strip()


def build_card(groups, description, title=None):
    """Fiche au nom du site, adresse actuelle et description d'origine."""
    if not groups:
        return None
    host = domain(groups[0]["url"])
    heading = safe_title(title) or safe_title(host)
    parts = [{"type": 10, "content": "## ✦ " + heading + " ✦"}]
    for group in groups:
        url = clean_url(group.get("url"))
        if not url:
            continue
        line = "🌐 " + url
        if group.get("older"):
            line += "\n-# ↳ Ancien : " + ", ".join(group["older"])
        if group.get("announced"):
            line += ("\n-# 🟢 Nouvelle adresse annoncée · serveur accessible"
                     if group.get("status") == "online"
                     else "\n-# 🟡 Nouvelle adresse annoncée · à vérifier")
        elif group.get("status") in ("offline", "suspect"):
            line += "\n-# 🟠 Accessibilité à vérifier"
        parts.extend({"type": 10, "content": chunk} for chunk in split_text(line))
    cleaned = strip_duplicate_source_url(description, groups)
    if cleaned:
        for i, chunk in enumerate(split_text(cleaned, 3800)):
            parts.append({
                "type": 10,
                "content": ("**📝 Description**\n" if i == 0 else "") + chunk,
            })
    if len(parts) > 38:
        raise ValueError("Description trop longue pour une carte Discord")
    return [{"type": 17, "accent_color": COLOR, "components": parts}]


def all_text(components):
    result = []
    for component in components or []:
        if component.get("type") == 10:
            result.append(str(component.get("content") or ""))
        result.extend(all_text(component.get("components") or []))
    return result


def normalize(components):
    def trim(value):
        if isinstance(value, dict):
            return {key: trim(data) for key, data in value.items()
                    if key not in ("id", "spoiler", "disabled")}
        if isinstance(value, list):
            return [trim(item) for item in value]
        return value
    return trim(components or [])


def is_own_card(message, bot_id):
    if str((message.get("author") or {}).get("id")) != bot_id:
        return False
    if LEGACY in (message.get("content") or ""):
        return True
    texts = all_text(message.get("components") or [])
    if any(LEGACY_BRAND in item for item in texts):
        return True
    return any(item.startswith("## ✦ ") and item.endswith(" ✦")
               for item in texts)


def card_digest(components):
    return hashlib.sha256(
        json.dumps(components, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
