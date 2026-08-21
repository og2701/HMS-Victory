"""Rock Paper Scissors - a 1v1 player-vs-player wager for UKPence (winner takes the pot).

Flow:
  /rps @rival <bet>  posts a challenge (Accept / Decline / Rules). Stakes are only taken
  once the rival ACCEPTS - then both buy-ins go into the house bank and the winner is paid
  the whole pot back out of it (the bank nets zero; the fixed UKP supply is conserved).

  Picks are SIMULTANEOUS and hidden. The match message carries one "Make your pick" button;
  pressing it opens an ephemeral hand only that player can see, and the round is not
  revealed until both hands are in. This is the whole game - a version where the second
  player can see the first one's choice is not rock paper scissors, it is a free pot.

  Best of three. A tied round is replayed and does not count, so a match always ends on
  someone actually winning two.

  Persistence: only a tiny escrow record (both players + stake) is persisted, keyed by the
  match message id, exactly as Connect 4 does. The live match and its clock don't survive a
  restart, so on boot an in-progress match is VOIDED and both stakes refunded. The record
  is deleted the instant a match settles, so a leftover entry always means "still owed".
"""

import asyncio
import logging

import discord
from discord import Interaction, Member

import config
from lib.economy.economy_manager import get_bb, remove_bb, wager_blocked_reason
from commands.economy.casino_base import (
    credit_from_bank, settle_pvp_pot, save_state, delete_state, reject_if_maintenance,
)

logger = logging.getLogger(__name__)

ROCK, PAPER, SCISSORS = "rock", "paper", "scissors"
HANDS = (ROCK, PAPER, SCISSORS)
EMOJI = {ROCK: "\U0001FAA8", PAPER: "\U0001F4C4", SCISSORS: "✂️"}
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}   # key beats value


def _wins_needed() -> int:
    return int(getattr(config, "RPS_WINS_NEEDED", 2))


def _round_seconds() -> int:
    return int(getattr(config, "RPS_ROUND_SECONDS", 120))


def rules_text() -> str:
    return (
        "## \U0001FAA8 \U0001F4C4 ✂️ Rock Paper Scissors - Rules\n"
        f"Best of **{_wins_needed() * 2 - 1}** - first to **{_wins_needed()}** rounds takes "
        "the pot.\n\n"
        "- Both players stake the same amount; **winner takes the whole pot** (2x the stake).\n"
        "- Press **Make your pick** and choose in private. Nobody sees anything until "
        "**both** hands are in, so there is no advantage to going second.\n"
        "- A **tied round is replayed** and doesn't count towards the match.\n"
        f"- ⏳ **{_round_seconds() // 60} minutes** per round. Leave your opponent hanging "
        "and you forfeit the pot.\n"
        "- Stakes are only taken **once the challenge is accepted** - decline or let it "
        "expire and nobody pays a thing.\n"
        "-# Losing a wager counts towards your daily transfer limit, the same as /pay."
    )


class HandPicker(discord.ui.View):
    """The ephemeral three-button hand, shown to one player only.

    Tied to the round it was dealt for (`seq`): a picker left open from an earlier round has
    live-looking buttons, and pressing one must not count towards the round in play.
    """

    def __init__(self, match: "RPSMatch", user_id: int):
        super().__init__(timeout=_round_seconds())
        self.match = match
        self.user_id = user_id
        self.seq = match.seq
        for hand in HANDS:
            self.add_item(self._button(hand))

    def _button(self, hand):
        button = discord.ui.Button(label=hand.capitalize(), emoji=EMOJI[hand],
                                   style=discord.ButtonStyle.secondary)

        async def chosen(interaction: Interaction):
            for child in self.children:
                child.disabled = True
            stale = self.seq != self.match.seq or self.match.game_over
            # Answer the click FIRST. Settling touches the database and edits the public
            # message, which can easily run past Discord's 3 second window - and an
            # unacknowledged interaction shows the player a red "didn't respond in time"
            # even though their pick went through.
            try:
                await interaction.response.edit_message(
                    content=("That round has already been played - use your newest prompt."
                             if stale else
                             f"Locked in: {EMOJI[hand]} **{hand}**. Nobody sees it until you "
                             "have both picked."),
                    view=self)
            except Exception:
                logger.debug("rps: could not close the picker", exc_info=True)
            if stale:
                return
            # Keep the freshest token for this player so the next round's hand can be pushed
            # straight to them instead of making them hunt for the public button again.
            self.match.hooks[self.user_id] = interaction
            await self.match.submit(interaction, self.user_id, hand, seq=self.seq)

        button.callback = chosen
        return button


