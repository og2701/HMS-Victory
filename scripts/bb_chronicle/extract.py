"""Export the whole Big Brother season from the bot's database as JSON for the season replay.

    python3 scripts/bb_chronicle/extract.py --db bb_snapshot.db --out scratch/big_brother_chronicle/data \
        --scenes scratch/big_brother_chronicle/story.json

Writes five files:
    housemates.json             the cast: persona, placing, eviction day, activity counts
    timeline.json               every day from move-in to the final, every event in it, plus chat volume
    challenges.json             all 15 challenge posts with their briefs and how long they ran
    nominations_and_votes.json  every round: nominations with reasons, votes with reasons, tallies, token moves
    iconic_chats.json           the hand-picked scenes in --scenes, each message re-read from the DB

Take the snapshot on the VM rather than pointing this at the live file, which the bot is writing to:
copy the bb_* tables into a fresh SQLite file and scp that down.

Anonymous diary entries carry no name or user id in the timeline, and named-diary counts only count
named entries, so a total can't give an anonymous writer away. The one exception is a diary entry a
hand-picked scene quotes: it carries "reveal" (the writer), which the replay unmasks on screen. Public voters in the final
weeks weren't housemates, so only their counts are kept, not who they were.
"""
import argparse, datetime, json, os, re, sqlite3
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

# Celebrity persona per Discord user id, as Big Brother named them on the eviction boards and team
# lists. Several people renamed mid-game; the persona is what the house called them.
PERSONAS = {
    "404634271861571584": ("Pooja", "ogme01", "🪷"),
    "1283837687551361117": ("James Blunt", "Kaizo", "🎸"),
    "1226934751588519997": ("Gemma Collins", "Fleetwire", "🩷"),
    "883005215471722546": ("Strawberry", "beetleyee", "🍓"),
    "447010711936303115": ("Piggy", "Top Chap", "🐷"),
    "1131019240485425242": ("Pete Burns", "adullandsimpleman", "💄"),
    "1185370569382838447": ("Pengrin", "Prime Minister Pengrin", "🐧"),
    "655342755521757195": ("Mr Bean", "Bunnywise", "🫘"),
    "479207279850291221": ("Nadine Coyle", "Steven", "🎤"),
    "285860055570579457": ("Jeremy Clarkson", "shuto", "🚗"),
    "1146144337302917242": ("Boris Johnson", "Darillion", "🌀"),
    "1398652914737741956": ("Harvey Price", "Count Lancula", "🧛"),
    "1449906006220996738": ("Hatty", "pastatwined", "🎩"),
    "1022210566871322754": ("Gary Busey", "Lou Skunt", "🎭"),
    "792139113587277835": ("Sir Michael Fabricant", "Tharan", "🎖️"),
    "828728237789020240": ("Michael Barrymore", "crayfish", "🦞"),
    "716917080116428821": ("Jane", "Jane", "🧶"),
    "860098855621623809": ("Tyler", "yorkshire_tea1", "🧍"),
    "1204435534416580679": ("Katie Price", "Gunner", "💋"),
    "692814294609559553": ("Phil McKracken", "DogIsGod", "🐶"),
    "797207976548499518": ("George Galloway", "Johnny Finance", "🐱"),
    "969768729548300288": ("Lila", "Dalila", "🎀"),
    "1504560326488756309": ("Batman", "labuschagne", "🦇"),
    "276119377395449856": ("Alex Reid", "Kian", "🍺"),
    "1377248229154095194": ("Maddie", "maddie", "🪐"),
    "412850506747215872": ("Mark Corrigan", "Danez", "📎"),
}
BIG_BROTHER = "795003706717372462"  # the host, who spoke as Big Brother

# The final on Day 19 brought every evicted housemate back in for the results; those re-adds
# aren't move-ins and mustn't overwrite anyone's eviction.
FINAL_REUNION_FROM = 1791310000

# Events that are the bot's own plumbing. They're kept, flagged, so nothing is dropped, but the
# replay hides them by default.
# Host-panel actions. Whoever pressed the button was acting as Big Brother, not as a housemate, so these
# carry no actor; standings_checked (peeking at the live split) isn't part of the story at all.
HOST_KINDS = {"standings_checked", "daily_roundup_drafted", "daily_roundup_discarded", "daily_roundup_revised",
              "daily_roundup_posted", "house_silenced", "house_unsilenced", "shop_catalogue_updated", "votes_cleared",
              "thread_message_cleared", "protection_set", "teams_cleared", "panel_thread_opened"}
