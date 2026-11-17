"""Word patrol feed (the 17 Nov 2026 poo patrol). Runs on the VM: ./run.sh - <last_id> <end_epoch> 0 < word_patrol_feed.py
Polls one channel (CH) for non-bot messages after <last_id> and prints them as JSON (id, u, t, media, reply_to), last_id
and whether <end_epoch> has passed. With 0 as the third arg it returns as soon as anything new arrives. Start it in a
backgrounded Bash call (never with & or /dev/null), read it, act, restart it from last_id. Change CH for another channel."""
import os,sys,time,json,requests
from dotenv import load_dotenv;load_dotenv(".env")
h={"Authorization":"Bot "+os.environ["DISCORD_TOKEN"]}
CH="959493057076666380"; last=sys.argv[1]; END=int(sys.argv[2]); start=time.time()
msgs=[]
while time.time()<END:
    try:
        r=requests.get(f"https://discord.com/api/v10/channels/{CH}/messages?after={last}&limit=100",headers=h,timeout=20).json()
        for m in sorted(r,key=lambda m:int(m["id"])):
            last=m["id"]
            if m["author"].get("bot"): continue
            media=[a["url"] for a in m.get("attachments",[])]+[(e.get("video") or e.get("image") or e.get("thumbnail") or {}).get("url") or e.get("url") for e in m.get("embeds",[])]+[f"https://media.discordapp.net/stickers/{s['id']}.png" for s in m.get("sticker_items",[])]
            msgs.append({"id":m["id"],"u":m["author"]["username"],"t":m.get("content","")[:400],"media":[x for x in media if x],"reply_to":(m.get("message_reference") or {}).get("message_id")})
    except Exception: pass
    if msgs and time.time()-start>=float(sys.argv[3] if len(sys.argv)>3 else 45): break
    time.sleep(8)
print(json.dumps({"msgs":msgs,"last_id":last,"ended":time.time()>=END},ensure_ascii=False))
