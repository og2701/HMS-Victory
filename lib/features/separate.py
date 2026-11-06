"""/separate: keep two members out of each other's voice calls, without telling either.

Border Force pairs two members up. From then on, whichever of the pair is in a voice call
(anything but the main stage) hides that one channel from the other: a member overwrite
denying View Channel and Connect on that channel alone. If A moves call, the block moves
with them; when A leaves voice or goes to the stage, it comes off. It works both ways, so
if they're in two different calls each one's channel is hidden from the other, and nobody
is disconnected for being in a different call.

The only disconnect is when both end up in the *same* channel (they were already together
when it was turned on, or they joined in the same instant). Then whoever got there most
recently is quietly dropped from the call and the block goes on.

How it stays correct: every change works out the blocks that *should* exist from the
pairs and the live voice states, and diffs that against the blocks Vic has put on. So a
missed event, a restart or a toggle-off all settle the same way. Each block remembers the
member's own overwrite on that channel from before (if they had one) and puts it back
exactly when the block lifts; otherwise Vic's overwrite is just deleted.

State lives in data/json/separations.json. Nothing is ever sent to either member: no DM, no
role, no message. The only trace is the audit log reason ("separate"), which is staff-only.
"""

import asyncio
import logging
import os
import time

import discord

import config
from lib.core.file_operations import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(config.JSON_DATA_DIR, "separations.json")
EXEMPT_CHANNEL_ID = 1133795002531709088  # the main stage: being there blocks nobody
AUDIT_REASON = "separate"
MEMBER_OVERWRITE = 1  # Discord's overwrite type for a member (0 is a role)

_lock = asyncio.Lock()
# member id -> monotonic time they last joined/moved into a channel, from voice events.
# Only used to settle "both in the same channel": the most recent arrival is dropped.
_join_times: dict[int, float] = {}


# ---------------------------------------------------------------- state

def _empty_state() -> dict:
    return {"pairs": [], "blocks": []}


def load_state() -> dict:
    try:
        state = load_json_file(STATE_FILE) or {}
    except Exception:
        logger.exception("[SEPARATE] could not read %s; starting empty", STATE_FILE)
        state = {}
    state.setdefault("pairs", [])
    state.setdefault("blocks", [])
    return state


def save_state(state: dict) -> None:
    atomic_write_json(STATE_FILE, state, indent=2)


def pair_key(a: int, b: int) -> tuple[int, int]:
    a, b = int(a), int(b)
    return (a, b) if a < b else (b, a)


def find_pair(state: dict, a: int, b: int):
    key = pair_key(a, b)
    for pair in state["pairs"]:
        if pair_key(pair["a"], pair["b"]) == key:
            return pair
    return None


def separated_ids(state: dict) -> set[int]:
    ids = set()
    for pair in state["pairs"]:
        ids.add(int(pair["a"]))
        ids.add(int(pair["b"]))
    return ids


def _find_block(state: dict, channel_id: int, member_id: int):
    for block in state["blocks"]:
        if int(block["channel"]) == channel_id and int(block["member"]) == member_id:
            return block
    return None


# ---------------------------------------------------------------- the pure part

def counted_channel(channel_id):
    """The channel id that counts for blocking, or None (not in voice / on the stage)."""
    if channel_id is None or int(channel_id) == EXEMPT_CHANNEL_ID:
        return None
    return int(channel_id)


