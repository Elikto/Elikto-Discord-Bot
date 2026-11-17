"""Vraies cartes Discord Components V2, compatibles avec GitHub Actions seul."""
from datetime import datetime, timezone
import hashlib
import unicodedata

FLAG_V2 = 32768
MARKER = "elikto:catalog:"
MAX_COMPONENTS = 40
MAX_TEXT = 4000

def fold(value):
    n = unicodedata.normalize("NFKD", value.casefold())
    return "".join(x for x in n if not unicodedata.combining(x))

def safe(value):
    s = str(value).replace("\n", " ").replace("\r", " ")
    special = set("\\*_~|>[]()") | {chr(96)}
    return "".join("\\" + c if c in special else c for c in s)

def marker_id(marker):
    return max(1, int.from_bytes(hashlib.blake2s(marker.encode(), digest_size=4).digest(), "big") & 0x7FFFFFFF)

def style(name):
    n = fold(name)
    # Les espaces de discussion sont toujours apres les ressources.
    if any(token in n for token in ("discussion", "blabla", "bavardage", "general-chat")):
        return (999, "💬", 0x64748B, "Échanges et discussions")
    if "stream" in n or "film" in n or "serie" in n:
        return (0, "🎬", 0x9C84F7, "Films, séries et plateformes")
    if "iptv" in n or "television" in n:
        return (1, "📺", 0x45B5D9, "Télévision et chaînes")
    if "anime" in n or "manga" in n:
        return (2, "🌸", 0xEC91C6, "Animés et animation")
    if any(x in n for x in ("telecharg", "download", "torrent")):
        return (3, "📥", 0x5F9CF0, "Téléchargements et ressources")
    if "anti" in n and ("pub" in n or "publicit" in n):
        return (4, "🛡️", 0x52CDAA, "Bloqueurs de publicité")
    if any(x in n for x in ("android", "application", "mobile", "smartphone")):
        return (5, "📱", 0x8ACB73, "Applications et outils")
    if any(x in n for x in ("jeu", "game", "gaming", "console")):
        return (6, "🎮", 0xF0B96A, "Jeux et ressources")
    if "livre" in n or "comic" in n or "lecture" in n:
        return (7, "📖", 0xD5A1FC, "Livres et lecture")
    if any(x in n for x in ("informatique", "ordinateur", "windows", "pc-", "logiciel")):
        return (8, "💻", 0x73B5E9, "Informatique et logiciels")
    if "musique" in n or "audio" in n or "son" == n:
        return (9, "🎵", 0xD2A7FD, "Musique et audio")
    if "video" in n or "youtube" in n:
        return (10, "📹", 0xF38F8F, "Vidéos et médias")
    if "vpn" in n or "securit" in n or "cyber" in n or "confidential" in n:
        return (11, "🔐", 0x7DCBB4, "Sécurité et confidentialité")
    if "intelligence artificielle" in n or n == "ia" or "intelligence-artificielle" in n:
        return (12, "🤖", 0x92A5F8, "Intelligence artificielle")
    if "reseau" in n or "internet" in n or "web" in n:
        return (13, "🌐", 0x64C1CA, "Internet et réseaux")
    if "tutoriel" in n or "guide" in n or "aide" in n:
        return (14, "🧭", 0xE6B56B, "Guides et tutoriels")
    if "bot" in n or "automatis" in n:
        return (15, "⚙️", 0xA5A9F2, "Automatisation")
    if "projet" in n or "developp" in n or "code" in n:
        return (16, "🚀", 0xEBAC75, "Projets et développement")
    if "outil" in n or "utilitaire" in n:
        return (17, "🧰", 0xB6BF77, "Outils et utilitaires")
    if "astuce" in n or "conseil" in n:
        return (18, "💡", 0xD4B86D, "Astuces et conseils")
    return (100, "🧩", 0x8493AA, "Ressources du serveur")


def is_discussion(name):
    n = fold(name).replace("_", "-").replace(" ", "-")
    return any(token in n for token in
               ("discussion", "blabla", "bavardage", "general-chat")) or n in (
                   "general", "chat", "salon-general", "salon-discussion"
               )


def salon_sort(item):
    return (1 if is_discussion(item[0]) else 0, fold(item[0]))


def category_visuals(grouped):
    """Une icone differente pour chaque categorie, y compris les generiques."""
    extras = [
        ("🧩", 0x8493AA), ("💡", 0xD9B06D), ("🛠️", 0x71BFC5),
        ("🗂️", 0xB69DED), ("🧪", 0x6DCBB1), ("🎨", 0xE694B5),
        ("🧭", 0x8AC1E5), ("📦", 0xD2A47A), ("🔍", 0xA7BFC4),
        ("🚀", 0xA5A8E5), ("🛰️", 0x8EB4E0), ("🔖", 0xC1A0C7),
        ("🔮", 0xA994E8), ("🖥️", 0x85A7CB), ("🧱", 0xC0AC88),
    ]
    used = set()
    output = {}
    for key, forums in ordered(grouped):
        rank, emoji, color, description = style(key[1])
        if rank == 100 or emoji in used:
            # Allocation stable dans l'ordre du catalogue, sans répétition
            # dans les categories affichées simultanement.
            emoji, color = next(
                ((e, c) for e, c in extras if e not in used),
                (emoji, color),
            )
        used.add(emoji)
        output[key] = (rank, emoji, color, description)
    return output

def icon(name):
    n = fold(name)
    if "film" in n and "serie" in n: return "📺"
    if "film" in n: return "🎞️"
    if "serie" in n: return "📺"
    if "iptv" in n: return "📡"
    if "anime" in n: return "🌸"
    if "jeu" in n or "game" in n: return "🎮"
    if "livre" in n or "comic" in n: return "📚"
    if "android" in n: return "📱"
    return "📂"

