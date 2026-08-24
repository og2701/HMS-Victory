"""Two detectors that watch the message stream rather than the door.

Everything else here is triggered by joining - anti_raid, join_watch, join_clusters. That
leaves two attacks uncovered, and both of them happen in chat:

**Coordinated messages.** The signature of a raid actually starting: the same text from
several accounts inside a minute. join_watch judges each member on their own, so it
structurally cannot see a correlation between them, and it only screens people who joined
while it was armed. This looks across authors instead of at any one of them.

**Account takeover.** A long-standing member whose account is stolen and starts posting
scam links. Nothing here has ever looked at established members - they are past every join
check by definition - and unlike staff, ordinary members have no 2FA requirement, so this
is the likeliest real incident a large server gets.

Both are deliberately narrow. A false positive here accuses a real member of being a bot,
so each fires on a conjunction of signals rather than any single one, and both exempt staff.
Neither ever acts on its own: they report, and a human decides.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

import discord

import config
from config import CHANNELS, JSON_DATA_DIR, ROLES
from database import DatabaseManager

log = logging.getLogger(__name__)

STAFF_ROLE_IDS = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE, ROLES.PCSO,
                  ROLES.DEPUTY_PM}
STATE_FILE = os.path.join(JSON_DATA_DIR, "behaviour_watch.json")

# --- coordinated messages ----------------------------------------------------------
COORD_WINDOW_SECONDS = 120        # how long the same text stays comparable
COORD_MIN_AUTHORS = 4             # distinct accounts before it is a pattern
COORD_MIN_CHARS = 20              # below this, identical text is just chat
COORD_COOLDOWN_SECONDS = 900      # do not re-report the same text repeatedly

# Things a crowd genuinely says at the same moment. Without this the detector fires every
# time somebody joins and four people type "welcome".
COORD_STOPLIST = {
    "welcome", "welcome to the server", "gm", "gn", "good morning", "good night",
    "lol", "lmao", "same", "yes", "no", "true", "real", "ok", "okay", "hello",
    "hi", "hey", "thanks", "thank you", "happy birthday", "congrats",
    "congratulations", "rip", "f", "gg", "nice", "based", "w", "l",
}

# --- account takeover --------------------------------------------------------------
ESTABLISHED_DAYS = 30             # how long before a member counts as established
ESTABLISHED_MESSAGES = 200        # ...and how much they must have said
LINK_RATIO_CEILING = 0.02         # "never posts links" means under 2% of their messages
LINK_BURST_COUNT = 3              # links in a burst before it is worth reporting
LINK_BURST_SECONDS = 300
CROSS_CHANNEL_COUNT = 3           # same text in this many channels...
CROSS_CHANNEL_SECONDS = 120       # ...this quickly

_URL_RE = re.compile(r"https?://\S+|discord\.gg/\S+|\b[\w-]+\.(?:com|net|org|gg|io|xyz|ru|tk)\b/?\S*",
                     re.I)
_PUNCT_RE = re.compile(r"[^\w\s]")
LINK_TOKEN = "__link__"

# Hosts where a link is a reaction image, not a link. Replayed over three months of real
# #general, treating these as links made both detectors useless: every trio of people
# posting GIFs matched each other, and one regular tripped the takeover check five times
# for posting Harry Potter reaction gifs.
MEDIA_HOSTS = (
    "tenor.com", "klipy.com", "giphy.com", "gfycat.com", "imgur.com",
    "cdn.discordapp.com", "media.discordapp.net", "discord.com/channels",
    "youtube.com", "youtu.be", "twitter.com", "x.com", "reddit.com",
    "redd.it", "twitch.tv", "spotify.com", "wikipedia.org",
)
_URL_ONLY_RE = re.compile(r"https?://\S+|discord\.gg/\S+", re.I)


# --- shared -------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Reduce a message to what makes two of them 'the same'.

    URLs collapse to a marker rather than vanishing: a scam campaign rotates the domain
    per message, so the shape of the message is the stable part, not the link. Case,
    punctuation and repeated whitespace all go, because those are what a lazy raider varies
    to defeat exact matching.
    """
    # The marker has to survive punctuation stripping below, so it is built from word
    # characters - "<link>" was reduced to "link" and stopped being distinguishable from
    # someone typing the word, which silently broke the short-message-with-a-link case.
    t = _URL_RE.sub(f" {LINK_TOKEN} ", str(text or ""))
    t = _PUNCT_RE.sub(" ", t.lower())
    return " ".join(t.split())


