"""Secret Santa - a one-off event run from a control panel rather than a slash command.

Everything is gated on config.SECRET_SANTA_ENABLED and reads its channel and operators
from the SECRET_SANTA_* block in config.py.

The organisers pick who's in from the control panel (operators only); there's no public
sign-up. Adding someone DMs them a private gifting-details form. Everyone must complete
it before the live draw. Coordinators review the answers in the private event channel;
approved answers are included in the assigned Santa's match DM.

The draw is a single loop (A buys for B, B for C, ... the last person for A), so nobody
gets themselves and no two people have each other.

Test mode posts the pairings and a preview of the DM in the channel and sends nothing.
That's safe for anyone to see, since the live draw reshuffles from scratch. Live mode DMs
every participant their match and never posts the pairings anywhere: they're kept only
in SECRET_SANTA_FILE on the VM. A live draw happens once, and the panel locks after it.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import random
import secrets
from typing import Optional

import discord

import config
from lib.core.file_operations import atomic_write_json, load_json_file

log = logging.getLogger(__name__)

ACCENT = 0xC0392B  # Christmas red
SANTA = "\N{FATHER CHRISTMAS}"
MODE_TEST = "test"
MODE_LIVE = "live"
MIN_PARTICIPANTS = 3  # with two, the loop is just a swap and both know who has them
DM_GAP_SECONDS = 1.0  # between live-draw DMs, so a burst of them doesn't look like spam
MAX_PAIR_LINES = 60   # pairing lines per test-draw message, to stay under Discord's text cap

_draw_lock = asyncio.Lock()
_panel_lock = asyncio.Lock()
_rng = random.SystemRandom()
NO_PINGS = discord.AllowedMentions.none()


# ---------------------------------------------------------------------------
# Config accessors (read live so a test override takes effect)
# ---------------------------------------------------------------------------

def enabled() -> bool:
    return bool(getattr(config, "SECRET_SANTA_ENABLED", False))


def channel_id() -> int:
    return int(getattr(config, "SECRET_SANTA_CHANNEL", 0))


def operator_ids() -> set[int]:
    return {int(x) for x in getattr(config, "SECRET_SANTA_OPERATOR_IDS", set())}


def is_operator(user_id: int) -> bool:
    return int(user_id) in operator_ids()


def _path() -> str:
    return getattr(config, "SECRET_SANTA_FILE", os.path.join(config.JSON_DATA_DIR, "secret_santa.json"))


# ---------------------------------------------------------------------------
# State: one JSON file on the VM (gitignored)
# ---------------------------------------------------------------------------

def _default_state() -> dict:
    return {
        "mode": MODE_TEST,
        "participants": {},       # str(user id) -> {"name", "joined_at", "added_by"}
        "draw": None,             # the live draw, once it has happened
        "panel_message_id": None,
    }


def load_state() -> dict:
    state = _default_state()
    state.update(load_json_file(_path()))
    return state


def save_state(state: dict) -> None:
    atomic_write_json(_path(), state, indent=2)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def participant_ids(state: dict) -> list[int]:
    return [int(u) for u in state["participants"]]


def is_drawn(state: dict) -> bool:
    return bool(state.get("draw"))


def missing_details(state: dict) -> list[int]:
    return [int(uid) for uid, p in state["participants"].items()
            if not all(p.get("details", {}).get(k, "").strip()
                       for k in ("locker", "parcel_name", "limits"))]


class DetailsModal(discord.ui.Modal, title="Secret Santa gifting details"):
    def __init__(self, details: Optional[dict] = None, target_id: Optional[int] = None,
                 revision: Optional[str] = None):
        super().__init__(timeout=600)
        self.target_id = target_id
        self.revision = revision
        details = details or {}
        self.fields = {}
        for key, label, required, limit, placeholder in (
            ("locker", "InPost locker name or address", True, 400, "Your preferred locker"),
            ("parcel_name", "Name on your parcel", True, 100, "Your real name or an alias"),
            ("email", "Email for tracking (optional)", False, 254, "Leave blank if you prefer"),
            ("limits", "Anything you don't want to receive?", True, 700, "Write none if you have no hard limits"),
        ):
            field = discord.ui.TextInput(label=label, required=required, max_length=limit,
                                         default=details.get(key) or None, placeholder=placeholder)
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = load_state()
        key = str(self.target_id or interaction.user.id)
        if not enabled() or key not in state["participants"] or is_drawn(state):
            await interaction.response.send_message("This form is no longer available.", ephemeral=True)
            return
        participant = state["participants"][key]
        if self.target_id is not None and (
                not is_operator(interaction.user.id)
                or participant.get("pending_details", {}).get("revision") != self.revision):
            await interaction.response.send_message("This review has changed or you cannot edit it.", ephemeral=True)
            return
        details = {k: str(field.value).strip() for k, field in self.fields.items()}
        if not all(details[k] for k in ("locker", "parcel_name", "limits")):
            await interaction.response.send_message("Please complete the locker, parcel name and hard limits fields.", ephemeral=True)
            return
        existing = participant.get("pending_details", {}).get("answers") or participant.get("details")
        if existing is not None and details == {k: str(existing.get(k, "")).strip() for k in self.fields}:
            status = "Your details are still awaiting review." if participant.get("pending_details") else "Your details remain approved."
            await interaction.response.send_message("Nothing changed. " + status, ephemeral=True)
            return
        revision = secrets.token_hex(8)
        participant.pop("details", None)
        participant["pending_details"] = {"revision": revision, "answers": details,
                                           "submitted_at": _now(), "submitted_by": interaction.user.id}
        save_state(state)
        await interaction.response.defer(ephemeral=True, thinking=True)
        ch = await _channel(interaction.client, channel_id())
        try:
            if ch is None:
                raise RuntimeError("review channel unavailable")
            await ch.send(view=DetailsReviewView(int(key), revision, details), allowed_mentions=NO_PINGS)
        except (discord.HTTPException, RuntimeError):
            await interaction.followup.send(
                "Your answers are pending, but I couldn't post the review in the coordinator channel. "
                "Please ask a coordinator to press Review pending details.", ephemeral=True)
        else:
            await interaction.followup.send(
                "Submitted for coordinator review! Once approved, your Santa will receive these details "
                "when names are drawn. You can use the form again to update them until then.", ephemeral=True)
        await refresh_messages(interaction.client)


class DetailsButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ss:details"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="Enter or edit gifting details",
                                           style=discord.ButtonStyle.primary, custom_id="ss:details"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        state = load_state()
        participant = state["participants"].get(str(interaction.user.id))
        if not enabled() or participant is None or is_drawn(state):
            await interaction.response.send_message("This form is no longer available.", ephemeral=True)
            return
        details = participant.get("pending_details", {}).get("answers") or participant.get("details")
        await interaction.response.send_modal(DetailsModal(details))


def _details_text(details: dict) -> str:
    safe = {k: discord.utils.escape_markdown(discord.utils.escape_mentions(str(v)))
            for k, v in details.items()}
    return (f"📦 **InPost locker:** {safe.get('locker', '')}\n"
            f"🏷️ **Parcel name:** {safe.get('parcel_name', '')}\n"
            f"✉️ **Email:** {safe.get('email') or 'Not provided'}\n"
            f"🚫 **Hard limits:** {safe.get('limits', '')}")


class DetailsReviewButton(discord.ui.DynamicItem[discord.ui.Button],
                          template=r"ss:review:(?P<action>edit|approve):(?P<uid>[0-9]+):(?P<revision>[a-f0-9]+)"):
    def __init__(self, action: str, uid: int, revision: str):
        self.action, self.uid, self.revision = action, uid, revision
        super().__init__(discord.ui.Button(
            label="Looks good" if action == "approve" else "Edit",
            style=discord.ButtonStyle.success if action == "approve" else discord.ButtonStyle.secondary,
            custom_id=f"ss:review:{action}:{uid}:{revision}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["uid"]), match["revision"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not enabled() or not is_operator(interaction.user.id) or interaction.channel_id != channel_id():
            await interaction.response.send_message("Only coordinators can review details in the Secret Santa channel.", ephemeral=True)
            return
        state = load_state()
        participant = state["participants"].get(str(self.uid), {})
        pending = participant.get("pending_details", {})
        if is_drawn(state) or pending.get("revision") != self.revision:
            await interaction.response.send_message("This review is no longer current.", ephemeral=True)
            return
        if self.action == "edit":
            await interaction.response.send_modal(DetailsModal(pending["answers"], self.uid, self.revision))
            return
        participant["details"] = pending["answers"]
        participant["details_approved_by"] = interaction.user.id
        participant["details_approved_at"] = _now()
        del participant["pending_details"]
        save_state(state)
        await interaction.response.edit_message(
            view=DetailsReviewView(self.uid, self.revision, participant["details"], approved=True),
            allowed_mentions=NO_PINGS)
        await refresh_messages(interaction.client)


class DetailsReviewView(discord.ui.LayoutView):
    def __init__(self, uid: int, revision: str, details: dict, approved: bool = False):
        super().__init__(timeout=None)
        card = discord.ui.Container(accent_colour=ACCENT)
        card.add_item(discord.ui.TextDisplay(
            f"## Gifting details for <@{uid}>\n"
            + ("✅ Approved and saved.\n\n" if approved else "Awaiting coordinator approval.\n\n")
            + _details_text(details)))
        if not approved:
            card.add_item(discord.ui.ActionRow(DetailsReviewButton("edit", uid, revision),
                                               DetailsReviewButton("approve", uid, revision)))
        self.add_item(card)


class DetailsRequestView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        card = discord.ui.Container(accent_colour=ACCENT)
        card.add_item(discord.ui.TextDisplay(
            "## 🎁 Secret Santa - gifting details 🎄\n"
            "You're in the Secret Santa list! Please use the button to provide your preferred "
            "InPost locker, parcel name (a real name or alias), optional email for tracking, "
            "and anything you don't want to receive.\n\n"
            "Find your locker: https://inpost.co.uk/lockers\n\n"
            "These aren't wishlists, just guidelines to avoid unwanted gifts. "
            "Your answers are posted in the private Secret Santa coordinator channel for review. "
            "Once approved, they are saved and shared with your assigned Santa when names are drawn. "
            "You can edit them until the draw. Please don't reply to this DM; use the form.\n\n"
            "Thanks everyone, and happy gifting! 🎅"))
        card.add_item(discord.ui.ActionRow(DetailsButton()))
        self.add_item(card)


async def send_details_requests(client: discord.Client, user_ids: list[int]) -> tuple[int, list[int]]:
    sent, failed = 0, []
    for uid in user_ids:
        state = load_state()
        if (is_drawn(state) or uid not in missing_details(state)
                or state["participants"][str(uid)].get("pending_details")):
            continue
        user = await _user(client, uid)
        delivered = False
        if user is not None:
            try:
                await user.send(view=DetailsRequestView(), allowed_mentions=NO_PINGS)
                delivered = True
            except discord.HTTPException:
                log.info("Secret Santa: details request to %s failed", uid)
        fresh = load_state()
        if str(uid) in fresh["participants"]:
            fresh["participants"][str(uid)]["details_dm"] = "sent" if delivered else "failed"
            save_state(fresh)
        if delivered:
            sent += 1
        else:
            failed.append(uid)
        await asyncio.sleep(DM_GAP_SECONDS)
    return sent, failed


def undelivered(state: dict) -> list[int]:
    """Givers whose match DM hasn't gone through: it failed, or the bot stopped before trying."""
    draw = state.get("draw") or {}
    return [int(g) for g in draw.get("pairs", {}) if draw.get("dms", {}).get(g) != "sent"]


