"""Diagnostic non destructif du fil cible. N'exporte aucun texte privé."""
import os
from urllib.parse import urlsplit
from sync_catalogue import DiscordAPI
from site_probe import extract_candidate_urls

TID = "1430988094374547559"

def main():
    api = DiscordAPI(os.environ["DISCORD_BOT_TOKEN"])
    me = str(api.call("GET", "/users/@me")["id"])
    thread = api.call("GET", f"/channels/{TID}")
    print("TARGET", {"id": thread["id"], "name": thread.get("name"),
          "parent_id": thread.get("parent_id"), "type": thread.get("type"),
          "archived": thread.get("thread_metadata", {}).get("archived"),
          "locked": thread.get("thread_metadata", {}).get("locked"),
          "starter_is_bot": str(thread.get("owner_id")) == me})
    try:
        first = api.call("GET", f"/channels/{TID}/messages/{TID}")
        print("STARTER", {
            "exists": True, "author_is_bot": str(first.get("author", {}).get("id")) == me,
            "content_length": len(first.get("content") or ""),
            "embeds": len(first.get("embeds") or []),
            "attachments": len(first.get("attachments") or []),
            "domains": sorted({urlsplit(url).hostname for url in extract_candidate_urls(first)}),
        })
    except RuntimeError as exc:
        print("STARTER", {"exists": False, "error_type": type(exc).__name__})
    try:
        messages = api.call("GET", f"/channels/{TID}/messages", params={"limit": 100})
        print("MESSAGES", {"count": len(messages),
             "with_content": sum(bool(m.get("content")) for m in messages),
             "with_embeds": sum(bool(m.get("embeds")) for m in messages),
             "bot_messages": sum(str(m.get("author",{}).get("id")) == me for m in messages)})
        for msg in sorted(messages, key=lambda m: int(m["id"]))[:4]:
            print("MSG", {"id":msg["id"], "is_bot": str(msg.get("author", {}).get("id")) == me,
                "content_length":len(msg.get("content") or ""),
                "domains":sorted({urlsplit(u).hostname for u in extract_candidate_urls(msg)})})
    except RuntimeError as exc:
        print("MESSAGES", {"available": False, "error": type(exc).__name__})
if __name__ == "__main__":
    main()