_DISCORD_TOKEN_RE = re.compile(r"<[^<>\n]{1,80}>")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF\uFE0F\u200D\u20E3]+")


def substance(text: str) -> str:
    """What is left once the things a crowd genuinely posts identically are removed.

    A custom emoji arrives as <a:bouncy_yaris:1540334892173733599>. Stripping punctuation
    turned that into a thirty-four character run, so three people posting the same reaction
    emoji read as three people posting the same sentence - which is what tripped this in
    #general. Mentions, channel links and unicode emoji all have the same problem: shared,
    identical, and evidence of nothing.
    """
    t = _DISCORD_TOKEN_RE.sub(" ", str(text or ""))
    t = _EMOJI_RE.sub(" ", t)
    return normalise(t).replace(LINK_TOKEN, " ").strip()


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def is_staff(member: Any) -> bool:
    return any(getattr(r, "id", None) in STAFF_ROLE_IDS
               for r in getattr(member, "roles", None) or ())


def _has_link(text: str) -> bool:
    return bool(_URL_RE.search(str(text or "")))


def urls_in(text: str) -> list[str]:
    """Every URL, lowercased and stripped of query strings so trackers do not split them."""
    out = []
    for raw in _URL_ONLY_RE.findall(str(text or "")):
        cleaned = raw.split("?")[0].split("#")[0].rstrip("/.,)").lower()
        if cleaned:
            out.append(cleaned)
    return out


def is_media(url: str) -> bool:
    return any(host in url for host in MEDIA_HOSTS)


def has_external_link(text: str) -> bool:
    """A link that is not a reaction image. This is the one a takeover posts."""
    return any(not is_media(u) for u in urls_in(text))


def content_key(text: str) -> tuple[str, str] | None:
    """What two messages have to share to count as 'the same', or None if nothing.

    Two different keys, because two different things are worth catching:
      - real wording in common, once the link token is discounted;
      - the exact same URL, which is the case where the message is only a link.
    A message that is just *a* link matches nothing: three people posting three
    different GIFs have nothing in common at all.
    """
    words = substance(text)
    if words in COORD_STOPLIST:
        return None
    if len(words) >= COORD_MIN_CHARS:
        # Digest the substance rather than the raw wording, so decorating the same line
        # with a different emoji each time does not split it into separate findings.
        return ("text", _digest(words))
    links = [u for u in urls_in(text) if not is_media(u)]
    if len(links) == 1:
        return ("url", _digest(links[0]))
    return None


def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        from lib.core.file_operations import atomic_write_json
        atomic_write_json(STATE_FILE, state, indent=2)
    except Exception:
        log.exception("could not persist behaviour-watch state")


# --- member profiles ----------------------------------------------------------------
# A takeover is only visible against what that account normally does, so this keeps the
# cheapest possible baseline: how long they have been here, how much they talk, and how
# often they post links. One upsert per message at a few thousand messages a day.
def ensure_schema() -> None:
    try:
        DatabaseManager.execute('''
            CREATE TABLE IF NOT EXISTS member_profile (
                user_id       TEXT PRIMARY KEY,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                messages      INTEGER NOT NULL DEFAULT 0,
                link_messages INTEGER NOT NULL DEFAULT 0
            )
        ''')
    except Exception:
        log.exception("could not create member_profile")


def get_profile(user_id) -> dict[str, Any] | None:
    row = DatabaseManager.fetch_one(
        "SELECT first_seen, last_seen, messages, link_messages FROM member_profile "
        "WHERE user_id = ?", (str(user_id),))
    if not row:
        return None
    return {"first_seen": row[0], "last_seen": row[1],
            "messages": row[2], "link_messages": row[3]}