def add_participants(members: list, added_by: Optional[int] = None) -> list[int]:
    """Add members to the hat. Returns the ids that weren't already in it."""
    state = load_state()
    added = []
    for m in members:
        key = str(m.id)
        if key in state["participants"] or getattr(m, "bot", False):
            continue
        state["participants"][key] = {"name": getattr(m, "display_name", None) or str(m),
                                      "joined_at": _now(), "added_by": added_by}
        added.append(int(m.id))
    if added:
        save_state(state)
    return added


def remove_participants(user_ids: list[int]) -> list[int]:
    state = load_state()
    removed = [int(u) for u in user_ids if state["participants"].pop(str(int(u)), None) is not None]
    if removed:
        save_state(state)
    return removed


# ---------------------------------------------------------------------------
# The draw
# ---------------------------------------------------------------------------

def draw_loop(user_ids: list[int], rng: Optional[random.Random] = None) -> dict[int, int]:
    """giver -> receiver, as one loop through everyone."""
    order = list(dict.fromkeys(int(u) for u in user_ids))
    if len(order) < MIN_PARTICIPANTS:
        raise ValueError(f"need at least {MIN_PARTICIPANTS} people, have {len(order)}")
    (rng or _rng).shuffle(order)
    return {order[i]: order[(i + 1) % len(order)] for i in range(len(order))}


