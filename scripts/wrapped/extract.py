"""Turn the bot's data into wrapped.json: every number, name and list the Wrapped video shows.

    python3 scripts/wrapped/extract.py --data DIR --out wrapped.json [--since 2026-01-01] [--until 2027-01-01]

DIR is what pull.sh writes. Dates are UK calendar days and --until is exclusive; it defaults to today,
so a run in October covers the year so far. Comparisons with last year use the same stretch of dates a
year earlier, except "the day we passed last year", which is against last year's whole total.

Bots are left out of every chat stat. Stats refer to people by id; the "people" map at the end carries
the name and avatar of everyone the video shows. Anyone who couldn't be named (they've left, and pull.sh
wasn't given their id) is listed in DIR/unresolved.txt for a second pull.

Pure stdlib, read-only, and nothing here talks to Discord.
"""
import argparse
import bisect
import json
import math
import random
import re
import sqlite3
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
VOICE_NOTE = 1 << 13  # MessageFlags.is_voice_message
TEST_ACCOUNTS = {818885214189256714}
HMS_VICTORY = 1171842947440967770  # the bot
DEVELOPER = 404634271861571584  # who builds it: its updates, and its no-reason server changes, are theirs  # the developer's test account, kept off the bot's own leaderboards too
PERSONAL_MIN, VIDEO_MIN = 10, 50  # messages this year for a personal card, and for a personal video
REGULAR = 10_000  # messages needed for the "share of your messages" awards, so they go to people everyone knows
LAUGH_REACTS = {"😂", "🤣", "😆", "💀", "😭", "😹"}
HEARTS = {"❤️", "♥️", "🩷", "💖", "💗", "💕", "💞", "💓", "😍", "🥰", "🫶", "💘", "💝", "❣️", "🧡", "💛", "💚", "💙",
          "💜", "🤍", "🖤", "🤎", "❤️‍🔥"}

# Days worth a caption on the calendar, checked against what people were saying that day. Days inside
# the Big Brother run are captioned with the day of the show automatically; anything else that isn't
# here gets its busiest channel instead.
MOMENTS = {
    "2026-05-08": "Local election results",
    "2026-07-15": "England v Argentina",
    "2026-08-14": "Everyone's phone got the emergency alert",
}

GAMES = {
    "mines": "Mines", "higherlower": "Higher-Lower", "roulette": "Roulette", "blackjack": "Blackjack",
    "slots": "Slots", "blockade": "Blockade Run", "chest": "Chest", "glass": "Glass Bridge",
    "reddog": "Red Dog", "tcp": "Three Card Poker", "penalty": "Penalty Shootout", "plinko": "Plinko",
    "darts": "Darts", "pennyfalls": "Penny Falls", "videopoker": "Video Poker",
}
# lib/activities/kart_card.py TRACK_NAMES, copied so this script runs without the bot's imports.
TRACKS = {"village": "Little Puddleton", "silverstone": "Silverstone", "wobbling": "Much Wobbling",
          "clanking": "Great Clanking", "wallop": "Nether Wallop", "rainbow": "Rainbow Road",
          "beach": "Pleasure Beach"}
SHOP = {"lucky_dip": "Lucky Dip", "shutcoin": "Shutcoin", "vip_case": "VIP Case",
        "custom_emoji_sticker": "Custom emoji", "roast_access": "Roast pass"}

BRITISH = {"innit", "mate", "proper", "cheers", "bruv", "bloody", "quid", "cuppa", "knackered", "gutted",
           "chuffed", "blimey", "cheeky", "bollocks", "brew", "lad", "lads", "init", "fortnight", "skint",
           "minging", "naff", "peckish", "bants", "wanker", "arse", "dodgy", "faff", "gobsmacked", "rubbish"}
SORRY = re.compile(r"\b(sorry|soz|apologies|my bad)\b")
DRY = re.compile(r"^(ok|okay|k|kk|ye|yea|yeah|ya|yep|yh|sure|fair|true|mhm|cool|nice)[.!]*$")
GIF = re.compile(r"(tenor\.com|giphy\.com|\.gif\b)", re.I)
JAB = re.compile(r"\b(shut up|stfu|shush|idiot|clown|loser|bozo|cope|seethe|ratio|dumbass|muppet|melt|nonce|div|pillock|"
                 r"you'?re wrong|ur wrong|nobody cares|no one cares)\b")
PEACE = re.compile(r"\b(calm down|chill out|calm it|cool it|guys stop|stop arguing|stop fighting|be nice|relax guys|everyone calm)\b")
LAUGH_ONLY = re.compile(r"^(lol+|lmao+|lmfao+|haha+|ha(ha)+|xd+|😭+|💀+|😂+|🤣+)$")
GOODNIGHT = re.compile(r"^(good ?night|gn|g'?night|nighty? ?night|night night|night all|night guys|night everyone|nini)\b"
                       r"|\b(off to bed|going to bed|going to sleep|off to sleep)\b")
# Swears, by the word they come from. "arse" leaves Arsenal alone.
SWEARS = {"fuck": r"f+u+c+k+\w*|motherfuck\w*|fck\w*|ffs", "shit": r"shit\w*|shite|bullshit\w*", "piss": r"piss\w*",
          "bollocks": r"bollock\w*", "bloody": r"bloody", "wanker": r"wank\w*", "twat": r"twat\w*", "bastard": r"bastard\w*",
          "prick": r"prick\w*", "dick": r"dick\w*", "bellend": r"bellend\w*", "cunt": r"cunt\w*", "arse": r"arse(?!nal)\w*|ass|asshole\w*",
          "crap": r"crap\w*"}
SWEAR = re.compile(r"\b(?:" + "|".join(f"(?P<{k}>{v})" for k, v in SWEARS.items()) + r")\b")
# Poo, the ways people actually spell it ("poopja" is a pun on a member's name). Pooh, pookie and dump mean other things.
POO = re.compile(r"\b(poo+s?|poo+ps?|poo+ped|poo+ping|poo+py|poo+ey|poo+ed|poo+ing|poo+pers?|poo+pies?|poo+poo+|(?:pee)+poo+(?:poo+)?|"
                 r"poopja|doo+doo+|dookie|dooky|sharts?|sharted|sharting|turds?)\b|💩")
LAUGHS = {"lol": r"\blol\b", "lmao": r"\blmao\b", "lmfao": r"\blmfao\b", "haha": r"\bha(ha)+h?\b",
          "💀": "💀", "😭": "😭", "😂": "😂"}

STOP = set("""a about above after again against all also am an and any are aren't as at be because been before
being below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further get gets got had hadn't has hasn't have haven't having he he'd he'll he's
her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its
itself just let's like me more most mustn't my myself no nor not now of off on once only or other ought our
ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some such than that
that's the their theirs them themselves then there there's these they they'd they'll they're they've this
those through to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when
when's where where's which while who who's whom why why's will with won't would wouldn't you you'd you'll
you're you've your yours yourself yourselves im dont thats yeah yes yea yeh ye nah na ok okay oh ah lol lmao
lmfao haha one two would could really think know going gonna want wanna said say says thing things people
time make made see well even still back way much many good also us get got go goes went need never
always actually literally probably maybe something someone anyone everyone nothing anything everything
though thought sure right didnt doesnt isnt wasnt cant wont youre theyre ive id ill hes shes whats theres
lot lots day days today yesterday tomorrow new old first last next mean means look looks looking come
coming came take took give gave tell told guy guys man bro dude u ur r tho rn idk tbh imo ngl omg wtf
ya yo hey hi hello thanks thank pls please y n bit little big great best better bad worse worst""".split())

# Kept out of anything that prints words on screen.
SLURS = re.compile(r"(nig+(?:er|a|ah|uh)s?|f+a+g+(?:ot|got)?s?|retard(?:ed|s)?|tran+(?:y|ie)s?|paki?s?|"
                   r"chink|spic|kike|coon|wog|gyp(?:sy|o)|tard|dyke|kys)$")

URL = re.compile(r"https?://\S+")
TOKEN_NOISE = re.compile(r"<a?:\w+:\d+>|<[@#][!&]?\d+>|<t:\d+(?::\w)?>")
WORD = re.compile(r"[a-z][a-z']{2,}")
CUSTOM_EMOJI = re.compile(r"<(a?):(\w+):(\d+)>")


def fmt_n(n):
    return f"{int(n):,}"


def day_bounds(since, until):
    lo = datetime.combine(since, datetime.min.time(), UK).timestamp()
    hi = datetime.combine(until, datetime.min.time(), UK).timestamp()
    return int(lo), int(hi)


def year_ago(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)


