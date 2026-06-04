"""HMS Victory - Fruit Machine (vs-the-house slots).

A one-shot spin: stake the bet, spin three weighted reels, and get paid from a tuned
paytable (RTP ~= 0.97, so the house keeps ~3% over time). Unlike blackjack / higher-
lower this resolves in a single interaction, so there's no in-flight state to persist -
a "Spin Again" button re-spins in-session (it just dies after a restart, like a finished
hand's Play Again). A busy flag drops double-clicks during the render.

Economy (UKP conserved; bank is the house): stake -> bank via remove_bb; a winning spin
pays mult x bet from the bank via add_bb(taxable=False). A losing spin keeps the stake.
The win is credited only after the result message is on screen; a failed render/send
refunds the stake (nothing was credited yet).
"""

import asyncio
import io
import html as _html
import logging
import random
import uuid

import discord
from discord import Interaction

from lib.economy.economy_manager import get_bb, add_bb, remove_bb, UKPenceManager
from lib.economy.casino_stats import record_result, session_footer_html
from lib.core.file_operations import read_html_template

logger = logging.getLogger(__name__)

# (key, emoji, reel weight). Rarer symbols pay more. Weights sum to 29 per reel.
REEL = [
    ("crown", "👑", 1),
    ("union", "🇬🇧", 2),
    ("lion", "🦁", 3),
    ("rose", "🌹", 4),
    ("anchor", "⚓", 5),
    ("pound", "💷", 6),
    ("cherry", "🍒", 8),
]
EMOJI = {k: e for k, e, _ in REEL}
NAME = {"crown": "Crowns", "union": "Union Jacks", "lion": "Lions", "rose": "Roses",
        "anchor": "Anchors", "pound": "Pound Notes", "cherry": "Cherries"}
_KEYS = [k for k, _, _ in REEL]
_WEIGHTS = [w for _, _, w in REEL]

# Three-of-a-kind payouts (x bet). Two cherries pays a frequent small consolation.
# Sized for the locked 800k economy (max bet 10k -> top jackpot 150k, not millions).
# RTP ~= 0.97 (house edge ~3%); monotonic by symbol rarity.
THREE_OF_A_KIND = {"crown": 20, "union": 16, "lion": 13, "rose": 10,
                   "anchor": 9, "pound": 8, "cherry": 7}
TWO_CHERRY = 4


def spin_reels() -> list:
    return random.choices(_KEYS, weights=_WEIGHTS, k=3)


def evaluate(reels: list) -> int:
    """Return the payout multiplier (x bet) for a spin. 0 = no win."""
    a, b, c = reels
    if a == b == c:
        return THREE_OF_A_KIND[a]
    if reels.count("cherry") == 2:
        return TWO_CHERRY
    return 0


def _result_label(reels: list, mult: int) -> tuple:
    """(headline, css class) for the result banner."""
    if mult <= 0:
        return "No Win", "lose"
    a, b, c = reels
    if a == b == c:
        return ("Jackpot!" if a == "crown" else f"Three {NAME[a]}!"), ("jackpot" if a == "crown" else "win")
    return "Two Cherries!", "win"


# ---------------------------------------------------------------------------
# Machine (one message; holds the latest spin for re-rendering)
# ---------------------------------------------------------------------------
class SlotMachine:
    def __init__(self, player_id, player_name, channel_id, bet):
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.bet = int(bet)
        self.message_id = None
        self.spin_id = uuid.uuid4().hex[:12]  # unique custom_id namespace for this machine
        self.reels = ["cherry", "cherry", "cherry"]
        self.prev_reels = ["cherry", "cherry", "cherry"]
        self.mult = 0
        self.win = 0
        self.net = 0
        self.busy = False
        self.lock = asyncio.Lock()
        # Session counter across this machine's Spin Again chain.
        self.session_count = 1
        self.session_net = 0

    def do_spin(self):
        self.prev_reels = list(self.reels)
        self.reels = spin_reels()
        self.mult = evaluate(self.reels)
        self.win = self.mult * self.bet
        self.net = self.win - self.bet
        return self.win


# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------
def _credit(uid: int, amount: int, reason: str):
    if amount <= 0:
        return
    if not add_bb(uid, amount, reason=reason, taxable=False):
        logger.critical("Bank insolvent paying %s of %s to %s - minting to honour the win.",
                        reason, amount, uid)
        UKPenceManager.add_amount(uid, amount, reason=f"{reason} [bank insolvent - minted]")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _banner_html(reels, mult, win) -> str:
    head, cls = _result_label(reels, mult)
    if mult > 0:
        sub = f"+{win:,} UKPence  ({mult}x)"
    else:
        sub = "Spin again?"
    return (f'<div class="banner {cls}"><div class="head">{head}</div>'
            f'<div class="sub">{sub}</div></div>')


def build_slots_html(machine: SlotMachine, reels: list = None, mult: int = None, win: int = None, spinning: bool = False) -> str:
    if reels is None:
        reels = machine.reels
    if mult is None:
        mult = machine.mult
    if win is None:
        win = machine.win

    template = read_html_template("templates/slots.html")
    cells = "".join(f'<div class="reel"><span class="sym">{EMOJI[k]}</span></div>' for k in reels)

    if spinning:
        win_glow = ""
        banner = '<div class="banner lose"><div class="head">Spinning...</div><div class="sub">Good luck!</div></div>'
    else:
        win_glow = " winline" if mult > 0 else ""
        banner = _banner_html(reels, mult, win)

    bal = get_bb(machine.player_id)
    return (
        template
        .replace("{{PLAYER_NAME}}", _html.escape(str(machine.player_name)[:24]) or "Player")
        .replace("{{REELS}}", cells)
        .replace("{{WINLINE}}", win_glow)
        .replace("{{BANNER}}", banner)
        .replace("{{BET}}", f"{machine.bet:,}")
        .replace("{{BALANCE}}", f"{bal:,}")
        .replace("{{SESSION}}", session_footer_html(
            machine.player_id, session_count=getattr(machine, "session_count", 1),
            session_net=getattr(machine, "session_net", 0),
            current_net=getattr(machine, "net", 0), over=not spinning))
    )


# ---------------------------------------------------------------------------
# Sliding-reel result animation (pre-generated; one GIF per 7x7x7 = 343 outcome)
# ---------------------------------------------------------------------------
# Each reel is a tall vertical strip that scrolls DOWNWARD and decelerates to land on
# its symbol, reels stopping left-to-right. The picture has no player/bet/balance/win
# text baked in (that's dynamic), so it depends only on the 3 final symbols -> 343
# variants that can be rendered once offline (scripts/bake_slots_results.py) and then
# served instantly at spin time. Until baked, the runtime falls back to rendering one.
SLIDE_TEMPLATE = "templates/slots_spin.html"
SLIDE_SIZE = (820, 1040)         # viewport; the screenshot clips to the .cabinet element
SLIDE_REEL_H = 230               # must match .reel / .strip .sym height in the template
SLIDE_LEADIN = 20                # random symbols scrolled past before the target lands
SLIDE_STOPS = (11, 15, 19)       # frame index at which reels 1, 2, 3 lock onto target
SLIDE_HOLD = 2                   # extra held frames after the last reel locks
SLIDE_FRAME_MS = 55              # per spin-frame duration
SLIDE_FINAL_MS = 1800            # last frame lingers (GIF plays once via loop=None)


def _ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


def _slide_durations(n_frames: int) -> list:
    return [SLIDE_FRAME_MS] * (n_frames - 1) + [SLIDE_FINAL_MS]