async def _user(client: discord.Client, user_id: int):
    u = client.get_user(user_id)
    if u is None:
        try:
            u = await client.fetch_user(user_id)
        except discord.HTTPException:
            return None
    return u


def _guild(client: discord.Client) -> Optional[discord.Guild]:
    return client.get_guild(int(getattr(config, "GUILD_ID", 0)))


def _person(client: discord.Client, user_id: int):
    """The member if they're still cached in the server, so the DM can show their server
    name and avatar; otherwise whatever the bot knows about them."""
    guild = _guild(client)
    return (guild.get_member(user_id) if guild else None) or client.get_user(user_id)


async def send_match_dms(client: discord.Client) -> tuple[int, list[int]]:
    """DM every giver whose match hasn't been delivered yet. Returns (sent now, still undelivered)."""
    async with _draw_lock:
        state = load_state()
        if not is_drawn(state):
            return 0, []
        sent = 0
        for giver in undelivered(state):
            receiver = int(state["draw"]["pairs"][str(giver)])
            name = state["participants"].get(str(receiver), {}).get("name", "someone")
            status = "failed"
            user = await _user(client, giver)
            if user is not None:
                try:
                    details = state["participants"].get(str(receiver), {}).get("details")
                    await user.send(view=MatchDMView(receiver, _person(client, receiver), name, details),
                                    allowed_mentions=NO_PINGS)
                    status = "sent"
                    sent += 1
                except discord.HTTPException as e:
                    log.info("Secret Santa: DM to %s failed: %s", giver, e)
            # Re-read before saving: the panel may have been re-posted while that DM went out.
            fresh = load_state()
            fresh["draw"].setdefault("dms", {})[str(giver)] = status
            save_state(fresh)
            await asyncio.sleep(DM_GAP_SECONDS)
        return sent, undelivered(load_state())


