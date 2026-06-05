"""HMS Hold'em - multiplayer Texas Hold'em at a shared table (MVP).

One table per channel. Players Sit (buy in from UKPence -> chips held in the bank as escrow),
the table deals hands using the tested engine in lib/economy/poker, and a single public board
message shows the community cards / pot / stacks. Each player taps "View / Act" to see their
own hole cards in an ephemeral, and the player to act gets fold/check/call/raise/all-in there.
A per-turn clock auto-checks-or-folds anyone who stalls. Stacks are checkpointed to escrow so a
restart refunds everyone (voiding any live hand) instead of stranding chips.
"""

import asyncio
import logging

import discord
from discord import Interaction

import config
from lib.economy.economy_manager import get_bb, remove_bb, credit_from_bank
from lib.economy.poker import escrow
from lib.economy.poker.engine import card_str, hand_category
from lib.economy.poker.hand import Hand

logger = logging.getLogger(__name__)

SB = config.POKER_SMALL_BLIND
BB = config.POKER_BIG_BLIND
MIN_BUYIN = config.POKER_MIN_BUYIN
MAX_BUYIN = config.POKER_MAX_BUYIN
MAX_SEATS = config.POKER_MAX_SEATS
TURN = config.POKER_TURN_SECONDS

_TABLES = {}  # channel_id -> Table


def _cards(cards):
    return " ".join(f"`{card_str(c)}`" for c in cards) if cards else ""


def get_table(channel_id):
    return _TABLES.get(channel_id)


