"""Bank Job's posts: one message per crew, in the channel the host opened the game in (#casino if
the bot can't post there), posted when it opens and edited as people sign up and as the job goes
in, gets its alarm and opens the vault. The result is posted afresh at the bottom of the channel
and the first message shrinks to a line pointing to it; a crew that breaks up before it goes in is
just edited.

The picture says it all (heist_card); the line above it says who, with mentions that ping nobody.
Each message has its own queue so edits land in order and no closer together than EDIT_GAP, and a
burst of steps (everyone legging it at once) collapses into the latest state.
"""

import asyncio
import io
import logging
import time
from dataclasses import dataclass

import discord

from lib.activities import heist, heist_card

log = logging.getLogger(__name__)

EDIT_GAP = 2.5
IMAGE = "heist.png"
NO_FILES_RETRY = 600
GOLD, WIN, GREY = 0xFFC93C, 0x23A55A, 0x6B7280
ENDED = ("over", "closed", "lapsed")


@dataclass
class Post:
    rid: str
    message_id: int | None
    state: dict | None = None
    event: str = ""
    dirty: bool = False
    task: asyncio.Task | None = None
    edited: float = 0.0
    channel: int | None = None          # where it was posted


_posts: dict[str, Post] = {}
_no_files_until: dict[int, float] = {}     # channel -> when to try pictures there again


def on_event(event: str, room: dict) -> None:
    """A step in a crew's job (heist.listeners)."""
    rid = room.get("id")
    if not rid:
        return
    post = _posts.get(rid)
    if post is None:
        found = heist.post_for(rid)
        post = _posts[rid] = Post(rid, found[1] if found else None, channel=found[0] if found else None)
    post.state, post.event = room, event
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
    from lib.activities.casino import base
    import config
    guild = base.CLIENT.get_guild(config.GUILD_ID) if base.CLIENT is not None else None
    member = guild.get_member(int(uid)) if guild else None
    return getattr(member, "display_name", None) or "Someone"


def _and(uids) -> str:
    who = [f"<@{u}>" for u in uids]
    return who[0] if len(who) == 1 else ", ".join(who[:-1]) + f" and {who[-1]}"


async def _faces(uids) -> dict:
    """Everyone's profile picture, for the post's picture (missing ones are left out)."""
    from lib.activities import avatars
    from lib.activities.casino import base
    uids = [u for u in dict.fromkeys(uids) if u is not None]
    got = await asyncio.gather(*(avatars.get(base.CLIENT, u) for u in uids))
    return {u: data for u, data in zip(uids, got) if data}


def headline(room: dict, event: str) -> str:
    """The line above the picture."""
    stake = int(room.get("stake") or 0)
    for_what = f" · **{stake:,} UKP** buy-in" if stake else " · a practice job"
    host = room["host"]
    t = heist.TARGETS.get(room.get("target"), heist.TARGETS["post"])
    if event in ("open", "seats"):
        seated = len(room["players"])
        return f"<@{host}> is getting a crew together for the **{t['name']}**{for_what} · {seated} of {heist.SEATS} in"
    if event == "closed":
        return f"<@{host}> called off their **Bank Job**"
    if event == "lapsed":
        return f"<@{host}>'s **Bank Job** never went in"
    if event != "over":
        many = len(room["players"]) > 1
        what = {"start": "making the plan", "job": "on the job", "report": "setting off the alarm",
                "vault": "in the vault", "out": "in the vault"}.get(event, "on the job")
        return f"{_and(room['players'])} {'are' if many else 'is'} {what} at the **{t['name']}**"
    results = room.get("results") or {}
    out = [int(u) for u, r in results.items() if r["how"] != "nicked"]
    nicked = [int(u) for u, r in results.items() if r["how"] == "nicked"]
    took = sum(r["payout"] for r in results.values())
    money = f" with **{took:,} UKP**" if stake and took else ""
    if not nicked:
        return f"{_and(out)} robbed the **{t['name']}** and got away{money}"
    if not out:
        return f"{_and(nicked)} got nicked at the **{t['name']}**"
    return f"{_and(out)} got away from the **{t['name']}**{money} · {_and(nicked)} got nicked"