def record_message(user_id, has_link: bool, now: int | None = None) -> dict[str, Any]:
    """Update the baseline and hand back the profile as it was *before* this message.

    Before, deliberately: the question is whether this message is unlike them, and folding
    it in first would dilute the very thing being measured.
    """
    current = int(time.time() if now is None else now)
    before = get_profile(user_id) or {"first_seen": current, "last_seen": current,
                                      "messages": 0, "link_messages": 0}
    try:
        DatabaseManager.execute(
            "INSERT INTO member_profile (user_id, first_seen, last_seen, messages, link_messages) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen, "
            "messages = messages + 1, link_messages = link_messages + excluded.link_messages",
            (str(user_id), current, current, 1 if has_link else 0),
        )
    except Exception:
        log.exception("could not update member profile for %s", user_id)
    return before


def is_established(profile: dict[str, Any], now: int | None = None) -> bool:
    current = int(time.time() if now is None else now)
    age_days = (current - int(profile.get("first_seen", current))) / 86400.0
    return age_days >= ESTABLISHED_DAYS and int(profile.get("messages", 0)) >= ESTABLISHED_MESSAGES


def never_posts_links(profile: dict[str, Any]) -> bool:
    msgs = max(1, int(profile.get("messages", 0)))
    return (int(profile.get("link_messages", 0)) / msgs) <= LINK_RATIO_CEILING


# --- in-memory windows ---------------------------------------------------------------
# Both detectors care about the last couple of minutes and nothing older, so none of this
# is persisted: a restart losing two minutes of context costs nothing, while persisting it
# would add write volume on the hottest path in the bot.
_coord: dict[str, deque] = defaultdict(deque)          # digest -> [(uid, ts, jump, name)]
_coord_reported: dict[str, int] = {}                   # digest -> last reported at
_links: dict[str, deque] = defaultdict(deque)          # uid -> [ts] of link messages
_by_channel: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))


def _sweep(window: deque, cutoff: int, index: int = 1) -> None:
    while window and window[0][index] < cutoff:
        window.popleft()


def reset_windows() -> None:
    """Test seam, and a way to clear state after a false positive."""
    _coord.clear()
    _coord_reported.clear()
    _links.clear()
    _by_channel.clear()


# --- detector 1: several accounts saying the same thing --------------------------------
def check_coordinated(user_id, text: str, channel_id, jump_url: str = "",
                      name: str = "", now: int | None = None,
                      established: bool = False) -> dict[str, Any] | None:
    """Return a finding when COORD_MIN_AUTHORS distinct accounts post the same text.

    Established members are not counted. A month here and two hundred messages is not
    something a farmed account has, so several regulars landing on the same line is a meme
    rather than a raid - which is what this kept reporting. A regular whose account has
    actually been taken over is check_takeover's job, and that one requires tenure instead
    of excluding it.
    """
    current = int(time.time() if now is None else now)
    if established:
        return None
    key = content_key(text)
    if key is None:
        return None
    kind, digest = key

    window = _coord[digest]
    _sweep(window, current - COORD_WINDOW_SECONDS)
    if not any(str(entry[0]) == str(user_id) for entry in window):
        window.append((str(user_id), current, jump_url, name))

    authors = {entry[0] for entry in window}
    if len(authors) < COORD_MIN_AUTHORS:
        return None
    last = _coord_reported.get(digest, 0)
    if current - last < COORD_COOLDOWN_SECONDS:
        return None
    _coord_reported[digest] = current
    return {
        "kind": "coordinated_message",
        "match": kind,
        "digest": digest,
        "text": (normalise(text) if kind == "text" else urls_in(text)[0])[:400],
        "authors": [{"user_id": e[0], "at": e[1], "jump": e[2], "name": e[3]}
                    for e in window],
        "channel_id": str(channel_id),
    }