class Extract:
    def __init__(self, data, since, until, hof_picks=None):
        self.data = Path(data)
        picks = json.loads(Path(hof_picks).read_text()) if hof_picks else None
        # The picks file is either a list of slides, or {"slides": [...], "interludes": [{"after", "id"}]}.
        self.hof_picks = picks.get("slides") if isinstance(picks, dict) else picks
        self.interlude_picks = picks.get("interludes", []) if isinstance(picks, dict) else []
        # ... and "iceberg": the stories behind some /iceberg entries, [{"entry", "story", "quote", "count"}].
        self.iceberg_picks = picks.get("iceberg", []) if isinstance(picks, dict) else []
        self.since, self.until = since, until
        self.lo, self.hi = day_bounds(since, until)
        self.plo, self.phi = day_bounds(year_ago(since), year_ago(until))
        self.yo_lo, self.yo_hi = day_bounds(date(since.year - 1, 1, 1), date(since.year, 1, 1))
        self.ch = sqlite3.connect(f"file:{self.data / 'chronicle.db'}?mode=ro", uri=True)
        self.db = sqlite3.connect(f"file:{self.data / 'database.db'}?mode=ro", uri=True)
        self.discord = json.loads((self.data / "discord.json").read_text())
        self.bots = {r[0] for r in self.ch.execute("SELECT DISTINCT user_id FROM messages WHERE is_bot = 1")}
        self.bots |= {int(k) for k, v in self.discord["people"].items() if v.get("bot")}
        self.bots |= TEST_ACCOUNTS  # counted like a bot: never shown, never in a total
        # Only people still in the server are named anywhere; their messages still count in the totals.
        self.members = {int(k) for k, v in self.discord["people"].items() if v.get("member") and not v.get("bot")} - self.bots
        self.featured = set()
        self.out = {}
        self.bb_start, self.bb_end = self.big_brother_dates()

    def big_brother_dates(self):
        try:
            state = dict(self.q("SELECT key, value FROM bb_state", db=self.db))
            end = self.q("SELECT MAX(closed_at) FROM bb_rounds", db=self.db)[0][0]
        except sqlite3.OperationalError:
            return None, None
        start = int(state.get("game_started_at") or 0)
        day = lambda t: datetime.fromtimestamp(t, UK).date().isoformat()
        return (day(start) if start else None), (day(end) if end else None)

    # ------------------------------------------------------------------ helpers
    def who(self, uid):
        uid = int(uid)
        self.featured.add(uid)
        return str(uid)

    def human(self, uid):
        return uid is not None and int(uid) not in self.bots

    def q(self, sql, params=(), db=None):
        return (db or self.ch).execute(sql, params).fetchall()

    def announcing(self, cid):
        """An announcement channel (announcements, directives, voting): posts there are news, not chat."""
        return (self.discord["channels"].get(str(cid)) or {}).get("type") == 5

    def channel_name(self, cid):
        c = self.discord["channels"].get(str(cid))
        return c["name"] if c else None

    # ------------------------------------------------------------------ chat, one pass
    def shown(self, uid):
        """People the video may name: current members of the server, never bots."""
        return uid is not None and int(uid) in self.members

    # ------------------------------------------------------------------ chat, one pass
    def scan_messages(self):
        """One pass over the year's human messages for everything per-person, per-pair and per-word."""
        per = defaultdict(lambda: Counter())
        days_active = defaultdict(set)
        day_counts = defaultdict(Counter)
        hours = defaultdict(lambda: [0] * 24)
        channels_seen = defaultdict(Counter)
        pairs = Counter()
        night_pairs = Counter()
        months = defaultdict(Counter)
        day_words = defaultdict(Counter)
        laughs = Counter()
        chain, argument = {}, {}
        replied_by = defaultdict(set)
        weekday = {}
        first = []
        longest = None
        # goodnights, checked at the end against each person's later messages; replies to other people,
        # checked against reactions for ratios
        goodnights, sent_at = defaultdict(list), defaultdict(list)
        first_text = {}
        human_replies = []
        jabs = Counter()
        swear_words, swear_by = Counter(), Counter()
        poo_month, poo_day = Counter(), Counter()
        rows = self.ch.execute(
            "SELECT message_id, user_id, content, local_day, local_hour, char_count, word_count, n_attachments, "
            "flags, edited_ts, COALESCE(parent_id, channel_id), msg_type, reply_to, reply_to_user, ts, stickers, attachments "
            "FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 ORDER BY ts", (self.lo, self.hi))
        for (mid, uid, content, day, hour, chars, words, atts, flags, edited, chan, mtype, rto_msg, rto, ts,
             stickers, att_json) in rows:
            c = per[uid]
            text = content or ""
            low = text.lower()
            c["msgs"] += 1
            c["chars"] += chars
            c["words"] += words
            if atts:
                c["files"] += 1
            if stickers and stickers not in ("[]", ""):
                c["stickers"] += 1
            if GIF.search(text) or (att_json and GIF.search(att_json)):
                c["gifs"] += 1
            elif "http" in low:
                c["links"] += 1
            if flags & VOICE_NOTE:
                c["voice"] += 1
            if edited:
                c["edited"] += 1
            letters = [ch for ch in text if ch.isalpha()]
            if len(letters) >= 6 and all(ch.isupper() for ch in letters):
                c["caps"] += 1
            if "?" in text:
                c["questions"] += 1
            if SORRY.search(low):
                c["sorry"] += 1
            bare = low.strip()
            if DRY.match(bare):
                c["dry"] += 1
            if LAUGH_ONLY.match(bare):
                c["laugh_only"] += 1
            if PEACE.search(low):
                c["peace"] += 1
            sw = [m.lastgroup for m in SWEAR.finditer(low)]
            if sw:
                swear_words.update(sw)
                swear_by.update((w, uid) for w in sw)
                c["swears"] += len(sw)
                c["swear_msgs"] += 1
            if POO.search(low):
                c["poo"] += 1
                poo_month[day[:7]] += 1
                poo_day[day] += 1
            if len(bare) <= 40 and GOODNIGHT.search(bare):
                goodnights[uid].append(ts)
            sent_at[uid].append(ts)
            if uid not in first_text and text.strip() and not URL.search(text) and len(text) <= 220 and not self.announcing(chan):
                first_text[uid] = (text, day)
            if hour is not None:
                hours[uid][hour] += 1
                if 1 <= hour < 5:
                    c["night"] += 1
                if 5 <= hour < 8:
                    c["early"] += 1
                if 12 <= hour < 14:
                    c["lunch"] += 1
            if day not in weekday:
                weekday[day] = date.fromisoformat(day).weekday()
            if weekday[day] >= 5:
                c["weekend"] += 1
            days_active[uid].add(day)
            day_counts[uid][day] += 1
            channels_seen[uid][chan] += 1
            if rto is not None and rto != uid and (mtype == "reply" or mtype is None):
                c["replies"] += 1
                if int(rto) in self.bots:
                    c["bot_replies"] += 1
                else:
                    human_replies.append((mid, uid, rto_msg))
                    if JAB.search(low):
                        jabs[(uid, rto)] += 1
                    pairs[(uid, rto)] += 1
                    k = (min(uid, rto), max(uid, rto))
                    if hour is not None and 1 <= hour < 5:
                        night_pairs[k] += 1
                    replied_by[rto_msg].add(uid)
                    # an argument: a reply to a reply to a reply, the same two people taking turns
                    prev = chain.get(rto_msg)
                    run = prev[1] + 1 if prev and prev[0] == k and prev[2] == rto else 2
                    chain[mid] = (k, run, uid)
                    if run > argument.get(k, (0,))[0]:
                        argument[k] = (run, day)
            for name, pat in LAUGHS.items():
                if re.search(pat, low):
                    laughs[name] += 1
            clean = TOKEN_NOISE.sub(" ", URL.sub(" ", low))
            toks = [w.strip("'") for w in WORD.findall(clean)]
            toks = [w for w in toks if len(w) >= 3 and w not in STOP and not SLURS.match(w)]
            months[day[:7]].update(toks)
            day_words[day].update(set(toks))
            c["british"] += sum(1 for w in toks if w in BRITISH)
            if len(first) < 6 and text.strip() and self.shown(uid):
                first.append({"user": uid, "text": text[:140], "ts": ts})
            if self.shown(uid) and (longest is None or chars > longest[0]):
                longest = (chars, uid, day, mid)
        # A fake goodnight: still posting half an hour later, the same night (within four hours).
        for uid, times in goodnights.items():
            later = sent_at[uid]
            for t in times:
                i = bisect.bisect_left(later, t + 1800)
                if i < len(later) and later[i] < t + 4 * 3600:
                    per[uid]["fake_gn"] += 1
        self.human_replies = human_replies
        self.first_text = first_text
        self.jabs = jabs
        self.swear_words, self.swear_by = swear_words, swear_by
        self.poo_month, self.poo_day = poo_month, poo_day
        # Everything below ranks people, so only people who can be shown take part. Totals elsewhere are
        # counted straight from the database and still include everyone.
        keep = lambda u: self.shown(u)
        self.per = {u: c for u, c in per.items() if keep(u)}
        self.days_active = {u: v for u, v in days_active.items() if keep(u)}
        self.day_counts = {u: v for u, v in day_counts.items() if keep(u)}
        self.hours = {u: v for u, v in hours.items() if keep(u)}
        self.channels_seen = {u: v for u, v in channels_seen.items() if keep(u)}
        self.pairs = Counter({p: n for p, n in pairs.items() if keep(p[0]) and keep(p[1])})
        self.night_pairs = Counter({p: n for p, n in night_pairs.items() if keep(p[0]) and keep(p[1])})
        self.argument = {p: v for p, v in argument.items() if keep(p[0]) and keep(p[1])}
        self.replied_by = replied_by
        self.months_words, self.day_words, self.laughs = months, day_words, laughs
        self.first_msgs, self.longest = first, longest

    # ------------------------------------------------------------------ sections
    def numbers(self):
        def totals(lo, hi):
            r = self.q("SELECT COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM(word_count),0), "
                       "COALESCE(SUM(n_attachments > 0),0), COALESCE(SUM((flags & ?) != 0),0) "
                       "FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0", (VOICE_NOTE, lo, hi))[0]
            return dict(zip(("messages", "people", "words", "files", "voice_notes"), r))

        def reactions(lo, hi):
            # Reactions people put on messages sent in the window, as they stand: every add the history
            # holds, less any taken off since. Counted by the message's date for both years, so they
            # compare like for like, and bots' reactions never count.
            n = 0
            for u, k in self.q("SELECT r.user_id, SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) FROM reactions r "
                               "JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? "
                               "GROUP BY r.user_id", (lo, hi)):
                if self.human(u):
                    n += k
            return n
        now, prev = totals(self.lo, self.hi), totals(self.plo, self.phi)
        now["reactions"], prev["reactions"] = reactions(self.lo, self.hi), reactions(self.plo, self.phi)
        self.out["numbers"] = {"now": now, "prev": prev}

    # ------------------------------------------------------------------ awards
    def awards(self):
        per = self.per
        regulars = [u for u, c in per.items() if c["msgs"] >= REGULAR]

        def best(key, pool=None, rate=False, minimum=1, reverse=True):
            pool = pool if pool is not None else per.keys()
            scored = []
            for u in pool:
                c = per[u]
                v = c[key] / c["msgs"] if rate else c[key]
                if c[key] >= minimum:
                    scored.append((v, c[key], u))
            scored.sort(reverse=reverse)
            return scored[:3]

        out = []

        def award(key, title, line, ranked, fmt):
            ranked = [r for r in ranked if self.shown(r[2])]
            if not ranked:
                return
            v, raw, u = ranked[0]
            out.append({"key": key, "title": title, "line": line if isinstance(line, str) else line(v, raw, u),
                        "user": self.who(u), "value": fmt(v, raw, u), "runners": [self.who(r[2]) for r in ranked[1:3]]})

        def counted(counter):
            return [(v, v, u) for u, v in counter.most_common(12) if self.shown(u)][:3]

        pct = lambda v, raw, u: f"{v * 100:.0f}%"
        num = lambda v, raw, u: f"{int(raw):,}"

        award("night_owl", "Night Owl", "of their messages sent between 1am and 5am", best("night", regulars, rate=True), pct)
        award("early_bird", "Early Bird", "of their messages sent before 8am", best("early", regulars, rate=True), pct)
        award("lunch", "Lunch Break", "of their messages sent between 12 and 2pm", best("lunch", regulars, rate=True), pct)
        award("weekend", "Weekend Warrior", "of their messages sent on a Saturday or Sunday", best("weekend", regulars, rate=True), pct)
        one_day = sorted(((max(d.values()), 0, u) for u, d in self.day_counts.items()), reverse=True)[:3]
        top_day = lambda u: max(self.day_counts[u].items(), key=lambda kv: kv[1])[0]
        award("one_day", "One-Day Wonder", lambda v, r, u: f"messages in a single day, on {self.long_day(top_day(u))}",
              one_day, lambda v, r, u: f"{v:,}")
        waffle = sorted(((per[u]["words"] / per[u]["msgs"], 0, u) for u in regulars), reverse=True)[:3]
        award("waffler", "The Waffler", "words per message, on average", waffle, lambda v, r, u: f"{v:.1f}")
        silent = sorted(((per[u]["chars"] / per[u]["msgs"], 0, u) for u in regulars))[:3]
        award("silent", "Short and Sweet", "characters per message, on average", silent, lambda v, r, u: f"{v:.0f}")
        award("shouter", "The Shouter", "of their messages in ALL CAPS", best("caps", regulars, rate=True), pct)
        award("nosey", "Nosey Parker", "of their messages were questions", best("questions", regulars, rate=True), pct)
        award("apologist", "The Apologist", "apologies", best("sorry"), num)
        award("british", "Most British", "innits, mates, quids and cheers", best("british"), num)
        award("perfectionist", "The Perfectionist", "of their messages edited after sending",
              best("edited", regulars, rate=True), pct)
        award("media", "Media Mogul", "messages with a file or picture", best("files"), num)
        award("gif", "GIF Machine", "GIFs sent", best("gifs"), num)
        award("voice", "Voice Note Royalty", "voice notes", best("voice"), num)
        award("sticker", "Sticker Fiend", "stickers sent", best("stickers"), num)
        award("replier", "The Replier", "replies sent", best("replies"), num)
        award("bot_friend", "The Bot's Best Friend", "replies to HMS Victory", best("bot_replies"), num)

        tourist = sorted(((len(v), 0, u) for u, v in self.channels_seen.items()), reverse=True)[:3]
        award("tourist", "The Tourist", "different channels posted in", tourist, lambda v, r, u: f"{v}")
        committed = sorted(((len(v), 0, u) for u, v in self.days_active.items()), reverse=True)[:3]
        n_days = (self.until - self.since).days
        award("committed", "The Committed", f"of {n_days} days with a message", committed, lambda v, r, u: f"{v}")

        partners = defaultdict(set)
        for (a, b), n in self.pairs.items():
            if n >= 3:
                partners[a].add(b)
                partners[b].add(a)
        glue = sorted(((len(s), 0, u) for u, s in partners.items()), reverse=True)[:3]
        award("glue", "The Glue", "different people they traded replies with", glue, lambda v, r, u: f"{v}")

        # Reactions, from people, on messages sent this year.
        given, got, laughs, hearts, memes, own = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
        for reactor, author, emoji, has_file, n in self.q(
                "SELECT r.user_id, r.author_id, r.emoji, m.n_attachments > 0, COUNT(*) FROM reactions r "
                "JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? AND r.action = 'add' "
                "GROUP BY 1, 2, 3, 4", (self.lo, self.hi)):
            if not self.human(reactor):
                continue
            if author == reactor:
                own[reactor] += n
                continue
            given[reactor] += n
            if self.human(author):
                got[author] += n
                if emoji in LAUGH_REACTS or any(s in emoji.lower() for s in ("kek", "laugh", "lmao", "lol")):
                    laughs[author] += n
                if emoji in HEARTS:
                    hearts[author] += n
                if has_file:
                    memes[author] += n
        award("hype", "Hype Machine", "reactions given", counted(given), num)
        award("crowd", "Crowd Pleaser", "reactions received", counted(got), num)
        award("comedian", "The Comedian", "laughing reactions on their messages", counted(laughs), num)
        award("heartthrob", "Heartthrob", "hearts on their messages", counted(hearts), num)
        award("meme", "Meme Lord", "reactions on the pictures and clips they posted", counted(memes), num)
        award("own_fan", "Own Biggest Fan", "reactions on their own messages", counted(own), num)
        award("fake_gn", "The Fake Goodnight", "goodnights, then still typing half an hour later", best("fake_gn"), num)
        award("dry", "The Dry Texter", "messages that were just \"ok\", \"yeah\" or \"fair\"", best("dry"), num)
        award("laugh_only", "The Laugh Track", "messages that were just \"lol\" or 😭", best("laugh_only"), num)
        # A ratio: a reply with more reactions than the message it answered (five at least), from
        # people other than the two involved, as the reactions stand now.
        score = Counter()
        for mid, reactor, author, n in self.q(
                "SELECT r.message_id, r.user_id, r.author_id, SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) FROM reactions r "
                "JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? - 86400 AND m.ts < ? GROUP BY 1, 2, 3",
                (self.lo, self.hi)):
            if self.human(reactor) and reactor != author and n > 0:
                score[mid] += 1
        ratios = Counter(uid for mid, uid, parent in self.human_replies if score[mid] >= 5 and score[mid] > score[parent])
        award("ratio", "Ratio King", "replies with more reactions than the message they answered", counted(ratios), num)

        sent_m, got_m, self.mention_pairs = Counter(), Counter(), Counter()
        for author, target, n in self.q(
                "SELECT user_id, target_id, COUNT(*) FROM mentions WHERE ts >= ? AND ts < ? AND kind = 'user' "
                "GROUP BY user_id, target_id", (self.lo, self.hi)):
            if self.human(author) and self.human(target) and author != target:
                sent_m[author] += n
                got_m[target] += n
                if self.shown(author) and self.shown(target):
                    self.mention_pairs[(min(author, target), max(author, target))] += n
        award("celeb", "Local Celeb", "times @mentioned", counted(got_m), num)
        award("gossip", "The Gossip", "@mentions of other people", counted(sent_m), num)
        emo = Counter()
        for u, n in self.q("SELECT user_id, SUM(n) FROM emoji_uses WHERE ts >= ? AND ts < ? GROUP BY user_id",
                           (self.lo, self.hi)):
            if self.human(u):
                emo[u] = n
        award("emoji", "Emoji Artist", "emoji used", counted(emo), num)

        gaps = defaultdict(list)
        for u, gap in self.q(
                "SELECT m.user_id, m.ts - p.ts FROM messages m JOIN messages p ON p.message_id = m.reply_to "
                "WHERE m.ts >= ? AND m.ts < ? AND m.is_bot = 0 AND (m.msg_type = 'reply' OR m.msg_type IS NULL) "
                "AND p.user_id != m.user_id AND p.is_bot = 0", (self.lo, self.hi)):
            if gap >= 0 and u in per:
                gaps[u].append(gap)
        fast = sorted((statistics.median(g), 0, u) for u, g in gaps.items() if len(g) >= 1000)[:3]
        award("fastest", "Fastest Fingers", "seconds to reply, typically", fast, lambda v, r, u: f"{v:.0f}")

        starters, killers = self.conversations()
        award("starter", "Conversation Starter", "quiet channels they brought back to life", counted(starters), num)
        kill_rate = sorted(((killers[u] / per[u]["msgs"] * 1000, killers[u], u) for u in regulars if killers[u]),
                           reverse=True)[:3]
        award("killer", "Conversation Killer", "last words before two hours of daytime silence", kill_rate,
              lambda v, r, u: f"{int(r):,}")

        hof = Counter()
        for mid in self.hof_ids():
            row = self.q("SELECT user_id, ts FROM messages WHERE message_id = ?", (mid,))
            if row and self.lo <= row[0][1] < self.hi and self.shown(row[0][0]):
                hof[row[0][0]] += 1
        award("hall_of_famer", "Hall of Famer", "messages in the Hall of Fame", counted(hof), num)

        def ledger(pattern):
            c = Counter()
            for u, n in self.q("SELECT user_id, COUNT(*) FROM user_transactions WHERE ts >= ? AND ts < ? AND reason LIKE ? "
                               "GROUP BY user_id", (self.lo, self.hi, pattern), db=self.db):
                c[int(u)] = n
            return c
        award("welcomer", "The Welcomer", "new members welcomed", counted(ledger("Welcomed a new member%")), num)
        award("wordle", "Wordle Wizard", "HMS Wordles solved", counted(ledger("HMS Wordle solve%")), num)
        award("bump", "Bump Champion", "server bumps on DISBOARD", counted(ledger("DISBOARD bump reward%")), num)
        award("tree", "Tree Hugger", "times they watered the tree", counted(ledger("Tree watering reward%")), num)
        award("oracle", "The Oracle", "predictions called right", counted(ledger("Prediction win:%")), num)
        roasted = Counter({int(u): n for u, n in self.q("SELECT user_id, count FROM roast_targets", db=self.db)})
        award("roasted", "Most Roasted", "times on the end of /roast", counted(roasted), num)

        before = dict(self.q("SELECT user_id, COUNT(*) FROM messages WHERE ts < ? AND is_bot = 0 GROUP BY user_id",
                             (self.lo,)))
        prev_window = dict(self.q("SELECT user_id, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                                  "GROUP BY user_id", (self.plo, self.phi)))
        rookies = sorted(((per[u]["msgs"], u) for u in per if u not in before), reverse=True)[:5]
        glow = sorted(((per[u]["msgs"] - prev_window.get(u, 0), u) for u in per if prev_window.get(u, 0) >= 100),
                      reverse=True)[:5]
        award("rookie", "Rookie of the Year", "messages since their first ever", [(n, n, u) for n, u in rookies], num)
        award("glowup", "Glow-up of the Year", "more messages than the same stretch last year",
              [(n, n, u) for n, u in glow], lambda v, r, u: f"+{int(v):,}")

        self.drama(award, counted, num, out)
        self.staff(award, counted, num)
        self.out["awards"] = out
        self.out["rookies"] = [{"user": self.who(u), "n": n, "first": min(self.days_active[u])} for n, u in rookies[:3]]
        self.out["glowups"] = [{"user": self.who(u), "n": per[u]["msgs"], "prev": prev_window.get(u, 0)}
                               for _, u in glow[:3]]

    def drama(self, award, counted, num, out):
        """The drama page: two people who argue all the time and talk all the time, the biggest simp, the regular
        nobody answers, the peacemaker, AutoMod's favourite and the nickname changer."""
        name = lambda u: self.discord["people"].get(str(u), {}).get("name", "someone")
        # Frenemies: insults traded both ways (three at least each way), among people who never stop talking.
        jab = self.jabs
        duos = sorted(((jab[(a, b)] + jab[(b, a)], a, b) for (a, b) in {tuple(sorted(k)) for k in jab}
                       if min(jab[(a, b)], jab[(b, a)]) >= 3 and self.shown(a) and self.shown(b)), reverse=True)
        if duos:
            n, a, b = duos[0]
            talk = self.pairs[(a, b)] + self.pairs[(b, a)]
            out.append({"key": "frenemies", "title": "Frenemies", "a": self.who(a), "b": self.who(b), "value": f"{n:,}",
                        "line": f"of their {talk:,} replies to each other were \"shut up\", \"idiot\" or \"clown\"",
                        "runners": []})
        hearts = Counter()
        for reactor, author, n in self.q(
                "SELECT r.user_id, r.author_id, SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) FROM reactions r JOIN messages m "
                "ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? AND r.emoji IN (%s) GROUP BY 1, 2"
                % ",".join("?" * len(HEARTS)), (self.lo, self.hi, *HEARTS)):
            if n > 0 and reactor != author and self.shown(reactor) and self.shown(author):
                hearts[(reactor, author)] = n
        simps = [(n, n, a) for (a, b), n in hearts.most_common(3)]
        target = {a: b for (a, b), _ in hearts.most_common(3)}
        award("simp", "Biggest Simp", lambda v, r, u: f"hearts on {name(target[u])}'s messages", simps, num)
        # Shouting into the void: the regular whose messages most often got no reply and no reaction.
        reacted = {m for (m,) in self.q(
            "SELECT r.message_id FROM reactions r JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? "
            "AND r.user_id != r.author_id GROUP BY 1 HAVING SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) > 0",
            (self.lo, self.hi))}
        regulars = {u for u, c in self.per.items() if c["msgs"] >= REGULAR}
        unheard = Counter()
        for mid, u in self.q("SELECT message_id, user_id FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0", (self.lo, self.hi)):
            if u in regulars and mid not in self.replied_by and mid not in reacted:
                unheard[u] += 1
        void = sorted(((unheard[u] / self.per[u]["msgs"], unheard[u], u) for u in regulars), reverse=True)[:3]
        award("void", "Shouting into the Void", "of their messages got no reply and no reaction", void,
              lambda v, r, u: f"{v * 100:.0f}%")
        award("peacemaker", "The Peacemaker", "times they said \"calm down\", \"chill out\" or \"guys stop\"",
              [(c["peace"], c["peace"], u) for u, c in sorted(self.per.items(), key=lambda kv: -kv[1]["peace"])[:3]
               if c["peace"]], num)
        blocked = Counter(u for (u,) in self.q("SELECT user_id FROM audit_log WHERE action = 'automod_block_message' "
                                                "AND ts >= ? AND ts < ?", (self.lo, self.hi)))
        award("automod", "AutoMod's Favourite", "messages blocked by AutoMod", counted(blocked), num)
        nicks = Counter(u for (u,) in self.q("SELECT user_id FROM member_events WHERE action = 'nick' AND ts >= ? AND ts < ?",
                                             (self.lo, self.hi)))
        award("identity", "Identity Crisis", "nickname changes", counted(nicks), num)

    def staff(self, award, counted, num):
        """The staff page. Mod cases and tickets come from Wick's and Ticket Tool's logs (mod_logs.json, the
        whole year); server changes from the audit log, which starts in August; the Shipwright from the
        bot's own history."""
        try:
            logs = json.loads((self.data / "mod_logs.json").read_text())
        except FileNotFoundError:
            return
        usernames = {k: int(v) for k, v in logs["usernames"].items()}
        cases, night, tickets = Counter(), Counter(), Counter()
        for m in logs["logs"]:
            t = datetime.fromisoformat(m["ts"]).timestamp()
            if not (self.lo <= t < self.hi) or not m["embeds"]:
                continue
            f = {x["name"].rstrip(":"): x["value"] for x in m["embeds"][0].get("fields") or []}
            if m["bot"] == "wick" and "Case" in f and "Moderator" in f:
                mod = re.search(r"`([^`]+)`", f["Moderator"])
                uid = usernames.get(mod.group(1)) if mod else None
                if uid and self.human(uid):
                    cases[uid] += 1
                    if datetime.fromtimestamp(t, UK).hour < 6:
                        night[uid] += 1
            elif m["bot"] == "ticket-tool" and "Ticket Owner" in f:
                owner = re.search(r"\d{15,}", f["Ticket Owner"])
                # everyone who spoke in the ticket apart from whoever opened it
                for _, uid in re.findall(r"(\d+) - <@!?(\d+)>", f.get("Users in transcript", "")):
                    if not (owner and uid == owner.group(0)) and self.human(int(uid)):
                        tickets[int(uid)] += 1
        jobs = Counter({u: cases[u] + tickets[u] for u in set(cases) | set(tickets)})
        msgs = lambda u: self.per[u]["msgs"] if u in self.per else 0
        award("hardest", "Hardest Worker", lambda v, r, u: f"mod cases and tickets ({cases[u]:,} and {tickets[u]:,})",
              counted(jobs), num)
        lowkey = sorted(((jobs[u] / max(msgs(u), 1), jobs[u], u) for u in jobs if jobs[u] >= 50 and self.shown(u)), reverse=True)
        award("lowkey", "Low-key Hardest Worker", lambda v, r, u: f"cases and tickets, and only {msgs(u):,} messages all year",
              lowkey[:3], num)
        award("ticket_hero", "Ticket Hero", "tickets worked", counted(tickets), num)
        award("night_shift", "Night Shift", "mod cases between midnight and 6am", counted(night), num)

        # The Shipwright: every update to the bot this year, wherever it was committed, and what it went on to say.
        since, until = (datetime.fromtimestamp(x, UK).isoformat() for x in (self.lo, self.hi))
        try:
            updates = int(subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-list", "--count",
                                          "--no-merges", "--all", f"--since={since}", f"--until={until}"],
                                         capture_output=True, text=True, check=True).stdout)
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            updates = 0
        said = self.q("SELECT COUNT(*) FROM messages WHERE user_id = ? AND ts >= ? AND ts < ?", (HMS_VICTORY, self.lo, self.hi))[0][0]
        if updates:
            award("shipwright", "The Shipwright", f"updates to HMS Victory, which sent {said:,} messages this year",
                  [(updates, updates, DEVELOPER)], num)

        # Server Architect: changes to channels, permissions, roles and the server's settings, by hand or through
        # HMS Victory. The bot's own changes count for whoever its reason names, or for its developer when it
        # gives none (their scripts); Big Brother, raid lockdowns and the Halloween icons run themselves.
        built_kinds = ("channel_", "overwrite_", "role_", "guild_update", "automod_rule", "onboarding_", "emoji_", "sticker_")
        built = Counter()
        for act, uid, reason in self.q("SELECT action, user_id, reason FROM audit_log WHERE ts >= ? AND ts < ?",
                                       (self.lo, self.hi)):
            if not act.startswith(built_kinds):
                continue
            if uid == HMS_VICTORY:
                r = (reason or "").strip()
                if r.startswith(("Big Brother", "Anti-raid", "Halloween")):
                    continue
                named = re.search(r"\b(?:by|at) ([\w.]+?)(?:'s request)?$", r)
                uid = usernames.get(named.group(1)) if named else DEVELOPER
            if uid and self.human(uid):
                built[uid] += 1
        award("architect", "Server Architect", "changes to the server", counted(built), num)

    def personal(self):
        """Every member's own Wrapped, for the personal card and video: their numbers, when and where they
        talk, their people, their habits, what they won, and who they are (the habit they do most compared
        with everyone else). Current members with PERSONAL_MIN messages; a video from VIDEO_MIN."""
        per = self.per
        ranked = sorted(per, key=lambda u: -per[u]["msgs"])
        rank = {u: i + 1 for i, u in enumerate(ranked)}
        prev = dict(self.q("SELECT user_id, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                           "GROUP BY user_id", (self.plo, self.phi)))
        known = self.discord["people"]

        def ref(u, n):
            p = known.get(str(u), {})
            return {"user": str(u), "name": p.get("name", "someone"), "avatar": p.get("avatar"), "n": n}

        # reactions people put on each other's messages, as they stand
        got, laughs, hearts, fans, gave, heart_pairs, laugh_pairs = (Counter() for _ in range(7))
        for reactor, author, emoji, n in self.q(
                "SELECT r.user_id, r.author_id, r.emoji, SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) FROM reactions r "
                "JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? GROUP BY 1, 2, 3",
                (self.lo, self.hi)):
            if n <= 0 or reactor == author or not self.human(reactor) or not self.human(author):
                continue
            got[author] += n
            gave[reactor] += n
            fans[(reactor, author)] += n
            if emoji in LAUGH_REACTS or any(k in emoji.lower() for k in ("kek", "laugh", "lmao", "lol")):
                laughs[author] += n
                laugh_pairs[(reactor, author)] += n
            if emoji in HEARTS:
                hearts[author] += n
                heart_pairs[(reactor, author)] += n
        emoji = defaultdict(Counter)
        for u, e, eid, n in self.q("SELECT user_id, emoji, emoji_id, SUM(n) FROM emoji_uses WHERE ts >= ? AND ts < ? "
                                   "GROUP BY 1, 2, 3", (self.lo, self.hi)):
            emoji[u][(e, eid)] += n
        medals = defaultdict(Counter)
        try:
            for u, tier in self.q("SELECT ub.user_id, b.rarity FROM user_badges ub JOIN badges b ON b.id = ub.badge_id "
                                  "WHERE ub.awarded_at >= ? AND ub.awarded_at < ?", (self.lo, self.hi), db=self.db):
                medals[int(u)][tier] += 1
        except sqlite3.OperationalError:
            pass
        casino = {}
        try:
            for u, n, net, best in self.q("SELECT user_id, COUNT(*), SUM(net), MAX(net) FROM casino_results "
                                          "WHERE timestamp >= ? AND timestamp < ? GROUP BY 1", (self.lo, self.hi), db=self.db):
                casino[int(u)] = {"games": n, "net": net, "best": max(best, 0)}
        except sqlite3.OperationalError:
            pass
        hof = Counter()
        for mid in self.hof_ids():
            r = self.q("SELECT user_id, ts FROM messages WHERE message_id = ?", (mid,))
            if r and self.lo <= r[0][1] < self.hi:
                hof[r[0][0]] += 1
        won = defaultdict(list)
        for a in self.out.get("awards", []):
            for u in (a.get("user"), a.get("a"), a.get("b")):
                if u:
                    won[int(u)].append(a)
        outs, ins = defaultdict(Counter), defaultdict(Counter)
        for (a, b), n in self.pairs.items():
            outs[a][b] += n
            ins[b][a] += n
        top_swear = {}
        for (w, u), n in self.swear_by.items():
            if n > top_swear.get(u, ("", 0))[1]:
                top_swear[u] = (w, n)

        def streak(days):
            ds = sorted(date.fromisoformat(d) for d in days)
            best = run = 1
            for a, b in zip(ds, ds[1:]):
                run = run + 1 if (b - a).days == 1 else 1
                best = max(best, run)
            return best if ds else 0

        # Who you are: the habit you're most unusual at, against everyone with VIDEO_MIN messages.
        personas = [("night", "THE NIGHT OWL", "{p}% of your messages came between 1am and 5am"),
                    ("early", "THE EARLY BIRD", "{p}% of your messages came before 8am"),
                    ("weekend", "THE WEEKENDER", "{p}% of your messages came at the weekend"),
                    ("questions", "THE CURIOUS ONE", "{p}% of your messages were questions"),
                    ("caps", "THE SHOUTER", "{p}% of your messages were IN CAPS"),
                    ("laugh_only", "THE LAUGH TRACK", "{p}% of your messages were just a laugh"),
                    ("dry", "THE DRY TEXTER", "{p}% of your messages were just \"ok\" or \"yeah\""),
                    ("swear_msgs", "THE POTTY MOUTH", "{p}% of your messages had a swear in them"),
                    ("files", "THE PHOTOGRAPHER", "{p}% of your messages had a picture or a file"),
                    ("gifs", "THE GIF MACHINE", "{p}% of your messages were GIFs"),
                    ("stickers", "THE STICKER FIEND", "{p}% of your messages were stickers"),
                    ("replies", "THE REPLY GUY", "{p}% of your messages were replies"),
                    ("poo", "THE POO ENTHUSIAST", "you brought up poo {n} times")]
        base = [u for u in per if per[u]["msgs"] >= VIDEO_MIN]
        rates = {k: sorted(per[u][k] / per[u]["msgs"] for u in base) for k, _, _ in personas}

        # Who you are, most impressive first: an award you won in the main video; then a top-20 place in the
        # whole server for something; then the habit you're most unusual at.
        favourite = ["shipwright", "architect", "hardest", "lowkey", "ticket_hero", "night_shift", "comedian", "heartthrob",
                     "celeb", "hall_of_famer", "crowd", "meme", "hype", "glue", "starter", "night_owl", "early_bird",
                     "committed", "one_day", "rookie", "glowup"]
        order = {k: i for i, k in enumerate(favourite)}
        boasts = [("laughs", "THE COMEDIAN", "laughs on your messages"), ("hearts", "THE HEARTTHROB", "hearts on your messages"),
                  ("reacts", "THE CROWD PLEASER", "reactions on your messages"),
                  ("hof", "THE HALL OF FAMER", "messages in the Hall of Fame"), ("streak", "THE EVER-PRESENT", "days in a row"),
                  ("night", "THE NIGHT OWL", "messages between 1am and 5am"), ("early", "THE EARLY BIRD", "messages before 8am"),
                  ("stickers", "THE STICKER FIEND", "stickers"), ("gifs", "THE GIF MACHINE", "GIFs"),
                  ("voice", "THE VOICE", "voice notes"), ("files", "THE PHOTOGRAPHER", "pictures and files"),
                  ("swears", "THE POTTY MOUTH", "swears"), ("poo", "THE POO ENTHUSIAST", "mentions of poo"),
                  ("questions", "THE CURIOUS ONE", "questions"), ("caps", "THE SHOUTER", "messages in ALL CAPS"),
                  ("replies", "THE REPLY GUY", "replies")]
        streaks = {u: streak(self.day_counts.get(u, {})) for u in per if per[u]["msgs"] >= PERSONAL_MIN}
        value = lambda u, k: {"laughs": laughs[u], "hearts": hearts[u], "reacts": got[u], "hof": hof[u],
                              "streak": streaks.get(u, 0)}.get(k, per[u][k])
        board = {k: sorted((value(u, k) for u in streaks), reverse=True) for k, _, _ in boasts}

        # Your circle: eleven ways someone can matter to you; each person gets the five that stand out most for
        # them (their strongest against what's typical for that kind across everyone with a video).
        pings = Counter()
        for a, b, n in self.q("SELECT user_id, target_id, COUNT(*) FROM mentions WHERE ts >= ? AND ts < ? AND kind = 'user' "
                              "GROUP BY 1, 2", (self.lo, self.hi)):
            if a != b and self.shown(a) and self.shown(b):
                pings[(a, b)] += n
        shuts = Counter()
        top = self.q("SELECT emoji_id, COUNT(*) n FROM reactions WHERE lower(emoji) = 'shut' AND emoji_id IS NOT NULL "
                     "GROUP BY 1 ORDER BY n DESC LIMIT 1")
        if top:
            for a, b, n in self.q("SELECT r.user_id, r.author_id, COUNT(*) FROM reactions r JOIN messages m ON m.message_id = r.message_id "
                                  "WHERE m.ts >= ? AND m.ts < ? AND r.action = 'add' AND r.emoji_id = ? GROUP BY 1, 2",
                                  (self.lo, self.hi, top[0][0])):
                if a != b and self.human(a):
                    shuts[(a, b)] += n
        index = defaultdict(lambda: defaultdict(Counter))  # index[u][angle][other] = n
        for angle, pairs, towards in (("fan", fans, True), ("react", fans, False), ("pinged_by", pings, True),
                                      ("pings", pings, False), ("hearts", heart_pairs, True), ("laughs", laugh_pairs, True),
                                      ("shut", shuts, True), ("you_reply", self.pairs, False), ("reply_you", self.pairs, True)):
            for (a, b), n in pairs.items():
                if towards:
                    index[b][angle][a] += n
                else:
                    index[a][angle][b] += n
        for (a, b), n in self.night_pairs.items():
            index[a]["night"][b] += n
            index[b]["night"][a] += n
        for (a, b), n in self.jabs.items():
            index[a]["rival"][b] += n
            index[b]["rival"][a] += n
        angles = ["fan", "react", "pinged_by", "pings", "night", "rival", "hearts", "laughs", "shut", "you_reply", "reply_you"]
        video = [u for u in per if per[u]["msgs"] >= VIDEO_MIN]
        typical = {a: max(statistics.median([max(index[u][a].values(), default=0) for u in video] or [1]), 1) for a in angles}

        def circle(u):
            # each kind's top person (people can come up more than once), the five that stand out most
            options = []
            for a in angles:
                other, n = next(((o, n) for o, n in index[u][a].most_common(4) if self.shown(o)), (None, 0))
                if other and n >= 3:
                    options.append((n / typical[a], a, other, n))
            return [{"k": a, **ref(other, n)} for _, a, other, n in sorted(options, reverse=True)[:5]]

        # Your biggest hits: your three most-reacted messages with words in them, outside announcement channels (no links, not too long), as the reactions
        # stand, from people other than you.
        msg_reacts, msg_total, msg_author = defaultdict(Counter), Counter(), {}
        for mid, reactor, author, e, eid, n in self.q(
                "SELECT r.message_id, r.user_id, r.author_id, r.emoji, r.emoji_id, SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) "
                "FROM reactions r JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? GROUP BY 1, 2, 4, 5",
                (self.lo, self.hi)):
            if n > 0 and reactor != author and self.human(reactor):
                msg_reacts[mid][(e, eid)] += n
                msg_total[mid] += n
                msg_author[mid] = author
        by_author = defaultdict(list)
        for mid, n in msg_total.items():
            by_author[msg_author[mid]].append((n, mid))

        def hits(u):
            out = []
            for n, mid in sorted(by_author.get(u, []), reverse=True)[:20]:
                row = self.q("SELECT content, local_day, COALESCE(parent_id, channel_id) FROM messages WHERE message_id = ?", (mid,))
                if (row and row[0][0] and row[0][0].strip() and not URL.search(row[0][0]) and len(row[0][0]) <= 160
                        and not self.announcing(row[0][2])):
                    out.append({"text": row[0][0], "day": row[0][1], "n": n,
                                "reactions": [{"e": e, "id": str(i) if i else None, "n": k} for (e, i), k in msg_reacts[mid].most_common(4)]})
                    if len(out) == 3:
                        break
            return out

        # Highlights: the stats each person stands out for most, against everyone with a card, so the vibe
        # scene and the card show what's actually notable about them (poo only for the people who bring it up).
        spots = ["laughs", "hearts", "reacts", "given", "swears", "poo", "gifs", "stickers", "voice", "files", "questions",
                 "caps", "hof", "badges", "casino"]

        def spot(u, k):
            if k == "given":
                return gave[u]
            if k == "badges":
                return sum(medals[u].values())
            if k == "casino":
                return (casino.get(u) or {}).get("games", 0)
            return value(u, k)
        spot_board = {k: sorted(spot(u, k) for u in streaks) for k in spots}

        def highlights(u):
            out = []
            for k in spots:
                n = spot(u, k)
                if n >= 3:
                    out.append((bisect.bisect_left(spot_board[k], n) / len(spot_board[k]), n, k))
            return [{"k": k, "n": n, "pct": round(p * 100)} for p, n, k in sorted(out, reverse=True)[:6]]

        def persona(u, c):
            mine = sorted(won.get(u, []), key=lambda a: order.get(a["key"], len(order)))
            if mine:
                a = mine[0]
                line = re.sub(r"\btheir\b", "your", re.sub(r"\bthey\b", "you", f"{a['value']} {a['line']}"))
                return {"title": a["title"].upper(), "line": line}
            best = None
            for k, title, label in boasts:
                n = value(u, k)
                if n < 10:
                    continue
                place = bisect.bisect_left([-x for x in board[k]], -n) + 1
                if place <= 20 and (not best or place < best[0]):
                    best = (place, title, label, n)
            if best:
                place, title, label, n = best
                return {"title": title, "line": (f"No one had more: {fmt_n(n)} {label}" if place == 1
                                                 else f"#{place} in the server: {fmt_n(n)} {label}")}
            return habit(c)

        def habit(c):
            best = None
            for k, title, line in personas:
                if c[k] < 5:
                    continue
                rate = c[k] / c["msgs"]
                beat = bisect.bisect_left(rates[k], rate) / max(len(rates[k]), 1)
                if beat >= 0.6 and (not best or (beat, rate) > best[:2]):
                    best = (beat, rate, k, title, line)
            if not best:
                return {"title": "THE ALL-ROUNDER", "line": "a bit of everything, all year"}
            beat, rate, k, title, line = best
            return {"title": title, "line": line.format(p=max(1, round(rate * 100)), n=c[k]),
                    "beat": min(99, int(beat * 100))}

        def most(counter, n_min=1):
            return max(counter.items(), key=lambda kv: kv[1], default=(None, 0))

        people = {}
        for u in ranked:
            c = per[u]
            if c["msgs"] < PERSONAL_MIN:
                break
            p = known.get(str(u), {})
            days = self.day_counts.get(u, {})
            busy = max(days.items(), key=lambda kv: kv[1]) if days else None
            hrs = self.hours.get(u, [0] * 24)
            mate = max(((min(n, ins[u].get(x, 0)), x) for x, n in outs[u].items() if self.shown(x)), default=(0, None))
            you_reply, reply_you = most(outs[u]), most(ins[u])
            fan = max(((n, a) for (a, b), n in fans.items() if b == u and self.shown(a)), default=(0, None))
            top_e = emoji[u].most_common(1)
            sw = top_swear.get(u)
            people[str(u)] = {
                "name": p.get("name", "you"), "avatar": p.get("avatar"), "video": c["msgs"] >= VIDEO_MIN,
                "msgs": c["msgs"], "rank": rank[u], "of": len(per), "prev": prev.get(u, 0), "words": c["words"],
                "days": len(days), "streak": streak(days), "busiest": busy and {"day": busy[0], "n": busy[1]},
                "hours": hrs, "peak": max(range(24), key=lambda h: hrs[h]),
                "channels": [{"name": (self.channel_name(ch) or "a channel").split("｜")[-1], "n": n}
                             for ch, n in self.channels_seen.get(u, Counter()).most_common(3)],
                "mate": ref(mate[1], mate[0]) if mate[1] and mate[0] >= 5 else None,
                "you_reply": ref(*you_reply) if you_reply[0] else None,
                "reply_you": ref(*reply_you) if reply_you[0] else None,
                "fan": ref(fan[1], fan[0]) if fan[1] else None,
                "emoji": {"e": top_e[0][0][0], "id": str(top_e[0][0][1]) if top_e[0][0][1] else None, "n": top_e[0][1]} if top_e else None,
                "reacts": got[u], "laughs": laughs[u], "hearts": hearts[u],
                "swears": c["swears"], "top_swear": sw and {"w": re.sub(r"[aeiou]", "*", sw[0], count=1), "n": sw[1]},
                "poo": c["poo"], "gifs": c["gifs"], "stickers": c["stickers"], "voice": c["voice"],
                "awards": [a["title"] for a in won.get(u, [])], "medals": {k.lower(): medals[u][k] for k in ("Gold", "Silver", "Bronze", "Secret")},
                "hof": hof[u], "casino": casino.get(u), "persona": persona(u, c), "highlights": highlights(u),
                "hits": hits(u) if c["msgs"] >= VIDEO_MIN else [],
                "circle": circle(u),
                "first": self.first_text.get(u) and {"text": self.first_text[u][0], "day": self.first_text[u][1]},
            }
        last_day = self.until - timedelta(days=1)
        return {"meta": {"since": self.since.isoformat(), "until": last_day.isoformat(), "year": self.since.year,
                         "full_year": self.until >= date(self.since.year + 1, 1, 1)},
                "people": people}

    def long_day(self, iso):
        d = date.fromisoformat(iso)
        return f"{d.day} {d.strftime('%B')}"

    def conversations(self):
        """One pass in channel order: who restarts quiet channels, who has the last word before they go quiet,
        which two people post straight after each other the most, and the busiest ten minutes anywhere."""
        starters, killers, self.tag_team = Counter(), Counter(), Counter()
        best_crowd = (0, None, None)
        rows = self.ch.execute(
            "SELECT COALESCE(parent_id, channel_id), user_id, ts, local_hour FROM messages WHERE ts >= ? AND ts < ? "
            "AND is_bot = 0 ORDER BY 1, ts", (self.lo, self.hi))
        chan, seq = None, []

        def flush(chan, seq):
            nonlocal best_crowd
            window, inside = Counter(), 0
            for i, (u, ts, hour) in enumerate(seq):
                gap_before = ts - seq[i - 1][1] if i else 10 ** 9
                if gap_before >= 45 * 60:
                    later = [s for s in seq[i + 1:i + 40] if s[1] - ts <= 15 * 60 and s[0] != u]
                    if len(later) >= 6 and len({s[0] for s in later}) >= 2:
                        starters[u] += 1
                if i + 1 < len(seq) and seq[i + 1][1] - ts >= 2 * 3600 and hour is not None and 11 <= hour < 21:
                    killers[u] += 1
                if i and seq[i - 1][0] != u and gap_before <= 60:
                    a, b = seq[i - 1][0], u
                    self.tag_team[(min(a, b), max(a, b))] += 1
                # ten-minute sliding window of different people talking
                window[u] += 1
                while seq[inside][1] < ts - 600:
                    window[seq[inside][0]] -= 1
                    if not window[seq[inside][0]]:
                        del window[seq[inside][0]]
                    inside += 1
                if len(window) > best_crowd[0]:
                    best_crowd = (len(window), chan, ts)
        for c, u, ts, hour in rows:
            if c != chan:
                flush(chan, seq)
                chan, seq = c, []
            seq.append((u, ts, hour))
        flush(chan, seq)
        self.tag_team = Counter({p: n for p, n in self.tag_team.items() if self.shown(p[0]) and self.shown(p[1])})
        self.busiest = best_crowd
        return (Counter({u: n for u, n in starters.items() if self.shown(u)}),
                Counter({u: n for u, n in killers.items() if self.shown(u)}))

    # ------------------------------------------------------------------ people
    def people_chapter(self):
        pairs = self.pairs
        got = Counter()
        from_whom = defaultdict(set)
        for (a, b), n in pairs.items():
            got[b] += n
            from_whom[b].add(a)
        replied = [{"user": self.who(u), "n": n, "from": len(from_whom[u])} for u, n in got.most_common(5)]
        both = Counter()
        for (a, b), n in pairs.items():
            both[(min(a, b), max(a, b))] += n
        self.both = both
        duos = [{"a": self.who(a), "b": self.who(b), "n": n, "ab": pairs[(a, b)], "ba": pairs[(b, a)]}
                for (a, b), n in both.most_common(5)]
        one_way = sorted(((n / (pairs.get((b, a), 0) + 1), a, b, n) for (a, b), n in pairs.items() if n >= 200),
                         reverse=True)[:3]

        fans = Counter()
        emoji_dir = defaultdict(Counter)
        for reactor, author, emoji, eid, n in self.q(
                "SELECT r.user_id, r.author_id, r.emoji, r.emoji_id, COUNT(*) FROM reactions r JOIN messages m "
                "ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? AND r.action = 'add' GROUP BY 1, 2, 3, 4",
                (self.lo, self.hi)):
            if reactor == author or not (self.shown(reactor) and self.shown(author)):
                continue
            fans[(reactor, author)] += n
            emoji_dir[(emoji, eid)][(reactor, author)] += n
        mates = sorted(((min(n, fans.get((b, a), 0)), a, b) for (a, b), n in fans.items() if a < b), reverse=True)[:3]

        # Reaction duos: for each emoji, the two people who traded it most, counting only what went both
        # ways (the smaller direction), so one person spamming another doesn't count as a duo.
        rduos = []
        for (emoji, eid), d in emoji_dir.items():
            pick = max(((min(n, d.get((b, a), 0)), a, b) for (a, b), n in d.items() if a < b), default=None)
            if pick and pick[0] >= 5:
                k, a, b = pick
                rduos.append((k, emoji, eid, a, b, d[(a, b)], d.get((b, a), 0)))
        rduos.sort(reverse=True)

        self.out["social"] = {
            "replied": replied,
            "duos": duos,
            "one_way": [{"from": self.who(a), "to": self.who(b), "n": n, "back": pairs.get((b, a), 0)}
                        for _, a, b, n in one_way],
            "fans": [{"from": self.who(a), "to": self.who(b), "n": n} for (a, b), n in fans.most_common(3)],
            "mates": [{"a": self.who(a), "b": self.who(b), "ab": fans[(a, b)], "ba": fans.get((b, a), 0)}
                      for _, a, b in mates],
            "reaction_duos": [{"e": e, "id": str(i) if i else None, "a": self.who(a), "b": self.who(b), "ab": ab, "ba": ba}
                              for _, e, i, a, b, ab, ba in rduos[:18]],
        }
        self.out["duo_awards"] = self.duo_awards()
        self.out["trio"] = self.trio()
        self.out["squads"] = self.squads()
        self.out["crowds"] = self.crowds()
        self.out["shut"] = self.shut()
        self.out["network"] = self.network()

    def duo_awards(self):
        out = []

        def duo(key, title, line, scores, fmt=lambda v: f"{v:,}", minimum=1):
            ranked = sorted(((v, p) for p, v in scores.items() if v >= minimum), reverse=True)
            if not ranked:
                return
            v, (a, b) = ranked[0]
            out.append({"key": key, "title": title, "line": line if isinstance(line, str) else line(a, b),
                        "a": self.who(a), "b": self.who(b), "value": fmt(v)})
        arg = {p: v[0] for p, v in self.argument.items()}
        duo("argument", "The Longest Argument",
            lambda a, b: f"replies straight back and forth, on {self.long_day(self.argument[(a, b)][1])}", arg)
        duo("tag_team", "Tag Team", "times one posted straight after the other", self.tag_team)
        duo("night_shift", "Night Shift Partners", "replies to each other between 1am and 5am", self.night_pairs)
        regs = [u for u, h in self.hours.items() if sum(h) >= 5000]

        def corr(a, b):
            x, y = self.hours[a], self.hours[b]
            mx, my = sum(x) / 24, sum(y) / 24
            num = sum((i - mx) * (j - my) for i, j in zip(x, y))
            den = math.sqrt(sum((i - mx) ** 2 for i in x) * sum((j - my) ** 2 for j in y)) or 1
            return num / den
        same = {(min(a, b), max(a, b)): corr(a, b) for i, a in enumerate(regs) for b in regs[i + 1:]}
        duo("same_schedule", "Same Schedule", "alike, hour by hour, across the whole year", same,
            fmt=lambda v: f"{v * 100:.0f}%", minimum=0)
        duo("ping_pong", "Ping Pong", "@mentions of each other", self.mention_pairs)
        pay = Counter()
        for a, b, n in self.q("SELECT payer_id, recipient_id, SUM(amount) FROM pay_transfers WHERE timestamp >= ? "
                              "AND timestamp < ? GROUP BY 1, 2", (self.lo, self.hi), db=self.db):
            a, b = int(a), int(b)
            if self.shown(a) and self.shown(b):
                pay[(min(a, b), max(a, b))] += n
        duo("business", "Business Partners", "UKPence passed back and forth with /pay", pay)
        pvp = Counter()
        for w, l in self.q("SELECT winner_id, loser_id FROM pvp_results WHERE timestamp >= ? AND timestamp < ?",
                           (self.lo, self.hi), db=self.db):
            if w and l and self.shown(int(w)) and self.shown(int(l)):
                pvp[(min(int(w), int(l)), max(int(w), int(l)))] += 1
        duo("rivals", "Arch-Rivals", "head-to-head games against each other", pvp, minimum=10)
        return out

    def trio(self):
        """The strongest triangle: three people who all reply to each other, scored by the weakest of the three."""
        weight = Counter()
        for (a, b), n in self.both.items():
            weight[a] += n
            weight[b] += n
        top = [u for u, _ in weight.most_common(80)]
        best = None
        for i, a in enumerate(top):
            for j in range(i + 1, len(top)):
                b = top[j]
                ab = self.both.get((min(a, b), max(a, b)), 0)
                if ab < 300 or (best and ab <= best[0]):
                    continue
                for c in top[j + 1:]:
                    w = min(ab, self.both.get((min(a, c), max(a, c)), 0), self.both.get((min(b, c), max(b, c)), 0))
                    if not best or w > best[0]:
                        best = (w, a, b, c)
        if not best:
            return None
        w, a, b, c = best
        k = lambda x, y: self.both.get((min(x, y), max(x, y)), 0)
        return {"people": [self.who(a), self.who(b), self.who(c)], "min": w,
                "pairs": [k(a, b), k(b, c), k(a, c)], "total": k(a, b) + k(b, c) + k(a, c)}

    def squads(self):
        """Friend groups: communities in the reply graph of the 150 busiest repliers, found by modularity, each
        named after the channel its members use far more than everyone else does (or the night, if that's
        what sets them apart)."""
        weight = Counter()
        for (a, b), n in self.both.items():
            weight[a] += n
            weight[b] += n
        nodes = [u for u, _ in weight.most_common(150)]
        idx = set(nodes)
        adj = defaultdict(Counter)
        for (a, b), n in self.both.items():
            if a in idx and b in idx and n >= 20:
                adj[a][b] += n
                adj[b][a] += n
        m2 = sum(sum(v.values()) for v in adj.values()) or 1
        deg = {u: sum(adj[u].values()) for u in nodes}
        comm = {u: u for u in nodes}
        tot = Counter({u: deg[u] for u in nodes})
        for _ in range(20):
            moved = False
            for u in sorted(nodes, key=lambda x: -deg[x]):
                cu = comm[u]
                links = Counter()
                for v, w in adj[u].items():
                    links[comm[v]] += w
                tot[cu] -= deg[u]
                best_c, best_gain = cu, links[cu] - tot[cu] * deg[u] / m2
                for c, w in links.items():
                    gain = w - tot[c] * deg[u] / m2
                    if gain > best_gain + 1e-9:
                        best_c, best_gain = c, gain
                tot[best_c] += deg[u]
                if best_c != cu:
                    comm[u] = best_c
                    moved = True
            if not moved:
                break
        groups = defaultdict(list)
        for u in nodes:
            groups[comm[u]].append(u)
        server_chan = Counter()
        for u, chans in self.channels_seen.items():
            server_chan.update(chans)
        server_total = sum(server_chan.values()) or 1
        night_all = sum(c["night"] for c in self.per.values()) / max(sum(c["msgs"] for c in self.per.values()), 1)
        out = []
        for members in sorted(groups.values(), key=lambda g: -sum(weight[u] for u in g)):
            if len(members) < 4:
                continue
            chans = Counter()
            for u in members:
                chans.update(self.channels_seen.get(u, {}))
            total = sum(chans.values()) or 1
            lift = sorted(((chans[c] / total) / (server_chan[c] / server_total), c) for c in chans
                          if chans[c] >= 300 and chans[c] / total >= 0.05)
            night = sum(self.per[u]["night"] for u in members if u in self.per) / max(sum(self.per[u]["msgs"] for u in members if u in self.per), 1)
            if night > night_all * 1.8:
                label, why = "The Night Crew", "busiest after midnight"
            elif lift and lift[-1][0] >= 1.5:
                ch = (self.channel_name(lift[-1][1]) or "a channel").split("｜")[-1]
                label, why = f"The #{ch} lot", f"live in #{ch}"
            else:
                label, why = "The #general lot", "keep #general going"
            faces = sorted(members, key=lambda u: -weight[u])
            out.append({"name": label, "why": why, "size": len(members), "people": [self.who(u) for u in faces[:6]]})
            if len(out) == 4:
                break
        return out

    def crowds(self):
        """The message the most different people replied to, and the busiest ten minutes anywhere."""
        best = None
        for mid, users in sorted(self.replied_by.items(), key=lambda kv: -len(kv[1]))[:40]:
            row = self.q("SELECT user_id, content, local_day, COALESCE(parent_id, channel_id) FROM messages WHERE message_id = ?", (mid,))
            if not row or not self.shown(row[0][0]) or not row[0][1]:
                continue
            uid, content, day, chan = row[0]
            if len(content) > 220 or URL.search(content):
                continue
            best = {"user": self.who(uid), "text": self.render_text(content), "day": day, "people": len(users),
                    "channel": (self.channel_name(chan) or "").split("｜")[-1]}
            break
        n, chan, ts = self.busiest
        busiest = None
        if n:
            when = datetime.fromtimestamp(ts - 600, UK)  # the ten minutes ending at ts
            words = Counter()
            for (content,) in self.q("SELECT content FROM messages WHERE COALESCE(parent_id, channel_id) = ? AND ts BETWEEN ? AND ? "
                                     "AND is_bot = 0", (chan, ts - 600, ts)):
                clean = TOKEN_NOISE.sub(" ", URL.sub(" ", (content or "").lower()))
                words.update(w for w in WORD.findall(clean) if w not in STOP and not SLURS.match(w))
            busiest = {"people": n, "channel": (self.channel_name(chan) or "").split("｜")[-1],
                       "day": when.date().isoformat(), "time": when.strftime("%H:%M"),
                       "caption": self.caption_for(when.date().isoformat()),
                       "words": [w for w, _ in words.most_common(4)]}
        return {"pile_on": best, "busiest": busiest}

    def shut(self):
        """The :Shut: reaction times its author out. Who got shut, who did the shutting, and the two who shut
        each other most, from this year's reactions; plus each person's all-time timeout count and the
        shutcoins bought."""
        top = self.q("SELECT emoji_id, COUNT(*) n FROM reactions WHERE lower(emoji) = 'shut' AND emoji_id IS NOT NULL "
                     "GROUP BY 1 ORDER BY n DESC LIMIT 1")
        if not top:
            return None
        eid = top[0][0]
        got, gave, pair = Counter(), Counter(), Counter()
        total = 0
        for reactor, author, n in self.q(
                "SELECT r.user_id, r.author_id, COUNT(*) FROM reactions r JOIN messages m ON m.message_id = r.message_id "
                "WHERE m.ts >= ? AND m.ts < ? AND r.action = 'add' AND r.emoji_id = ? GROUP BY 1, 2", (self.lo, self.hi, eid)):
            if not self.human(reactor) or reactor == author:
                continue
            total += n
            if self.shown(author):
                got[author] += n
            if self.shown(reactor):
                gave[reactor] += n
            if self.shown(author) and self.shown(reactor):
                pair[(reactor, author)] += n
        mutual = sorted(((min(n, pair.get((b, a), 0)), a, b) for (a, b), n in pair.items() if a < b), reverse=True)
        timeouts = {int(u): n for u, n in self.q("SELECT user_id, count FROM shut_counts", db=self.db)}
        coins = self.q("SELECT COALESCE(SUM(quantity), 0) FROM shop_purchases WHERE item_id = 'shutcoin' AND purchase_time >= ? "
                       "AND purchase_time < ?", (self.lo, self.hi), db=self.db)[0][0]
        rivals = None
        if mutual and mutual[0][0] >= 3:
            _, a, b = mutual[0]
            rivals = {"a": self.who(a), "b": self.who(b), "ab": pair[(a, b)], "ba": pair.get((b, a), 0)}
        return {
            "emoji_id": str(eid), "total": total, "coins": coins,
            "most_shut": [{"user": self.who(u), "n": n, "timeouts": timeouts.get(u, 0)} for u, n in got.most_common(3)],
            "shutters": [{"user": self.who(u), "n": n} for u, n in gave.most_common(3)],
            "rivals": rivals,
        }

    def caption_for(self, d):
        if d in MOMENTS:
            return MOMENTS[d]
        if self.bb_start and self.bb_start <= d <= (self.bb_end or d):
            return f"Big Brother, day {(date.fromisoformat(d) - date.fromisoformat(self.bb_start)).days + 1}"
        return None

    def network(self, size=16):
        """The ring of the busiest repliers and the replies between them; the player lays it out."""
        weight = Counter()
        for (a, b), n in self.both.items():
            weight[a] += n
            weight[b] += n
        nodes = [u for u, _ in weight.most_common(size)]
        idx = {u: i for i, u in enumerate(nodes)}
        edges = [(idx[a], idx[b], n) for (a, b), n in self.both.most_common() if a in idx and b in idx and n >= 60][:40]
        return {"nodes": [{"user": self.who(u), "w": weight[u]} for u in nodes], "edges": edges}

    def scan_last_year_words(self):
        prev = Counter()
        laughs = Counter()
        self.prev_swears = self.prev_poo = 0
        rows = self.ch.execute("SELECT content, ts FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0",
                               (self.yo_lo, self.yo_hi))
        n_window = 0
        for content, ts in rows:
            low = (content or "").lower()
            clean = TOKEN_NOISE.sub(" ", URL.sub(" ", low))
            prev.update(w.strip("'") for w in WORD.findall(clean))
            if self.plo <= ts < self.phi:
                n_window += 1
                for name, pat in LAUGHS.items():
                    if re.search(pat, low):
                        laughs[name] += 1
                self.prev_swears += len(SWEAR.findall(low))
                self.prev_poo += bool(POO.search(low))
        self.prev_words, self.prev_laughs, self.prev_n = prev, laughs, n_window

    # ------------------------------------------------------------------ sections
    def race(self):
        """Cumulative messages this year against last year, and the day this year passed last year's total."""
        def per_day(lo, hi):
            return self.q("SELECT local_day, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                          "GROUP BY local_day ORDER BY local_day", (lo, hi))
        this = per_day(self.lo, self.hi)
        last = per_day(self.yo_lo, self.yo_hi)
        last_total = sum(n for _, n in last)
        run, passed = 0, None
        for d, n in this:
            run += n
            if passed is None and run >= last_total:
                passed = d
        self.out["race"] = {"this": [n for _, n in this], "last": [n for _, n in last],
                            "this_start": this[0][0] if this else None, "last_total": last_total,
                            "passed_on": passed, "this_total": run}

    def calendar(self):
        days = self.q("SELECT local_day, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                      "GROUP BY local_day ORDER BY local_day", (self.lo, self.hi))
        top = sorted(days, key=lambda r: -r[1])[:6]
        captions = []
        year_df = Counter()
        for d in self.day_words.values():
            year_df.update(d)
        n_days = max(len(self.day_words), 1)
        for d, n in top:
            chan = self.q("SELECT COALESCE(parent_id, channel_id) c, COUNT(*) k FROM messages WHERE local_day = ? "
                          "AND is_bot = 0 GROUP BY c ORDER BY k DESC LIMIT 1", (d,))
            words = self.day_words.get(d, Counter())
            spikes = sorted(((cnt / (year_df[w] / n_days + 1), w) for w, cnt in words.items() if cnt >= 25),
                            reverse=True)[:5]
            caption = MOMENTS.get(d)
            if not caption and self.bb_start and self.bb_start <= d <= (self.bb_end or d):
                caption = f"Big Brother, day {(date.fromisoformat(d) - date.fromisoformat(self.bb_start)).days + 1}"
            captions.append({"day": d, "n": n, "caption": caption,
                             "channel": self.channel_name(chan[0][0]) if chan else None,
                             "words": [w for _, w in spikes]})
        months = self.q("SELECT substr(local_day, 1, 7) m, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? "
                        "AND is_bot = 0 GROUP BY m ORDER BY m", (self.lo, self.hi))
        prev_months = self.q("SELECT substr(local_day, 1, 7) m, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? "
                             "AND is_bot = 0 GROUP BY m ORDER BY m", (self.yo_lo, self.yo_hi))
        hours = dict(self.q("SELECT local_hour, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                            "GROUP BY local_hour", (self.lo, self.hi)))
        weekdays = Counter()
        for d, n in days:
            weekdays[date.fromisoformat(d).weekday()] += n
        self.out["calendar"] = {
            "days": [[d, n] for d, n in days], "top": captions,
            "months": [[m, n] for m, n in months], "prev_months": [[m, n] for m, n in prev_months],
            "hours": [hours.get(h, 0) for h in range(24)],
            "weekdays": [weekdays.get(i, 0) for i in range(7)],
        }

    def channels(self):
        def ranked(lo, hi):
            return self.q("SELECT COALESCE(parent_id, channel_id) c, COUNT(*) n FROM messages WHERE ts >= ? AND ts < ? "
                          "AND is_bot = 0 GROUP BY c ORDER BY n DESC", (lo, hi))
        now, prev = ranked(self.lo, self.hi), ranked(self.plo, self.phi)
        prev_rank = {c: i + 1 for i, (c, _) in enumerate(prev)}
        prev_n = dict(prev)
        rows = []
        for i, (c, n) in enumerate(now[:10]):
            rows.append({"name": self.channel_name(c) or "deleted-channel", "n": n, "rank": i + 1,
                         "prev_rank": prev_rank.get(c), "prev_n": prev_n.get(c, 0)})
        # Risers: grew the most against the same stretch last year, among channels that matter now.
        risers = sorted(((n / (prev_n.get(c, 0) + 50), c, n) for c, n in now[:40] if n >= 3000 and prev_n.get(c, 0)),
                        reverse=True)[:3]
        born = [(c, n) for c, n in now[:40] if n >= 3000 and not prev_n.get(c)]
        self.out["channels"] = {"top": rows, "count": len(now),
                                "risers": [{"name": self.channel_name(c) or "deleted-channel", "n": n,
                                            "prev": prev_n.get(c, 0)} for _, c, n in risers],
                                "born": [{"name": self.channel_name(c) or "deleted-channel", "n": n} for c, n in born]}

    # ------------------------------------------------------------------ awards
    # ------------------------------------------------------------------ people
    # ------------------------------------------------------------------ hall of fame and words
    def render_text(self, text):
        """Mentions and channel links to names; custom emoji kept as tokens the player draws."""
        def user(m):
            uid = int(m.group(1))
            p = self.discord["people"].get(str(uid))
            return "@" + (p["name"] if p else "someone")
        text = re.sub(r"<@!?(\d+)>", user, text)
        text = re.sub(r"<#(\d+)>", lambda m: "#" + (self.channel_name(m.group(1)) or "channel"), text)
        text = re.sub(r"<@&\d+>", "@role", text)
        return text

    def hof_ids(self):
        try:
            return [int(x) for x in json.loads((self.data / "hall_of_fame.json").read_text())]
        except FileNotFoundError:
            return []

    def hof_message(self, mid):
        row = self.q("SELECT user_id, content, ts, local_day FROM messages WHERE message_id = ?", (mid,))
        if not row:
            return None
        uid, content, ts, day = row[0]
        if not (self.lo <= ts < self.hi) or not content or not self.shown(uid) or URL.search(content) or len(content) > 220:
            return None
        reacts = Counter()
        for e, eid, u, act in self.q("SELECT emoji, emoji_id, user_id, action FROM reactions WHERE message_id = ?", (mid,)):
            if self.human(u):
                reacts[(e, eid)] += 1 if act == "add" else -1
        laughs = sum(n for (e, _), n in reacts.items() if n > 0 and (e in LAUGH_REACTS or "kek" in e.lower() or "laugh" in e.lower()))
        return {"id": str(mid), "user": uid, "text": self.render_text(content), "day": day,
                "total": sum(n for n in reacts.values() if n > 0), "laughs": laughs,
                "reactions": [{"e": e, "id": str(i) if i else None, "n": n} for (e, i), n in reacts.most_common(5) if n > 0]}

    def hall_of_fame(self):
        """The Hall of Fame slides. With --hof-picks (a JSON list of message ids, in order) those are shown;
        without, the most-reacted short Hall of Fame messages. Either way hof_candidates.json is written to the
        data dir: the Hall of Fame plus the year's most-laughed-at short messages, best first, to pick from."""
        cands = {}
        for mid in self.hof_ids():
            m = self.hof_message(mid)
            if m:
                cands[mid] = {**m, "source": "hall of fame"}
        marks = ",".join("?" * len(LAUGH_REACTS))
        for mid, _ in self.q(f"SELECT r.message_id, COUNT(*) n FROM reactions r JOIN messages m ON m.message_id = r.message_id "
                             f"WHERE m.ts >= ? AND m.ts < ? AND r.action = 'add' AND r.emoji IN ({marks}) "
                             f"GROUP BY 1 ORDER BY n DESC LIMIT 400", (self.lo, self.hi, *LAUGH_REACTS)):
            if mid not in cands:
                m = self.hof_message(mid)
                if m:
                    cands[mid] = {**m, "source": "laughs"}
        ranked = sorted(cands.values(), key=lambda m: -(m["laughs"] * 2 + m["total"]))
        names = self.discord["people"]
        (self.data / "hof_candidates.json").write_text(json.dumps(
            [{**m, "name": names.get(str(m["user"]), {}).get("name")} for m in ranked], ensure_ascii=False, indent=1))
        # Picks are slides: [{"title": "...", "ids": [...]}, ...]. Without them, the top Hall of Fame posts.
        if self.hof_picks:
            slides = [{"title": g.get("title") or "HALL OF FAME",
                       "messages": [m for m in (cands.get(int(i)) or self.hof_message(int(i)) for i in g["ids"]) if m]}
                      for g in self.hof_picks]
        else:
            top = sorted((m for m in cands.values() if m["source"] == "hall of fame"), key=lambda m: -m["total"])[:6]
            top.sort(key=lambda m: m["day"])
            slides = [{"title": "YOUR FAVOURITES", "messages": top[:3]}, {"title": "AND THESE", "messages": top[3:6]}]
        for slide in slides:
            for m in slide["messages"]:
                m["user"] = self.who(m["user"])
        self.out["hall_of_fame"] = [s for s in slides if s["messages"]]
        # Interludes: single funny messages dropped between scenes ("after" is the scene id they follow).
        interludes = []
        for pick in self.interlude_picks:
            m = cands.get(int(pick["id"])) or self.hof_message(int(pick["id"]))
            if m:
                m["user"] = self.who(m["user"])
                interludes.append({"after": pick["after"], "kicker": pick.get("kicker"), "message": m})
        self.out["interludes"] = interludes

    def iceberg(self):
        """The /iceberg: every entry where the bot draws it on the picture, how many submissions staff turned
        down, and the stories behind some entries. A story comes from the picks file: the entry's text, a line
        or two of context (where "{n}" is filled from "count": how many of one person's messages this year
        match a regex, "bot" meaning HMS Victory), and optionally a quote, a message id with an optional
        shortened "text". Quotes are only kept from current members and the bot itself."""
        try:
            rows = self.q("SELECT id, text, level, x, y, color, rotation FROM iceberg WHERE x IS NOT NULL", db=self.db)
            subs = dict(self.q("SELECT status, COUNT(*) FROM pending_iceberg_submissions GROUP BY 1", db=self.db))
        except sqlite3.OperationalError:
            return
        if not rows:
            return
        entries = [{"id": i, "text": t, "level": l, "x": x, "y": y, "color": c, "rot": r or 0} for i, t, l, x, y, c, r in rows]
        by_text = {e["text"].strip().lower(): e for e in entries}
        stories = []
        for pick in self.iceberg_picks:
            e = by_text.get(pick["entry"].strip().lower())
            if not e:
                print(f"iceberg: no entry {pick['entry']!r}", file=sys.stderr)
                continue
            text = pick["story"]
            if "count" in pick:
                c = pick["count"]
                uid = HMS_VICTORY if c["user"] == "bot" else int(c["user"])
                lo = max(self.lo, day_bounds(date.fromisoformat(c["after"]), self.until)[0]) if c.get("after") else self.lo
                rx = re.compile(c["regex"], re.I)
                n = sum(1 for (m,) in self.q("SELECT content FROM messages WHERE user_id = ? AND ts >= ? AND ts < ? AND content IS NOT NULL",
                                             (uid, lo, self.hi)) if rx.search(m))
                text = text.replace("{n}", fmt_n(n))
            story = {"entry": e["id"], "story": text}
            q = pick.get("quote")
            row = self.q("SELECT user_id, content, local_day FROM messages WHERE message_id = ?", (int(q["id"]),)) if q else None
            if row and (row[0][0] == HMS_VICTORY or self.shown(row[0][0])):
                uid, content, day = row[0]
                story["quote"] = {"user": self.who(uid), "text": q.get("text") or self.render_text(content), "day": day}
            stories.append(story)
        place = {e["id"]: (e["level"], e["y"]) for e in entries}
        stories.sort(key=lambda s: place[s["entry"]])
        self.out["iceberg"] = {"entries": entries, "denied": subs.get("denied", 0), "stories": stories}

    def regular_names(self):
        names = set()
        for uid, c in self.per.items():
            if c["msgs"] >= 300:
                p = self.discord["people"].get(str(uid))
                if p:
                    names.update(w for w in WORD.findall(p["name"].lower()) if len(w) >= 3)
        return names

    def words(self):
        months = self.months_words
        names = self.regular_names()
        total = Counter()
        for c in months.values():
            total.update(c)
        n_all = sum(total.values()) or 1
        by_month = []
        for m in sorted(months):
            c = months[m]
            n_m = sum(c.values()) or 1
            scored = []
            for w, k in c.items():
                if k < 40 or w in names:
                    continue
                rest = total[w] - k
                lift = (k / n_m) / ((rest + 5) / (n_all - n_m))
                scored.append((lift * math.log(k), w, k))
            scored.sort(reverse=True)
            by_month.append({"month": m, "words": [{"w": w, "n": k} for _, w, k in scored[:4]]})
        born = []
        for w, k in total.most_common(4000):
            if k >= 150 and self.prev_words.get(w, 0) <= k * 0.03 and w not in STOP and w not in names:
                first_month = min(m for m in months if months[m][w])
                born.append({"w": w, "n": k, "prev": self.prev_words.get(w, 0), "first": first_month})
        n_now = self.out["numbers"]["now"]["messages"] or 1
        laugh = [{"k": k, "now": round(self.laughs[k] / n_now * 10000, 1),
                  "prev": round(self.prev_laughs[k] / max(self.prev_n, 1) * 10000, 1)} for k in LAUGHS]
        emo = Counter()
        for u, e, eid, n in self.q("SELECT user_id, emoji, emoji_id, SUM(n) FROM emoji_uses WHERE ts >= ? AND ts < ? "
                                   "GROUP BY 1, 2, 3", (self.lo, self.hi)):
            if self.human(u):
                emo[(e, eid)] += n
        emoji = [{"e": e, "id": str(i) if i else None, "n": n} for (e, i), n in emo.most_common(10)]
        rc = Counter()
        for u, e, eid, act, n in self.q("SELECT r.user_id, r.emoji, r.emoji_id, r.action, COUNT(*) FROM reactions r "
                                        "JOIN messages m ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? "
                                        "GROUP BY 1, 2, 3, 4", (self.lo, self.hi)):
            if self.human(u):
                rc[(e, eid)] += n if act == "add" else -n
        reacts = [{"e": e, "id": str(i) if i else None, "n": n} for (e, i), n in rc.most_common(10)]
        chars, uid, day, mid = self.longest
        content = self.q("SELECT content FROM messages WHERE message_id = ?", (mid,))[0][0] or ""
        per10k = lambda n, d: round(n / max(d, 1) * 10000, 1)
        censor = lambda w: re.sub(r"[aeiou]", "*", w, count=1)  # sh*t, f*ck
        shown_per = {u: c for u, c in self.per.items()}
        regulars = [u for u, c in shown_per.items() if c["msgs"] >= REGULAR]
        mouth = max(shown_per, key=lambda u: shown_per[u]["swears"])
        clean = min(regulars, key=lambda u: shown_per[u]["swear_msgs"] / shown_per[u]["msgs"]) if regulars else None
        def loudest(word):
            # whoever said this one the most
            k, u = max(((k, u) for (w, u), k in self.swear_by.items() if w == word and self.shown(u)), default=(0, None))
            return u and {"user": self.who(u), "n": k}
        n_sw = sum(self.swear_words.values())
        self.out["swears"] = {
            "n": n_sw, "now": per10k(n_sw, n_now), "prev": per10k(self.prev_swears, self.prev_n),
            "every": round(n_now / max(sum(c["swear_msgs"] for c in self.per.values()), 1)),
            "words": [{"w": censor(w), "n": n, "top": loudest(w)} for w, n in self.swear_words.most_common(6)],
            "mouth": {"user": self.who(mouth), "n": shown_per[mouth]["swears"]},
            "clean": clean and {"user": self.who(clean), "msgs": shown_per[clean]["msgs"],
                                "every": round(shown_per[clean]["msgs"] / max(shown_per[clean]["swear_msgs"], 1))},
        }
        poo_reacts = sum(n for u, n in self.q(
            "SELECT r.user_id, SUM(CASE WHEN r.action = 'add' THEN 1 ELSE -1 END) FROM reactions r JOIN messages m "
            "ON m.message_id = r.message_id WHERE m.ts >= ? AND m.ts < ? AND r.emoji = '💩' GROUP BY 1",
            (self.lo, self.hi)) if self.human(u) and n > 0)
        n_poo = sum(self.poo_month.values())
        chief = max(shown_per, key=lambda u: shown_per[u]["poo"])
        peak = max(self.poo_day.items(), key=lambda kv: kv[1]) if self.poo_day else None
        self.out["poo"] = {
            "n": n_poo, "reacts": poo_reacts, "now": per10k(n_poo, n_now), "prev": per10k(self.prev_poo, self.prev_n),
            "months": [{"m": m, "n": self.poo_month[m]} for m in sorted(self.months_words)],
            "peak": peak and {"day": peak[0], "n": peak[1]},
            "chief": {"user": self.who(chief), "n": shown_per[chief]["poo"]},
        }
        self.out["words"] = {
            "by_month": by_month, "born": born[:8], "laugh": laugh, "emoji": emoji, "reactions": reacts,
            "longest": {"user": self.who(uid), "chars": chars, "day": day, "words": len(content.split()),
                        "lines": [len(line) for line in content.split("\n")][:60]},
            "first": [{"user": self.who(f["user"]), "text": self.render_text(f["text"]),
                       "time": datetime.fromtimestamp(f["ts"], UK).strftime("%H:%M:%S")} for f in self.first_msgs[:3]],
        }

    # ------------------------------------------------------------------ casino and money
    def casino(self):
        lo, hi = self.lo, self.hi
        bots = {str(b) for b in self.bots}
        rows = self.q("SELECT user_id, game, staked, payout, net, timestamp FROM casino_results "
                      "WHERE timestamp >= ? AND timestamp < ?", (lo, hi), db=self.db)
        rows = [r for r in rows if r[0] not in bots]
        if not rows:
            self.out["casino"] = None
            return
        games = defaultdict(lambda: [0, 0, 0, set()])
        per = defaultdict(lambda: [0, 0, 0])
        days = Counter()
        hours = Counter()
        for u, g, staked, payout, net, ts in rows:
            gm = games[g]
            gm[0] += 1
            gm[1] += staked
            gm[2] -= net
            gm[3].add(u)
            if not self.shown(u):
                continue
            p = per[u]
            p[0] += 1
            p[1] += staked
            p[2] += net
            d = datetime.fromtimestamp(ts, UK)
            days[(u, d.date().isoformat())] += net
            hours[d.hour] += staked
        named = [r for r in rows if self.shown(r[0])]
        wins = sorted(named, key=lambda r: -r[4])[:3]
        losses = sorted(named, key=lambda r: r[4])[:3]
        game_rows = sorted(({"game": GAMES.get(g, g.title()), "bets": v[0], "staked": v[1], "house": v[2],
                             "players": len(v[3])} for g, v in games.items()), key=lambda r: -r["staked"])
        by_staked = sorted(per.items(), key=lambda kv: -kv[1][1])
        by_net = sorted(per.items(), key=lambda kv: kv[1][2])
        worst_day = min(days.items(), key=lambda kv: kv[1])
        best_day = max(days.items(), key=lambda kv: kv[1])

        def bet(r):
            return {"user": self.who(r[0]), "game": GAMES.get(r[1], r[1]), "staked": r[2], "payout": r[3],
                    "net": r[4], "day": datetime.fromtimestamp(r[5], UK).date().isoformat()}
        self.out["casino"] = {
            "since": datetime.fromtimestamp(min(r[5] for r in rows), UK).date().isoformat(),
            "bets": len(rows), "players": len({r[0] for r in rows}), "staked": sum(r[2] for r in rows),
            "house": -sum(r[4] for r in rows), "games": game_rows,
            "biggest_wins": [bet(r) for r in wins], "biggest_losses": [bet(r) for r in losses],
            "high_rollers": [{"user": self.who(u), "staked": v[1], "bets": v[0], "net": v[2]} for u, v in by_staked[:3]],
            "losers": [{"user": self.who(u), "net": v[2], "bets": v[0], "staked": v[1]} for u, v in by_net[:3]],
            "winners": [{"user": self.who(u), "net": v[2], "bets": v[0], "staked": v[1]} for u, v in by_net[::-1][:3]],
            "worst_day": {"user": self.who(worst_day[0][0]), "day": worst_day[0][1], "net": worst_day[1]},
            "best_day": {"user": self.who(best_day[0][0]), "day": best_day[0][1], "net": best_day[1]},
            "peak_hour": max(hours, key=hours.get) if hours else None,
            "hours": [hours.get(h, 0) for h in range(24)],
        }

    def money(self):
        lo, hi = self.lo, self.hi
        bots = {str(b) for b in self.bots}
        try:
            metrics = json.loads((self.data / "economy_metrics.json").read_text())
        except FileNotFoundError:
            metrics = {}
        supply = []
        for d in sorted(metrics):
            v = metrics[d].get("total_circulation_end_of_day")
            if v and self.since.isoformat() <= d < self.until.isoformat():
                supply.append([d, v])

        def flow(pattern):
            return self.q("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM user_transactions WHERE ts >= ? AND ts < ? "
                          "AND reason LIKE ?", (lo, hi, pattern), db=self.db)[0]
        tax = flow("%tax%")
        booster = flow("Server booster%")
        wordle = flow("HMS Wordle%")
        welcomes = flow("Welcomed a new member%")
        chat = flow("Chatting activity%")
        pays = self.q("SELECT payer_id, recipient_id, amount, timestamp FROM pay_transfers WHERE timestamp >= ? "
                      "AND timestamp < ?", (lo, hi), db=self.db)
        pays = [p for p in pays if self.shown(p[0]) and self.shown(p[1])]
        biggest = max(pays, key=lambda p: p[2]) if pays else None
        returned = biggest and any(p[0] == biggest[1] and p[1] == biggest[0] and p[2] == biggest[2]
                                   and abs(p[3] - biggest[3]) < 86400 for p in pays)
        givers = Counter()
        receivers = defaultdict(set)
        pair = Counter()
        for a, b, n, _ in pays:
            givers[a] += n
            receivers[a].add(b)
            pair[(a, b)] += n
        everyone = self.q("SELECT user_id, balance FROM ukpence", db=self.db)
        bank = sum(int(b) for u, b in everyone if u in bots)  # the bot's balance is the Server Bank
        balances = sorted(((int(b), u) for u, b in everyone if u not in bots), reverse=True)
        total_h = sum(b for b, _ in balances) or 1
        named = [(b, u) for b, u in balances if self.shown(u)]
        rags = self.rags_to_riches()
        shop = self.q("SELECT item_id, SUM(quantity), SUM(price_paid) FROM shop_purchases WHERE purchase_time >= ? "
                      "AND purchase_time < ? GROUP BY item_id ORDER BY 3 DESC", (lo, hi), db=self.db)
        spender = [r for r in self.q("SELECT user_id, SUM(price_paid) s FROM shop_purchases WHERE purchase_time >= ? "
                                     "AND purchase_time < ? GROUP BY user_id ORDER BY s DESC LIMIT 20", (lo, hi), db=self.db)
                   if self.shown(r[0])][:1]
        lottery = self.q("SELECT winner_id, prize, pot, drawn_at FROM lottery_rounds WHERE drawn_at >= ? AND drawn_at < ? "
                         "AND winner_id IS NOT NULL ORDER BY prize DESC", (lo, hi), db=self.db)
        self.out["money"] = {
            "supply": supply, "bank": bank,
            "tax": -tax[0], "tax_n": tax[1], "booster": booster[0], "wordle": wordle[0], "wordle_n": wordle[1],
            "welcomes": welcomes[0], "welcomes_n": welcomes[1], "chat": chat[0],
            "pay_total": sum(p[2] for p in pays), "pay_n": len(pays),
            "biggest_pay": biggest and {"from": self.who(biggest[0]), "to": self.who(biggest[1]), "n": biggest[2],
                                        "day": datetime.fromtimestamp(biggest[3], UK).date().isoformat(),
                                        "returned": bool(returned)},
            "givers": [{"user": self.who(u), "n": n, "people": len(receivers[u])} for u, n in givers.most_common(3)],
            "pair": [{"from": self.who(a), "to": self.who(b), "n": n} for (a, b), n in pair.most_common(2)],
            "richest": [{"user": self.who(u), "n": b} for b, u in named[:5]],
            "top10_share": sum(b for b, _ in balances[:10]) / total_h,
            "top100": [b for b, _ in balances[:100]],
            "holders": sum(1 for b, _ in balances if b > 0),
            "rags": rags,
            "shop": [{"item": SHOP.get(i, i.replace("_", " ").title()), "n": q, "spent": s} for i, q, s in shop[:5]],
            "spender": spender and {"user": self.who(spender[0][0]), "n": spender[0][1]},
            "lottery": [{"user": self.who(w), "prize": p, "pot": pot} for w, p, pot, _ in lottery if self.shown(w)][:3],
        }

    def rags_to_riches(self):
        best = None
        cur, low, low_ts = None, None, None
        bots = {str(b) for b in self.bots}
        for u, ts, bal in self.db.execute("SELECT user_id, ts, balance_after FROM user_transactions WHERE ts >= ? "
                                          "AND ts < ? ORDER BY user_id, ts", (self.lo, self.hi)):
            if not self.shown(u):
                continue
            if u != cur:
                cur, low, low_ts = u, bal, ts
            if bal < low:
                low, low_ts = bal, ts
            rise = bal - low
            if best is None or rise > best[0]:
                best = (rise, u, low, bal, low_ts, ts)
        if not best:
            return None
        rise, u, low, high, t0, t1 = best
        day = lambda t: datetime.fromtimestamp(t, UK).date().isoformat()
        return {"user": self.who(u), "low": low, "high": high, "from": day(t0), "to": day(t1)}

    # ------------------------------------------------------------------ games and the rest
    def games(self):
        lo, hi = self.lo, self.hi
        bots = {str(b) for b in self.bots}
        out = {}
        kart = self.q("SELECT r.race_id, r.user_id, r.place, r.best_lap, k.track, k.mode FROM kart_results r "
                      "JOIN kart_races k ON k.id = r.race_id WHERE k.ended >= ? AND k.ended < ? AND k.outcome = 'race'",
                      (lo, hi), db=self.db)
        if kart:
            wins, laps = Counter(), {}
            races = set()
            for race, u, place, lap, track, mode in kart:
                races.add(race)
                if not u or not self.shown(u):
                    continue
                if mode == "race" and place == 1:
                    wins[u] += 1
                if lap and (track not in laps or lap < laps[track][0]):
                    laps[track] = (lap, u)
            out["kart"] = {"races": len(races), "racers": len({u for _, u, *_ in kart if u}),
                           "champions": [{"user": self.who(u), "wins": n} for u, n in wins.most_common(3)],
                           "laps": [{"track": TRACKS.get(t, t.title()), "lap": round(v[0], 2), "user": self.who(v[1])}
                                    for t, v in sorted(laps.items(), key=lambda kv: kv[1][0])][:6]}
        pvp = self.q("SELECT winner_id, loser_id, game FROM pvp_results WHERE timestamp >= ? AND timestamp < ?",
                     (lo, hi), db=self.db)
        rival = Counter()
        for w, l, g in pvp:
            if w and l and self.shown(w) and self.shown(l):
                rival[(min(w, l), max(w, l))] += 1
        if rival and rival.most_common(1)[0][1] >= 10:
            (a, b), n = rival.most_common(1)[0]
            a_wins = sum(1 for w, l, _ in pvp if w == a and l == b)
            out["rival"] = {"a": self.who(a), "b": self.who(b), "n": n, "a_wins": a_wins, "b_wins": n - a_wins}
        out["pvp_games"] = len(pvp)

        def daily(table, col, label):
            r = self.q(f"SELECT COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM({col}),0), MIN(date) FROM {table} "
                       f"WHERE started >= ? AND started < ?", (lo, hi), db=self.db)[0]
            score = "height" if col == "height" else "score"
            top = [t for t in self.q(f"SELECT user_id, MAX({score}) s FROM {table} WHERE started >= ? AND started < ? "
                                     f"GROUP BY user_id ORDER BY s DESC LIMIT 20", (lo, hi), db=self.db) if self.shown(t[0])][:1]
            if not r[0]:
                return None
            return {"label": label, "runs": r[0], "players": r[1], "total": r[2], "since": r[3],
                    "best": top and {"user": self.who(top[0][0]), "n": top[0][1]}}
        lo_ms, hi_ms = lo * 1000, hi * 1000
        # The daily games store start times in milliseconds.
        sample = self.q("SELECT MAX(started) FROM climb_runs", db=self.db)[0][0] or 0
        if sample > 10 ** 11:
            lo, hi = lo_ms, hi_ms
        out["daily"] = [d for d in (daily("climb_runs", "height", "Climb"), daily("paperboy_runs", "papers", "Paperboy"),
                                    daily("spitfire_runs", "score", "Spitfire")) if d]
        lo, hi = self.lo, self.hi
        try:
            profiles = json.loads((self.data / "skyrim_profiles.json").read_text())
        except FileNotFoundError:
            profiles = {}
        if profiles:
            stats = Counter()
            deaths, dragons = Counter(), Counter()
            for uid, p in profiles.items():
                s = p.get("stats", {})
                for k in ("delves", "clears", "deaths", "kills", "dragons", "sweetrolls"):
                    stats[k] += s.get(k, 0)
                if self.shown(uid):
                    deaths[uid] = s.get("deaths", 0)
                    dragons[uid] = s.get("dragons", 0)
            out["skyrim"] = {"players": len(profiles), **stats,
                             "since": min((p.get("created") for p in profiles.values() if p.get("created")), default=None),
                             "most_dead": {"user": self.who(deaths.most_common(1)[0][0]), "n": deaths.most_common(1)[0][1]},
                             "dragonborn": {"user": self.who(dragons.most_common(1)[0][0]), "n": dragons.most_common(1)[0][1]}}
        self.out["games"] = out

    def badges(self):
        """Bronze, silver, gold and secret badges earned by members: the haul, the medal table (golds first,
        like the Olympics), the rarest, the ones nobody has yet, and the secret ones, which are counted
        but never named or shown, because finding out what they are is the point of them."""
        try:
            cat = {r[0]: {"name": r[1], "desc": r[2], "icon": r[3], "tier": r[4]}
                   for r in self.q("SELECT id, name, description, icon_path, rarity FROM badges", db=self.db)}
            rows = self.q("SELECT user_id, badge_id, awarded_at FROM user_badges WHERE awarded_at >= ? AND awarded_at < ?",
                          (self.lo, self.hi), db=self.db)
        except sqlite3.OperationalError:
            return
        rows = [(int(u), b, t) for u, b, t in rows if b in cat and self.shown(u)]
        if not rows:
            return
        tier = lambda b: cat[b]["tier"]
        holders, per = defaultdict(set), defaultdict(Counter)
        for u, b, _ in rows:
            holders[b].add(u)
            per[u][tier(b)] += 1
        order = {"Gold": 0, "Silver": 1, "Bronze": 2, "Secret": 3}
        public = sorted((b for b in cat if tier(b) != "Secret"), key=lambda b: order.get(tier(b), 9))
        secret = [b for b in cat if tier(b) == "Secret"]
        shown = lambda b: {k: cat[b][k] for k in ("name", "desc", "icon", "tier")}
        table = sorted(per.items(), key=lambda kv: tuple(-kv[1][t] for t in ("Gold", "Silver", "Bronze", "Secret")))[:8]
        # The rarest three, spread around: a badge only one person already featured holds is skipped.
        rarest, seen = [], set()
        for b in sorted((b for b in public if holders[b]), key=lambda b: (len(holders[b]), order.get(tier(b), 9))):
            if not holders[b] <= seen and len(rarest) < 3:
                rarest.append(b)
                seen |= holders[b]
        common = max(public, key=lambda b: len(holders[b]))
        # The top secret hunter: most found, and of those, whoever got to that many first.
        got = defaultdict(list)
        for u, b, t in rows:
            if tier(b) == "Secret":
                got[u].append(t)
        hunter = min(got.items(), key=lambda kv: (-len(kv[1]), max(kv[1]))) if got else None
        try:
            paid = sum(a for u, a in self.q("SELECT user_id, amount FROM badge_rewards WHERE paid_at >= ? AND paid_at < ?",
                                            (self.lo, self.hi), db=self.db) if self.shown(u))
        except sqlite3.OperationalError:
            paid = None
        self.out["badges"] = {
            "since": datetime.fromtimestamp(min(t for _, _, t in rows), UK).date().isoformat(),
            "n": len(rows), "people": len(per), "paid": paid,
            "tiers": {t: sum(1 for _, b, _ in rows if tier(b) == t) for t in order},
            "catalogue": {t: sum(1 for b in cat if tier(b) == t) for t in order},
            "table": [{"user": self.who(u), **{t.lower(): c[t] for t in order}, "n": sum(c.values())} for u, c in table],
            "rarest": [{**shown(b), "n": len(holders[b]), "holders": [self.who(u) for u in sorted(holders[b])[:3]]} for b in rarest],
            "common": {**shown(common), "n": len(holders[common])},
            "unclaimed": [shown(b) for b in public if not holders[b]],
            "secrets": {"found_by": sorted((len(holders[b]) for b in secret), reverse=True), "finders": len(got),
                        "hunter": hunter and {"user": self.who(hunter[0]), "n": len(hunter[1])}},
        }

    def big_brother(self):
        try:
            housemates = self.q("SELECT user_id, status FROM bb_housemates", db=self.db)
        except sqlite3.OperationalError:
            return
        if not housemates:
            return
        winner = next((u for u, s in housemates if s == "winner"), None)
        count = lambda t: self.q(f"SELECT COUNT(*) FROM {t}", db=self.db)[0][0]
        self.out["big_brother"] = {
            "housemates": len(housemates), "winner": winner and self.shown(winner) and self.who(winner),
            "messages": count("bb_messages"), "votes": count("bb_votes"), "diary": count("bb_diary"),
            "rounds": count("bb_rounds"), "challenges": count("bb_challenges"),
            "started": self.bb_start, "ended": self.bb_end,
        }

    def server(self):
        rows = self.q("SELECT date, data FROM daily_summaries WHERE date >= ? AND date < ?",
                      (self.since.isoformat(), self.until.isoformat()), db=self.db)
        joined = left = banned = boosts = 0
        for _, d in rows:
            d = json.loads(d)
            joined += d.get("members_joined", 0)
            left += d.get("members_left", 0)
            banned += d.get("members_banned", 0)
            boosts += d.get("boosters_gained", 0)
        members = sum(1 for p in self.discord["people"].values() if p.get("member") and not p.get("bot"))
        self.out["server"] = {"joined": joined, "left": left, "banned": banned, "boosts": boosts,
                              "days_covered": len(rows), "first_day": min((r[0] for r in rows), default=None),
                              "members": members}

    def wall(self, n=512):
        rows = self.q("SELECT user_id, COUNT(*) k FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                      "GROUP BY user_id ORDER BY k DESC LIMIT ?", (self.lo, self.hi, n * 2))
        ids = [str(u) for u, _ in rows if self.shown(u)][:n]
        self.out["wall"] = ids
        self.featured.update(int(i) for i in ids)

    # ------------------------------------------------------------------ assemble
    def run(self):
        self.scan_messages()
        self.scan_last_year_words()
        self.numbers()
        self.race()
        self.calendar()
        self.channels()
        self.awards()
        self.people_chapter()
        self.hall_of_fame()
        self.iceberg()
        self.words()
        self.casino()
        self.money()
        self.games()
        self.big_brother()
        self.badges()
        self.server()
        self.wall()
        people, unresolved = {}, []
        known = self.discord["people"]
        for uid in sorted(self.featured):
            p = known.get(str(uid))
            if p:
                people[str(uid)] = {"name": p["name"], "avatar": p["avatar"]}
            else:
                unresolved.append(str(uid))
                people[str(uid)] = {"name": "A former member",
                                    "avatar": f"https://cdn.discordapp.com/embed/avatars/{(uid >> 22) % 6}.png"}
        (self.data / "unresolved.txt").write_text("\n".join(unresolved))
        emoji_names = {k: v for k, v in self.discord.get("emojis", {}).items()}
        last_day = self.until - timedelta(days=1)
        return {
            "meta": {"since": self.since.isoformat(), "until": last_day.isoformat(), "year": self.since.year,
                     "full_year": self.until >= date(self.since.year + 1, 1, 1),
                     "generated": datetime.now(UK).isoformat(timespec="minutes"),
                     "guild": self.discord.get("guild", {})},
            **self.out,
            "people": people,
            "emoji_names": emoji_names,
            "unresolved": len(unresolved),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default=f"{date.today().year}-01-01")
    ap.add_argument("--until", default=date.today().isoformat())
    ap.add_argument("--hof-picks", help="JSON list of message ids for the Hall of Fame, in order")
    ap.add_argument("--personal", help="also write everyone's personal Wrapped here (personal.json)")
    a = ap.parse_args()
    ex = Extract(a.data, date.fromisoformat(a.since), date.fromisoformat(a.until), a.hof_picks)
    result = ex.run()
    if a.personal:
        mine = ex.personal()
        Path(a.personal).write_text(json.dumps(mine, ensure_ascii=False, separators=(",", ":")))
        print(f"wrote {a.personal}: {len(mine['people'])} people, "
              f"{sum(p['video'] for p in mine['people'].values())} with a video")
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {a.out}: {len(result['people'])} people, {result['unresolved']} unresolved")


if __name__ == "__main__":
    main()
