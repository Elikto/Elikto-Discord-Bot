"""Catalogue Discord compact : TOUTES les fiches sont visibles et cliquables.

Le message reste sur une seule carte d'accueil et une carte par categorie.
On retire les textes repetitifs plutot que de masquer les publications.
Discord Components V2 : maximum 40 composants / 4 000 caracteres de texte.
"""
from datetime import datetime, timezone

from catalogue_v2 import (
    MARKER, FLAG_V2, marker_id, category_visuals, salon_sort,
    icon, safe, fold, ordered, count_items, text, sep, section, container,
    total_components, total_chars, normalize,
)

MAX_COMPONENTS = 40
MAX_CHARS = 4000


def forum_link(url, label="Ouvrir"):
    """Des noms cliquables, jamais des mentions #inconnu."""
    if not url:
        return safe(label)
    return f"[{safe(label)}]({url})"


def entries_for_forum(name, entries, include_forum_link=True):
    """Un lien cliquable par fiche : aucun seuil ni texte de remplacement."""
    source = next(
        (url for title, url in entries
         if title == "Ouvrir le forum" or title.startswith("#")), None
    )
    is_forum = any(title == "Ouvrir le forum" for title, _ in entries)
    items = sorted(
        [(title, url) for title, url in entries
         if title != "Ouvrir le forum" and not title.startswith("#")],
        key=lambda item: fold(item[0]),
    )
    heading = f"**{icon(name)} {safe(name.replace('-', ' ').replace('_', ' ').upper())}**"
    if source and (include_forum_link or not items):
        heading += f" · {forum_link(source, 'Forum' if is_forum else 'Salon')}"
    if items:
        heading += f" · {len(items)} {'fiche' if len(items) == 1 else 'fiches'}"
        links = [f"• {forum_link(url, title)}" for title, url in items]
        return heading + "\n" + "\n".join(links)
    return heading


def build(grouped, *, include_forum_links=True, compact=False, now=None):
    """Une mise en page complete, sans pagination destructive."""
    sorted_categories = ordered(grouped)
    visuals = category_visuals(grouped)
    count = sum(count_items(forums) for forums in grouped.values())
    updated = int((now or datetime.now(timezone.utc)).timestamp())

    intro = (
        "# ✦ LES NOTES D'ELIKTO\n"
        f"**{len(grouped)} catégories · {count} fiches**  ·  "
        f"<t:{updated}:f>"
    )
    parts = container(
        MARKER + "index",
        0x977AF4,
        [
            text(intro),
            sep(),
            section(
                "🌐 **Catalogue web** · recherche, alertes et disponibilité",
                "https://elikto.github.io/Elikto-Discord-Bot/",
                "Ouvrir le site",
            ),
        ],
    )

    for (cat_id, cat_name), forums in sorted_categories:
        _, emoji, color, _ = visuals[(cat_id, cat_name)]
        blocks = [
            entries_for_forum(name, entries, include_forum_links)
            for name, entries in sorted(forums.items(), key=salon_sort)
        ]
        body = "\n\n".join(blocks) or "-# Aucun forum référencé."
        label = (
            f"## {emoji} {safe(cat_name.upper())} · {count_items(forums)} fiches"
            if not compact else
            f"**{emoji} {safe(cat_name.upper())}**"
        )
        children = [text(label), sep(), text(body)]
        parts.extend(container(f"{MARKER}dashboard:{cat_id}", color, children))
    return parts


def render_dashboard(grouped, now=None):
    """Priorite absolue aux liens des fiches, pas aux descriptions.

    Commence avec les liens de forums, puis retire uniquement ces liens
    secondaires si necessaire. Toutes les fiches demeurent presentes.
    Leve une erreur plutot que publier un index incomplet.
    """
    for include_forum_links, compact in (
        (True, False),
        (False, False),
        (False, True),
    ):
        parts = build(
            grouped, include_forum_links=include_forum_links,
            compact=compact, now=now,
        )
        if total_components(parts) <= MAX_COMPONENTS and total_chars(parts) <= MAX_CHARS:
            return parts
    raise ValueError(
        "Le catalogue complet depasse les limites d'un message Discord. "
        "Aucune fiche ne sera masquee. Il faut repartir les fiches sur "
        "plusieurs messages de catalogue."
    )