def build_slots_slide_frames(reels: list, *, seed: int = 0) -> list:
    """Frames for a downward reel-slide that lands on ``reels`` (left-to-right stop).

    Each reel's strip = [target, then SLIDE_LEADIN random symbols]; the strip starts
    scrolled up by the lead-in and eases back to 0 so the target slides in from above.
    Deterministic per ``seed`` so a re-bake of the same outcome is identical.
    """
    import random as _random
    rng = _random.Random(seed)
    strips = []
    for target in reels:
        strips.append([target] + [rng.choice(_KEYS) for _ in range(SLIDE_LEADIN)])

    template = read_html_template(SLIDE_TEMPLATE)
    travel = SLIDE_LEADIN * SLIDE_REEL_H
    n = SLIDE_STOPS[2] + SLIDE_HOLD
    is_win = evaluate(reels) > 0

    frames = []
    for f in range(n + 1):
        reels_html = []
        all_locked = True
        for r in range(3):
            stop = SLIDE_STOPS[r]
            p = 1.0 if f >= stop else (f / stop)
            if p < 1.0:
                all_locked = False
            offset = -(1 - _ease_out_cubic(p)) * travel      # -travel .. 0 (lands on target)
            cells = "".join(f'<div class="sym">{EMOJI[k]}</div>' for k in strips[r])
            # `top:` (a layout property) rather than transform: the fast frame-sequence
            # renderer swaps frames via innerHTML and reuses the page; a transform makes
            # a compositor layer it screenshots stale, but a top change forces a reflow
            # it captures correctly - so the whole 343-GIF bake stays fast.
            reels_html.append(
                f'<div class="reel"><div class="strip" '
                f'style="top:{offset:.1f}px">{cells}</div></div>'
            )
        winline = " winline" if (all_locked and is_win) else ""
        frames.append(template.replace("{{WINLINE}}", winline).replace("{{REELS}}", "".join(reels_html)))
    return frames


async def render_slots_result_gif(reels: list, *, seed: int) -> io.BytesIO:
    """Render the reel-slide as an animated GIF (plays once, holds the final frame).

    Uses the fast frame-sequence renderer (one page load, innerHTML swaps): correct here
    because the frames position reels with `top:` (a reflow), not a compositor transform.
    """
    from lib.core.image_processing import screenshot_html_sequence
    frames = build_slots_slide_frames(reels, seed=seed)
    return await screenshot_html_sequence(
        frames, size=SLIDE_SIZE, element_selector=".cabinet",
        durations=_slide_durations(len(frames)), loop=None,
    )


def results_dir() -> str:
    import config
    import os
    return os.path.join(config.DATA_DIR, "slots_results")


def _result_gif_path(reels: list) -> str:
    import os
    return os.path.join(results_dir(), f"{reels[0]}_{reels[1]}_{reels[2]}.gif")


async def render_slots_slide_gif(machine: SlotMachine) -> io.BytesIO:
    """Runtime fallback: render the slide GIF for this spin (used only when the
    pre-generated file is missing)."""
    return await render_slots_result_gif(machine.reels, seed=random.randint(0, 9999))


def _result_text(machine: SlotMachine, *, show_reels: bool = False) -> str:
    """The dynamic result text shown in the Components V2 message under the reel GIF
    (player/bet/balance/win - none of which can live in the pre-baked image). With
    ``show_reels`` it also prints the symbol row, for the no-image native fallback."""
    head, _ = _result_label(machine.reels, machine.mult)
    lines = []
    if show_reels:
        row = "  ".join(EMOJI[k] for k in machine.reels)
        lines.append("## 🎰 HMS Victory Fruit Machine")
        lines.append(f"### ┃ {row} ┃")
    if machine.mult > 0:
        lines.append(f"## 🎉 {head}")
        lines.append(f"**+{machine.win:,} UKPence**  ·  {machine.mult}x")
    else:
        lines.append(f"## {head}")
        lines.append("Better luck next spin.")
    lines.append(f"-# {machine.player_name} · Bet {machine.bet:,} · Balance {get_bb(machine.player_id):,} UKPence")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Components V2 view
# ---------------------------------------------------------------------------
ACCENT = discord.Colour(0xD4AF37)  # brass


def _action_row(machine: SlotMachine) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    again = discord.ui.Button(
        label="Spin Again", emoji="🎰", style=discord.ButtonStyle.success,
        custom_id=f"slots:{machine.spin_id}:spin",
    )
    again.callback = _make_cb(machine, "spin")
    row.add_item(again)

    change = discord.ui.Button(
        label="Change Bet", emoji="✏️", style=discord.ButtonStyle.secondary,
        custom_id=f"slots:{machine.spin_id}:changebet",
    )
    change.callback = _make_cb(machine, "changebet")
    row.add_item(change)

    rules = discord.ui.Button(
        label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
        custom_id=f"slots:{machine.spin_id}:rules",
    )
    rules.callback = _make_cb(machine, "rules")
    row.add_item(rules)
    return row