class Table:
    def __init__(self, channel, client):
        self.channel = channel
        self.channel_id = channel.id
        self.client = client
        self.seats = []                 # [{id, name, stack}]
        self.button = -1
        self.message = None             # public board
        self.lock = asyncio.Lock()
        self.status = "lobby"           # lobby | playing | between
        self.hand = None
        self._turn_event = None
        self._loop_task = None
        self._next_task = None
        self._prehand = {}

    # --- seat helpers ----------------------------------------------------------
    def seat_of(self, uid):
        return next((s for s in self.seats if s["id"] == uid), None)

    def _checkpoint(self):
        escrow.checkpoint(self.channel_id, {s["id"]: s["stack"] for s in self.seats})

    # --- rendering -------------------------------------------------------------
    def _text(self):
        lines = ["## \U0001f0cf HMS Hold'em"]
        h = self.hand
        if h is None:
            lines.append(f"Blinds **{SB}/{BB}** · buy-in **{MIN_BUYIN:,}-{MAX_BUYIN:,}** UKPence")
            if self.seats:
                lines.append("**Seated**")
                for s in self.seats:
                    lines.append(f"- {s['name']} · {s['stack']:,}")
            else:
                lines.append("No players yet - tap **Sit** to buy in.")
            lines.append(f"-# {len(self.seats)}/{MAX_SEATS} seated · need 2+ to deal")
            return "\n".join(lines)
        actor = h.current_player()
        lines.append(f"**Board** {_cards(h.board) or '_(pre-flop)_'}   ·   **Pot** {h.pot():,}")
        for i, s in enumerate(self.seats):
            sid = s["id"]
            mark = "\U0001f518" if i == h.button else "　"
            tags = []
            if sid in h.folded:
                tags.append("folded")
            elif sid in h.allin:
                tags.append("all-in")
            if sid == actor:
                tags.append("← to act")
            bet = h.committed.get(sid, 0)
            betstr = f" · bet {bet}" if bet else ""
            lines.append(f"{mark} **{s['name']}** {h.stack[sid]:,}{betstr}  {' '.join(tags)}")
        if actor is not None:
            to_call = h.current_bet - h.committed.get(actor, 0)
            lines.append(f"\n→ <@{actor}> to act" + (f" (call {to_call})" if to_call > 0 else " (check)"))
            lines.append(f"-# Tap **View / Act**. Auto-{'check' if to_call == 0 else 'fold'} in {TURN}s.")
        return "\n".join(lines)

    def _view(self):
        v = discord.ui.View(timeout=None)
        if self.status == "playing":
            b = discord.ui.Button(label="View / Act", emoji="\U0001f0cf", style=discord.ButtonStyle.primary)
            b.callback = self._on_act
            v.add_item(b)
        else:
            if len(self.seats) < MAX_SEATS:
                b = discord.ui.Button(label="Sit", emoji="➕", style=discord.ButtonStyle.success)
                b.callback = self._on_sit
                v.add_item(b)
            if len([s for s in self.seats if s["stack"] > 0]) >= 2:
                b = discord.ui.Button(label="Deal", emoji="\U0001f3b4", style=discord.ButtonStyle.primary)
                b.callback = self._on_deal
                v.add_item(b)
            b = discord.ui.Button(label="Leave", style=discord.ButtonStyle.secondary)
            b.callback = self._on_leave
            v.add_item(b)
        return v

    async def render(self):
        try:
            content = self._text()
            view = self._view()
            if self.message is None:
                self.message = await self.channel.send(content=content, view=view)
            else:
                await self.message.edit(content=content, view=view)
        except Exception:
            logger.error("poker render failed", exc_info=True)

    # --- lobby actions ---------------------------------------------------------
    async def _on_sit(self, interaction: Interaction):
        if self.seat_of(interaction.user.id):
            await interaction.response.send_message("You're already seated.", ephemeral=True)
            return
        if len(self.seats) >= MAX_SEATS:
            await interaction.response.send_message("Table's full.", ephemeral=True)
            return
        await interaction.response.send_modal(BuyInModal(self, interaction.user))

    async def add_player(self, interaction, user, amount):
        bal = get_bb(user.id)
        if amount < MIN_BUYIN or amount > MAX_BUYIN:
            await interaction.response.send_message(
                f"Buy-in must be {MIN_BUYIN:,}-{MAX_BUYIN:,} UKPence.", ephemeral=True)
            return
        if amount > bal:
            await interaction.response.send_message(
                f"You only have {bal:,} UKPence.", ephemeral=True)
            return
        async with self.lock:
            if self.seat_of(user.id) or len(self.seats) >= MAX_SEATS:
                await interaction.response.send_message("Can't seat you right now.", ephemeral=True)
                return
            if not remove_bb(user.id, amount, reason="Poker buy-in", to_bank=True):
                await interaction.response.send_message("Buy-in failed.", ephemeral=True)
                return
            self.seats.append({"id": user.id, "name": discord.utils.escape_markdown(user.display_name),
                               "stack": int(amount)})
            self._checkpoint()
        await interaction.response.send_message(
            f"Seated with **{amount:,}** chips. Good luck!", ephemeral=True)
        await self.render()

    async def _on_leave(self, interaction: Interaction):
        async with self.lock:
            if self.status == "playing":
                await interaction.response.send_message(
                    "You can leave between hands.", ephemeral=True)
                return
            seat = self.seat_of(interaction.user.id)
            if not seat:
                await interaction.response.send_message("You're not seated.", ephemeral=True)
                return
            credit_from_bank(seat["id"], seat["stack"], reason="Poker cash-out")
            cashed = seat["stack"]
            self.seats.remove(seat)
            self._checkpoint()
        await interaction.response.send_message(f"Cashed out **{cashed:,}** UKPence.", ephemeral=True)
        if not self.seats:
            await self.close()
        else:
            await self.render()

    async def _on_deal(self, interaction: Interaction):
        await interaction.response.defer()
        if self.status == "playing":
            return
        await self.start_hand()

    # --- hand flow -------------------------------------------------------------
    async def start_hand(self):
        async with self.lock:
            if self.status == "playing":
                return
            self.seats = [s for s in self.seats if s["stack"] > 0]
            if len(self.seats) < 2:
                self.status = "lobby"
                self.hand = None
                await self.render()
                return
            self.button = (self.button + 1) % len(self.seats)
            ids = [s["id"] for s in self.seats]
            stacks = {s["id"]: s["stack"] for s in self.seats}
            self._prehand = dict(stacks)
            self.hand = Hand(ids, stacks, self.button, SB, BB)
            self.status = "playing"
        self._loop_task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            while self.status == "playing" and self.hand and not self.hand.finished:
                self._turn_event = asyncio.Event()
                actor = self.hand.current_player()
                if actor is None:
                    break
                await self.render()
                try:
                    await asyncio.wait_for(self._turn_event.wait(), timeout=TURN)
                except asyncio.TimeoutError:
                    async with self.lock:
                        if self.hand and not self.hand.finished and self.hand.current_player() == actor:
                            la = self.hand.legal_actions()
                            self.hand.act("check" if la.get("check") else "fold")
            await self._end_hand()
        except Exception:
            logger.error("poker hand loop crashed", exc_info=True)
            await self._recover()

    async def apply_action(self, interaction, kind, amount=None):
        async with self.lock:
            h = self.hand
            if not h or h.finished or h.current_player() != interaction.user.id:
                return False, "It's not your turn."
            try:
                h.act(kind, amount)
            except Exception as e:
                return False, f"Invalid action: {e}"
        if self._turn_event:
            self._turn_event.set()
        return True, None

    async def _end_hand(self):
        h = self.hand
        async with self.lock:
            for s in self.seats:
                if s["id"] in h.final_stack:
                    s["stack"] = h.final_stack[s["id"]]
            self.status = "between"
            self.hand = None
            self._checkpoint()
        await self._render_results(h)
        self.seats = [s for s in self.seats if s["stack"] > 0]
        if len([s for s in self.seats if s["stack"] > 0]) >= 2:
            self._next_task = asyncio.create_task(self._auto_next())
        else:
            self.status = "lobby"
            await self.render()

    async def _auto_next(self):
        await asyncio.sleep(10)
        if self.status == "between" and len([s for s in self.seats if s["stack"] > 0]) >= 2:
            await self.start_hand()

    async def _render_results(self, h):
        live = [s for s in self.seats if s["id"] not in h.folded]
        lines = ["## \U0001f0cf HMS Hold'em - hand over"]
        if h.board:
            lines.append(f"**Board** {_cards(h.board)}")
        if len(live) == 1:
            w = live[0]
            lines.append(f"\U0001f3c6 **{w['name']}** wins **{h.payouts[w['id']]:,}** (everyone folded)")
        else:
            for s in live:
                hole = h.hole[s["id"]]
                cat = hand_category(list(hole) + list(h.board))
                won = h.payouts.get(s["id"], 0)
                tag = f" \U0001f3c6 +{won:,}" if won > 0 else ""
                lines.append(f"{s['name']}: {_cards(hole)} ({cat}){tag}")
        lines.append("\n**Stacks** " + " · ".join(
            f"{s['name']} {s['stack']:,}" for s in self.seats))
        lines.append("-# Next hand shortly. Tap **Deal** to go now, or **Leave** to cash out.")
        try:
            await self.channel.send("\n".join(lines))
        except Exception:
            logger.error("poker results render failed", exc_info=True)

    async def _recover(self):
        """A crash mid-hand: void it by restoring pre-hand stacks, then return to lobby."""
        async with self.lock:
            for s in self.seats:
                if s["id"] in self._prehand:
                    s["stack"] = self._prehand[s["id"]]
            self.status = "lobby"
            self.hand = None
            self._checkpoint()
        await self.render()

    async def close(self):
        async with self.lock:
            for s in self.seats:
                credit_from_bank(s["id"], s["stack"], reason="Poker table closed")
            self.seats = []
            self.status = "lobby"
            self.hand = None
        escrow.clear_table(self.channel_id)
        _TABLES.pop(self.channel_id, None)
        try:
            if self.message:
                await self.message.edit(content="## \U0001f0cf HMS Hold'em\nTable closed. Everyone cashed out.", view=None)
        except Exception:
            pass

    # --- per-player ephemeral action UI ---------------------------------------
    async def _on_act(self, interaction: Interaction):
        seat = self.seat_of(interaction.user.id)
        if not seat:
            await interaction.response.send_message(
                "You're not in this hand. Wait for the next one and tap **Sit**.", ephemeral=True)
            return
        h = self.hand
        if not h:
            await interaction.response.send_message("No hand in progress.", ephemeral=True)
            return
        hole = _cards(h.hole.get(interaction.user.id, []))
        header = f"**Your hand:** {hole}\n**Board:** {_cards(h.board) or '(pre-flop)'}  ·  **Pot** {h.pot():,}"
        if h.current_player() != interaction.user.id:
            await interaction.response.send_message(header + "\n\n_Waiting for your turn..._", ephemeral=True)
            return
        await interaction.response.send_message(header, view=ActionView(self), ephemeral=True)