# --- detector 2: this does not look like them ------------------------------------------
def check_takeover(user_id, text: str, channel_id, profile: dict[str, Any],
                   name: str = "", now: int | None = None) -> dict[str, Any] | None:
    """Return a finding when an established member behaves unlike themselves.

    Two shapes, both requiring the account to be established first, because a new member
    posting links is just a new member:

      - a burst of links from somebody who effectively never posts links,
      - the same text pushed across several channels in a couple of minutes.
    """
    current = int(time.time() if now is None else now)
    if not is_established(profile, current):
        return None

    uid = str(user_id)
    if has_external_link(text) and never_posts_links(profile):
        window = _links[uid]
        while window and window[0] < current - LINK_BURST_SECONDS:
            window.popleft()
        window.append(current)
        if len(window) >= LINK_BURST_COUNT:
            window.clear()
            return {
                "kind": "account_takeover",
                "signal": "link_burst",
                "user_id": uid,
                "name": name,
                "text": str(text or "")[:400],
                "detail": (f"{LINK_BURST_COUNT}+ links in "
                           f"{LINK_BURST_SECONDS // 60} minutes from an account whose "
                           f"last {profile['messages']:,} messages were "
                           f"{profile['link_messages']:,} links"),
            }

    norm = normalise(text)
    if norm and len(norm) >= COORD_MIN_CHARS:
        seen = _by_channel[uid][_digest(norm)]
        while seen and seen[0][1] < current - CROSS_CHANNEL_SECONDS:
            seen.popleft()
        if not any(c == str(channel_id) for c, _ in seen):
            seen.append((str(channel_id), current))
        if len({c for c, _ in seen}) >= CROSS_CHANNEL_COUNT:
            seen.clear()
            return {
                "kind": "account_takeover",
                "signal": "cross_channel",
                "user_id": uid,
                "name": name,
                "text": str(text or "")[:400],
                "detail": (f"the same message in {CROSS_CHANNEL_COUNT} channels within "
                           f"{CROSS_CHANNEL_SECONDS // 60} minutes"),
            }
    return None


# --- reporting ------------------------------------------------------------------------
# Same shape as the join-cluster report: state what the signal means before showing the
# data, put the action next to the thing it acts on, and never do anything automatically.
def build_coordinated_view(finding: dict[str, Any],
                           handled: list[str] | None = None) -> discord.ui.LayoutView:
    handled = set(handled or [])
    authors = finding["authors"]
    live = [a for a in authors if a["user_id"] not in handled]
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE74C3C if live else 0x95A5A6)
    card.add_item(discord.ui.TextDisplay(
        "## 📡 Several accounts are posting the same thing\n"
        f"{len(authors)} accounts posted the same message within "
        f"{COORD_WINDOW_SECONDS // 60} minutes. One person repeating themselves is normal; "
        "several accounts saying something identical at once is how a raid starts."))
    card.add_item(discord.ui.Separator())
    card.add_item(discord.ui.TextDisplay(
        f"**What they posted**\n>>> {discord.utils.escape_markdown(finding['text'])[:600]}"))
    rows = []
    for a in authors:
        jump = f" · [jump]({a['jump']})" if a.get("jump") else ""
        mark = " ~~handled~~" if a["user_id"] in handled else ""
        rows.append(f"<@{a['user_id']}> `{a['user_id']}` · <t:{int(a['at'])}:R>{jump}{mark}")
    card.add_item(discord.ui.TextDisplay(("### Accounts\n" + "\n".join(rows))[:1500]))
    if live:
        card.add_item(discord.ui.ActionRow(
            CoordTimeoutButton(finding["digest"], len(live)),
            CoordBanButton(finding["digest"], len(live)),
            CoordDismissButton(finding["digest"]),
        ))
        card.add_item(discord.ui.TextDisplay(
            "-# ⏳ silences them without removing anyone · 🔨 removes them · ✅ says this "
            "was nothing. Identical wording can be a copypasta joke - read it first."))
    else:
        card.add_item(discord.ui.TextDisplay("✅ **Dealt with.**"))
    view.add_item(card)
    return view


