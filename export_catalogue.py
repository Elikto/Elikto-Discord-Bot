"""Exporter les fiches publiques de Discord vers un catalogue web statique.

Aucune donnée privée ni jeton n'est exporté. Le JSON ne contient que les noms
et liens des forums et publications déjà indexables par le bot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from catalogue_v2 import category_visuals, ordered, salon_sort, icon, count_items


def serialize(grouped, now=None):
    visuals = category_visuals(grouped)
    categories = []
    for category_key, forums in ordered(grouped):
        category_id, name = category_key
        rank, emoji, color, description = visuals[category_key]
        channels = []
        for channel_name, entries in sorted(forums.items(), key=salon_sort):
            forum_url = next(
                (url for title, url in entries if title == "Ouvrir le forum"), None
            )
            text_url = next(
                (url for title, url in entries if title.startswith("#")), None
            )
            items = sorted(
                [
                    {"title": title, "url": url}
                    for title, url in entries
                    if title != "Ouvrir le forum" and not title.startswith("#")
                ],
                key=lambda item: item["title"].casefold(),
            )
            channels.append(
                {
                    "name": channel_name,
                    "kind": "forum" if forum_url else "text",
                    "url": forum_url or text_url,
                    "icon": icon(channel_name),
                    "items": items,
                }
            )
        categories.append(
            {
                "id": category_id,
                "name": name,
                "icon": emoji,
                "color": f"#{color:06x}",
                "description": description,
                "channels": channels,
                "count": count_items(forums),
                "rank": rank,
            }
        )
    return {
        "schema_version": 1,
        "site": "Les notes d'Elikto",
        "updated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "categories": categories,
        "stats": {
            "categories": len(categories),
            "channels": sum(len(c["channels"]) for c in categories),
            "entries": sum(c["count"] for c in categories),
        },
        "visibility": "public_everyone_only",
    }


def write_catalogue(grouped, output="site/catalogue.json"):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize(grouped)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        "Catalogue web exporte :",
        payload["stats"]["categories"], "categories,",
        payload["stats"]["entries"], "fiches publiques."
    )
    return path