def _action_row_disabled(machine: SlotMachine) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    again = discord.ui.Button(
        label="Spinning...", emoji="🔄", style=discord.ButtonStyle.success,
        custom_id=f"slots:{machine.spin_id}:spin_disabled",
        disabled=True,
    )
    row.add_item(again)

    change = discord.ui.Button(
        label="Change Bet", emoji="✏️", style=discord.ButtonStyle.secondary,
        custom_id=f"slots:{machine.spin_id}:changebet_disabled",
        disabled=True,
    )
    row.add_item(change)

    rules = discord.ui.Button(
        label="Rules", emoji="📖", style=discord.ButtonStyle.secondary,
        custom_id=f"slots:{machine.spin_id}:rules_disabled",
        disabled=True,
    )
    row.add_item(rules)
    return row



def get_random_spinning_gif() -> str:
    import config
    import os
    idx = random.randint(0, 19)
    path = os.path.join(config.DATA_DIR, "slots_spinning", f"spin_{idx}.gif")
    if os.path.exists(path):
        return path
    return None


def build_spin_anim_layout(machine: SlotMachine) -> tuple:
    """The spinning phase: the pre-baked full-size reel-slide GIF (or a native fallback),
    controls disabled while it animates. The GIF already lands on this spin's result; the
    full result frame (with the player's name/bet/balance baked in) swaps in right after."""
    import config
    import os
    view = discord.ui.LayoutView(timeout=None)
    files = []
    used_image = False
    if getattr(config, "SLOTS_IMAGE_ENABLED", True):
        # 1) the pre-baked per-result slide GIF (lands on this spin's reels);
        # 2) else a generic full-size spinner placeholder (e.g. before the 343 are
        #    deployed to the server, or if one is missing) so the spin is always instant.
        gif_path = _result_gif_path(machine.reels)
        if not os.path.exists(gif_path):
            gif_path = get_random_spinning_gif()
        if gif_path and os.path.exists(gif_path):
            try:
                files = [discord.File(gif_path, filename="slots.gif")]
                gallery = discord.ui.MediaGallery()
                gallery.add_item(media="attachment://slots.gif")
                view.add_item(gallery)
                used_image = True
            except Exception:
                logger.warning("Failed to attach slots spin GIF", exc_info=True)
    if not used_image:
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(
            "## 🎰 HMS Victory Fruit Machine\n### ┃ 🔄  🔄  🔄 ┃\n**Spinning...** Good luck!"))
        view.add_item(container)
    view.add_item(_action_row_disabled(machine))
    return view, files


async def build_slots_layout(machine: SlotMachine, client):
    """Result message: the FULL machine rendered with the player's name, bet, balance and
    result banner baked into one image (no separate text panel) - the same look as before
    the pre-gen change. The slide animation already played via build_spin_anim_layout."""
    import config
    view = discord.ui.LayoutView(timeout=None)
    files = []
    used_image = False
    if getattr(config, "SLOTS_IMAGE_ENABLED", True):
        try:
            from lib.core.image_processing import screenshot_html
            html = build_slots_html(machine)  # spinning=False -> result banner + real HUD
            img = await screenshot_html(html, size=(820, 1000), element_selector=".cabinet")
            files = [discord.File(img, filename="slots.png")]
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media="attachment://slots.png")
            view.add_item(gallery)
            used_image = True
        except Exception:
            logger.warning("Slots result render failed; using native layout.", exc_info=True)
    if not used_image:
        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(_result_text(machine, show_reels=True)))
        view.add_item(container)
    view.add_item(_action_row(machine))
    return view, files


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(machine: SlotMachine, action: str):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, machine, action)
    return _cb


async def _show_rules(interaction: Interaction):
    import config
    mn = getattr(config, "SLOTS_MIN_BET", 5)
    mx = getattr(config, "SLOTS_MAX_BET", 100_000)
    pay = "\n".join(f"- {EMOJI[k]}{EMOJI[k]}{EMOJI[k]}  three {NAME[k].lower()} - **{THREE_OF_A_KIND[k]}x**"
                    for k in _KEYS)
    rules = (
        "## 🎰 Fruit Machine - House Rules\n"
        "Stake your bet and spin three reels. Match symbols on the line to win.\n\n"
        f"{pay}\n"
        f"- {EMOJI['cherry']}{EMOJI['cherry']}  any two cherries - **{TWO_CHERRY}x**\n\n"
        "Prizes are multiples of your bet (a `10x` win pays ten times your stake). "
        "The reels are weighted so the house keeps a small edge over time.\n"
        f"- **Bets:** {mn:,} - {mx:,} UKPence. Stakes go to the house bank; wins are paid from it.\n\n"
        "-# Good luck. 🇬🇧"
    )
    await interaction.response.send_message(rules, ephemeral=True)