def plan(pairs, voice: dict, stored_blocks, join_times: dict):
    """Work out what should happen, without touching Discord.

    pairs: [{"a": id, "b": id}, ...]
    voice: member id -> channel id they're in (absent or None = not in voice)
    stored_blocks: the blocks Vic has on now, [{"channel": id, "member": id, ...}]
    join_times: member id -> when they last joined a channel (bigger = more recent)

    Returns (disconnect, desired): the member ids to drop from their call, and the set of
    (channel_id, member_id) blocks that should exist once they've gone.
    """
    where = {int(m): counted_channel(c) for m, c in voice.items()}
    stored = {(int(b["channel"]), int(b["member"])) for b in stored_blocks}

    disconnect = []
    for pair in pairs:
        a, b = int(pair["a"]), int(pair["b"])
        ch_a, ch_b = where.get(a), where.get(b)
        if ch_a is None or ch_a != ch_b:
            continue
        # Both in the same call: drop whoever got there last.
        if (ch_a, b) in stored and (ch_a, a) not in stored:
            loser = b  # b was already blocked from here, so a was there first
        elif (ch_a, a) in stored and (ch_a, b) not in stored:
            loser = a
        elif a in join_times and b in join_times:
            loser = a if join_times[a] > join_times[b] else b
        elif a in join_times or b in join_times:
            # Only one arrival has been processed: the other's voice update is still on its
            # way (a same-instant race), so the first one processed keeps the call.
            loser = b if a in join_times else a
        else:
            loser = b  # no way to tell (e.g. both there before the bot started): the second named
        disconnect.append(loser)
        where[loser] = None

    desired = set()
    for pair in pairs:
        a, b = int(pair["a"]), int(pair["b"])
        if where.get(a) is not None:
            desired.add((where[a], b))
        if where.get(b) is not None:
            desired.add((where[b], a))
    return disconnect, desired


# ---------------------------------------------------------------- Discord side

def _existing_overwrite(channel, member_id: int):
    """The member's own overwrite on this channel as (allow, deny) ints, or None."""
    for target, ow in channel.overwrites.items():
        if getattr(target, "id", None) == member_id and not isinstance(target, discord.Role):
            allow, deny = ow.pair()
            return [allow.value, deny.value]
    return None


async def _edit_overwrite(channel, member_id: int, allow: int, deny: int):
    # The raw route takes a bare id, so it works whether or not the member is cached
    # (or still in the server, when a block has to come off after they left).
    await channel._state.http.edit_channel_permissions(
        channel.id, member_id, str(allow), str(deny), MEMBER_OVERWRITE, reason=AUDIT_REASON)


async def _delete_overwrite(channel, member_id: int):
    await channel._state.http.delete_channel_permissions(channel.id, member_id, reason=AUDIT_REASON)


async def _apply_block(channel, member_id: int) -> dict | None:
    previous = _existing_overwrite(channel, member_id)
    allow, deny = previous if previous else (0, 0)
    blocked = discord.Permissions.view_channel.flag | discord.Permissions.connect.flag
    allow &= ~blocked
    deny |= blocked
    try:
        await _edit_overwrite(channel, member_id, allow, deny)
    except discord.HTTPException:
        logger.exception("[SEPARATE] could not block %s from %s", member_id, channel.id)
        return None
    return {"channel": channel.id, "member": member_id, "previous": previous}


async def _lift_block(guild, block: dict) -> bool:
    channel = guild.get_channel(int(block["channel"]))
    if channel is None:
        return True  # channel's gone, and the overwrite with it
    member_id = int(block["member"])
    previous = block.get("previous")
    try:
        if previous:
            await _edit_overwrite(channel, member_id, int(previous[0]), int(previous[1]))
        else:
            await _delete_overwrite(channel, member_id)
    except discord.NotFound:
        return True
    except discord.HTTPException:
        logger.exception("[SEPARATE] could not lift the block on %s in %s", member_id, channel.id)
        return False
    return True


def _voice_map(guild, member_ids) -> dict:
    voice = {}
    for mid in member_ids:
        member = guild.get_member(mid)
        if member is not None and member.voice is not None and member.voice.channel is not None:
            voice[mid] = member.voice.channel.id
    return voice


async def _sync_locked(guild) -> None:
    state = load_state()
    ids = separated_ids(state)
    voice = _voice_map(guild, ids)
    disconnect, desired = plan(state["pairs"], voice, state["blocks"], _join_times)

    for mid in disconnect:
        member = guild.get_member(mid)
        if member is None or member.voice is None:
            continue
        try:
            await member.move_to(None, reason=AUDIT_REASON)
        except discord.HTTPException:
            logger.exception("[SEPARATE] could not disconnect %s", mid)

    # Blocks that should come off: lift them first, so a member moving between calls never
    # holds two at once for longer than it takes.
    kept = []
    for block in state["blocks"]:
        key = (int(block["channel"]), int(block["member"]))
        if key in desired:
            kept.append(block)
            continue
        if not await _lift_block(guild, block):
            kept.append(block)  # try again next time
    state["blocks"] = kept
    save_state(state)

    have = {(int(b["channel"]), int(b["member"])) for b in state["blocks"]}
    for channel_id, member_id in sorted(desired - have):
        channel = guild.get_channel(channel_id)
        if channel is None:
            continue
        block = await _apply_block(channel, member_id)
        if block is not None:
            state["blocks"].append(block)
            save_state(state)


