"""The /ukpence guide: a full, ephemeral walkthrough of the UKPence economy.

Plain embeds (not a baked image) so it stays accurate as amounts change and renders
crisply on mobile.
"""

import discord

import config

ACCENT = 0xD4AF37  # brass


async def handle_ukpence_guide_command(interaction: discord.Interaction):
    tree = getattr(config, "TREE_CHANNEL_ID", 0)
    tree_mention = f"<#{tree}>" if tree else "the tree channel"

    earn = discord.Embed(
        title="🪙 UKPence - The Complete Guide",
        colour=ACCENT,
        description=(
            "**UKPence (UKP)** is HMS Victory's currency, and it's a **closed economy**: a fixed "
            "**800,000 UKP** that never grows or shrinks. The **Server Bank** pays out the passive "
            "rewards below and collects everything you stake or spend - so money just circulates "
            "between players and the bank. None is ever created or destroyed.\n\n"
            "Check your balance any time with **/balance**, and see the whole economy live with "
            "**/ukpeconomy**.\n\n"
            "**__💰 Ways to earn__**"
        ),
    )
    earn.add_field(name="💬 Chatting",
                   value="A chance of **+1 UKP** every 10 minutes of activity (the chance shrinks as you get richer).",
                   inline=False)
    earn.add_field(name="🗣️ Stage participation",
                   value="**+1 UKP** for every full minute you spend in a Stage channel.", inline=False)
    earn.add_field(name="🏆 Top chatters",
                   value="**+50 UKP** awarded daily to the 5 most active chatters of the previous day.", inline=False)
    earn.add_field(name="🚀 Server boosting",
                   value="**+100 UKP** per day for boosting the server (a thank-you for the real-money support).",
                   inline=False)
    earn.add_field(name="🎉 Welcome bonus",
                   value="**+10 UKP** the very first time you take part in the economy.", inline=False)
    earn.add_field(name="🌳 Tree watering",
                   value=(f"Water the server tree in {tree_mention} for **20 UKP**. The first few waters each day "
                          "pay full, then it decays by 1 per water down to 1 - so casual waterers get the best rate."),
                   inline=False)
    earn.add_field(name="🧾 /benefits",
                   value="Skint? Claim a daily handout of **40–100 UKP** while your balance is under **400**. "
                         "One claim per day (resets at midnight).",
                   inline=False)
    earn.add_field(name="🌟 Hall of Fame",
                   value="**+100 UKP** (sent by DM) when one of your messages earns **6+ reactions** and makes the Hall of Fame.",
                   inline=False)
    earn.add_field(name="🎟️ Support tickets",
                   value="Open a genuine support ticket and staff can award you **+100 UKP** when it's closed.",
                   inline=False)
    earn.add_field(name="🎲 Gambling",
                   value="Win at the **casino**, scoop the weekly **lottery** pot, call **predictions** right, "
                         "or beat someone in a **/wager** - winnings from these are yours to keep.",
                   inline=False)

    spend = discord.Embed(
        title="🛒 Spending, Saving & Commands",
        colour=ACCENT,
        description="Anything you stake or spend flows back into the Server Bank.",
    )
    spend.add_field(name="🏦 /bond - save & earn interest",
                    value="Lock UKP for a fixed term (**3d / 7d / 30d** at **2% / 6% / 30%**) and the bank repays it "
                          "with interest when it matures. One bond at a time, up to **5,000**. Break early and you "
                          "forfeit the interest plus a small penalty.",
                    inline=False)
    spend.add_field(name="🎰 Casino",
                    value="**/casino** opens the lobby - Blackjack, Roulette, Slots, Video Poker, Red Dog and "
                          "3-Card Poker. The house keeps a small edge, so play for the fun of it.",
                    inline=False)
    spend.add_field(name="🎫 /lottery",
                    value="Buy tickets for the weekly National Lottery - one winner takes the whole pot.", inline=False)
    spend.add_field(name="🔮 Predictions & /wager",
                    value="Bet on event outcomes, or challenge another player to a head-to-head wager.", inline=False)
    spend.add_field(name="💸 /pay",
                    value="Send UKP straight to another member - no fee, up to **10,000/day** total "
                          "(resets at midnight). Paying the bank doesn't count toward that limit.",
                    inline=False)
    spend.add_field(name="🛍️ /shop",
                    value="Spend on shutcoins, VIP cases, custom emojis & roles, and more.", inline=False)
    spend.add_field(name="📋 Handy commands",
                    value=("**/balance** - check your UKP\n"
                           "**/ukpeconomy** - live economy dashboard\n"
                           "**/benefits** - daily handout if you're broke\n"
                           "**/bond** - open or manage a savings bond\n"
                           "**/casino** · **/lottery** · **/shop** - play & spend\n"
                           "**/pay** - send UKP to someone\n"
                           "**/casino-stats** - your gambling record"),
                    inline=False)
    spend.set_footer(text="HMS Victory · A closed economy - total UKP fixed at 800,000")

    tax = discord.Embed(
        title="🏛️ Taxes & demurrage",
        colour=ACCENT,
        description=(
            "To keep UKP circulating, the bank claws a little back from the wealthy - normal "
            "balances never feel it."
        ),
    )
    tax.add_field(
        name="📈 Wealth tax (on earnings)",
        value="Passive earnings are taxed once your wealth passes **10k** (**60%** to 20k, "
              "**85%** to 30k, **95%** above) - so the rich earn slower. Winnings from gambling, "
              "predictions and wagers are never taxed.",
        inline=False)
    tax.add_field(
        name="🏚️ Wealth demurrage (on hoards)",
        value="**5% per week** on the part of a balance above **20k**, taken every Friday.",
        inline=False)
    tax.add_field(
        name="💤 Inactivity tax",
        value="Go dormant for **60+ days** and **20% per week** of your balance returns to the bank.",
        inline=False)
    tax.add_field(
        name="🔀 No dodging by shuffling",
        value="All three are charged on your **effective wealth** = your balance **+** what you've "
              "sent out **−** what you've been sent (last 7 days), and demurrage uses your **highest** "
              "balance that week. So parking UKP on an alt, splitting it up, or emptying out right "
              "before Friday doesn't lower the bill - and the person you send to isn't charged for "
              "money just passing through.",
        inline=False)
    tax.set_footer(text="/pay has no fee but is capped at 10,000/day per person. The taxes just follow where the money really sits.")

    await interaction.response.send_message(embeds=[earn, spend, tax], ephemeral=True)