async def _refresh(interaction: Interaction, machine: SlotMachine, client):
    view, files = await build_slots_layout(machine, client)
    await interaction.edit_original_response(view=view, attachments=files)
    try:
        client.add_view(view, message_id=machine.message_id)
    except Exception:
        logger.debug("add_view after slots refresh failed (non-fatal)", exc_info=True)


async def _handle_action(interaction: Interaction, machine: SlotMachine, action: str):
    if action == "rules":
        await _show_rules(interaction)
        return

    if interaction.user.id != machine.player_id:
        await interaction.response.send_message(
            "This isn't your machine - spin your own with `/slots`.", ephemeral=True
        )
        return

    # Change Bet opens a modal (must be the immediate response, before defer/busy).
    if action == "changebet":
        await interaction.response.send_modal(ChangeBetModal(machine))
        return

    await _do_spin_round(interaction, machine, interaction.client, via_modal=False)


async def _do_spin_round(interaction: Interaction, machine: SlotMachine, client, *, via_modal: bool):
    """Deduct the stake, spin, show the result, then pay any win. Drives both Spin Again
    (button) and Change Bet (modal). via_modal picks the message-edit path."""
    if machine.busy:
        await interaction.response.defer()
        return
    machine.busy = True
    try:
        if getattr(interaction.client, "maintenance_mode", False):
            await interaction.response.send_message(
                "🔧 **Under maintenance** - the bot is restarting. Hold on a minute.", ephemeral=True
            )
            return
        if get_bb(machine.player_id) < machine.bet:
            await interaction.response.send_message(
                f"You need {machine.bet:,} UKPence for that spin.", ephemeral=True
            )
            return
        if not remove_bb(machine.player_id, machine.bet, reason="Slots bet"):
            await interaction.response.send_message("You don't have enough UKPence.", ephemeral=True)
            return

        machine.session_net += machine.net   # fold the previous spin into the session
        machine.session_count += 1
        machine.do_spin()  # decide first so we know which pre-baked result GIF to use

        # Two-phase: the pre-baked slide GIF plays the animation instantly (no
        # placeholder), then the full-size result frame - with this player's name, bet,
        # balance and result banner baked in - is rendered and swapped in.
        await interaction.response.defer()
        spin_view, spin_files = build_spin_anim_layout(machine)
        try:
            if via_modal:
                await interaction.message.edit(view=spin_view, attachments=spin_files)
            else:
                await interaction.edit_original_response(view=spin_view, attachments=spin_files)
        except Exception:
            logger.error("Slots spin animation edit failed.", exc_info=True)

        try:
            view, files = await build_slots_layout(machine, client)
            if via_modal:
                await interaction.message.edit(view=view, attachments=files)
            else:
                await interaction.edit_original_response(view=view, attachments=files)
        except Exception:
            logger.error("Slots result render failed; refunding stake.", exc_info=True)
            _credit(machine.player_id, machine.bet, "Slots stake refund (spin failed)")
            try:
                # Re-enable buttons on failure
                enabled_view = discord.ui.LayoutView(timeout=None)
                enabled_view.add_item(_action_row(machine))
                if via_modal:
                    await interaction.message.edit(view=enabled_view)
                else:
                    await interaction.edit_original_response(view=enabled_view)
            except Exception:
                pass
            return
        _credit(machine.player_id, machine.win, "Slots win")  # paid only once on screen
        record_result(machine.player_id, "slots", machine.bet, machine.bet, machine.win,
                      f"{machine.mult}x" if machine.win else "no win")
        try:
            client.add_view(view, message_id=machine.message_id)
        except Exception:
            logger.debug("slots add_view after spin failed (non-fatal)", exc_info=True)
    finally:
        machine.busy = False