class RPSMatch(discord.ui.View):
    """The public match message: a pick button, the running score, and the last reveal."""

    def __init__(self, p1_id, p1_name, p2_id, p2_name, stake, channel_id):
        super().__init__(timeout=None)
        self.p1_id, self.p1_name = int(p1_id), p1_name
        self.p2_id, self.p2_name = int(p2_id), p2_name
        self.stake = int(stake)
        self.channel_id = channel_id
        self.scores = {self.p1_id: 0, self.p2_id: 0}
        self.picks = {}
        self.round = 1
        self.seq = 0                # bumps every time a round resolves; stale pickers check it
        self.history = []           # (round no, p1 hand, p2 hand, winner id or None)
        self.hooks = {}             # user id -> their latest interaction, for private prompts
        self.last = None            # (p1 hand, p2 hand, winner id or None)
        self.game_over = False
        self.message = None
        self.client = None
        self._lock = asyncio.Lock()
        self._timer_task = None

    # --- rendering ---------------------------------------------------------
    def _score_line(self):
        return (f"## {self.p1_name}  {self.scores[self.p1_id]} - "
                f"{self.scores[self.p2_id]}  {self.p2_name}")

    def _round_line(self, n, h1, h2, winner):
        """A finished round. The winning hand leads and the winner's name closes the line, so
        you can always tell whose hand was whose - "📄 vs 🪨" on its own tells you nothing."""
        if winner is None:
            return f"`R{n}`  both played {EMOJI[h1]} {h1} - replayed"
        name = self.p1_name if winner == self.p1_id else self.p2_name
        won, lost = (h1, h2) if winner == self.p1_id else (h2, h1)
        return f"`R{n}`  {EMOJI[won]} {won} beats {EMOJI[lost]} {lost} - **{name}**"

    def _embed(self):
        lines = [self._score_line(),
                 f"-# best of {_wins_needed() * 2 - 1} · pot **{self.stake * 2:,} UKPence**"]
        if self.history:
            lines.append("")
            lines += [self._round_line(*r) for r in self.history]
        if self.game_over:
            colour, footer = 0x2ECC71, "Rock Paper Scissors · winner takes the pot"
        else:
            ticks = "   ".join(
                f"{'✅' if uid in self.picks else '⬜'} {name}"
                for uid, name in ((self.p1_id, self.p1_name), (self.p2_id, self.p2_name)))
            lines += ["", f"**Round {self.round}** - choose in private", f"picked   {ticks}",
                      "-# Hands stay hidden until you have both picked. Lost your prompt? "
                      "Press the button."]
            colour = 0x3498DB
            footer = f"⏳ {_round_seconds() // 60} min per round, or you forfeit"
        embed = discord.Embed(title="\U0001FAA8 \U0001F4C4 ✂️ Rock Paper Scissors",
                              description="\n".join(lines), colour=colour)
        embed.set_footer(text=footer)
        return embed

    async def _render(self):
        if self.message is None:
            return False
        try:
            # The label is the only cue on the public message that a NEW pick is wanted.
            self.pick.label = ("Make your pick" if not self.history
                               else f"Make your pick · round {self.round}")
        except Exception:
            pass
        try:
            await self.message.edit(embed=self._embed(), view=None if self.game_over else self)
            return True
        except Exception:
            logger.debug("rps: could not edit the match message", exc_info=True)
            return False

    async def _deal_pickers(self):
        """A round resolved and the match goes on: push a fresh private hand at each player.

        Without this they have to notice the public embed changed and press the button again,
        which is exactly what made the first version unreadable to play.
        """
        for uid in (self.p1_id, self.p2_id):
            hook = self.hooks.get(uid)
            if hook is None:
                continue
            try:
                await hook.followup.send(
                    content=f"**Round {self.round}** - choose your hand.",
                    view=HandPicker(self, uid), ephemeral=True)
            except Exception:
                logger.debug("rps: could not push a fresh picker", exc_info=True)

    # --- the pick button ---------------------------------------------------
    @discord.ui.button(label="Make your pick", emoji="\U0001FAA8",
                       style=discord.ButtonStyle.primary)
    async def pick(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1_id, self.p2_id):
            await interaction.response.send_message("This isn't your match.", ephemeral=True)
            return
        if self.game_over:
            await interaction.response.send_message("This match is over.", ephemeral=True)
            return
        if interaction.user.id in self.picks:
            await interaction.response.send_message(
                "You've already picked this round - waiting on your opponent.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Round **{self.round}**. Choose your hand - your opponent won't see it until "
            "they've picked too.",
            view=HandPicker(self, interaction.user.id), ephemeral=True)
        self.hooks[interaction.user.id] = interaction

    # --- the round ---------------------------------------------------------
    async def submit(self, interaction: Interaction, user_id: int, hand: str, seq=None):
        new_round = False
        async with self._lock:
            if self.game_over or user_id in self.picks:
                return
            if seq is not None and seq != self.seq:
                return
            self.picks[user_id] = hand
            if len(self.picks) < 2:
                self._start_timer()
            else:
                self._cancel_timer()
                h1, h2 = self.picks[self.p1_id], self.picks[self.p2_id]
                winner = None if h1 == h2 else (self.p1_id if BEATS[h1] == h2 else self.p2_id)
                self.last = (h1, h2, winner)
                self.history.append((self.round, h1, h2, winner))
                self.picks = {}
                self.seq += 1
                if winner is not None:
                    self.scores[winner] += 1
                if max(self.scores.values()) >= _wins_needed():
                    await self._finish(winner=winner)
                    return
                if winner is not None:       # a tie replays the same round number
                    self.round += 1
                self._start_timer()
                new_round = True
        await self._render()
        if new_round:
            await self._deal_pickers()

    # --- forfeit clock -----------------------------------------------------
    def _start_timer(self):
        self._cancel_timer()
        self._timer_task = asyncio.create_task(self._forfeit_after())

    def _cancel_timer(self):
        task = self._timer_task
        self._timer_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _forfeit_after(self):
        try:
            await asyncio.sleep(_round_seconds())
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.game_over:
                return
            # Whoever has not picked loses. If neither has, nobody was waiting on anybody -
            # void it and give both stakes back rather than pick a loser at random.
            missing = [uid for uid in (self.p1_id, self.p2_id) if uid not in self.picks]
            if len(missing) == 2:
                await self._finish(winner=None, void=True)
            else:
                await self._finish(winner=next(uid for uid in (self.p1_id, self.p2_id)
                                               if uid not in missing), forfeit=True)

    # --- settling ----------------------------------------------------------
    async def _finish(self, *, winner, forfeit=False, void=False):
        """Pay out. Drops the escrow record BEFORE crediting, so a crash mid-settle can
        never double-pay on the next boot."""
        self.game_over = True
        self._cancel_timer()
        if self.message is not None:
            delete_state(self.message.id)
        pot = self.stake * 2
        if void or winner is None:
            credit_from_bank(self.p1_id, self.stake, "Rock Paper Scissors void refund")
            credit_from_bank(self.p2_id, self.stake, "Rock Paper Scissors void refund")
        else:
            loser = self.p2_id if winner == self.p1_id else self.p1_id
            settle_pvp_pot(winner, loser, pot, "Rock Paper Scissors win", own_stake=self.stake)
        try:
            from lib.economy import pvp_stats
            if void or winner is None:
                pvp_stats.record_result("rps", None, None, self.stake, "draw")
            else:
                loser = self.p2_id if winner == self.p1_id else self.p1_id
                pvp_stats.record_result("rps", winner, loser, self.stake,
                                        "forfeit" if forfeit else "win")
        except Exception:
            logger.error("rps: failed to record the result", exc_info=True)

        if void:
            tail = ("Nobody picked in time - the match is void and both stakes have been "
                    "refunded.")
        else:
            name = self.p1_name if winner == self.p1_id else self.p2_name
            why = " by forfeit" if forfeit else ""
            tail = f"\U0001F3C6 **{name}** wins the match{why} and takes **{pot:,} UKPence**."
        embed = self._embed()
        embed.description = f"{embed.description}\n\n{tail}"
        if self.message is not None:
            try:
                # Clear the "both press Make your pick" line - it is not true any more.
                await self.message.edit(content=None, embed=embed, view=None)
            except Exception:
                logger.debug("rps: could not render the result", exc_info=True)
        self.stop()


