"""County Balls Discord layer - spawns, the catch modal, and /county-* handlers."""

import io
import logging
import os
import random
import string
from html import escape as html_escape

import discord
from discord import Interaction

import config
from lib.core.file_operations import load_persistent_views, save_persistent_views
from lib.features.counties import engine as E
from lib.features.counties.data import COUNTIES, base_stats, match_county

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


def _stat_line(county_key: str, clout_b: int, grit_b: int) -> str:
    base_c, base_g = base_stats(county_key)
    clout = base_c * (100 + clout_b) // 100
    grit = base_g * (100 + grit_b) // 100
    return f"⚔️ {clout} Clout ({clout_b:+d}%) · 🛡️ {grit} Grit ({grit_b:+d}%)"


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
        first, owned, clout_b, grit_b = E.record_catch(
            interaction.user.id, county_key, interaction.channel_id or 0)
        _unregister_spawn(interaction.message.id)
        newness = "a **new** county for their collection" if first else f"a duplicate (x{owned})"
        await interaction.response.send_message(
            f"🎉 **{interaction.user.display_name}** caught **{county.name}** "
            f"({TIER_LABELS[county.tier]}) - {newness}!\n"
            f"{_stat_line(county_key, clout_b, grit_b)}"
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

    if getattr(config, "COUNTY_DEBUG_ANNOUNCE", False):
        try:
            await channel.send(
                f"🐛 debug: that's **||{county.name}||** ({TIER_LABELS[county.tier]})"
            )
        except discord.HTTPException:
            pass
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
_TIER_ORDER = {"legendary": 0, "rare": 1, "uncommon": 2, "common": 3}
_thumb_cache: dict = {}


def _thumb_data_uri(county_key: str) -> str | None:
    """Small PNG data URI of a county's art for the dex grid, cached per process."""
    if county_key in _thumb_cache:
        return _thumb_cache[county_key]
    for ext in ("webp", "png"):
        path = os.path.join(config.COUNTY_ASSET_DIR, f"{county_key}.{ext}")
        if not os.path.exists(path):
            continue
        try:
            import base64
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((96, 96))
                buf = io.BytesIO()
                im.save(buf, format="PNG")
            uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            from lib.core.image_processing import encode_image_to_data_uri
            uri = encode_image_to_data_uri(path)
        _thumb_cache[county_key] = uri
        return uri
    return None


async def _render_dex_image(display_name: str, owned: dict) -> io.BytesIO:
    from lib.core.file_operations import read_html_template
    from lib.core.image_processing import screenshot_html

    keys = sorted(COUNTIES, key=lambda k: (_TIER_ORDER[COUNTIES[k].tier], COUNTIES[k].name))
    cells = []
    for k in keys:
        c = COUNTIES[k]
        has = k in owned
        uri = _thumb_data_uri(k)
        img = f'<img src="{uri}">' if uri else ""
        badge = f'<div class="badge">x{owned[k]}</div>' if has and owned[k] > 1 else ""
        name = c.name if has else "???"
        cells.append(
            f'<div class="cell {c.tier} {"owned" if has else "missing"}">'
            f'{badge}{img}<div class="cname">{name}</div></div>'
        )
    sections = [f'<div class="grid">{"".join(cells)}</div>']

    total = len(COUNTIES)
    caught = len(owned)
    html = (
        read_html_template("templates/county_dex.html")
        .replace("{{ TITLE }}", f"{html_escape(display_name)}'s County Dex")
        .replace("{{ COMPLETION }}", f"{caught}/{total}")
        .replace("{{ SUBTITLE }}", f"{caught / total:.0%} of the realm collected")
        .replace("{{ SECTIONS }}", "".join(sections))
    )
    return await screenshot_html(html, size=(1100, 1200), element_selector=".container")


def _dex_embed(display_name: str, owned: dict) -> discord.Embed:
    total = len(COUNTIES)
    caught = len(owned)
    embed = discord.Embed(
        title=f"{display_name}'s County Dex",
        description=f"**{caught}/{total}** counties collected "
                    f"({caught / total:.0%})",
        colour=discord.Colour.dark_green(),
    )
    for tier in ("legendary", "rare", "uncommon", "common"):
        keys = [k for k, c in COUNTIES.items() if c.tier == tier]
        got = [k for k in keys if k in owned]
        names = ", ".join(
            f"{COUNTIES[k].name}" + (f" x{owned[k]}" if owned[k] > 1 else "")
            for k in sorted(got, key=lambda k: COUNTIES[k].name)
        ) or "*none yet*"
        if len(names) > 1000:
            names = names[:997] + "..."
        embed.add_field(name=f"{TIER_LABELS[tier]} - {len(got)}/{len(keys)}",
                        value=names, inline=False)
    return embed


async def handle_county_dex_command(interaction: Interaction):
    if await _deny_if_gated(interaction):
        return
    owned = E.collection(interaction.user.id)
    if not getattr(config, "COUNTY_DEX_IMAGE_ENABLED", True):
        await interaction.response.send_message(
            embed=_dex_embed(interaction.user.display_name, owned))
        return
    await interaction.response.defer()
    try:
        buf = await _render_dex_image(interaction.user.display_name, owned)
        await interaction.followup.send(file=discord.File(buf, filename="county_dex.png"))
    except Exception:
        logger.error("County dex render failed, falling back to embed", exc_info=True)
        await interaction.followup.send(
            embed=_dex_embed(interaction.user.display_name, owned))


async def handle_county_info_command(interaction: Interaction, county: str):
    if await _deny_if_gated(interaction):
        return
    key = match_county(county)
    if not key:
        await interaction.response.send_message(
            f"No county matches \"{county}\".", ephemeral=True)
        return
    c = COUNTIES[key]
    base_c, base_g = base_stats(key)
    embed = discord.Embed(title=c.name, colour=TIER_COLOURS[c.tier])
    embed.add_field(name="Nation", value=c.nation)
    embed.add_field(name="Rarity", value=TIER_LABELS[c.tier])
    embed.add_field(name="Sell price", value=f"{config.COUNTY_SELL_PRICES[c.tier]:,} UKP")
    embed.add_field(name="Base stats", value=f"⚔️ {base_c} Clout · 🛡️ {base_g} Grit")
    embed.add_field(name="You own", value=str(E.owned_count(interaction.user.id, key)))
    embed.add_field(name="Caught server-wide", value=str(E.server_caught_count(key)))
    best = E.best_instance(interaction.user.id, key)
    if best:
        embed.add_field(name="Your best copy", value=_stat_line(key, *best), inline=False)
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


class CountySellView(discord.ui.View):
    """Interactive seller: dropdown of your counties, sort buttons, sell buttons.
    Lowest-stat copies always sell first. Ephemeral per-user, so no persistence."""

    _SORTS = [("Price", "price"), ("Dupes", "dupes"), ("A-Z", "name")]

    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.sort = "price"
        self.selected: str | None = None
        self.truncated = False
        self._rebuild()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This sell menu belongs to someone else - run /county-sell yourself.",
                ephemeral=True)
            return False
        return True

    def _holdings(self):
        owned = E.collection(self.user_id)
        rows = [
            (k, COUNTIES[k], n, config.COUNTY_SELL_PRICES[COUNTIES[k].tier])
            for k, n in owned.items()
        ]
        if self.sort == "price":
            rows.sort(key=lambda r: (-r[3], r[1].name))
        elif self.sort == "dupes":
            rows.sort(key=lambda r: (-r[2], r[1].name))
        else:
            rows.sort(key=lambda r: r[1].name)
        return rows

    def _rebuild(self):
        self.clear_items()
        rows = self._holdings()
        self.truncated = len(rows) > 25
        if self.selected not in {r[0] for r in rows}:
            self.selected = None

        if rows:
            options = [
                discord.SelectOption(
                    label=c.name, value=k, default=(k == self.selected),
                    description=f"x{n} owned · {price:,} UKP each · {TIER_LABELS[c.tier]}",
                )
                for k, c, n, price in rows[:25]
            ]
            select = discord.ui.Select(placeholder="Pick a county to sell...",
                                       options=options, row=0)
            select.callback = self._on_select
            self.add_item(select)

        for label, mode in self._SORTS:
            btn = discord.ui.Button(
                label=f"Sort: {label}", row=1,
                style=discord.ButtonStyle.primary if self.sort == mode
                else discord.ButtonStyle.secondary,
            )
            btn.callback = self._sorter(mode)
            self.add_item(btn)

        count = next((n for k, _c, n, _p in rows if k == self.selected), 0)
        for label, mode, enabled in [
            ("Sell one", "one", count >= 1),
            ("Sell dupes (keep 1)", "dupes", count >= 2),
            ("Sell ALL of it", "all", count >= 1),
        ]:
            btn = discord.ui.Button(label=label, row=2,
                                    style=discord.ButtonStyle.danger,
                                    disabled=not enabled)
            btn.callback = self._seller(mode)
            self.add_item(btn)

    def _content(self, note: str = "") -> str:
        rows = self._holdings()
        total = sum(n for _k, _c, n, _p in rows)
        value = sum(n * p for _k, _c, n, p in rows)
        lines = [f"💷 **Sell county balls** - you hold **{total}** "
                 f"(full buyback value {value:,} UKP)."]
        if self.selected:
            c = COUNTIES[self.selected]
            n = next((n for k, _c, n, _p in rows if k == self.selected), 0)
            lines.append(
                f"Selected: **{c.name}** x{n} at "
                f"{config.COUNTY_SELL_PRICES[c.tier]:,} UKP each. "
                "Lowest-stat copies sell first.")
        else:
            lines.append("Pick a county from the dropdown.")
        if self.truncated:
            lines.append("*Showing the top 25 by current sort.*")
        if note:
            lines.append(note)
        return "\n".join(lines)

    async def _redraw(self, interaction: Interaction, note: str = ""):
        self._rebuild()
        await interaction.response.edit_message(content=self._content(note), view=self)

    async def _on_select(self, interaction: Interaction):
        self.selected = interaction.data["values"][0]
        await self._redraw(interaction)

    def _sorter(self, mode: str):
        async def cb(interaction: Interaction):
            self.sort = mode
            await self._redraw(interaction)
        return cb

    def _seller(self, mode: str):
        async def cb(interaction: Interaction):
            key = self.selected
            if not key:
                await self._redraw(interaction)
                return
            have = E.owned_count(self.user_id, key)
            qty = {"one": 1, "dupes": max(have - 1, 0), "all": have}[mode]
            if qty < 1 or have < qty:
                await self._redraw(interaction, "Nothing to sell there any more.")
                return
            paid = E.sell(self.user_id, key, qty)
            if paid is None:
                await self._redraw(
                    interaction, "⚠️ The bank couldn't cover that sale - try again later.")
                return
            name = COUNTIES[key].name
            gone = " That was your last one - it's gone from your dex!" if qty == have else ""
            await self._redraw(
                interaction, f"✅ Sold {qty}x **{name}** for **{paid:,} UKPence**.{gone}")
        return cb


async def handle_county_sell_command(interaction: Interaction, county: str | None,
                                     quantity: int):
    if await _deny_if_gated(interaction):
        return

    if county is None:
        if not E.collection(interaction.user.id):
            await interaction.response.send_message(
                "You don't own any county balls yet - go catch some!", ephemeral=True)
            return
        view = CountySellView(interaction.user.id)
        await interaction.response.send_message(view._content(), view=view, ephemeral=True)
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