def finished_view(room: dict) -> discord.ui.LayoutView:
    """The first post once the game's over: one line, the result having gone below it."""
    v = discord.ui.LayoutView(timeout=None)
    v.add_item(discord.ui.TextDisplay(f"{_and(room['players'])} did a **Bank Job** · how it went is below"))
    return v


def view(room: dict, event: str, image: str | None) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    line = headline(room, event)
    if image:
        v.add_item(discord.ui.TextDisplay(line))
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{image}")
        v.add_item(gallery)
    else:
        colour = WIN if event == "over" else GREY if event in ENDED else GOLD
        box = discord.ui.Container(accent_colour=discord.Colour(colour))
        box.add_item(discord.ui.TextDisplay(line))
        v.add_item(box)
    row = discord.ui.ActionRow()
    open_seats = event in ("open", "seats") and len(room["players"]) < heist.SEATS
    if open_seats:                      # just the one button: two side by side read as two ways to join
        row.add_item(discord.ui.Button(label="Join", style=discord.ButtonStyle.success,
                                       custom_id=f"ukplace:heist:{room['id']}"))
    else:
        row.add_item(discord.ui.Button(label="Play Bank Job",
                                       style=discord.ButtonStyle.success if event in ENDED else discord.ButtonStyle.secondary,
                                       custom_id="ukplace:play:heist"))
    v.add_item(row)
    return v


async def _send(post: Post, ch: int, room: dict, event: str, png: bytes | None) -> None:
    """Post or edit in ``ch``: with the picture if the app can attach files there, else as text."""
    from lib.activities import launcher

    async def go(with_picture: bool):
        v = view(room, event, IMAGE if with_picture else None)
        files = [discord.File(io.BytesIO(png), filename=IMAGE)] if with_picture else None
        if post.message_id is None:
            mid = await launcher.post_view(ch, v, files=files)
            if mid is not None:
                post.message_id, post.channel = mid, ch
                heist.remember_post(post.rid, mid, ch)
        else:
            await launcher.edit_view(ch, post.message_id, v, files=files)

    picture = png is not None and time.time() >= _no_files_until.get(ch, 0)
    try:
        await go(picture)
    except discord.Forbidden:
        if not picture:
            raise
        _no_files_until[ch] = time.time() + NO_FILES_RETRY
        log.warning("the activities app can't attach files in %s; posting the Bank Job as text", ch)
        await go(False)


async def _flush(post: Post) -> None:
    while post.dirty:
        wait = post.edited + EDIT_GAP - time.time()
        if wait > 0 and post.message_id is not None:
            await asyncio.sleep(wait)
        post.dirty = False
        post.edited = time.time()
        room, event = post.state, post.event
        # a post is edited where it is; a new one goes in the channel the game was opened in,
        # or #casino if the bot can't post there
        places = [post.channel or _casino()] if post.message_id is not None else [room.get("ch"), _casino()]
        places = [c for c in dict.fromkeys(places) if c]
        if not places:
            return
        if event == "over" and post.message_id is not None:
            # the result goes at the bottom of the channel, where people will see it; the first
            # post, likely far up by now, becomes a line pointing down to it
            from lib.activities import launcher
            try:
                await launcher.edit_view(places[0], post.message_id, finished_view(room), files=None)
            except Exception:
                log.warning("couldn't shorten the Bank Job post %s", post.rid, exc_info=True)
            post.message_id = None
            places = [c for c in dict.fromkeys([places[0], _casino()]) if c]
        png = None
        if any(time.time() >= _no_files_until.get(c, 0) for c in places):
            png = await heist_card.png(room, {u: _name(u) for u in room["players"]}, event, await _faces(room["players"]))
        for i, ch in enumerate(places):
            try:
                await _send(post, ch, room, event, png)
                break
            except (discord.Forbidden, discord.NotFound):
                if i + 1 < len(places):
                    log.info("can't post Bank Job %s in %s; posting it in #casino instead", post.rid, ch)
                    continue
                log.warning("couldn't post Bank Job %s in %s", post.rid, ch, exc_info=True)
            except Exception:
                log.warning("couldn't post Bank Job %s in %s", post.rid, ch, exc_info=True)
                break
    if post.event in ENDED and not post.dirty:
        _posts.pop(post.rid, None)
