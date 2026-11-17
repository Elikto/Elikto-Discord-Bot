"""Tableau de bord Discord en un SEUL message Components V2.

Aucun serveur actif en permanence : les liens sont natifs et les messages
sont seulement actualises par GitHub Actions.
"""
from datetime import datetime, timezone

from catalogue_v2 import (
    MARKER, FLAG_V2, marker_id, style, icon, safe, fold, ordered,
    count_items, text, sep, container, total_components, total_chars,
    normalize,
)

MAX_COMPONENTS = 40
MAX_CHARS = 4000
MAX_CARDS = 7


def forum_link(url):
    """La mention Discord <#id> est un lien court directement vers la fiche."""
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1].isdigit() and "discord.com" in url:
        return f"<#{parts[-1]}>"
    return f"[Ouvrir]({url})"


def entries_for_forum(name, entries, cap):
    original = next((url for title, url in entries
                     if title == "Ouvrir le forum" or title.startswith("#")), None)
    entries = sorted(
        [(title, url) for title, url in entries
         if title != "Ouvrir le forum" and not title.startswith("#")],
        key=lambda x: fold(x[0]),
    )
    count = len(entries)
    title = safe(name.replace("-", " ").replace("_", " ").upper())
    heading = f"**{icon(name)}  {title}**"
    if original:
        heading += f"  ·  {forum_link(original)}"
    if count:
        visible = entries if cap is None else entries[:cap]
        link_lines = ["  ".join(forum_link(url) for _, url in visible[i:i + 5])
                      for i in range(0, len(visible), 5)]
        body = "\n".join(link_lines)
        hidden = count - len(visible)
        if hidden:
            body += f"\n-# +{hidden} fiches accessibles dans le forum ci-dessus"
        return heading + f"  ·  {count} fiches\n" + body
    return heading + ("\n-# Salon textuel" if original else "")


def build(grouped, *, max_entries=None, now=None):
    sorted_categories = ordered(grouped)
    count = sum(count_items(forums) for forums in grouped.values())
    updated = int((now or datetime.now(timezone.utc)).timestamp())

    headline = container(
        MARKER + "index", 0x977AF4,
        [
            text(
                "# ✦ LES NOTES D'ELIKTO\n"
                "### 📚 CATALOGUE GÉNÉRAL\n"
                "-# Streaming, téléchargements, anti-pub et toutes les ressources du serveur"
            ),
            sep(),
            text(
                f"**📁 {len(grouped)} catégories**   ·   **📚 {count} fiches**\n"
                f"-# Actualisé le <t:{updated}:f> · Prochaine vérification automatique sous 6 h"
            ),
        ],
    )

    parts = headline[:]
    for (cat_id, cat_name), forums in sorted_categories[:MAX_CARDS]:
        _, emoji, color, description = style(cat_name)
        blocks = [
            entries_for_forum(name, entries, max_entries)
            for name, entries in sorted(forums.items(), key=lambda item: fold(item[0]))
        ]
        body = "\n\n".join(blocks) or "-# Aucun forum référencé."
        # Ne jamais couper une adresse au milieu : le mode compact choisira
        # combien de fiches montrer si la limite Discord est depassee.
        parts.extend(container(
            f"{MARKER}dashboard:{cat_id}", color,
            [
                text(
                    f"## {emoji}  {safe(cat_name.upper())}\n"
                    f"-# {description}  ·  {count_items(forums)} fiches"
                ),
                sep(),
                text(body),
            ],
        ))

    other = sorted_categories[MAX_CARDS:]
    if other:
        body_lines = []
        for (_, name), forums in other:
            overview = " ".join(
                forum_link(url)
                for entries in forums.values()
                for title, url in entries
                if title == "Ouvrir le forum" or title.startswith("#")
            )
            body_lines.append(f"• **{safe(name)}** · {count_items(forums)} fiches\n  {overview}")
        parts.extend(container(
            MARKER + "dashboard:other", 0x8493AA,
            [text("## 📂  AUTRES CATÉGORIES"), sep(),
             text("\n".join(body_lines)[:1600])],
        ))

    parts.append(
        text("-# 🔗 Clique sur une fiche ou sur un forum pour l'ouvrir directement. "
             "🔒 Les salons privés sont exclus de cet index.")
    )
    return parts


def render_dashboard(grouped, now=None):
    """Tente le catalogue complet puis compacte si les limites Discord sont atteintes."""
    for cap in (None, 20, 12, 8, 5, 3, 1, 0):
        parts = build(grouped, max_entries=cap, now=now)
        if total_components(parts) <= MAX_COMPONENTS and total_chars(parts) <= MAX_CHARS:
            return parts
    raise ValueError(
        "Le serveur compte trop de categories/salons pour un message Discord. "
        "Il faut reduire la densite des cartes avant de publier."
    )