async def run_live_draw(client: discord.Client, by: int) -> tuple[int, list[int]]:
    """Draw for real, store it, then DM everyone. The pairings are saved before the first DM
    goes out, so a crash halfway through loses nothing: Retry picks up where it stopped."""
    async with _draw_lock:
        state = load_state()
        if is_drawn(state):
            raise RuntimeError("names have already been drawn")
        if state["mode"] != MODE_LIVE:
            raise RuntimeError("the panel is in test mode")
        if missing_details(state):
            raise RuntimeError("everyone must have approved gifting details before the live draw")
        pairs = draw_loop(participant_ids(state))
        state["draw"] = {"drawn_at": _now(), "drawn_by": int(by),
                         "pairs": {str(g): str(r) for g, r in pairs.items()}, "dms": {}}
        save_state(state)
        log.info("Secret Santa: live draw of %d people by %s", len(pairs), by)
    return await send_match_dms(client)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _match_container(receiver_id: int, person, name: str, details: Optional[dict] = None) -> discord.ui.Container:
    display = getattr(person, "display_name", None) or name
    handle = f" (@{person.name})" if person is not None and getattr(person, "name", None) else ""
    text = (f"## {SANTA} Secret Santa\n"
            f"You're buying a present for **{display}**{handle}: <@{receiver_id}>\n\n"
            "Keep it secret! The fun is that they don't know it's you.\n\n"
            "📦 **Post your parcel by 15-20 December at the latest.** Sending it earlier is fine. "
            "Once it arrives at a locker, they have 3 days to collect it.\n\n"
            "Please reach out to Oggers or Kaizo if you have *any* problems. This is all about fun and "
            "we don't want anyone to feel any stress over it. We'd rather know and sort out an "
            "alternative than anyone ends up with nothing!")
    card = discord.ui.Container(accent_colour=ACCENT)
    avatar = getattr(getattr(person, "display_avatar", None), "url", None)
    if avatar:
        card.add_item(discord.ui.Section(discord.ui.TextDisplay(text),
                                         accessory=discord.ui.Thumbnail(avatar)))
    else:
        card.add_item(discord.ui.TextDisplay(text))
    if details:
        card.add_item(discord.ui.TextDisplay(
            "### Gifting details\n" + _details_text(details) + "\n\n"
            "Please keep these details private and use them only for this gift."))
    return card