class ChangeBetModal(discord.ui.Modal, title="Fruit Machine - change your bet"):
    def __init__(self, machine: SlotMachine):
        super().__init__()
        self.machine = machine
        self.amount = discord.ui.TextInput(
            label="New bet (UKPence)", placeholder=f"{machine.bet:,}", required=True, max_length=12,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: Interaction):
        import config
        raw = str(self.amount.value).replace(",", "").strip()
        try:
            amount = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a whole number of UKPence.", ephemeral=True
            )
            return
        mn = getattr(config, "SLOTS_MIN_BET", 5)
        mx = getattr(config, "SLOTS_MAX_BET", 10_000)
        if amount < mn or amount > mx:
            await interaction.response.send_message(
                f"Bets must be between {mn:,} and {mx:,} UKPence.", ephemeral=True
            )
            return
        self.machine.bet = amount  # the new stake sticks for subsequent spins too
        await _do_spin_round(interaction, self.machine, interaction.client, via_modal=True)


# ---------------------------------------------------------------------------
# Slash command entry point
# ---------------------------------------------------------------------------
async def handle_slots_command(interaction: Interaction, amount: int):
    import config

    if getattr(interaction.client, "maintenance_mode", False):
        await interaction.response.send_message(
            "🔧 **Under maintenance** - the bot is restarting for an update. "
            "Hold on a minute before spinning.", ephemeral=True
        )
        return
    if not getattr(config, "SLOTS_ENABLED", True):
        await interaction.response.send_message("The fruit machine is switched off.", ephemeral=True)
        return

    mn = getattr(config, "SLOTS_MIN_BET", 5)
    mx = getattr(config, "SLOTS_MAX_BET", 100_000)
    if amount < mn:
        await interaction.response.send_message(f"The minimum bet is {mn:,} UKPence.", ephemeral=True)
        return
    if amount > mx:
        await interaction.response.send_message(f"The maximum bet is {mx:,} UKPence.", ephemeral=True)
        return

    balance = get_bb(interaction.user.id)
    if balance < amount:
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {balance:,}.", ephemeral=True
        )
        return

    if not remove_bb(interaction.user.id, amount, reason="Slots bet"):
        await interaction.response.send_message(
            f"You don't have enough UKPence. Your balance is {get_bb(interaction.user.id):,}.",
            ephemeral=True,
        )
        return

    name = discord.utils.escape_markdown(interaction.user.display_name)
    machine = SlotMachine(interaction.user.id, name, interaction.channel_id, amount)
    machine.do_spin()  # decide first - the pre-baked spin GIF is chosen by the result

    # Phase 1: post the spinning animation (pre-baked full-size slide GIF) - instant.
    try:
        await interaction.response.defer(thinking=True)
        spin_view, spin_files = build_spin_anim_layout(machine)
        msg = await interaction.followup.send(view=spin_view, files=spin_files)
        machine.message_id = msg.id
    except Exception:
        logger.error("Failed to send initial slots message; refunding stake.", exc_info=True)
        _credit(interaction.user.id, amount, "Slots stake refund (failed to send initial message)")
        try:
            await interaction.followup.send(
                "The fruit machine jammed - your stake has been refunded.", ephemeral=True
            )
        except Exception:
            pass
        return

    # Phase 2: swap in the full result frame (this player's name/bet/balance + result).
    try:
        view, files = await build_slots_layout(machine, interaction.client)
        await msg.edit(view=view, attachments=files)
    except Exception:
        logger.error("Slots spin render failed; refunding stake.", exc_info=True)
        _credit(interaction.user.id, amount, "Slots stake refund (spin failed)")
        try:
            enabled_view = discord.ui.LayoutView(timeout=None)
            enabled_view.add_item(_action_row(machine))
            await msg.edit(view=enabled_view)
        except Exception:
            pass
        return

    _credit(interaction.user.id, machine.win, "Slots win")  # pay only after it's on screen
    record_result(machine.player_id, "slots", machine.bet, machine.bet, machine.win,
                  f"{machine.mult}x" if machine.win else "no win")
    try:
        interaction.client.add_view(view, message_id=machine.message_id)
    except Exception:
        logger.debug("slots add_view failed (non-fatal)", exc_info=True)
