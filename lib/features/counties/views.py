"""County Balls Discord layer - spawns, the catch modal, and /county-* handlers."""

import io
import logging
import os
import random
import string

import discord
from discord import Interaction

import config
from lib.core.file_operations import load_persistent_views, save_persistent_views
from lib.features.counties import engine as E
from lib.features.counties.data import COUNTIES, NATIONS, match_county

logger = logging.getLogger(__name__)

TIER_LABELS = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare", "legendary": "LEGENDARY"}
TIER_COLOURS = {
    "common": discord.Colour.light_grey(),
    "uncommon": discord.Colour.green(),
    "rare": discord.Colour.blue(),
    "legendary": discord.Colour.gold(),
}


async def _deny_if_gated(interaction: Interaction) -> bool:
    """Testing gate: while COUNTY_ALLOWED_ROLE_IDS is set, only those roles may
    catch or use /county-* commands. Sends the refusal itself; True = blocked."""
    allowed = getattr(config, "COUNTY_ALLOWED_ROLE_IDS", [])
    if not allowed:
        return False
    from lib.core.discord_helpers import has_any_role
    if has_any_role(interaction, allowed):
        return False
    await interaction.response.send_message(
        "🔒 County balls are in closed testing - Deputy PMs only for now.",
        ephemeral=True,
    )
    return True


def _asset_file(county_key: str) -> discord.File | None:
    """The county's art, under a random filename so it never leaks the answer."""
    for ext in ("webp", "png"):
        path = os.path.join(config.COUNTY_ASSET_DIR, f"{county_key}.{ext}")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            name = "".join(random.choices(string.ascii_lowercase, k=12))
            return discord.File(io.BytesIO(data), filename=f"{name}.{ext}")
    return None


# ---------------------------------------------------------------------------
# Spawning
# ---------------------------------------------------------------------------
class CountyCatchModal(discord.ui.Modal, title="Catch the county ball!"):
    guess = discord.ui.TextInput(label="Which county is this?", placeholder="Your guess",
                                 max_length=60)

    async def on_submit(self, interaction: Interaction):
        if await _deny_if_gated(interaction):
            return
        active = E.active_spawn()
        if not active or active.get("message_id") != interaction.message.id:
            await interaction.response.send_message("Too slow - it's already been caught!",
                                                    ephemeral=True)
            return

        county_key = active["county"]
        if match_county(self.guess.value) != county_key:
            wrong = E.note_wrong_guess()
            await interaction.response.send_message(
                f"**{interaction.user.display_name}** guessed \"{self.guess.value}\" - wrong county!"
            )
            if wrong >= config.COUNTY_HINT_AFTER and not active.get("hinted"):
                E.mark_hinted()
                county = COUNTIES[county_key]
                try:
                    await interaction.message.edit(
                        content=interaction.message.content
                        + f"\n💡 Hint: a county of **{county.nation}**, "
                          f"starting with **{county.name[0]}**."
                    )
                except discord.HTTPException:
                    pass
            return

        # Correct, and this modal got here first: claim it.
        E.clear_active()
        county = COUNTIES[county_key]
        first, owned = E.record_catch(interaction.user.id, county_key,
                                      interaction.channel_id or 0)
        _unregister_spawn(interaction.message.id)
        newness = "a **new** county for their collection" if first else f"a duplicate (x{owned})"
        await interaction.response.send_message(
            f"🎉 **{interaction.user.display_name}** caught **{county.name}** "
            f"({TIER_LABELS[county.tier]}) - {newness}!"
        )
        try:
            await interaction.message.edit(
                content=f"Caught by **{interaction.user.display_name}** - "
                        f"it was **{county.name}**!",
                view=None,
            )
        except discord.HTTPException:
            pass


class CountySpawnView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Catch it!", style=discord.ButtonStyle.primary,
                       custom_id="county_catch")
    async def catch(self, interaction: Interaction, button: discord.ui.Button):
        if await _deny_if_gated(interaction):
            return
        active = E.active_spawn()
        if not active or active.get("message_id") != interaction.message.id:
            await interaction.response.send_message("Too slow - it's already been caught!",
                                                    ephemeral=True)
            try:
                button.disabled = True
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
            return
        await interaction.response.send_modal(CountyCatchModal())


def _register_spawn(message_id: int, county_key: str, channel_id: int) -> None:
    views = load_persistent_views()
    views[str(message_id)] = {"type": "county", "county": county_key, "channel_id": channel_id}
    save_persistent_views(views)


def _unregister_spawn(message_id: int) -> None:
    views = load_persistent_views()
    if views.pop(str(message_id), None) is not None:
        save_persistent_views(views)