class MatchDMView(discord.ui.LayoutView):
    """The DM a giver gets: who they're buying for."""

    def __init__(self, receiver_id: int, person=None, name: str = "someone", details: Optional[dict] = None):
        super().__init__(timeout=None)
        self.add_item(_match_container(receiver_id, person, name, details))


def _test_draw_views(client: discord.Client, state: dict, pairs: dict[int, int]) -> list[discord.ui.LayoutView]:
    lines = [f"<@{g}> → <@{r}>" for g, r in pairs.items()]
    chunks = [lines[i:i + MAX_PAIR_LINES] for i in range(0, len(lines), MAX_PAIR_LINES)]
    views = []
    for i, chunk in enumerate(chunks):
        view = discord.ui.LayoutView(timeout=None)
        card = discord.ui.Container(accent_colour=0x7F8C8D)
        head = (f"## \N{TEST TUBE} Test draw - nothing was sent\n"
                f"{len(pairs)} people, one loop. Giver → who they buy for:\n") if i == 0 else ""
        card.add_item(discord.ui.TextDisplay(head + "\n".join(chunk)))
        view.add_item(card)
        views.append(view)
    giver, receiver = next(iter(pairs.items()))
    name = state["participants"].get(str(receiver), {}).get("name", "someone")
    person = _person(client, receiver)
    preview = discord.ui.LayoutView(timeout=None)
    preview.add_item(discord.ui.TextDisplay(
        f"-# The DM <@{giver}> would get. The live draw reshuffles from scratch, so none of these pairings carry over."))
    preview.add_item(_match_container(receiver, person, name,
        {"locker": "Example locker", "parcel_name": "Example name", "email": "", "limits": "Example limits"}))
    views.append(preview)
    return views


def _participant_line(state: dict, limit: int = 120) -> str:
    ids = participant_ids(state)
    if not ids:
        return "nobody yet"
    shown = ", ".join(f"<@{u}>" for u in ids[:limit])
    return shown + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


