import discord
from lib.economy.bank_manager import BankManager
from datetime import datetime


def _pct_bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, int(round(pct / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def tax_overview_embed() -> discord.Embed:
    """The tax + reserve-policy panel, behind a button so the main status stays readable.

    Everything here is derived from the ledger and the live balances, so it can't drift
    from a stored counter the way the old headline tax figure did.
    """
    import config
    from lib.economy.reserve_policy import reserve_state, dividend_pot, dividend_rate

    ledger = BankManager.get_ledger_stats()
    st = reserve_state()
    e = discord.Embed(
        title="🏛️ Tax & Reserve Policy",
        colour=0x8e44ad,
        description="Where the bank's money comes from, and what it does when reserves fall.",
    )

    # --- reserves + the throttle -----------------------------------------------------
    if st:
        pct = st["pct_of_supply"]
        floor_txt = (f"**{st['above_floor']:,}** above the {st['floor']:,} floor"
                     if st["above_floor"] >= 0
                     else f"⚠️ **{abs(st['above_floor']):,} BELOW** the {st['floor']:,} floor")
        e.add_field(
            name="🏦 Reserves",
            value=(f"`{_pct_bar(pct)}` **{pct:.1f}%**\n"
                   f"{st['reserves']:,} of {st['supply']:,} UKP · {floor_txt}"),
            inline=False,
        )
        if st["throttled"]:
            e.add_field(
                name="🚦 Throttle ACTIVE",
                value=(f"Discretionary rewards are paying **{st['multiplier']:.0%}**.\n"
                       "-# Chat, tree, welcome, bump, HoF, stage, benefits and booster "
                       "bonuses are scaled. Casino wins, refunds and bond maturities are "
                       "never scaled - those are debts, not gifts."),
                inline=False,
            )
        else:
            tiers = getattr(config, "RESERVE_THROTTLE_TIERS", ())
            nxt = max((c for c, _m in tiers), default=0)
            e.add_field(
                name="🚦 Throttle",
                value=(f"Inactive - rewards paying in full. Scaling begins at "
                       f"**{nxt:,}** ({100*nxt/st['supply']:.0f}% of supply)."),
                inline=False,
            )
        e.add_field(name="🖨️ Mint headroom",
                    value=f"{st['mint_headroom']:,} UKP\n-# ceiling {st['supply_cap']:,}",
                    inline=True)
        e.add_field(name="💸 Circulating", value=f"{st['circulating']:,} UKP", inline=True)

    # --- the dividend pot ------------------------------------------------------------
    if getattr(config, "DEMURRAGE_DIVIDEND_ENABLED", True):
        pot, rate = dividend_pot(), dividend_rate()
        share = float(getattr(config, "DEMURRAGE_DIVIDEND_PCT", 0.5))
        state = "dry - chat rewards paused" if pot <= 0 else f"paying at **{rate:.0%}** rate"
        e.add_field(
            name="🎁 Demurrage dividend pot",
            value=(f"**{pot:,} UKP** · {state}\n"
                   f"-# {share:.0%} of each weekly demurrage run is earmarked here to pay "
                   "chat activity rewards. An earmark on the bank's balance, not a second "
                   "wallet - no UKP is created."),
            inline=False,
        )

    # --- what each tax does ----------------------------------------------------------
    dem_th = getattr(config, "WEALTH_DEMURRAGE_THRESHOLD", 10000)
    dem_rate = getattr(config, "WEALTH_DEMURRAGE_RATE", 0.05)
    inact = getattr(config, "INACTIVITY_TAX_RATE", 0.20)
    dorm_floor = getattr(config, "ECONOMY_DORMANT_FLOOR", 100)
    dorm_rate = getattr(config, "ECONOMY_DORMANT_RATE", 0.10)
    dorm_days = getattr(config, "ECONOMY_DORMANT_DAYS", 60)
    e.add_field(
        name="📋 The four charges",
        value=(
            f"**Wealth tax** - progressive, on taxable bank-funded earnings at 10k+. "
            f"Charged at the moment you're paid.\n"
            f"**Demurrage** - {dem_rate:.0%}/wk on the balance above **{dem_th:,}**. "
            f"Taxes the stock, so untaxed earnings can't dodge it.\n"
            f"**Inactivity** - {inact:.0%}/wk on effective wealth after "
            f"**60 days without chatting**.\n"
            f"**Economy-dormant** - {dorm_rate:.0%}/wk on the balance above "
            f"**{dorm_floor:,}** after **{dorm_days} days** without gambling, shopping, "
            f"/pay, bonds or the lottery."
        ),
        inline=False,
    )
    e.add_field(
        name="🧾 Total tax collected (all time)",
        value=f"{ledger['tax_collected']:,} UKPence",
        inline=False,
    )
    e.set_footer(text="All taxes run silently - members are never DM'd, and statements "
                      "fold them into 'Rewards & other'.")
    return e


class BankStatusView(discord.ui.View):
    """Bond + tax overviews hang off /bank-status so the headline stays scannable."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Bond Overview", emoji="🏦",
                       style=discord.ButtonStyle.secondary, custom_id="bankstatus:bonds")
    async def bonds(self, interaction: discord.Interaction, button: discord.ui.Button):
        from lib.economy.bonds import bonds_overview_embed
        await interaction.response.send_message(embed=bonds_overview_embed(), ephemeral=True)

    @discord.ui.button(label="Tax & Reserves", emoji="🏛️",
                       style=discord.ButtonStyle.secondary, custom_id="bankstatus:tax")
    async def tax(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=tax_overview_embed(), ephemeral=True)


async def handle_bank_status_command(interaction: discord.Interaction):
    """Show current bank status. Tax and reserve policy live behind the Tax button."""
    bank_info = BankManager.get_bank_info()
    ledger = BankManager.get_ledger_stats()

    embed = discord.Embed(
        title="🏦 Server Bank Status",
        color=0x00aa00
    )

    embed.add_field(
        name="💰 Current Balance",
        value=f"{bank_info['balance']:,} UKPence",
        inline=True
    )

    embed.add_field(
        name="📈 Total Revenue",
        value=f"{bank_info['total_revenue']:,} UKPence",
        inline=True
    )

    def _short_pl(net):
        sign = "+" if net >= 0 else "-"
        note = "house ahead" if net > 0 else ("players ahead" if net < 0 else "even")
        return f"{sign}{abs(net):,} ({note})"

    # Per-game house P/L (positive = the bank is ahead), three across.
    embed.add_field(name="🎴 Blackjack", value=_short_pl(ledger['blackjack_net']), inline=True)
    embed.add_field(name="🔼 Higher/Lower", value=_short_pl(ledger['higherlower_net']), inline=True)
    embed.add_field(name="🎰 Fruit Machine", value=_short_pl(ledger['slots_net']), inline=True)
    embed.add_field(name="🃏 Video Poker", value=_short_pl(ledger['videopoker_net']), inline=True)
    embed.add_field(name="🐕 Red Dog", value=_short_pl(ledger['reddog_net']), inline=True)
    embed.add_field(name="♣️ 3-Card Poker", value=_short_pl(ledger['tcp_net']), inline=True)
    embed.add_field(name="🎡 Roulette", value=_short_pl(ledger['roulette_net']), inline=True)
    embed.add_field(name="💣 Mines", value=_short_pl(ledger['mines_net']), inline=True)
    embed.add_field(name="⚽ Penalty Shootout", value=_short_pl(ledger['penalty_net']), inline=True)
    embed.add_field(name="🧰 Chest Upgrade", value=_short_pl(ledger['chest_net']), inline=True)
    embed.add_field(name="🚢 Blockade Run", value=_short_pl(ledger['blockade_net']), inline=True)
    embed.add_field(name="🎯 Darts", value=_short_pl(ledger['darts_net']), inline=True)

    casino_net = ledger['casino_net']
    casino_sign = "+" if casino_net >= 0 else "-"
    casino_note = "house ahead" if casino_net > 0 else ("players ahead" if casino_net < 0 else "even")
    embed.add_field(
        name="🏰 Total Casino (House P/L)",
        value=(
            f"{casino_sign}{abs(casino_net):,} UKPence ({casino_note})\n"
            f"`{ledger['casino_in']:,}` staked in · `{ledger['casino_out']:,}` paid out"
        ),
        inline=False
    )

    # A one-line warning on the headline when the throttle is live - the detail is on the
    # Tax button, but a scaled-down economy shouldn't be invisible to staff.
    try:
        from lib.economy.reserve_policy import reserve_state
        st = reserve_state()
        if st and st["throttled"]:
            embed.add_field(
                name="🚦 Reserve throttle ACTIVE",
                value=(f"Reserves at **{st['pct_of_supply']:.1f}%** - discretionary rewards "
                       f"paying **{st['multiplier']:.0%}**. See **Tax & Reserves**."),
                inline=False,
            )
    except Exception:
        pass

    if bank_info['last_updated'] > 0:
        last_updated = datetime.fromtimestamp(bank_info['last_updated'])
        embed.add_field(
            name="⏰ Last Updated",
            value=last_updated.strftime("%Y-%m-%d %H:%M:%S"),
            inline=False
        )

    embed.set_footer(text="💡 Bank accumulates UKPence from shop purchases, taxes & the house edge")

    await interaction.response.send_message(embed=embed, view=BankStatusView(), ephemeral=True)