class RPSChallengeView(discord.ui.View):
    def __init__(self, challenger: Member, opponent: Member, amount: int):
        super().__init__(timeout=int(getattr(config, "RPS_ACCEPT_SECONDS", 300)))
        self.challenger = challenger
        self.opponent = opponent
        self.amount = amount
        self.resolved = False
        self.message = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Only the challenged player can accept this.", ephemeral=True)
            return
        if self.resolved:
            await interaction.response.defer()
            return
        if await reject_if_maintenance(interaction):
            return
        if get_bb(self.challenger.id) < self.amount:
            await interaction.response.send_message(
                f"{self.challenger.display_name} no longer has {self.amount:,} UKPence.",
                ephemeral=True)
            return
        if get_bb(self.opponent.id) < self.amount:
            await interaction.response.send_message(
                f"You no longer have {self.amount:,} UKPence.", ephemeral=True)
            return
        # Re-checked here: the allowance can be spent between the challenge and the accept.
        for uid, name in ((self.challenger.id, self.challenger.display_name),
                          (self.opponent.id, "You")):
            if why := wager_blocked_reason(uid, self.amount, interaction.client.user.id,
                                           name=name):
                await interaction.response.send_message(why, ephemeral=True)
                return
        # Claim before any await/payout so two fast clicks can't both stake the pot.
        self.resolved = True
        if not remove_bb(self.challenger.id, self.amount, reason="Rock Paper Scissors stake"):
            self.resolved = False
            await interaction.response.send_message(
                f"{self.challenger.display_name} can't cover the stake.", ephemeral=True)
            return
        if not remove_bb(self.opponent.id, self.amount, reason="Rock Paper Scissors stake"):
            credit_from_bank(self.challenger.id, self.amount,
                             "Rock Paper Scissors stake refund")
            self.resolved = False
            await interaction.response.send_message(
                "You can't cover the stake.", ephemeral=True)
            return

        p1_name = discord.utils.escape_markdown(self.challenger.display_name)
        p2_name = discord.utils.escape_markdown(self.opponent.display_name)
        match = RPSMatch(self.challenger.id, p1_name, self.opponent.id, p2_name,
                         self.amount, interaction.channel_id)
        match.message = interaction.message
        match.client = interaction.client
        # Persist the escrow record BEFORE showing the match, so a crash right after staking
        # is still recoverable on boot.
        save_state(interaction.message.id, {
            "type": "rps",
            "p1_id": self.challenger.id, "p2_id": self.opponent.id,
            "p1_name": p1_name, "p2_name": p2_name,
            "stake": self.amount, "channel_id": interaction.channel_id,
        })
        self.stop()
        try:
            await interaction.response.edit_message(
                content=f"{self.challenger.mention} vs {self.opponent.mention} - both press "
                        "**Make your pick**.",
                embed=match._embed(), view=match)
        except Exception:
            logger.error("rps: could not open the match", exc_info=True)
            return
        match._start_timer()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Only the challenged player can decline this.", ephemeral=True)
            return
        self.resolved = True
        self.stop()
        await interaction.response.edit_message(
            content=f"{self.opponent.mention} declined the challenge. No stakes were taken.",
            embed=None, view=None)

    @discord.ui.button(label="Rules", style=discord.ButtonStyle.secondary)
    async def rules(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_message(rules_text(), ephemeral=True)

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        try:
            await self.message.edit(
                content="⏱️ The challenge expired. No stakes were taken.",
                embed=None, view=None)
        except Exception:
            pass


async def handle_rps_command(interaction: Interaction, opponent: Member, bet: int):
    if not getattr(config, "RPS_ENABLED", True):
        await interaction.response.send_message("Rock Paper Scissors is switched off.",
                                                ephemeral=True)
        return
    if await reject_if_maintenance(interaction):
        return
    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message(
            "Pick someone else to play against.", ephemeral=True)
        return
    min_bet = int(getattr(config, "RPS_MIN_BET", 5))
    max_bet = int(getattr(config, "RPS_MAX_BET", 2000))
    if bet < min_bet or bet > max_bet:
        await interaction.response.send_message(
            f"The stake must be between {min_bet:,} and {max_bet:,} UKPence.", ephemeral=True)
        return
    if get_bb(interaction.user.id) < bet:
        await interaction.response.send_message(
            f"You don't have {bet:,} UKPence to stake.", ephemeral=True)
        return
    if get_bb(opponent.id) < bet:
        await interaction.response.send_message(
            f"{opponent.display_name} doesn't have {bet:,} UKPence to match your stake.",
            ephemeral=True)
        return
    # Losing moves UKP to the other player, so it spends the same daily allowance /pay does.
    for uid, name in ((interaction.user.id, "You"), (opponent.id, opponent.display_name)):
        if why := wager_blocked_reason(uid, bet, interaction.client.user.id, name=name):
            await interaction.response.send_message(why, ephemeral=True)
            return

    accept_min = int(getattr(config, "RPS_ACCEPT_SECONDS", 300)) // 60
    embed = discord.Embed(
        title="\U0001FAA8 \U0001F4C4 ✂️ Rock Paper Scissors Challenge",
        description=(f"{interaction.user.mention} challenges {opponent.mention} for "
                     f"**{bet:,} UKPence** a side.\n"
                     f"Best of **{_wins_needed() * 2 - 1}**, winner takes **{bet * 2:,}**."),
        colour=0x3498DB)
    embed.set_footer(text=f"{accept_min} min to accept · stakes are only taken on accept")
    view = RPSChallengeView(interaction.user, opponent, bet)
    await interaction.response.send_message(content=opponent.mention, embed=embed, view=view)
    view.message = await interaction.original_response()


# ---------------------------------------------------------------------------
# Restart recovery (called from event_handlers.reattach_persistent_views)
# ---------------------------------------------------------------------------
def reattach_rps_view(client, key, value):
    """A match was in progress when the bot restarted. The live picks and the round clock
    can't be rebuilt, so VOID it: refund both stakes and prune the record."""
    try:
        p1 = int(value["p1_id"])
        p2 = int(value["p2_id"])
        stake = int(value["stake"])
    except Exception as e:
        logger.error(f"Pruning malformed rps entry {key}: {e}")
        delete_state(key)
        return
    delete_state(key)      # drop first so a re-run can't double-refund
    credit_from_bank(p1, stake, "Rock Paper Scissors void refund (bot restart)")
    credit_from_bank(p2, stake, "Rock Paper Scissors void refund (bot restart)")

    channel_id = value.get("channel_id")

    async def _notify():
        try:
            channel = client.get_channel(int(channel_id)) if channel_id else None
            if channel is None:
                return
            await channel.send(
                f"\U0001FAA8 A Rock Paper Scissors match between <@{p1}> and <@{p2}> was "
                f"voided by a restart - both **{stake:,} UKPence** stakes have been refunded.",
                allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.debug("rps: could not post the void notice", exc_info=True)

    try:
        asyncio.get_event_loop().create_task(_notify())
    except Exception:
        pass
