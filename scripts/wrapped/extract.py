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
import json
import math
import random
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
VOICE_NOTE = 1 << 13  # MessageFlags.is_voice_message

# Days worth a caption on the calendar, checked against what people were saying that day. Days inside
# the Big Brother run are captioned with the day of the show automatically; anything else that isn't
# here gets its busiest channel instead.
MOMENTS = {
    "2026-05-08": "Local election results",
    "2026-07-15": "England v Argentina",
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
    def __init__(self, data, since, until):
        self.data = Path(data)
        self.since, self.until = since, until
        self.lo, self.hi = day_bounds(since, until)
        self.plo, self.phi = day_bounds(year_ago(since), year_ago(until))
        self.yo_lo, self.yo_hi = day_bounds(date(since.year - 1, 1, 1), date(since.year, 1, 1))
        self.ch = sqlite3.connect(f"file:{self.data / 'chronicle.db'}?mode=ro", uri=True)
        self.db = sqlite3.connect(f"file:{self.data / 'database.db'}?mode=ro", uri=True)
        self.discord = json.loads((self.data / "discord.json").read_text())
        self.bots = {r[0] for r in self.ch.execute("SELECT DISTINCT user_id FROM messages WHERE is_bot = 1")}
        self.bots |= {int(k) for k, v in self.discord["people"].items() if v.get("bot")}
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

    def channel_name(self, cid):
        c = self.discord["channels"].get(str(cid))
        return c["name"] if c else None

    # ------------------------------------------------------------------ chat, one pass
    def scan_messages(self):
        """One pass over the year's human messages for everything per-person and per-word."""
        per = defaultdict(lambda: Counter())
        days_active = defaultdict(set)
        channels_seen = defaultdict(set)
        pairs = Counter()
        months = defaultdict(Counter)
        day_words = defaultdict(Counter)
        laughs = Counter()
        first = []
        longest = None
        rows = self.ch.execute(
            "SELECT message_id, user_id, content, local_day, local_hour, char_count, word_count, n_attachments, "
            "flags, edited_ts, COALESCE(parent_id, channel_id), msg_type, reply_to_user, ts "
            "FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 ORDER BY ts", (self.lo, self.hi))
        for (mid, uid, content, day, hour, chars, words, atts, flags, edited, chan, mtype, rto, ts) in rows:
            c = per[uid]
            text = content or ""
            low = text.lower()
            c["msgs"] += 1
            c["chars"] += chars
            c["words"] += words
            if atts:
                c["files"] += 1
            if "http" in low:
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
            if hour is not None:
                if 1 <= hour < 5:
                    c["night"] += 1
                if 5 <= hour < 8:
                    c["early"] += 1
            days_active[uid].add(day)
            channels_seen[uid].add(chan)
            if rto is not None and rto != uid and (mtype == "reply" or mtype is None):
                c["replies"] += 1
                if int(rto) in self.bots:
                    c["bot_replies"] += 1
                else:
                    pairs[(uid, rto)] += 1
            for name, pat in LAUGHS.items():
                if re.search(pat, low):
                    laughs[name] += 1
            clean = TOKEN_NOISE.sub(" ", URL.sub(" ", low))
            toks = [w.strip("'") for w in WORD.findall(clean)]
            toks = [w for w in toks if len(w) >= 3 and w not in STOP and not SLURS.match(w)]
            month = day[:7]
            months[month].update(toks)
            day_words[day].update(set(toks))
            c["british"] += sum(1 for w in toks if w in BRITISH)
            if len(first) < 6 and text.strip():
                first.append({"user": uid, "text": text[:140], "ts": ts})
            if longest is None or chars > longest[0]:
                longest = (chars, uid, day, mid)
        self.per, self.days_active, self.channels_seen = per, days_active, channels_seen
        self.pairs, self.months_words, self.day_words, self.laughs = pairs, months, day_words, laughs
        self.first_msgs, self.longest = first, longest

    def scan_last_year_words(self):
        prev = Counter()
        laughs = Counter()
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
        self.prev_words, self.prev_laughs, self.prev_n = prev, laughs, n_window

    # ------------------------------------------------------------------ sections
    def numbers(self):
        def totals(lo, hi):
            r = self.q("SELECT COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM(word_count),0), "
                       "COALESCE(SUM(n_attachments > 0),0), COALESCE(SUM((flags & ?) != 0),0) "
                       "FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0", (VOICE_NOTE, lo, hi))[0]
            return dict(zip(("messages", "people", "words", "files", "voice_notes"), r))
        now, prev = totals(self.lo, self.hi), totals(self.plo, self.phi)
        reactions = self.q("SELECT COUNT(*) FROM reactions WHERE ts >= ? AND ts < ? AND action = 'add'",
                           (self.lo, self.hi))[0][0]
        prev_reactions = self.q("SELECT COUNT(*) FROM reactions WHERE ts >= ? AND ts < ? AND action = 'add'",
                                (self.plo, self.phi))[0][0]
        now["reactions"], prev["reactions"] = reactions, prev_reactions
        self.out["numbers"] = {"now": now, "prev": prev}

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
    def awards(self):
        per = self.per
        regulars = [u for u, c in per.items() if c["msgs"] >= 2000]

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
            if not ranked:
                return
            v, raw, u = ranked[0]
            out.append({"key": key, "title": title, "line": line, "user": self.who(u), "value": fmt(v, raw, u),
                        "runners": [self.who(r[2]) for r in ranked[1:3]]})

        pct = lambda v, raw, u: f"{v * 100:.0f}%"
        num = lambda v, raw, u: f"{int(raw):,}"

        award("night_owl", "Night Owl", "of their messages sent between 1am and 5am",
              best("night", regulars, rate=True), pct)
        award("early_bird", "Early Bird", "of their messages sent before 8am",
              best("early", regulars, rate=True), pct)
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
        award("plug", "The Plug", "links shared", best("links"), num)
        award("voice", "Voice Note Royalty", "voice notes", best("voice"), num)
        award("replier", "The Replier", "replies sent", best("replies"), num)
        award("bot_friend", "The Bot's Best Friend", "replies to HMS Victory", best("bot_replies"), num)

        tourist = sorted(((len(self.channels_seen[u]), 0, u) for u in per), reverse=True)[:3]
        award("tourist", "The Tourist", "different channels posted in", tourist, lambda v, r, u: f"{v}")
        committed = sorted(((len(self.days_active[u]), 0, u) for u in per), reverse=True)[:3]
        n_days = (self.until - self.since).days
        award("committed", "The Committed", f"of {n_days} days with a message", committed, lambda v, r, u: f"{v}")

        # The glue: the most different people talked to, either direction.
        partners = defaultdict(set)
        for (a, b), n in self.pairs.items():
            if n >= 3:
                partners[a].add(b)
                partners[b].add(a)
        glue = sorted(((len(s), 0, u) for u, s in partners.items()), reverse=True)[:3]
        award("glue", "The Glue", "different people they traded replies with", glue, lambda v, r, u: f"{v}")

        # Reactions and mentions, people only.
        given, got = Counter(), Counter()
        for reactor, author, n in self.q(
                "SELECT user_id, author_id, COUNT(*) FROM reactions WHERE ts >= ? AND ts < ? AND action = 'add' "
                "GROUP BY user_id, author_id", (self.lo, self.hi)):
            if self.human(reactor) and author != reactor:
                given[reactor] += n
                if self.human(author):
                    got[author] += n
        self.reaction_got = got
        award("hype", "Hype Machine", "reactions given", [(v, v, u) for u, v in given.most_common(3)], num)
        award("crowd", "Crowd Pleaser", "reactions received", [(v, v, u) for u, v in got.most_common(3)], num)
        sent_m, got_m = Counter(), Counter()
        for author, target, n in self.q(
                "SELECT user_id, target_id, COUNT(*) FROM mentions WHERE ts >= ? AND ts < ? AND kind = 'user' "
                "GROUP BY user_id, target_id", (self.lo, self.hi)):
            if self.human(author) and self.human(target) and author != target:
                sent_m[author] += n
                got_m[target] += n
        award("celeb", "Local Celeb", "times @mentioned", [(v, v, u) for u, v in got_m.most_common(3)], num)
        award("gossip", "The Gossip", "@mentions of other people", [(v, v, u) for u, v in sent_m.most_common(3)], num)
        emo = Counter()
        for u, n in self.q("SELECT user_id, SUM(n) FROM emoji_uses WHERE ts >= ? AND ts < ? GROUP BY user_id",
                           (self.lo, self.hi)):
            if self.human(u):
                emo[u] = n
        award("emoji", "Emoji Artist", "emoji used", [(v, v, u) for u, v in emo.most_common(3)], num)

        # Fastest fingers: the typical gap between a message and someone's reply to it.
        gaps = defaultdict(list)
        for u, gap in self.q(
                "SELECT m.user_id, m.ts - p.ts FROM messages m JOIN messages p ON p.message_id = m.reply_to "
                "WHERE m.ts >= ? AND m.ts < ? AND m.is_bot = 0 AND (m.msg_type = 'reply' OR m.msg_type IS NULL) "
                "AND p.user_id != m.user_id AND p.is_bot = 0", (self.lo, self.hi)):
            if gap >= 0:
                gaps[u].append(gap)
        fast = sorted((statistics.median(g), 0, u) for u, g in gaps.items() if len(g) >= 1000)[:3]
        award("fastest", "Fastest Fingers", "seconds to reply, typically", fast, lambda v, r, u: f"{v:.0f}")

        starters, killers = self.conversations()
        award("starter", "Conversation Starter", "quiet channels they brought back to life",
              [(v, v, u) for u, v in starters.most_common(3)], num)
        kill_rate = sorted(((killers[u] / per[u]["msgs"] * 1000, killers[u], u) for u in regulars if killers[u]),
                           reverse=True)[:3]
        award("killer", "Conversation Killer", "last words before two hours of daytime silence", kill_rate,
              lambda v, r, u: f"{int(r):,}")

        # Rookies and glow-ups need everyone's history before this window.
        before = dict(self.q("SELECT user_id, COUNT(*) FROM messages WHERE ts < ? AND is_bot = 0 GROUP BY user_id",
                             (self.lo,)))
        prev_window = dict(self.q("SELECT user_id, COUNT(*) FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                                  "GROUP BY user_id", (self.plo, self.phi)))
        rookies = sorted(((per[u]["msgs"], u) for u in per if u not in before), reverse=True)[:5]
        glow = sorted(((per[u]["msgs"] - prev_window.get(u, 0), u) for u in per if prev_window.get(u, 0) >= 100),
                      reverse=True)[:5]
        award("rookie", "Rookie of the Year", "messages since their first ever",
              [(n, n, u) for n, u in rookies], num)
        award("glowup", "Glow-up of the Year", "more messages than the same stretch last year",
              [(n, n, u) for n, u in glow], lambda v, r, u: f"+{int(v):,}")

        self.out["awards"] = out
        self.out["rookies"] = [{"user": self.who(u), "n": n,
                                "first": min(self.days_active[u])} for n, u in rookies[:3]]
        self.out["glowups"] = [{"user": self.who(u), "n": per[u]["msgs"], "prev": prev_window.get(u, 0)}
                               for _, u in glow[:3]]

    def conversations(self):
        """Who restarts quiet channels, and who has the last word before they go quiet."""
        starters, killers = Counter(), Counter()
        rows = self.ch.execute(
            "SELECT channel_id, user_id, ts, local_hour FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
            "ORDER BY channel_id, ts", (self.lo, self.hi))
        chan, seq = None, []

        def flush(seq):
            for i, (u, ts, hour) in enumerate(seq):
                gap_before = ts - seq[i - 1][1] if i else 10 ** 9
                if gap_before >= 45 * 60:
                    window = [s for s in seq[i + 1:i + 40] if s[1] - ts <= 15 * 60 and s[0] != u]
                    if len(window) >= 6 and len({s[0] for s in window}) >= 2:
                        starters[u] += 1
                if i + 1 < len(seq) and seq[i + 1][1] - ts >= 2 * 3600 and hour is not None and 11 <= hour < 21:
                    killers[u] += 1
        for c, u, ts, hour in rows:
            if c != chan:
                flush(seq)
                chan, seq = c, []
            seq.append((u, ts, hour))
        flush(seq)
        return starters, killers

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
        split = {}
        for (a, b), n in pairs.items():
            k = (min(a, b), max(a, b))
            both[k] += n
        duos = []
        for (a, b), n in both.most_common(5):
            duos.append({"a": self.who(a), "b": self.who(b), "n": n, "ab": pairs[(a, b)], "ba": pairs[(b, a)]})
        one_way = sorted(((n / (pairs.get((b, a), 0) + 1), a, b, n) for (a, b), n in pairs.items() if n >= 200),
                         reverse=True)[:3]

        fans = Counter()
        for reactor, author, n in self.q(
                "SELECT user_id, author_id, COUNT(*) FROM reactions WHERE ts >= ? AND ts < ? AND action = 'add' "
                "GROUP BY user_id, author_id HAVING COUNT(*) >= 50", (self.lo, self.hi)):
            if self.human(reactor) and self.human(author) and reactor != author:
                fans[(reactor, author)] = n
        mates = sorted(((min(n, fans.get((b, a), 0)), a, b) for (a, b), n in fans.items() if a < b), reverse=True)[:3]

        self.out["social"] = {
            "replied": replied,
            "duos": duos,
            "one_way": [{"from": self.who(a), "to": self.who(b), "n": n, "back": pairs.get((b, a), 0)}
                        for _, a, b, n in one_way],
            "fans": [{"from": self.who(a), "to": self.who(b), "n": n} for (a, b), n in fans.most_common(3)],
            "mates": [{"a": self.who(a), "b": self.who(b), "ab": fans[(a, b)], "ba": fans.get((b, a), 0)}
                      for _, a, b in mates],
        }
        self.out["network"] = self.network()

    def network(self, size=44):
        """The reply graph's busiest people, laid out in 3D with a seeded force layout."""
        weight = Counter()
        for (a, b), n in self.pairs.items():
            weight[a] += n
            weight[b] += n
        nodes = [u for u, _ in weight.most_common(size)]
        idx = {u: i for i, u in enumerate(nodes)}
        both = Counter()
        for (a, b), n in self.pairs.items():
            if a in idx and b in idx:
                both[(min(a, b), max(a, b))] += n
        edges = [(a, b, n) for (a, b), n in both.most_common(160) if n >= 120]
        rng = random.Random(2026)
        pos = [[rng.uniform(-1, 1) for _ in range(3)] for _ in nodes]
        k = 0.55
        maxw = max((n for _, _, n in edges), default=1)
        for it in range(500):
            disp = [[0.0, 0.0, 0.0] for _ in nodes]
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    d = [pos[i][x] - pos[j][x] for x in range(3)]
                    dist = math.sqrt(sum(v * v for v in d)) + 1e-3
                    f = k * k / dist
                    for x in range(3):
                        disp[i][x] += d[x] / dist * f
                        disp[j][x] -= d[x] / dist * f
            for a, b, n in edges:
                i, j = idx[a], idx[b]
                d = [pos[i][x] - pos[j][x] for x in range(3)]
                dist = math.sqrt(sum(v * v for v in d)) + 1e-3
                f = dist * dist / k * (0.4 + n / maxw)
                for x in range(3):
                    disp[i][x] -= d[x] / dist * f
                    disp[j][x] += d[x] / dist * f
            temp = 0.12 * (1 - it / 500) + 0.002
            for i in range(len(nodes)):
                for x in range(3):
                    disp[i][x] -= pos[i][x] * 0.05  # gentle pull to the middle
                dl = math.sqrt(sum(v * v for v in disp[i])) + 1e-9
                for x in range(3):
                    pos[i][x] += disp[i][x] / dl * min(dl, temp)
        span = max(max(abs(v) for v in p) for p in pos) or 1
        return {"nodes": [{"user": self.who(u), "w": weight[u], "p": [round(v / span, 4) for v in pos[i]]}
                          for i, u in enumerate(nodes)],
                "edges": [[idx[a], idx[b], n] for a, b, n in edges]}

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

    def hall_of_fame(self):
        try:
            ids = [int(x) for x in json.loads((self.data / "hall_of_fame.json").read_text())]
        except FileNotFoundError:
            ids = []
        picks = []
        for mid in ids:
            row = self.q("SELECT user_id, content, ts, local_day, n_attachments FROM messages WHERE message_id = ?", (mid,))
            if not row:
                continue
            uid, content, ts, day, atts = row[0]
            if not (self.lo <= ts < self.hi) or not content or not self.human(uid):
                continue
            if len(content) > 200 or URL.search(content):
                continue
            reacts = self.q("SELECT emoji, emoji_id, count FROM reaction_counts WHERE message_id = ? ORDER BY count DESC",
                            (mid,))
            total = sum(r[2] for r in reacts)
            picks.append({"user": uid, "text": self.render_text(content), "day": day, "total": total,
                          "reactions": [{"e": e, "id": str(i) if i else None, "n": n} for e, i, n in reacts[:5]]})
        picks.sort(key=lambda p: -p["total"])
        chosen = picks[:6]
        for p in chosen:
            p["user"] = self.who(p["user"])
        self.out["hall_of_fame"] = sorted(chosen, key=lambda p: p["day"])

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
        emoji = []
        for e, eid, n in self.q("SELECT emoji, emoji_id, SUM(n) t FROM emoji_uses WHERE ts >= ? AND ts < ? "
                                "GROUP BY emoji, emoji_id ORDER BY t DESC LIMIT 10", (self.lo, self.hi)):
            emoji.append({"e": e, "id": str(eid) if eid else None, "n": n})
        reacts = []
        for e, eid, n in self.q("SELECT emoji, emoji_id, COUNT(*) t FROM reactions WHERE ts >= ? AND ts < ? "
                                "AND action = 'add' GROUP BY emoji, emoji_id ORDER BY t DESC LIMIT 10", (self.lo, self.hi)):
            reacts.append({"e": e, "id": str(eid) if eid else None, "n": n})
        chars, uid, day, mid = self.longest
        content = self.q("SELECT content FROM messages WHERE message_id = ?", (mid,))[0][0] or ""
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
            p = per[u]
            p[0] += 1
            p[1] += staked
            p[2] += net
            d = datetime.fromtimestamp(ts, UK)
            days[(u, d.date().isoformat())] += net
            hours[d.hour] += staked
        wins = sorted(rows, key=lambda r: -r[4])[:3]
        losses = sorted(rows, key=lambda r: r[4])[:3]
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
            "bets": len(rows), "players": len(per), "staked": sum(r[2] for r in rows),
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
        pays = [p for p in pays if p[0] not in bots and p[1] not in bots]
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
        balances = [(int(b), u) for u, b in everyone if u not in bots]
        balances.sort(reverse=True)
        total_h = sum(b for b, _ in balances) or 1
        rags = self.rags_to_riches()
        shop = self.q("SELECT item_id, SUM(quantity), SUM(price_paid) FROM shop_purchases WHERE purchase_time >= ? "
                      "AND purchase_time < ? GROUP BY item_id ORDER BY 3 DESC", (lo, hi), db=self.db)
        spender = self.q("SELECT user_id, SUM(price_paid) s FROM shop_purchases WHERE purchase_time >= ? "
                         "AND purchase_time < ? GROUP BY user_id ORDER BY s DESC LIMIT 1", (lo, hi), db=self.db)
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
            "richest": [{"user": self.who(u), "n": b} for b, u in balances[:5]],
            "top10_share": sum(b for b, _ in balances[:10]) / total_h,
            "top100": [b for b, _ in balances[:100]],
            "holders": sum(1 for b, _ in balances if b > 0),
            "rags": rags,
            "shop": [{"item": SHOP.get(i, i.replace("_", " ").title()), "n": q, "spent": s} for i, q, s in shop[:5]],
            "spender": spender and {"user": self.who(spender[0][0]), "n": spender[0][1]},
            "lottery": [{"user": self.who(w), "prize": p, "pot": pot} for w, p, pot, _ in lottery[:3]],
        }

    def rags_to_riches(self):
        best = None
        cur, low, low_ts = None, None, None
        bots = {str(b) for b in self.bots}
        for u, ts, bal in self.db.execute("SELECT user_id, ts, balance_after FROM user_transactions WHERE ts >= ? "
                                          "AND ts < ? ORDER BY user_id, ts", (self.lo, self.hi)):
            if u in bots:
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
                if not u or u in bots:
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
            if w and l and w not in bots and l not in bots:
                rival[(min(w, l), max(w, l))] += 1
        if rival:
            (a, b), n = rival.most_common(1)[0]
            a_wins = sum(1 for w, l, _ in pvp if w == a and l == b)
            out["rival"] = {"a": self.who(a), "b": self.who(b), "n": n, "a_wins": a_wins, "b_wins": n - a_wins}
        out["pvp_games"] = len(pvp)

        def daily(table, col, label):
            r = self.q(f"SELECT COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM({col}),0), MIN(date) FROM {table} "
                       f"WHERE started >= ? AND started < ?", (lo, hi), db=self.db)[0]
            top = self.q(f"SELECT user_id, MAX(score) s FROM {table} WHERE started >= ? AND started < ? "
                         f"GROUP BY user_id ORDER BY s DESC LIMIT 1", (lo, hi), db=self.db) if col != "height" else \
                self.q("SELECT user_id, MAX(height) s FROM climb_runs WHERE started >= ? AND started < ? "
                       "GROUP BY user_id ORDER BY s DESC LIMIT 1", (lo, hi), db=self.db)
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
                deaths[uid] = s.get("deaths", 0)
                dragons[uid] = s.get("dragons", 0)
            out["skyrim"] = {"players": len(profiles), **stats,
                             "since": min((p.get("created") for p in profiles.values() if p.get("created")), default=None),
                             "most_dead": {"user": self.who(deaths.most_common(1)[0][0]), "n": deaths.most_common(1)[0][1]},
                             "dragonborn": {"user": self.who(dragons.most_common(1)[0][0]), "n": dragons.most_common(1)[0][1]}}
        badges = self.q("SELECT ub.user_id, b.rarity FROM user_badges ub JOIN badges b ON b.id = ub.badge_id "
                        "WHERE ub.awarded_at >= ? AND ub.awarded_at < ?", (lo, hi), db=self.db)
        badges = [b for b in badges if b[0] not in bots]
        if badges:
            hunters = Counter(u for u, _ in badges)
            out["badges"] = {"n": len(badges), "people": len(hunters),
                             "rarity": dict(Counter(r for _, r in badges)),
                             "hunter": {"user": self.who(hunters.most_common(1)[0][0]), "n": hunters.most_common(1)[0][1]}}
        self.out["games"] = out

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
            "housemates": len(housemates), "winner": winner and self.who(winner),
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

    def wall(self, n=560):
        rows = self.q("SELECT user_id, COUNT(*) k FROM messages WHERE ts >= ? AND ts < ? AND is_bot = 0 "
                      "GROUP BY user_id ORDER BY k DESC LIMIT ?", (self.lo, self.hi, n * 2))
        people = self.discord["people"]
        ids = [str(u) for u, _ in rows if str(u) in people][:n]
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
        self.words()
        self.casino()
        self.money()
        self.games()
        self.big_brother()
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
    a = ap.parse_args()
    result = Extract(a.data, date.fromisoformat(a.since), date.fromisoformat(a.until)).run()
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {a.out}: {len(result['people'])} people, {result['unresolved']} unresolved")


if __name__ == "__main__":
    main()