def text(content):
    return {"type": 10, "content": content}

def sep():
    return {"type": 14, "divider": True, "spacing": 1}

def section(content, url, label="Ouvrir"):
    return {"type": 9, "components": [text(content)],
            "accessory": {"type": 2, "style": 5, "label": label, "url": url}}

def container(marker, color, children):
    return [{"type": 17, "id": marker_id(marker),
             "accent_color": color, "components": children}]

def count_items(forums):
    return sum(1 for entries in forums.values() for name, _ in entries
               if name != "Ouvrir le forum" and not name.startswith("#"))

def ordered(grouped):
    return sorted(grouped.items(), key=lambda e: (style(e[0][1])[0], fold(e[0][1])))

def split_forum(name, entries):
    url = next((u for t, u in entries if t == "Ouvrir le forum" or t.startswith("#")), None)
    is_forum = any(t == "Ouvrir le forum" for t, _ in entries)
    items = [(t, u) for t, u in entries if t != "Ouvrir le forum" and not t.startswith("#")]
    items.sort(key=lambda x: fold(x[0]))
    head = f"### {icon(name)}  {safe(name.replace('-', ' ').replace('_', ' ').upper())}\n"
    head += (f"-# {len(items)} fiches · Liens directs vers les publications\n"
             if is_forum else "-# Salon textuel\n")
    if not items:
        return [(head + "-# Consulte ce salon pour retrouver ses ressources.", url)]
    blocks, current = [], head
    for title, link in items:
        line = f"• [{safe(title[:95])}]({link})\n"
        if len(current) + len(line) > 1000 and current != head:
            blocks.append((current.rstrip(), url))
            current = head.replace("### ", "### ↳ ", 1)
        current += line
    blocks.append((current.rstrip(), url))
    return blocks

def total_components(component_list):
    def visit(x):
        return 1 + sum(visit(y) for y in x.get("components", [])) + (
            visit(x["accessory"]) if "accessory" in x else 0)
    return sum(visit(x) for x in component_list)

def total_chars(component_list):
    def visit(x):
        return len(x.get("content", "")) + sum(visit(y) for y in x.get("components", []))
    return sum(visit(x) for x in component_list)

def validate(parts):
    if total_components(parts) > MAX_COMPONENTS or total_chars(parts) > MAX_TEXT:
        raise ValueError("Page Discord V2 trop volumineuse")
    return parts

def render_categories(grouped):
    result = []
    for (cat_id, cat_name), forums in ordered(grouped):
        _, emoji, color, description = style(cat_name)
        blocks = []
        for name, entries in sorted(forums.items(), key=lambda e: fold(e[0])):
            blocks.extend(split_forum(name, entries))
        if not blocks:
            blocks = [("Aucun forum pour cette rubrique.", None)]
        pages, current, size = [], [], 0
        for body, url in blocks:
            if current and (len(current) >= 7 or size + len(body) > 3000):
                pages.append(current)
                current, size = [], 0
            current.append((body, url))
            size += len(body)
        if current:
            pages.append(current)
        for i, page in enumerate(pages, 1):
            marker = f"{MARKER}cat:{cat_id}:{i}"
            header = (f"## {emoji}  {safe(cat_name.upper())}\n"
                      f"-# {description} · {count_items(forums)} fiches · {len(forums)} salons"
                      + (f" · Page {i}/{len(pages)}" if len(pages) > 1 else ""))
            children = [text(header), sep()]
            for idx, (body, url) in enumerate(page):
                if idx: children.append(sep())
                children.append(section(body, url, "Voir le forum") if url else text(body))
            children += [sep(), text("-# 📚 Les notes d'Elikto  ·  Catalogue synchronisé")]
            result.append((marker, str(cat_id), validate(container(marker, color, children))))
    return result

def render_home(grouped, links):
    count = sum(count_items(forums) for forums in grouped.values())
    now = int(datetime.now(timezone.utc).timestamp())
    children = [
        text("# 📚  LES NOTES D'ELIKTO\n"
             "### CATALOGUE GÉNÉRAL\n"
             "-# Tous les sites et ressources du serveur, au même endroit."),
        sep(),
        text(f"**{len(grouped)} CATÉGORIES**  •  **{count} FICHES**\n"
             f"-# Dernière mise à jour : <t:{now}:f>"),
        sep(),
        text("### 🧭  EXPLORER LE CATALOGUE"),
    ]
    overflow = []
    for i, ((cat_id, name), forums) in enumerate(ordered(grouped)):
        emoji, desc = style(name)[1], style(name)[3]
        label = f"**{emoji}  {safe(name.upper())}**\n-# {desc} · {count_items(forums)} fiches"
        url = links.get(str(cat_id))
        if i < 7 and url:
            children.append(section(label, url, "Explorer"))
            if i < min(6, len(grouped) - 1):
                children.append(sep())
        else:
            overflow.append(f"• {emoji} [{safe(name)}]({url})" if url else f"• {emoji} {safe(name)}")
    if overflow:
        children.extend([sep(), text("**AUTRES RUBRIQUES**\n" + "\n".join(overflow)[:800])])
    children.extend([sep(), text("-# 🔄 Synchronisation toutes les 6 h  ·  🔒 Salons privés exclus")])
    return validate(container(MARKER + "index", 0x977AF4, children))

def normalize(parts):
    # Discord remplit automatiquement des identifiants sur les composants enfants.
    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if k not in ("id", "spoiler", "disabled")}
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        return obj
    return clean(parts)