def panel_text(state: dict) -> str:
    live = state["mode"] == MODE_LIVE
    mode = ("\N{LARGE RED CIRCLE} **Live** - drawing DMs everyone their match"
            if live else "\N{TEST TUBE} **Test** - draws post here and nothing is sent")
    lines = [f"## {SANTA} Secret Santa - control panel",
             f"**Mode:** {mode}",
             f"**In the hat ({len(state['participants'])}):** {_participant_line(state)}"]
    missing = missing_details(state)
    lines.append(f"**Gifting details approved:** {len(state['participants']) - len(missing)}/{len(state['participants'])}")
    pending = [int(uid) for uid, p in state["participants"].items() if p.get("pending_details")]
    if pending and not is_drawn(state):
        lines.append("**Awaiting review:** " + ", ".join(f"<@{u}>" for u in pending[:60]))
    if missing and not is_drawn(state):
        lines.append("**Awaiting details:** " + ", ".join(f"<@{u}>" for u in missing if u not in pending)[:1500])
    failed_requests = [uid for uid, p in state["participants"].items() if p.get("details_dm") == "failed"]
    if failed_requests and not is_drawn(state):
        lines.append("**Request DM failed:** " + ", ".join(f"<@{u}>" for u in failed_requests[:60])
                     + "\n-# Ask them to open DMs, then press Request missing details.")
    draw = state.get("draw")
    if draw:
        when = datetime.datetime.fromisoformat(draw["drawn_at"])
        missing = undelivered(state)
        delivered = len(draw["pairs"]) - len(missing)
        lines.append(f"**Drawn** <t:{int(when.timestamp())}:f> by <@{draw['drawn_by']}>. "
                     f"{delivered}/{len(draw['pairs'])} DMs delivered.")
        if missing:
            lines.append("**No DM yet:** " + ", ".join(f"<@{u}>" for u in missing)
                         + "\n-# Ask them to open their DMs, then press Retry.")
        lines.append("-# Who has who is kept on the VM only, never shown here.")
    return "\n".join(lines)


class ControlButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ss:ctl:(?P<action>[a-z_]+)"):
    """A control panel button. Operators only; the handler is looked up by name at press time."""

    def __init__(self, action: str, label: str = "", style=discord.ButtonStyle.secondary,
                 emoji=None, disabled: bool = False):
        self.action = action
        super().__init__(discord.ui.Button(label=label or action, style=style, emoji=emoji,
                                           disabled=disabled, custom_id=f"ss:ctl:{action}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not enabled():
            await interaction.response.send_message("Secret Santa is switched off.", ephemeral=True)
            return
        if not is_operator(interaction.user.id):
            await interaction.response.send_message("Only the Secret Santa coordinators can use this panel.", ephemeral=True)
            return
        handler = PANEL_ACTIONS.get(self.action)
        if handler is None:
            await interaction.response.send_message("Unknown action.", ephemeral=True)
            return
        await handler(interaction)


class ControlPanelView(discord.ui.LayoutView):
    def __init__(self, state: Optional[dict] = None):
        super().__init__(timeout=None)
        state = state or load_state()
        drawn = is_drawn(state)
        live = state["mode"] == MODE_LIVE
        card = discord.ui.Container(accent_colour=ACCENT)
        card.add_item(discord.ui.TextDisplay(panel_text(state)))
        card.add_item(discord.ui.ActionRow(
            ControlButton("add", "Add people", discord.ButtonStyle.success, "\N{HEAVY PLUS SIGN}", disabled=drawn),
            ControlButton("remove", "Remove people", emoji="\N{HEAVY MINUS SIGN}", disabled=drawn),
            ControlButton("refresh", "Refresh", emoji="\N{ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS}"),
        ))
        draw_btn = (ControlButton("draw", "Draw and DM everyone", discord.ButtonStyle.danger, SANTA, disabled=drawn)
                    if live else ControlButton("draw", "Test draw", discord.ButtonStyle.primary, "\N{TEST TUBE}"))
        card.add_item(discord.ui.ActionRow(
            ControlButton("mode", "Switch to test" if live else "Switch to live",
                          emoji="\N{TEST TUBE}" if live else "\N{LARGE RED CIRCLE}", disabled=drawn),
            draw_btn,
        ))
        card.add_item(discord.ui.ActionRow(
            ControlButton("details", "Request missing details", emoji="✉️", disabled=drawn),
            ControlButton("review", "Review pending details", emoji="📋", disabled=drawn)))
        if drawn and undelivered(state):
            card.add_item(discord.ui.ActionRow(
                ControlButton("retry", "Retry failed DMs", discord.ButtonStyle.primary, "\N{ENVELOPE}")))
        self.add_item(card)


# ---------------------------------------------------------------------------
# Posting and refreshing the two messages
# ---------------------------------------------------------------------------

async def _channel(client: discord.Client, cid: int):
    if not cid:
        return None
    ch = client.get_channel(cid)
    if ch is None:
        try:
            ch = await client.fetch_channel(cid)
        except discord.HTTPException:
            return None
    return ch


async def _upsert(client: discord.Client, key: str, cid: int, view: discord.ui.LayoutView) -> None:
    """Edit the stored message in place, or post a fresh one if it's gone."""
    ch = await _channel(client, cid)
    if ch is None:
        log.warning("Secret Santa: channel %s not found", cid)
        return
    state = load_state()
    mid = state.get(key)
    if mid:
        try:
            msg = await ch.fetch_message(int(mid))
            await msg.edit(view=view)
            return
        except discord.HTTPException:
            pass
    msg = await ch.send(view=view, allowed_mentions=NO_PINGS)
    state = load_state()
    state[key] = msg.id
    save_state(state)
    log.info("Secret Santa: posted %s %s in %s", key, msg.id, ch.id)


async def refresh_messages(client: discord.Client) -> None:
    async with _panel_lock:
        await _upsert(client, "panel_message_id", channel_id(), ControlPanelView())


async def ensure_panel(client: discord.Client) -> None:
    """On ready: post the control panel once, then keep editing it."""
    if not enabled():
        return
    try:
        await refresh_messages(client)
    except Exception:
        log.exception("Secret Santa: could not ensure the panel")


# ---------------------------------------------------------------------------
# Control panel handlers
# ---------------------------------------------------------------------------
# Every state change below is a load-modify-save with no await in the middle, so two
# presses can't interleave inside one; only the DM sends and message edits await.

async def _act_mode(interaction: discord.Interaction) -> None:
    state = load_state()
    if not is_drawn(state):
        state["mode"] = MODE_TEST if state["mode"] == MODE_LIVE else MODE_LIVE
        save_state(state)
    await interaction.response.edit_message(view=ControlPanelView())


async def _act_refresh(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    await refresh_messages(interaction.client)


class _PeopleSelect(discord.ui.UserSelect):
    def __init__(self, adding: bool):
        super().__init__(placeholder="Pick people to add" if adding else "Pick people to remove",
                         min_values=1, max_values=25)
        self.adding = adding

    async def callback(self, interaction: discord.Interaction) -> None:
        if not enabled() or not is_operator(interaction.user.id):
            await interaction.response.send_message("Only the Secret Santa coordinators can use this panel.", ephemeral=True)
            return
        if is_drawn(load_state()):
            await interaction.response.edit_message(content="Names have already been drawn.", view=None)
            return
        if self.adding:
            changed = add_participants(self.values, added_by=interaction.user.id)
        else:
            changed = remove_participants([u.id for u in self.values])
        verb = "Added" if self.adding else "Removed"
        names = ", ".join(f"<@{u}>" for u in changed) or "nobody new"
        await interaction.response.edit_message(content=f"{verb} {names}.", view=None,
                                                allowed_mentions=NO_PINGS)
        if self.adding and changed:
            sent, failed = await send_details_requests(interaction.client, changed)
            await interaction.edit_original_response(
                content=f"{verb} {names}. {sent} gifting-details requests delivered."
                + (" Could not DM: " + ", ".join(f"<@{u}>" for u in failed) if failed else ""),
                allowed_mentions=NO_PINGS)
        await refresh_messages(interaction.client)


class _PeoplePicker(discord.ui.View):
    def __init__(self, adding: bool):
        super().__init__(timeout=300)
        self.add_item(_PeopleSelect(adding))


async def _act_add(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Who should go in the hat? Each new person gets a gifting-details form by DM, "
        "including in test mode. Up to 25 at a time.",
        view=_PeoplePicker(True), ephemeral=True)


async def _act_remove(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Who should come out of the hat?", view=_PeoplePicker(False), ephemeral=True)


class _LiveDrawConfirm(discord.ui.View):
    def __init__(self, count: int):
        super().__init__(timeout=120)
        self.go.label = f"Draw and DM all {count}"

    @discord.ui.button(label="Draw and DM everyone", style=discord.ButtonStyle.danger, emoji=SANTA)
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not enabled() or not is_operator(interaction.user.id):
            await interaction.response.send_message("Only the Secret Santa coordinators can draw.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Drawing names and sending DMs...", view=None)
        try:
            sent, missing = await run_live_draw(interaction.client, interaction.user.id)
        except (RuntimeError, ValueError) as e:
            await interaction.edit_original_response(content=f"Didn't draw: {e}.")
            return
        msg = f"Done. {sent} DMs delivered."
        if missing:
            msg += f" {len(missing)} didn't go through, and they're listed on the panel with a Retry button."
        await interaction.edit_original_response(content=msg)
        await refresh_messages(interaction.client)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled. Nothing was sent.", view=None)


async def _act_draw(interaction: discord.Interaction) -> None:
    state = load_state()
    n = len(state["participants"])
    if is_drawn(state):
        await interaction.response.send_message("Names have already been drawn.", ephemeral=True)
        return
    if n < MIN_PARTICIPANTS:
        await interaction.response.send_message(
            f"Need at least {MIN_PARTICIPANTS} people in the hat to draw (there are {n}).", ephemeral=True)
        return
    if state["mode"] == MODE_LIVE:
        if missing_details(state):
            await interaction.response.send_message(
                "Everyone must have approved gifting details first. Request missing details or review pending submissions.",
                ephemeral=True)
            return
        await interaction.response.send_message(
            f"This draws for real: all {n} people get a DM with their match, "
            "and it can't be undone or redrawn. Go?",
            view=_LiveDrawConfirm(n), ephemeral=True)
        return
    await interaction.response.defer()
    pairs = draw_loop(participant_ids(state))
    for view in _test_draw_views(interaction.client, state, pairs):
        await interaction.channel.send(view=view, allowed_mentions=NO_PINGS)


async def _act_retry(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    sent, missing = await send_match_dms(interaction.client)
    await interaction.followup.send(
        f"{sent} delivered this time."
        + (f" Still not through: {len(missing)}." if missing else " Everyone has their match now."),
        ephemeral=True)
    await refresh_messages(interaction.client)


async def _act_details(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    state = load_state()
    if is_drawn(state):
        await interaction.followup.send("Names have already been drawn.", ephemeral=True)
        return
    sent, failed = await send_details_requests(interaction.client, missing_details(state))
    await interaction.followup.send(
        f"{sent} gifting-details requests delivered."
        + (" Could not DM: " + ", ".join(f"<@{u}>" for u in failed) if failed else ""),
        ephemeral=True, allowed_mentions=NO_PINGS)
    await refresh_messages(interaction.client)


async def _act_review(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    state = load_state()
    if is_drawn(state):
        await interaction.followup.send("Names have already been drawn.", ephemeral=True)
        return
    ch = await _channel(interaction.client, channel_id())
    if ch is None:
        await interaction.followup.send("The coordinator channel is unavailable.", ephemeral=True)
        return
    count = 0
    for uid, participant in state["participants"].items():
        pending = participant.get("pending_details")
        if pending:
            await ch.send(view=DetailsReviewView(int(uid), pending["revision"], pending["answers"]),
                          allowed_mentions=NO_PINGS)
            count += 1
    await interaction.followup.send(f"Posted {count} pending reviews.", ephemeral=True)


PANEL_ACTIONS = {
    "review": _act_review,
    "details": _act_details,
    "mode": _act_mode,
    "refresh": _act_refresh,
    "add": _act_add,
    "remove": _act_remove,
    "draw": _act_draw,
    "retry": _act_retry,
}
