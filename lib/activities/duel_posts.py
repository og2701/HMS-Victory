"""Broadside's posts: one message per challenge, in the channel the challenger opened the game
in (#casino if the bot can't post there), posted when it goes up and edited as it's taken,
played round by round and won (or as it lapses, is withdrawn or is turned down).

The picture says it all (duel_card); the line above it says who, with mentions that only ping
the person a challenge is addressed to. Each message has its own queue so edits land in order
and no closer together than EDIT_GAP, and a burst of rounds collapses into the latest state.
Matches against HMS Victory are practice and stay off the channel.
"""

import asyncio
import io
import logging
import time
from dataclasses import dataclass

import discord

from lib.activities import duel, duel_card

log = logging.getLogger(__name__)

EDIT_GAP = 2.5
IMAGE = "broadside.png"
NO_FILES_RETRY = 600
GOLD, WIN, GREY = 0xFFC93C, 0x23A55A, 0x6B7280
ENDED = ("over", "lapsed", "withdrawn", "declined")


@dataclass
class Post:
    cid: str
    message_id: int | None
    state: dict | None = None
    dirty: bool = False
    task: asyncio.Task | None = None
    edited: float = 0.0
    channel: int | None = None          # where it was posted
    ping: int | None = None


_posts: dict[str, Post] = {}
_no_files_until: dict[int, float] = {}     # channel -> when to try pictures there again


def _people(s: dict) -> list:
    return [s.get(k) for k in ("from", "to", "a", "b") if s.get(k) is not None]


def on_event(event: str, obj: dict) -> None:
    """A step in a challenge or match (duel.listeners). Practice against the bot isn't posted."""
    if duel.BOT in _people(obj):
        return
    cid = obj.get("cid") if "a" in obj else obj.get("id")
    if not cid:
        return
    post = _posts.get(cid)
    if post is None:
        found = duel.post_for(cid)
        post = _posts[cid] = Post(cid, found[1] if found else None, channel=found[0] if found else None)
    post.state = {"event": event, **obj}
    if event == "challenge" and obj.get("to"):
        post.ping = int(obj["to"])
    post.dirty = True
    if post.task is None or post.task.done():
        try:
            post.task = asyncio.get_running_loop().create_task(_flush(post))
        except RuntimeError:            # no loop (tests, scripts): nowhere to post
            pass


def _casino() -> int | None:
    from lib.activities.casino import sessions
    return sessions._channel()


def _name(uid) -> str:
    if uid == duel.BOT:
        return duel.BOT_NAME
    from lib.activities.casino import base
    import config
    guild = base.CLIENT.get_guild(config.GUILD_ID) if base.CLIENT is not None else None
    member = guild.get_member(int(uid)) if guild else None
    return getattr(member, "display_name", None) or "Someone"


async def _faces(uids) -> dict:
    """Everyone's profile picture, for the post's picture (missing ones are left out)."""
    from lib.activities import avatars
    from lib.activities.casino import base
    uids = [u for u in dict.fromkeys(uids) if u is not None]
    got = await asyncio.gather(*(avatars.get(base.CLIENT, u) for u in uids))
    return {u: data for u, data in zip(uids, got) if data}


def headline(s: dict) -> str:
    """The line above the picture."""
    e = s["event"]
    stake = int(s.get("stake") or 0)
    for_what = f" for **{stake:,} UKP**" if stake else " · a friendly"
    if e == "challenge":
        if s.get("to"):
            return f"<@{s['to']}>, <@{s['from']}> challenges you to **Broadside**{for_what}"
        return f"<@{s['from']}> challenges anyone to **Broadside**{for_what} · first to accept plays"
    if e == "lapsed":
        return (f"<@{s['to']}> didn't take <@{s['from']}>'s **Broadside** challenge" if s.get("to")
                else f"<@{s['from']}>'s **Broadside** challenge lapsed · nobody took it")
    if e == "withdrawn":
        return f"<@{s['from']}> withdrew their **Broadside** challenge"
    if e == "declined":
        return f"<@{s['to']}> turned down <@{s['from']}>'s **Broadside** challenge"
    a, b = s["a"], s["b"]
    if e != "over":
        return f"<@{a}> v <@{b}> · **Broadside** · in progress"
    rounds = s.get("rounds", [])
    wa = sum(1 for r in rounds if r["w"] == "a")
    wb = sum(1 for r in rounds if r["w"] == "b")
    if s.get("winner") in ("a", "b"):
        w, l, sw, sl = (a, b, wa, wb) if s["winner"] == "a" else (b, a, wb, wa)
        line = f"<@{w}> beat <@{l}> at **Broadside** {sw} v {sl}"
        if s.get("how") == "left":
            line = f"<@{w}> beat <@{l}> at **Broadside** · <@{l}> left the fight"
        elif s.get("how") == "forfeit":
            line = f"<@{w}> beat <@{l}> at **Broadside** · <@{l}> struck their colours"
        return line + (f" and took **{int(s.get('payout', 0)):,} UKP**" if stake else "")
    if s.get("how") == "void":
        return f"<@{a}> v <@{b}> at **Broadside** · nobody gave an order" + (" · stakes returned" if stake else "")
    return f"<@{a}> and <@{b}> drew at **Broadside**" + (" · stakes returned" if stake else "")