class BuyInModal(discord.ui.Modal, title="Sit at the table"):
    amount = discord.ui.TextInput(label="Buy-in (UKPence)", placeholder=f"{MIN_BUYIN}-{MAX_BUYIN}")

    def __init__(self, table, user):
        super().__init__()
        self.table = table
        self.user = user

    async def on_submit(self, interaction: Interaction):
        try:
            amt = int(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        await self.table.add_player(interaction, self.user, amt)


class RaiseModal(discord.ui.Modal, title="Raise"):
    amount = discord.ui.TextInput(label="Raise to (total)", placeholder="amount")

    def __init__(self, table):
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: Interaction):
        try:
            to = int(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        ok, err = await self.table.apply_action(interaction, "raise", to)
        if not ok:
            await interaction.response.send_message(err, ephemeral=True)
        else:
            await interaction.response.send_message(f"Raised to **{to:,}**.", ephemeral=True)


class ActionView(discord.ui.View):
    def __init__(self, table):
        super().__init__(timeout=TURN)
        self.table = table
        h = table.hand
        la = h.legal_actions() if h else {}
        if la.get("check"):
            self._btn("Check", "check", discord.ButtonStyle.secondary)
        if la.get("call", 0) > 0:
            self._btn(f"Call {la['call_amount']:,}", "call", discord.ButtonStyle.success)
        if la.get("can_raise"):
            rb = discord.ui.Button(label="Raise", style=discord.ButtonStyle.primary)
            rb.callback = self._raise
            self.add_item(rb)
            self._btn(f"All-in {la['max_raise_to']:,}", "allin", discord.ButtonStyle.danger)
        self._btn("Fold", "fold", discord.ButtonStyle.secondary)

    def _btn(self, label, kind, style):
        b = discord.ui.Button(label=label, style=style)

        async def cb(interaction: Interaction):
            ok, err = await self.table.apply_action(interaction, kind)
            if not ok:
                await interaction.response.send_message(err, ephemeral=True)
            else:
                await interaction.response.edit_message(content=f"Action: **{label}** registered.", view=None)
        b.callback = cb
        self.add_item(b)

    async def _raise(self, interaction: Interaction):
        await interaction.response.send_modal(RaiseModal(self.table))


async def handle_poker_command(interaction: Interaction):
    if interaction.channel_id not in getattr(config, "CASINO_CHANNELS", []):
        await interaction.response.send_message(
            "Poker can only be played in the casino channels.", ephemeral=True)
        return
    table = _TABLES.get(interaction.channel_id)
    if table is None:
        table = Table(interaction.channel, interaction.client)
        _TABLES[interaction.channel_id] = table
        await interaction.response.send_message(
            "Dealing you in - the table's below. Tap **Sit** to buy in.", ephemeral=True)
        await table.render()
    else:
        await interaction.response.send_message(
            "There's already a table in this channel - scroll up and tap **Sit**.", ephemeral=True)