SYSTEM_KINDS = {"acknowledged", "standings_checked", "bb_dm", "votes_cleared", "thread_message_cleared",
                "vote_moved_to_thread", "panel_thread_opened", "outsider_votes_dropped",
                "shop_catalogue_updated", "daily_roundup_drafted", "daily_roundup_discarded",
                "daily_roundup_revised", "daily_roundup_posted", "daily_roundup_auto_posted",
                "house_silenced", "house_unsilenced", "protection_set", "shop_purchase", "test"}


def persona(uid):
    uid = str(uid) if uid is not None else None
    if uid == BIG_BROTHER:
        return "Big Brother"
    return PERSONAS.get(uid, (None,))[0]


ROLES = {"1550132346295029892": "Housemates"}  # the role Big Brother pinged to reach everyone in the house


def name_ids(text):
    """Swap <@id> mentions and bare ids for persona names, and role pings and channel links for their names."""
    text = re.sub(r"<@&(\d+)>", lambda m: "@" + ROLES.get(m.group(1), "role"), text or "")
    text = re.sub(r"<#\d+>", "#channel", text)
    def rep(m):
        uid = m.group(1) or m.group(2)
        p = persona(uid)
        if not p:
            return m.group(0) if m.group(2) else "@someone"
        return ("@" + p) if m.group(1) else p
    return re.sub(r"<@!?(\d{17,20})>|\b(\d{17,20})\b", rep, text or "")


def game_day(ts, start):
    """Big Brother's own day count: London calendar days from launch, Day 1 = launch day, and the
    small hours up to 6.30am still belong to the day before (the house was asleep, not into the next)."""
    s = datetime.datetime.fromtimestamp(start, LONDON)
    t = datetime.datetime.fromtimestamp(ts, LONDON)
    days = (t.date() - s.date()).days
    if (t.hour < 6 or (t.hour == 6 and t.minute < 30)) and days > 0:
        return days
    return days + 1


