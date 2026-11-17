"""Tableau de bord Discord en un SEUL message Components V2.

Aucun serveur actif en permanence : les liens sont natifs et les messages
sont seulement actualises par GitHub Actions.
"""
from datetime import datetime, timezone

from catalogue_v2 import (
    MARKER, FLAG_V2, marker_id, style, category_visuals, salon_sort,
    icon, safe, fold, ordered, count_items, text, sep, section, container,
    total_components, total_chars, normalize,
)

MAX_COMPONENTS = 40
MAX_CHARS = 4000
MAX_CARDS = 7


def forum_link(url, label="Ouvrir"):
    """Liens Markdown nommés : pas de #inconnu avec les fils Discord archivés."""
    if not url:
        return safe(label)
    return f"[{safe(label)}]({url})"


def entries_for_forum(name, entries, cap):
    original = next((url for title, url in entries
                     if title == "Ouvrir le forum" or title.startswith("#")), None)
    is_forum = any(title == "Ouvrir le forum" for title, _ in entries)
    entries = sorted(
        [(title, url) for title, url in entries
         if title != "Ouvrir le forum" and not title.startswith("#")],
        key=lambda x: fold(x[0]),
    )
    count = len(entries)
    title = safe(name.replace("-", " ").replace("_", " ").upper())
    heading = f"**{icon(name)}  {title}**"
    if original:
        heading += f"  ·  {forum_link(original, 'Forum' if is_forum else 'Salon')}"
    if count:
        visible = entries if cap is None else entries[:cap]
        link_lines = [
            "  ·  ".join(forum_link(url, title[:46]) for title, url in visible[i:i + 2])
            for i in range(0, len(visible), 2)
        ]
        body = "\n".join(link_lines)
        hidden = count - len(visible)
        if hidden:
            body += f"\n-# +{hidden} fiches accessibles dans le forum ci-dessus"
        return heading + f"  ·  {count} {'fiche' if count == 1 else 'fiches'}\n" + body
    return heading + ("\n-# Salon textuel" if original else "")


def build(grouped, *, max_entries=None, now=None):
    sorted_categories = ordered(grouped)
    visuals = category_visuals(grouped)
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
            sep(),
            section(
                "### 🌐 Catalogue web interactif\n"
                "-# Recherche instantanée, catégories et fiches avec liens directs.",
                "https://elikto.github.io/Elikto-Discord-Bot/",
                "Ouvrir le site",
            ),
        ],
    )

    parts = headline[:]
    for (cat_id, cat_name), forums in sorted_categories[:MAX_CARDS]:
        _, emoji, color, description = visuals[(cat_id, cat_name)]
        blocks = [
            entries_for_forum(name, entries, max_entries)
            for name, entries in sorted(forums.items(), key=salon_sort)
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
        for (cat_id, name), forums in other:
            emoji = visuals[(cat_id, name)][1]
            overview = "  ·  ".join(
                forum_link(url, name_of_forum)
                for name_of_forum, entries in sorted(forums.items(), key=salon_sort)
                for title, url in entries
                if title == "Ouvrir le forum" or title.startswith("#")
            )
            body_lines.append(f"• {emoji} **{safe(name)}** · {count_items(forums)} fiches\n  {overview}")
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