def build_takeover_view(finding: dict[str, Any], profile: dict[str, Any],
                        handled: bool = False) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xF39C12 if not handled else 0x95A5A6)
    age_days = int((time.time() - profile.get("first_seen", time.time())) // 86400)
    card.add_item(discord.ui.TextDisplay(
        "## 🕵️ This doesn't look like them\n"
        f"<@{finding['user_id']}> has been here {age_days} days with "
        f"{profile.get('messages', 0):,} messages, and just did something they never do: "
        f"{finding['detail']}.\n\n"
        "That is the usual shape of a stolen account. It is also the usual shape of "
        "somebody sharing a few links about something they are excited about."))
    card.add_item(discord.ui.Separator())
    card.add_item(discord.ui.TextDisplay(
        f"**Message**\n>>> {discord.utils.escape_markdown(finding['text'])[:600]}"))
    if not handled:
        card.add_item(discord.ui.ActionRow(
            TakeoverTimeoutButton(int(finding["user_id"])),
            TakeoverDismissButton(int(finding["user_id"])),
        ))
        card.add_item(discord.ui.TextDisplay(
            "-# ⏳ times them out for an hour so the damage stops while you check - it is "
            "reversible and does not remove them. Reach the person another way before banning."))
    else:
        card.add_item(discord.ui.TextDisplay("✅ **Dealt with.**"))
    view.add_item(card)
    return view


async def _get_channel(client: Any, channel_id: int) -> Any:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


async def report_coordinated(client: Any, finding: dict[str, Any]) -> None:
    channel = await _get_channel(client, CHANNELS.POLICE_STATION)
    if channel is None:
        return
    state = _load_state()
    state.setdefault("coord", {})[finding["digest"]] = {
        "finding": finding, "handled": [], "at": int(time.time())}
    _save_state(state)
    try:
        await channel.send(view=build_coordinated_view(finding),
                           allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.exception("could not post the coordinated-message report")


async def report_takeover(client: Any, finding: dict[str, Any],
                          profile: dict[str, Any]) -> None:
    channel = await _get_channel(client, CHANNELS.POLICE_STATION)
    if channel is None:
        return
    state = _load_state()
    state.setdefault("takeover", {})[str(finding["user_id"])] = {
        "finding": finding, "profile": profile, "handled": False, "at": int(time.time())}
    _save_state(state)
    try:
        await channel.send(view=build_takeover_view(finding, profile),
                           allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.exception("could not post the takeover report")


# --- staff actions ---------------------------------------------------------------------
def _staff_only(user: Any) -> bool:
    return is_staff(user)


async def _apply(guild: Any, ids: list[str], action: str, actor: Any,
                 hours: int = 1) -> tuple[list[str], list[str]]:
    from datetime import timedelta
    done, failed = [], []
    reason = f"behaviour-watch {action} · authorised by {getattr(actor, 'id', '?')}"
    for uid in ids:
        try:
            if action == "ban":
                await guild.ban(discord.Object(id=int(uid)), reason=reason[:500],
                                delete_message_seconds=0)
            else:
                member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
                await member.timeout(discord.utils.utcnow() + timedelta(hours=hours),
                                     reason=reason[:500])
            done.append(uid)
        except Exception:
            log.exception("behaviour-watch %s failed for %s", action, uid)
            failed.append(uid)
    return done, failed


class _CoordAction(discord.ui.DynamicItem[discord.ui.Button], template=r"$"):
    """Shared behaviour for the coordinated-report buttons."""

    action = "timeout"

    def __init__(self, digest: str = "", count: int = 0, label: str = "", emoji: str = "",
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 prefix: str = ""):
        self.digest = str(digest)
        super().__init__(discord.ui.Button(
            label=label.format(n=count) if count else label,
            emoji=emoji, style=style, custom_id=f"{prefix}:{self.digest}"))

    async def _run(self, interaction: discord.Interaction, action: str) -> None:
        if not _staff_only(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        state = _load_state()
        entry = (state.get("coord") or {}).get(self.digest)
        if not entry:
            await interaction.followup.send("That report has expired.", ephemeral=True)
            return
        handled = set(entry.get("handled", []))
        ids = [a["user_id"] for a in entry["finding"]["authors"] if a["user_id"] not in handled]
        if not ids:
            await interaction.followup.send("Nothing left to act on.", ephemeral=True)
            return
        done, failed = await _apply(interaction.guild, ids, action, interaction.user)
        entry["handled"] = sorted(handled | set(done))
        _save_state(state)
        await interaction.message.edit(
            view=build_coordinated_view(entry["finding"], entry["handled"]),
            allowed_mentions=discord.AllowedMentions.none())
        note = f"{'🔨 Banned' if action == 'ban' else '⏳ Timed out'} {len(done)} account(s)."
        if failed:
            note += f" Failed for {len(failed)}."
        await interaction.followup.send(note, ephemeral=True)


class CoordTimeoutButton(_CoordAction, template=r"bw:coordto:(?P<d>\w+)"):
    def __init__(self, digest: str = "", count: int = 0):
        super().__init__(digest, count, "Time out {n}", "⏳",
                         discord.ButtonStyle.primary, "bw:coordto")

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["d"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._run(interaction, "timeout")


class CoordBanButton(_CoordAction, template=r"bw:coordban:(?P<d>\w+)"):
    def __init__(self, digest: str = "", count: int = 0):
        super().__init__(digest, count, "Ban {n}", "🔨",
                         discord.ButtonStyle.danger, "bw:coordban")

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["d"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._run(interaction, "ban")


class CoordDismissButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r"bw:coorddis:(?P<d>\w+)"):
    def __init__(self, digest: str = ""):
        self.digest = str(digest)
        super().__init__(discord.ui.Button(
            label="Not a raid", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"bw:coorddis:{self.digest}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["d"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _staff_only(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        state = _load_state()
        entry = (state.get("coord") or {}).get(self.digest)
        if entry:
            entry["handled"] = [a["user_id"] for a in entry["finding"]["authors"]]
            _save_state(state)
            await interaction.response.edit_message(
                view=build_coordinated_view(entry["finding"], entry["handled"]),
                allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message("That report has expired.", ephemeral=True)


class TakeoverTimeoutButton(discord.ui.DynamicItem[discord.ui.Button],
                            template=r"bw:tkto:(?P<uid>\d+)"):
    def __init__(self, user_id: int = 0):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label="Time out 1h", emoji="⏳", style=discord.ButtonStyle.primary,
            custom_id=f"bw:tkto:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _staff_only(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        done, failed = await _apply(interaction.guild, [str(self.user_id)], "timeout",
                                    interaction.user)
        state = _load_state()
        entry = (state.get("takeover") or {}).get(str(self.user_id))
        if entry:
            entry["handled"] = True
            _save_state(state)
            await interaction.message.edit(
                view=build_takeover_view(entry["finding"], entry["profile"], handled=True),
                allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(
            "⏳ Timed out for an hour. Try to reach them another way - if it is a stolen "
            "account they will want to know." if done else "Couldn't time them out.",
            ephemeral=True)


class TakeoverDismissButton(discord.ui.DynamicItem[discord.ui.Button],
                            template=r"bw:tkdis:(?P<uid>\d+)"):
    def __init__(self, user_id: int = 0):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label="They're fine", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"bw:tkdis:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _staff_only(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        state = _load_state()
        entry = (state.get("takeover") or {}).get(str(self.user_id))
        if entry:
            entry["handled"] = True
            _save_state(state)
            await interaction.response.edit_message(
                view=build_takeover_view(entry["finding"], entry["profile"], handled=True),
                allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message("That report has expired.", ephemeral=True)


# --- the hot path ----------------------------------------------------------------------
async def observe_message(client: Any, message: Any) -> None:
    """on_message hook for both detectors. Never raises; never blocks the message."""
    try:
        if message.guild is None or message.author is None:
            return
        if getattr(message.author, "bot", False) or is_staff(message.author):
            return
        content = message.content or ""
        if not content.strip():
            return

        uid = message.author.id
        name = getattr(message.author, "display_name", None) or str(uid)
        profile = record_message(uid, has_external_link(content))

        finding = check_coordinated(uid, content, message.channel.id,
                                    getattr(message, "jump_url", ""), name,
                                    established=is_established(profile))
        if finding:
            await report_coordinated(client, finding)

        taken = check_takeover(uid, content, message.channel.id, profile, name)
        if taken:
            await report_takeover(client, taken, profile)
    except Exception:
        log.exception("behaviour watch failed for message %s", getattr(message, "id", "?"))