def view(s: dict, image: str | None) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    line = headline(s)
    if image:
        v.add_item(discord.ui.TextDisplay(line))
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{image}")
        v.add_item(gallery)
    else:
        colour = GOLD if s["event"] in ("challenge", "start", "round") else WIN if s["event"] == "over" else GREY
        box = discord.ui.Container(accent_colour=discord.Colour(colour))
        box.add_item(discord.ui.TextDisplay(line))
        v.add_item(box)
    row = discord.ui.ActionRow()
    if s["event"] == "challenge":
        row.add_item(discord.ui.Button(label="Accept", style=discord.ButtonStyle.success, custom_id=f"ukplace:duel:{s['id']}"))
        row.add_item(discord.ui.Button(label="Play Broadside", style=discord.ButtonStyle.secondary, custom_id="ukplace:play:duel"))
    else:
        row.add_item(discord.ui.Button(label="Play Broadside",
                                       style=discord.ButtonStyle.success if s["event"] in ENDED else discord.ButtonStyle.secondary,
                                       custom_id="ukplace:play:duel"))
    v.add_item(row)
    return v


async def _send(post: Post, ch: int, s: dict, png: bytes | None) -> None:
    """Post or edit in ``ch``: with the picture if the app can attach files there, else as text."""
    from lib.activities import launcher

    async def go(with_picture: bool):
        v = view(s, IMAGE if with_picture else None)
        files = [discord.File(io.BytesIO(png), filename=IMAGE)] if with_picture else None
        if post.message_id is None:
            mid = await launcher.post_view(ch, v, files=files, ping=post.ping)
            if mid is not None:
                post.message_id, post.channel = mid, ch
                duel.remember_post(post.cid, mid, ch)
        else:
            await launcher.edit_view(ch, post.message_id, v, files=files)

    picture = png is not None and time.time() >= _no_files_until.get(ch, 0)
    try:
        await go(picture)
    except discord.Forbidden:
        if not picture:
            raise
        _no_files_until[ch] = time.time() + NO_FILES_RETRY
        log.warning("the activities app can't attach files in %s; posting Broadside as text", ch)
        await go(False)


async def _flush(post: Post) -> None:
    while post.dirty:
        wait = post.edited + EDIT_GAP - time.time()
        if wait > 0 and post.message_id is not None:
            await asyncio.sleep(wait)
        post.dirty = False
        post.edited = time.time()
        s = post.state
        # a post is edited where it is; a new one goes in the channel the game was opened in,
        # or #casino if the bot can't post there
        places = [post.channel or _casino()] if post.message_id is not None else [s.get("ch"), _casino()]
        places = [c for c in dict.fromkeys(places) if c]
        if not places:
            return
        png = None
        if any(time.time() >= _no_files_until.get(c, 0) for c in places):
            png = await duel_card.png(s, {u: _name(u) for u in _people(s)}, await _faces(u for u in _people(s) if u != duel.BOT))
        for i, ch in enumerate(places):
            try:
                await _send(post, ch, s, png)
                break
            except (discord.Forbidden, discord.NotFound):
                if i + 1 < len(places):
                    log.info("can't post Broadside %s in %s; posting it in #casino instead", post.cid, ch)
                    continue
                log.warning("couldn't post Broadside %s in %s", post.cid, ch, exc_info=True)
            except Exception:
                log.warning("couldn't post Broadside %s in %s", post.cid, ch, exc_info=True)
                break
    if post.state and post.state["event"] in ENDED and not post.dirty:
        _posts.pop(post.cid, None)