async def sync(guild) -> None:
    async with _lock:
        await _sync_locked(guild)


# ---------------------------------------------------------------- hooks

async def on_voice_state_update(member, before, after) -> None:
    """Called from the bot's voice handler. Cheap when the member isn't in a pair."""
    try:
        if before.channel == after.channel:
            return
        if member.id not in separated_ids(load_state()):
            return
        if after.channel is not None:
            _join_times[member.id] = time.monotonic()
        await sync(member.guild)
    except Exception:
        logger.exception("[SEPARATE] voice update for %s failed", getattr(member, "id", "?"))


async def reconcile(client) -> None:
    """On startup: put on or take off whatever the current voice states call for."""
    guild = client.get_guild(config.GUILD_ID)
    if guild is None:
        return
    try:
        state = load_state()
        if state["pairs"] or state["blocks"]:
            await sync(guild)
    except Exception:
        logger.exception("[SEPARATE] startup reconcile failed")


async def toggle(guild, a: int, b: int, actor_id: int) -> bool:
    """Separate a and b, or stop if they already are. Returns True if now separated."""
    async with _lock:
        state = load_state()
        pair = find_pair(state, a, b)
        if pair is not None:
            state["pairs"].remove(pair)
            now_on = False
        else:
            state["pairs"].append({"a": int(a), "b": int(b), "by": int(actor_id), "at": int(time.time())})
            now_on = True
        save_state(state)
        await _sync_locked(guild)
        return now_on


# ---------------------------------------------------------------- the command

def can_use(user) -> bool:
    if getattr(user, "id", None) == config.USERS.OGGERS:
        return True
    return any(r.id == config.ROLES.BORDER_FORCE for r in getattr(user, "roles", []))


def _describe(guild, uid: int) -> str:
    member = guild.get_member(uid) if guild else None
    return f"<@{uid}>" + (f" ({member})" if member else f" (`{uid}`, not in the server)")


def list_text(guild) -> str:
    state = load_state()
    if not state["pairs"]:
        return "No members are separated right now."
    lines = ["**Separated pairs**"]
    for pair in state["pairs"]:
        a, b = int(pair["a"]), int(pair["b"])
        active = [blk for blk in state["blocks"] if int(blk["member"]) in (a, b)]
        line = f"- {_describe(guild, a)} and {_describe(guild, b)}, by <@{pair.get('by')}>"
        if pair.get("at"):
            line += f" <t:{int(pair['at'])}:R>"
        for blk in active:
            line += f"\n  - <@{blk['member']}> kept out of <#{blk['channel']}>"
        lines.append(line)
    return "\n".join(lines)


async def handle_separate_command(interaction, user_one=None, user_two=None, action: str = "toggle"):
    if not can_use(interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    no_mentions = discord.AllowedMentions.none()
    if action == "list" or (user_one is None and user_two is None):
        await interaction.response.send_message(list_text(interaction.guild), ephemeral=True,
                                                allowed_mentions=no_mentions)
        return
    if user_one is None or user_two is None:
        await interaction.response.send_message("Pick two members to separate.", ephemeral=True)
        return
    if user_one.id == user_two.id:
        await interaction.response.send_message("Those are the same person.", ephemeral=True)
        return
    if user_one.bot or user_two.bot:
        await interaction.response.send_message("Bots can't be separated.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    now_on = await toggle(interaction.guild, user_one.id, user_two.id, interaction.user.id)
    if now_on:
        msg = (f"Separated {_describe(interaction.guild, user_one.id)} and {_describe(interaction.guild, user_two.id)}. "
               "Whichever of them is in a call (not the stage), that channel is hidden from the other. "
               "Neither is told. Run it again on the same two to stop.")
    else:
        msg = (f"Stopped separating {_describe(interaction.guild, user_one.id)} and "
               f"{_describe(interaction.guild, user_two.id)}. Any channels hidden from them are back.")
    await interaction.followup.send(msg, ephemeral=True, allowed_mentions=no_mentions)
