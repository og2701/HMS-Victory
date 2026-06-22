"""/bets - an ephemeral, per-user dashboard of every open prediction and YOUR position on each
(which side, how much, potential return), with filters and pagination.

Built with Components V2 (LayoutView): a shared prediction message can't show per-user state, so
this ephemeral per-user view is where "which have I bet on / which side" lives. Each market is a
colour-accented card (green = you're in, orange = closing soon, grey = locked, blue = open) with
a jump button to the original message. Read-only over data the prediction system already stores.
"""
import time

import discord

from lib.economy.economy_manager import get_bb

_BLUE = discord.Colour(0x00247D)
_GREEN = discord.Colour(0x2ECC71)
_ORANGE = discord.Colour(0xE67E22)
_GREY = discord.Colour(0x607D8B)

_OPTION_EMOJIS = ["🟢", "🔵", "🟠", "🔴", "🟣"]
_SOON_SECONDS = 1800   # "closing soon" = closes within 30 minutes
_PER_PAGE = 5          # kept low so the V2 component cap (~40) is never hit

_FILTERS = [("all", "All"), ("unbet", "Not bet yet"), ("mine", "My bets"), ("soon", "Closing soon")]
_FILTER_LABEL = dict(_FILTERS)


class BetsDashboardView(discord.ui.LayoutView):
    def __init__(self, client, user_id: int, guild_id):
        super().__init__(timeout=600)
        self.client = client
        self.user_id = int(user_id)
        self.guild_id = guild_id
        self.filter = "all"
        self.page = 0
        self.build()

    # --- data helpers ------------------------------------------------------
    def _live(self):
        preds = [p for p in getattr(self.client, "predictions", {}).values()
                 if not getattr(p, "winner", None) and not getattr(p, "drawn", False)]
        # open & soonest-closing first, locked ones last
        preds.sort(key=lambda p: (p.locked, (p.end_ts or 9e18)))
        return preds

    def _my(self, p):
        """(side, amount) the user has staked on this prediction, or (None, 0)."""
        for side, pool in p.bets.items():
            if self.user_id in pool:
                return side, pool[self.user_id]
        return None, 0

    def _soon(self, p, now):
        return (not p.locked) and p.end_ts and (p.end_ts - now) <= _SOON_SECONDS

    def _filtered(self, now):
        out = []
        for p in self._live():
            side, _ = self._my(p)
            if self.filter == "unbet" and (side is not None or p.locked):
                continue
            if self.filter == "mine" and side is None:
                continue
            if self.filter == "soon" and not self._soon(p, now):
                continue
            out.append(p)
        return out

    # --- rendering ---------------------------------------------------------
    def build(self):
        self.clear_items()
        now = time.time()
        live = self._live()
        open_n = sum(1 for p in live if not p.locked)
        mine = [p for p in live if self._my(p)[0] is not None]
        mine_locked = sum(1 for p in mine if p.locked)
        in_note = f"**{len(mine)}**" + (f" ({mine_locked} locked)" if mine_locked else "")

        header = discord.ui.Container(accent_colour=_BLUE)
        header.add_item(discord.ui.TextDisplay(
            "## 📊 Your Bets\n"
            f"-# **{open_n}** open · you're in {in_note} · 💰 **{get_bb(self.user_id):,}** UKP · "
            f"filter: **{_FILTER_LABEL[self.filter]}**"))
        self.add_item(header)
        self.add_item(discord.ui.ActionRow(*self._filter_buttons()))

        rows = self._filtered(now)
        pages = max(1, (len(rows) + _PER_PAGE - 1) // _PER_PAGE)
        self.page = max(0, min(self.page, pages - 1))
        page_rows = rows[self.page * _PER_PAGE:(self.page + 1) * _PER_PAGE]

        if not page_rows:
            empty = discord.ui.Container(accent_colour=_GREY)
            empty.add_item(discord.ui.TextDisplay("-# " + {
                "unbet": "🎉 You've bet on every open prediction.",
                "mine": "You haven't placed any bets yet.",
                "soon": "Nothing's closing in the next 30 minutes.",
            }.get(self.filter, "No open predictions right now.")))
            self.add_item(empty)
        else:
            for p in page_rows:
                self.add_item(self._card(p, now))

        if pages > 1:
            self.add_item(discord.ui.ActionRow(*self._page_buttons(pages)))

    def _card(self, p, now):
        em = discord.utils.escape_markdown
        totals = p.totals()
        pool = sum(totals)
        side, amt = self._my(p)
        soon = self._soon(p, now)

        accent = _GREEN if side is not None else (_GREY if p.locked else (_ORANGE if soon else _BLUE))
        status = "🔒 locked" if p.locked else ("⏰ closing soon" if soon else "🟢 open")
        when = f" · closes <t:{int(p.end_ts)}:R>" if (p.end_ts and not p.locked) else ""
        head = f"### {em(p.title)}\n-# {status} · **{pool:,}** UKP pool{when}"

        if side is not None:
            st = totals[side - 1] or 1
            ret = int(amt * pool / st)
            emj = _OPTION_EMOJIS[(side - 1) % len(_OPTION_EMOJIS)]
            body = f"\n✅ **You:** {amt:,} on {emj} **{em(p.options[side - 1])}** → returns ~**{ret:,}**"
        else:
            opts = "  ·  ".join(f"{_OPTION_EMOJIS[i % len(_OPTION_EMOJIS)]} {em(o)}"
                                for i, o in enumerate(p.options))
            body = f"\n⬜ _not bet_  ·  {opts}"

        container = discord.ui.Container(accent_colour=accent)
        if self.guild_id and p.channel_id:
            jump = discord.ui.Button(
                style=discord.ButtonStyle.link, label="Open", emoji="↗️",
                url=f"https://discord.com/channels/{self.guild_id}/{p.channel_id}/{p.msg_id}")
            container.add_item(discord.ui.Section(discord.ui.TextDisplay(head + body), accessory=jump))
        else:
            container.add_item(discord.ui.TextDisplay(head + body))
        return container

    # --- buttons -----------------------------------------------------------
    def _filter_buttons(self):
        out = []
        for key, label in _FILTERS:
            b = discord.ui.Button(
                label=label, custom_id=f"bets:filter:{key}",
                style=discord.ButtonStyle.primary if self.filter == key else discord.ButtonStyle.secondary,
                disabled=(self.filter == key))
            b.callback = self._filter_cb(key)
            out.append(b)
        return out

    def _page_buttons(self, pages):
        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary,
                                 disabled=(self.page <= 0), custom_id="bets:prev")
        prev.callback = self._page_cb(-1)
        ind = discord.ui.Button(label=f"{self.page + 1}/{pages}", style=discord.ButtonStyle.secondary,
                                disabled=True, custom_id="bets:ind")
        nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary,
                                disabled=(self.page >= pages - 1), custom_id="bets:next")
        nxt.callback = self._page_cb(1)
        return [prev, ind, nxt]

    def _filter_cb(self, key):
        async def cb(interaction: discord.Interaction):
            self.filter, self.page = key, 0
            self.build()
            await interaction.response.edit_message(view=self)
        return cb

    def _page_cb(self, delta):
        async def cb(interaction: discord.Interaction):
            self.page += delta
            self.build()
            await interaction.response.edit_message(view=self)
        return cb


async def open_bets_dashboard(interaction: discord.Interaction):
    view = BetsDashboardView(interaction.client, interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(view=view, ephemeral=True)