async def _expire_previous(client, old: dict) -> None:
    """An uncaught spawn scarpers when a new one appears."""
    _unregister_spawn(old["message_id"])
    channel = client.get_channel(old["channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(old["message_id"])
        await msg.edit(content="It scarpered before anyone could catch it! 💨", view=None)
    except discord.HTTPException:
        pass


async def spawn_county(client, channel, county_key: str | None = None) -> bool:
    county_key = county_key or E.pick_county()
    county = COUNTIES[county_key]

    old = E.clear_active()
    if old:
        await _expire_previous(client, old)

    content = "🎱 A wild county ball has appeared! Guess the county to catch it."
    file = _asset_file(county_key)
    if file is None:
        # No art yet (pre-codex testing): spoiler the answer so it stays playable.
        content += f"\n*(no art yet - it's ||{county.name}||)*"
    try:
        message = await channel.send(
            content,
            view=CountySpawnView(),
            **({"file": file} if file else {}),
        )
    except discord.HTTPException:
        logger.error("Failed to spawn county ball in %s", channel, exc_info=True)
        return False

    E.begin_spawn(county_key, message.id, channel.id)
    _register_spawn(message.id, county_key, channel.id)
    logger.info("County ball spawned: %s in #%s", county_key, getattr(channel, "name", "?"))
    return True


_spawn_in_flight = False


async def county_on_message(client, message) -> None:
    """on_message hook: count qualifying chat and spawn when the bar fills."""
    global _spawn_in_flight
    if not getattr(config, "COUNTY_ENABLED", False):
        return
    if not E.channel_eligible(message.channel):
        return
    if E.note_message(message.author.id, message.content or "") and not _spawn_in_flight:
        _spawn_in_flight = True
        try:
            await spawn_county(client, message.channel)
        finally:
            _spawn_in_flight = False


def reattach_county_view(client, key, value) -> None:
    """Re-register the catch button for a live spawn after a restart; prune
    registry entries that no longer match the engine's active spawn."""
    active = E.active_spawn()
    if not active or active.get("message_id") != int(key):
        _unregister_spawn(int(key))
        return
    client.add_view(CountySpawnView(), message_id=int(key))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def handle_county_dex_command(interaction: Interaction):
    if await _deny_if_gated(interaction):
        return
    owned = E.collection(interaction.user.id)
    total = len(COUNTIES)
    caught = len(owned)
    embed = discord.Embed(
        title=f"{interaction.user.display_name}'s County Dex",
        description=f"**{caught}/{total}** counties collected "
                    f"({caught / total:.0%})",
        colour=discord.Colour.dark_green(),
    )
    for nation in NATIONS:
        keys = [k for k, c in COUNTIES.items() if c.nation == nation]
        got = [k for k in keys if k in owned]
        names = ", ".join(
            f"{COUNTIES[k].name}" + (f" x{owned[k]}" if owned[k] > 1 else "")
            for k in sorted(got, key=lambda k: COUNTIES[k].name)
        ) or "*none yet*"
        if len(names) > 1000:
            names = names[:997] + "..."
        embed.add_field(name=f"{nation} - {len(got)}/{len(keys)}", value=names, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_county_info_command(interaction: Interaction, county: str):
    if await _deny_if_gated(interaction):
        return
    key = match_county(county)
    if not key:
        await interaction.response.send_message(
            f"No county matches \"{county}\".", ephemeral=True)
        return
    c = COUNTIES[key]
    embed = discord.Embed(title=c.name, colour=TIER_COLOURS[c.tier])
    embed.add_field(name="Nation", value=c.nation)
    embed.add_field(name="Rarity", value=TIER_LABELS[c.tier])
    embed.add_field(name="Sell price", value=f"{config.COUNTY_SELL_PRICES[c.tier]:,} UKP")
    embed.add_field(name="You own", value=str(E.owned_count(interaction.user.id, key)))
    embed.add_field(name="Caught server-wide", value=str(E.server_caught_count(key)))
    file = _asset_file(key)
    if file:
        embed.set_image(url=f"attachment://{file.filename}")
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_county_give_command(interaction: Interaction, member: discord.Member,
                                     county: str):
    if await _deny_if_gated(interaction):
        return
    key = match_county(county)
    if not key:
        await interaction.response.send_message(
            f"No county matches \"{county}\".", ephemeral=True)
        return
    if member.bot or member.id == interaction.user.id:
        await interaction.response.send_message("Pick another member of the realm.",
                                                ephemeral=True)
        return
    if not E.transfer_one(interaction.user.id, member.id, key):
        await interaction.response.send_message(
            f"You don't own **{COUNTIES[key].name}**.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"🤝 **{interaction.user.display_name}** gave **{COUNTIES[key].name}** "
        f"to **{member.display_name}**."
    )


async def handle_county_sell_command(interaction: Interaction, county: str, quantity: int):
    if await _deny_if_gated(interaction):
        return
    key = match_county(county)
    if not key:
        await interaction.response.send_message(
            f"No county matches \"{county}\".", ephemeral=True)
        return
    if quantity < 1:
        await interaction.response.send_message("Sell at least one.", ephemeral=True)
        return
    have = E.owned_count(interaction.user.id, key)
    if have < quantity:
        await interaction.response.send_message(
            f"You own {have}x **{COUNTIES[key].name}**, not {quantity}.", ephemeral=True)
        return
    paid = E.sell(interaction.user.id, key, quantity)
    if paid is None:
        await interaction.response.send_message(
            "The bank couldn't cover that sale right now - try again later.", ephemeral=True)
        return
    left = have - quantity
    note = " That was your last one - it's gone from your dex!" if left == 0 else ""
    await interaction.response.send_message(
        f"💷 Sold {quantity}x **{COUNTIES[key].name}** for **{paid:,} UKPence**.{note}",
        ephemeral=True,
    )


async def handle_county_spawn_command(interaction: Interaction, county: str | None):
    """Admin force-spawn, in the current channel."""
    key = None
    if county:
        key = match_county(county)
        if not key:
            await interaction.response.send_message(
                f"No county matches \"{county}\".", ephemeral=True)
            return
    await interaction.response.send_message("Spawning...", ephemeral=True)
    await spawn_county(interaction.client, interaction.channel, key)