def load(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def rows(db, sql, *args):
    return [dict(r) for r in db.execute(sql, args)]


def payload(r):
    try:
        return json.loads(r["payload"]) if r["payload"] else {}
    except ValueError:
        return {"raw": r["payload"]}


def extract(db):
    state = {r["key"]: r["value"] for r in rows(db, "SELECT key, value FROM bb_state")}
    start = int(state["game_started_at"])
    events = rows(db, "SELECT id, at, kind, actor_id, target_id, payload FROM bb_events ORDER BY at, id")
    for e in events:
        e["p"] = payload(e)
    snugs = rows(db, "SELECT id, thread_id, opened_by, members, created_at, closed_at FROM bb_snugs ORDER BY id")
    snug_threads = {s["thread_id"]: s for s in snugs}
    team_threads = set()
    for e in events:
        for cid in (e["p"].get("rooms") or {}).values():
            team_threads.add(str(cid))
    house = max(rows(db, "SELECT thread_id, COUNT(*) n FROM bb_messages GROUP BY thread_id"), key=lambda r: r["n"])["thread_id"]

    def where(thread_id):
        if thread_id == house:
            return "house"
        if thread_id in snug_threads:
            return "snug"
        if thread_id in team_threads:
            return "team"
        return "other"

    def day_of(ts):
        return game_day(ts, start)

    # ---- the cast -------------------------------------------------------------------------
    moved_in, evicted = {}, {}
    for e in events:
        if e["kind"] == "housemate_added" and e["at"] < FINAL_REUNION_FROM:
            moved_in.setdefault(e["target_id"], e["at"])
        if e["kind"] == "evicted" and e["target_id"] not in evicted:
            evicted[e["target_id"]] = e
    crowned = next((e for e in events if e["kind"] == "winner_crowned"), None)

    msg_counts = Counter(r["user_id"] for r in rows(db, "SELECT user_id FROM bb_messages"))
    snugs_opened = Counter(s["opened_by"] for s in snugs)
    snugs_in = Counter(m for s in snugs for m in json.loads(s["members"]))
    named_diary = Counter(r["user_id"] for r in rows(db, "SELECT user_id FROM bb_diary WHERE anonymous = 0"))
    noms_received = Counter(r["nominee_id"] for r in rows(db, "SELECT nominee_id FROM bb_nominations"))
    # Every grant adds one token; the payload's "tokens" is the holder's new balance, not the amount.
    # Grants with source "host" are mostly Big Brother crediting a gift by hand (gifts didn't always
    # land on their own), so only mission grants count as earned.
    tokens_earned = Counter()
    tokens_used = Counter()
    for e in events:
        if e["kind"] == "token_granted" and e["target_id"] and str(e["p"].get("source", "")).startswith("mission"):
            tokens_earned[e["target_id"]] += 1
        if e["kind"] == "immunity_used" and e["actor_id"] and not e["p"].get("source"):
            tokens_used[e["actor_id"]] += 1

    order = sorted(evicted.values(), key=lambda e: e["at"])
    finalists = [u for u in PERSONAS if u not in evicted]
    housemates = []
    for uid, (p, handle, emoji) in PERSONAS.items():
        ev = evicted.get(uid)
        status = "evicted" if ev else "finalist"
        if crowned and crowned["target_id"] == uid:
            status = "winner"
        housemates.append({
            "id": uid, "persona": p, "handle": handle, "emoji": emoji,
            "moved_in_at": moved_in.get(uid), "status": status,
            "evicted_at": ev["at"] if ev else None, "evicted_day": day_of(ev["at"]) if ev else None,
            "eviction_order": order.index(ev) + 1 if ev else None,
            "messages": msg_counts.get(uid, 0), "snugs_opened": snugs_opened.get(uid, 0),
            "snugs_in": snugs_in.get(uid, 0), "named_diary_entries": named_diary.get(uid, 0),
            "nominations_received": noms_received.get(uid, 0),
            "tokens_earned": tokens_earned.get(uid, 0), "tokens_used": tokens_used.get(uid, 0),
        })

    # ---- rounds: nominations, votes, tallies, the token moves made while each was open -------
    noms = rows(db, "SELECT round_id, nominator_id, nominee_id, created_at, reason FROM bb_nominations ORDER BY created_at")
    votes = rows(db, "SELECT round_id, voter_id, nominee_id, created_at, reason FROM bb_votes ORDER BY created_at")
    housemate_ids = set(PERSONAS)
    rounds = []
    for r in rows(db, "SELECT id, kind, status, opened_at, closed_at, nominees, votes_each FROM bb_rounds ORDER BY id"):
        rid = r["id"]
        closed = next((e["p"] for e in events if e["kind"] in ("vote_closed", "public_vote_closed", "nominations_closed")
                       and e["p"].get("round_id") == rid), {})
        rnoms = [n for n in noms if n["round_id"] == rid]
        rvotes = [v for v in votes if v["round_id"] == rid]
        public = r["kind"] == "public_vote"
        moves = [{"at": e["at"], "actor": e["actor_id"], "target": e["target_id"], "mode": e["p"].get("mode")}
                 for e in events if e["kind"] == "immunity_used" and not e["p"].get("source")
                 and (e["p"].get("round_id") == rid or r["opened_at"] <= e["at"] <= (r["closed_at"] or e["at"]))]
        changed = Counter(e["actor_id"] for e in events if e["kind"] == "nominated"
                          and e["p"].get("round_id") == rid and e["p"].get("changed"))
        tally = closed.get("tally") or closed.get("standings") or []
        # bb_rounds.nominees is who was up when it closed; token swaps change it while it's open.
        opened = next((e["p"] for e in events if e["kind"] in ("vote_opened", "public_vote_started")
                       and e["p"].get("round_id") == rid), {})
        rounds.append({
            "id": rid, "kind": r["kind"], "day": day_of(r["opened_at"]),
            "opened_at": r["opened_at"], "closed_at": r["closed_at"], "votes_each": r["votes_each"],
            "nominees": [str(x) for x in json.loads(r["nominees"] or "[]")],
            "nominees_at_open": [str(x) for x in opened.get("nominees", [])],
            "mode": closed.get("mode") or ("save" if closed.get("bottom3") else ("evict" if public else None)),
            "tally": [[str(t[0]), (len(t[1]) if isinstance(t[1], list) else t[1])] for t in tally],
            "nominated_by": {str(t[0]): [str(x) for x in t[1]] for t in tally if isinstance(t[1], list)},
            "bottom3": [str(x) for x in closed.get("bottom3", [])],
            "evicted": [str(x) for x in closed.get("evicted", [])],
            "winner": str(closed["winner"]) if closed.get("winner") else None,
            "total": closed.get("total") or closed.get("total_votes") or len(rvotes),
            "nominations": [{"by": n["nominator_id"], "nominee": n["nominee_id"], "at": n["created_at"],
                             "reason": name_ids(n["reason"])} for n in rnoms],
            "changed_minds": dict(changed),
            # Public voters were the wider server, not housemates: keep the vote, not the voter.
            "votes": [{"by": v["voter_id"] if v["voter_id"] in housemate_ids else None,
                       "nominee": v["nominee_id"], "at": v["created_at"],
                       "reason": name_ids(v["reason"]) if not public else None} for v in rvotes],
            "token_moves": moves,
        })

    # ---- every event, as the replay's day-by-day log ----------------------------------------
    diary = rows(db, "SELECT id, user_id, anonymous, text, created_at FROM bb_diary ORDER BY created_at")
    missions = {m["id"]: m for m in rows(db, "SELECT id, user_id, brief, status, created_at, resolved_at FROM bb_missions")}
    shop_tasks = rows(db, "SELECT id, brief, budget, required, status, opened_at, closes_at, closed_at, result FROM bb_shop_tasks ORDER BY id")
    shop_items = {i["id"]: i for i in rows(db, "SELECT id, category, name, price FROM bb_shop_items")}
    purchases = defaultdict(list)
    for pch in rows(db, "SELECT task_id, item_id, user_id, price, at FROM bb_shop_purchases ORDER BY at"):
        item = shop_items.get(pch["item_id"], {})
        purchases[pch["task_id"]].append({"item": item.get("name"), "aisle": item.get("category"),
                                          "by": pch["user_id"], "price": pch["price"], "at": pch["at"]})
    snug_stats = {r["thread_id"]: r for r in rows(
        db, "SELECT thread_id, COUNT(*) n, MIN(at) first, MAX(at) last FROM bb_messages GROUP BY thread_id")}

    log = []

    def add(at, type_, **kw):
        log.append({"at": at, "day": day_of(at), "type": type_, **kw})

    for e in events:
        k, p, a, t = e["kind"], e["p"], e["actor_id"], e["target_id"]
        if k in ("diary", "snug_opened", "snug_closed", "nominated", "vote_cast", "public_vote_cast"):
            continue  # each comes from its own table below, which holds the full record
        if k == "standings_checked":
            continue
        if k in HOST_KINDS:
            a = t = None
        if k == "housemate_added":
            add(e["at"], "reunion" if e["at"] >= FINAL_REUNION_FROM else "move_in", who=t)
        elif k == "bb_announcement":
            add(e["at"], "announcement", text=name_ids(p.get("text")))
        elif k in ("challenge_posted", "challenge_ended"):
            add(e["at"], k, challenge_id=p.get("challenge_id"), title=p.get("title"))
        elif k == "evicted":
            add(e["at"], "evicted", who=t, announced=p.get("announced"))
        elif k in ("vote_opened", "public_vote_started", "nominations_opened"):
            add(e["at"], k, round_id=p.get("round_id"), nominees=[str(x) for x in p.get("nominees", [])])
        elif k in ("vote_closed", "public_vote_closed", "nominations_closed"):
            add(e["at"], k, round_id=p.get("round_id"))
        elif k == "immunity_used":
            # A gift moves one token; "tokens" in its payload is what the recipient now holds.
            add(e["at"], "token_" + (p.get("mode") or "use"), actor=a, target=t, round_id=p.get("round_id"),
                holds=p.get("tokens"), from_mission=bool(p.get("source")))
        elif k in ("token_granted", "immunity_granted"):
            src = p.get("source", "")
            add(e["at"], k, who=t, holds=p.get("tokens"), source="mission" if src.startswith("mission") else src)
        elif k in ("immunity_expired", "immunity_cleared"):
            add(e["at"], k, who=[str(x) for x in p.get("housemates", [])] or [t], reason=p.get("reason"))
        elif k == "exposed":
            add(e["at"], "exposed", actor=a, target=t, what=name_ids(p.get("what")), on_mission=p.get("was_on_mission"))
        elif k == "mission_assigned":
            m = missions.get(p.get("mission_id"), {})
            add(e["at"], "mission_assigned", who=t or m.get("user_id"), brief=name_ids(p.get("brief") or m.get("brief")))
        elif k == "mission_resolved":
            add(e["at"], "mission_resolved", who=t, status=p.get("status"))
        elif k in ("team_rooms_opened",):
            add(e["at"], k, teams={team: [str(x) for x in ids] for team, ids in (p.get("teams") or {}).items()})
        elif k == "winner_crowned":
            add(e["at"], "winner_crowned", who=t, prize=p.get("prize"))
        elif k in ("shop_opened", "shop_closed", "shop_buying_opened"):
            add(e["at"], k, task_id=p.get("task_id"))
        else:
            add(e["at"], k, actor=a, target=t, system=k in SYSTEM_KINDS,
                detail={x: y for x, y in p.items() if x not in ("text",)} or None)
        if k in SYSTEM_KINDS:
            log[-1]["system"] = True

    for d in diary:
        anon = bool(d["anonymous"])
        add(d["created_at"], "diary", who=None if anon else d["user_id"], anonymous=anon, text=name_ids(d["text"]))
    for n in noms:
        add(n["created_at"], "nomination", actor=n["nominator_id"], target=n["nominee_id"], round_id=n["round_id"],
            reason=name_ids(n["reason"]))
    for v in votes:
        public = v["voter_id"] not in housemate_ids
        add(v["created_at"], "public_vote" if public else "vote", actor=None if public else v["voter_id"],
            target=v["nominee_id"], round_id=v["round_id"], reason=None if public else name_ids(v["reason"]))
    for s in snugs:
        st = snug_stats.get(s["thread_id"], {})
        add(s["created_at"], "snug", snug_id=s["id"], opened_by=s["opened_by"],
            members=[str(m) for m in json.loads(s["members"])], messages=st.get("n", 0),
            minutes=round((st["last"] - st["first"]) / 60) if st.get("first") else 0)
    for task in shop_tasks:
        bought = purchases.get(task["id"], [])
        add(task["opened_at"], "shop_task", task_id=task["id"], brief=task["brief"], budget=task["budget"],
            result=task["result"], spent=sum(b["price"] for b in bought), purchases=bought)
    log.sort(key=lambda x: (x["at"], x["type"]))

    # ---- chat volume per day and per hour ------------------------------------------------------
    vol = defaultdict(Counter)
    heat = defaultdict(lambda: [0] * 24)
    for r in rows(db, "SELECT at, thread_id FROM bb_messages"):
        d = day_of(r["at"])
        vol[d][where(r["thread_id"])] += 1
        heat[d][datetime.datetime.fromtimestamp(r["at"], LONDON).hour] += 1

    last_day = max(e["day"] for e in log)
    days = []
    for d in range(0, last_day + 1):
        evs = [e for e in log if e["day"] == d]
        date = (datetime.datetime.fromtimestamp(start, LONDON).date() + datetime.timedelta(days=d - 1))
        days.append({
            "day": d, "date": date.isoformat(), "weekday": date.strftime("%A"),
            "messages": dict(vol[d]), "hourly": heat[d],
            "snugs_opened": sum(1 for e in evs if e["type"] == "snug"),
            "diary_entries": sum(1 for e in evs if e["type"] == "diary"),
            "evicted": [e["who"] for e in evs if e["type"] == "evicted"],
            "events": evs,
        })

    # ---- challenges ----------------------------------------------------------------------------
    challenges = []
    for c in rows(db, "SELECT id, title, body, answer, status, winner_id, created_at, resolved_at FROM bb_challenges ORDER BY id"):
        ended = next((e["at"] for e in events if e["kind"] == "challenge_ended" and e["p"].get("challenge_id") == c["id"]), None)
        challenges.append({
            "id": c["id"], "title": c["title"], "body": name_ids(c["body"]), "answer": c["answer"],
            "status": c["status"], "winner": c["winner_id"], "posted_at": c["created_at"],
            "ended_at": ended or c["resolved_at"], "day": day_of(c["created_at"]),
            "test": c["body"].strip().lower() == "test" or c["title"].strip().lower() == "test",
        })

    totals = {
        "messages": sum(msg_counts.values()), "snugs": len(snugs), "diary_entries": len(diary),
        "anonymous_diary_entries": sum(1 for d in diary if d["anonymous"]),
        "nominations": len(noms), "votes": len(votes), "events": len(events), "rounds": len(rounds),
        "challenges": len(challenges), "missions": len(missions), "shop_tasks": len(shop_tasks),
        "shop_purchases": sum(len(v) for v in purchases.values()),
        "tokens_earned": sum(tokens_earned.values()),
        "token_moves": sum(1 for e in events if e["kind"] == "immunity_used" and not e["p"].get("source")),
    }
    meta = {"game_started_at": start, "first_day": 0, "last_day": last_day, "house_thread": house,
            "winner": crowned["target_id"] if crowned else None, "prize": crowned["p"].get("prize") if crowned else None,
            "finalists": finalists, "totals": totals}
    return {"housemates": housemates, "rounds": rounds, "days": days, "challenges": challenges, "meta": meta,
            "where": where, "day_of": day_of}


EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")


def resolve_scenes(db, scenes_path, ctx):
    """Re-read every message a scene cites straight from the DB, so the replay quotes what was
    actually said and when, whatever the scene file was drafted from."""
    story = json.load(open(scenes_path))
    scenes = story.get("scenes", []) + [s for c in story.get("chapters", []) for s in c.get("scenes", [])]
    by_id = {}
    wanted = [m["message_id"] for s in scenes for m in s.get("messages", []) if m.get("message_id")]
    for i in range(0, len(wanted), 500):
        chunk = wanted[i:i + 500]
        for r in rows(db, f"SELECT message_id, user_id, content, at, attachments, reply_to, thread_id FROM bb_messages "
                          f"WHERE message_id IN ({','.join('?' * len(chunk))})", *chunk):
            by_id[r["message_id"]] = r
    diary = {d["id"]: d for d in rows(db, "SELECT id, user_id, anonymous, text, created_at FROM bb_diary")}
    missing = []
    for s in scenes:
        # Diary entries cited by id come back with the writer's name only if they signed it.
        for e in s.get("entries", []):
            d = diary.get(e.get("diary_id"))
            if d:
                e.update({"who": None if d["anonymous"] else d["user_id"], "at": d["created_at"],
                          "text": e.get("text") or name_ids(d["text"])})
                if d["anonymous"]:
                    e["reveal"] = d["user_id"]
        for m in s.get("messages", []):
            r = by_id.get(m.get("message_id"))
            if r is None:
                if m.get("message_id"):
                    missing.append(m["message_id"])
                continue
            text = r["content"] or ""
            m.update({"user_id": r["user_id"], "at": r["at"], "where": ctx["where"](r["thread_id"]),
                      "attachments": r["attachments"], "day": ctx["day_of"](r["at"])})
            # Keep a hand-trimmed excerpt if the scene has one; otherwise the full message.
            if not m.get("text") or m["text"] not in name_ids(text):
                m["text"] = name_ids(text)
            m["emoji"] = sorted({(e[1], e[2], bool(e[0])) for e in EMOJI_RE.findall(text)})
    story["missing_message_ids"] = missing
    return story


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scenes", help="hand-picked scene file to resolve into iconic_chats.json")
    a = ap.parse_args()
    db = load(a.db)
    data = extract(db)
    os.makedirs(a.out, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(a.out, name), "w") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

    dump("housemates.json", {"meta": data["meta"], "housemates": data["housemates"]})
    dump("timeline.json", {"meta": data["meta"], "days": data["days"]})
    dump("challenges.json", data["challenges"])
    dump("nominations_and_votes.json", data["rounds"])
    if a.scenes:
        story = resolve_scenes(db, a.scenes, data)
        dump("iconic_chats.json", story)
        if story["missing_message_ids"]:
            print(f"warning: {len(story['missing_message_ids'])} scene messages not found in the DB")
    t = data["meta"]["totals"]
    print(f"days 0-{data['meta']['last_day']}: {t['events']} events, {t['messages']} messages, {t['snugs']} snugs, "
          f"{t['diary_entries']} diary entries, {t['nominations']} nominations, {t['votes']} votes")


if __name__ == "__main__":
    main()
