"""Runs on the VM (piped over ssh), read-only. argv: [days] [how many server emojis], default 120 and 80.

The emoji palette for reacting: the server's own emojis that still exist, ranked by how often members reacted with
them lately, each with a message it landed on so the name makes sense, then the most used normal emojis. Custom ones
print as name:id, ready for react.py."""
import json, os, sqlite3, sys, time, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
G = "959493056242008184"
days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
top = int(sys.argv[2]) if len(sys.argv) > 2 else 80
since = int(time.time()) - days * 86400
# Reactions that set off Vic's own reaction features: timeouts (Shut, bedtime, oggersglare), translations (flags)
# and deleting Vic's posts (the cross). Vic must never use them.
BLOCKED_NAMES = {"shut", "bedtime", "oggersglare"}


def blocked(emoji):
    name = emoji.split(":")[0].lower()
    flag = any(0x1F1E6 <= ord(c) <= 0x1F1FF or 0xE0000 <= ord(c) <= 0xE007F for c in emoji) or "\U0001F3F4" in emoji
    return name in BLOCKED_NAMES or flag or emoji in ("\u274c", "\u274e", "\u2716\ufe0f", "\u2716")

req = urllib.request.Request(f"https://discord.com/api/v10/guilds/{G}/emojis",
                             headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
server = {str(e["id"]): e for e in json.load(urllib.request.urlopen(req, timeout=20))
          if e.get("available", True) and not blocked(e["name"])}

db = sqlite3.connect("file:chronicle.db?mode=ro", uri=True)
rows = db.execute("SELECT emoji, emoji_id, COUNT(*), MAX(message_id) FROM reactions "
                  "WHERE action = 'add' AND ts >= ? GROUP BY emoji, emoji_id ORDER BY COUNT(*) DESC", (since,)).fetchall()
has_messages = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages'").fetchone()


def sample(message_id):
    if not has_messages:
        return ""
    row = db.execute("SELECT content FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    text = " ".join((row[0] or "").split()) if row else ""
    return f' | e.g. on "{text[:70]}"' if text else ""


custom = [(n, str(i), c, m) for n, i, c, m in rows if i and str(i) in server]
print(f"Server emojis by reactions in the last {days} days ({len(custom)} used, "
      f"{len(server) - len(custom)} unused):")
for name, eid, count, last in custom[:top]:
    anim = " (animated)" if server[eid].get("animated") else ""
    print(f"  {name}:{eid}{anim}  x{count}{sample(last)}")
if len(custom) > top:
    print("Less used:", ", ".join(f"{n}:{i}" for n, i, _, _ in custom[top:]))
unused = sorted(e["name"] + ":" + i for i, e in server.items() if i not in {c[1] for c in custom})
if unused:
    print("Never reacted with lately:", ", ".join(unused))
normal = [(n, c) for n, i, c, _ in rows if not i and not blocked(n)][:30]
print("Most used normal emojis:", "  ".join(f"{n} x{c}" for n, c in normal))
