"""Skyrim Discord layer - Components V2 views, button routing and the /skyrim entry.

Two surfaces:
  • The HUB - everything personal (character sheet, shop, perks, rankings, help,
    picking a destination) lives in ONE ephemeral message that edits itself in
    place like a tiny app, so channels never fill with menu spam.
  • The DELVE - the actual adventure is a public message (spectators welcome),
    owner-clicked, rebuilt on every action, persisted by message id and resumed
    across restarts (reattach_skyrim_view), exactly like the casino boards.

Art: data/skyrim/<key>.png transparent panels shown in a MediaGallery above the
text. Every scene falls back cleanly to text if its file is missing, so the game
is fully playable before/without the art drop.
"""

import io
import os
import logging

import discord
from discord import Interaction
from PIL import Image

import config
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E

logger = logging.getLogger(__name__)

ACCENT = discord.Colour(0x5A7D9A)        # cold Nordic steel-blue
_ASSET_DIR = os.path.join("data", "skyrim")
_ASSET_PX = 512
_asset_cache = {}


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------
def _asset_bytes(name: str):
    """data/skyrim/<name>.webp (or .png) downscaled + cached; None (cached) when
    absent. Scenes ship as WebP - a fraction of PNG's size on the VM's small disk -
    and are re-encoded to a 512px WebP for the actual Discord upload."""
    if name in _asset_cache:
        return _asset_cache[name]
    data = None
    try:
        for ext in ("webp", "png"):
            path = os.path.join(_ASSET_DIR, f"{name}.{ext}")
            if os.path.exists(path):
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((_ASSET_PX, _ASSET_PX), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, format="WEBP", quality=85)
                    data = buf.getvalue()
                break
    except Exception:
        logger.debug("skyrim asset load failed: %s", name, exc_info=True)
    _asset_cache[name] = data
    return data


def _scene_art(delve: E.Delve) -> str:
    if delve.state == "cleared":
        return "victory"
    if delve.state == "dead":
        return "death"
    if delve.state == "launched":
        return "giant"
    if delve.state in ("left", "fled", "abandoned"):
        return "leave"
    r = delve.room
    if r["kind"] == "enemy":
        e = D.ENEMIES[r["key"]]
        # Dragons change picture with the fight: airborne (wheeling, breathing) vs
        # grounded (slammed down by the Voice). Falls back to the base art if the
        # <art>_air / <art>_grounded variants haven't been dropped yet.
        if e["type"] == "dragon":
            variant = f"{e['art']}_{'grounded' if delve.grounded else 'air'}"
            if _asset_bytes(variant) is not None:
                return variant
        return e["art"]
    return D.EVENTS[r["key"]]["art"]


def _gallery_files(view: discord.ui.LayoutView, art_key: str, fname: str = "skyrim.webp"):
    data = _asset_bytes(art_key)
    if data is None:
        return []
    gallery = discord.ui.MediaGallery()
    gallery.add_item(media=f"attachment://{fname}")
    view.add_item(gallery)
    return [discord.File(io.BytesIO(data), filename=fname)]


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------
def _hearts_str(delve: E.Delve, profile) -> str:
    mx = E.heart_max(profile)
    return "❤️" * max(0, delve.hearts) + "🖤" * max(0, mx - delve.hearts)


def _bar(value: int, lo: int = 15, hi: int = 100, width: int = 8) -> str:
    filled = round(width * (value - lo) / (hi - lo))
    return "▰" * max(0, filled) + "▱" * max(0, width - filled)


def _status_line(delve: E.Delve, profile) -> str:
    stone = D.STONES[profile["stone"]]
    bits = [f"{stone['emoji']} <@{delve.player_id}> Lv {E.level(profile)}",
            _hearts_str(delve, profile), f"🧪 {profile['potions']}"]
    if profile["words"] > 0:
        bits.append(f"🗣️ {delve.shout_charges}")
    bits.append(f"💰 {delve.satchel:,} in satchel")
    if delve.stirred:
        bits.append(f"🔥 {E.stirred_name(delve.stirred)}")
    if delve.pacts:
        bits.append(f"⚖️ x{E.pact_mult(delve):g}")
    return "  ·  ".join(bits)


def _delve_text(delve: E.Delve, profile) -> str:
    loc = delve.loc
    n = len(delve.rooms)
    daily_tag = "  ·  📅 Daily Delve" if delve.daily else ""
    if delve.playing():
        r = delve.room
        if getattr(delve, "kind", None) == "soulcairn":
            head = f"## 💀 The Soul Cairn - Depth {delve.depth}"
        elif r["kind"] == "enemy" and r["boss"]:
            head = f"## {loc['emoji']} {loc['name']} - the final chamber{daily_tag}"
        else:
            head = f"## {loc['emoji']} {loc['name']} - room {delve.idx + 1}/{n}{daily_tag}"
    else:
        head = f"## {loc['emoji']} {loc['name']}{daily_tag}"
    lines = [head, _status_line(delve, profile), ""]
    if delve.log:
        lines.extend(delve.log)
        lines.append("")

    if not delve.playing():
        lines.append(delve.result_line)
        left = E.delves_left(profile)
        lines.append(f"-# ⚔️ {delve.kills} kills this delve  ·  🛌 {left} "
                     f"delve{'s' if left != 1 else ''} left today")
        return "\n".join(lines)

    r = delve.room
    if r["kind"] == "enemy":
        e = D.ENEMIES[r["key"]]
        nd = E.named_dragon(delve)
        name = nd["name"] if nd else e["name"]
        if delve.engaged:
            lines.append(f"{e['emoji']} The **{name}** presses the attack!")
        else:
            intro = D.pick(e["intro"])
            if nd:                                # name the week's dragon in place of "Dragon"
                intro = intro.replace("**Dragon**", f"**{nd['name']}**")
            lines.append(f"{e['emoji']} {intro}")
        if nd:
            lines.append(f"-# 🐲 **{nd['name']}** - {nd['twist']}")
        if r.get("affix"):
            aff = D.AFFIXES[r["affix"]]
            lines.append(f"-# {aff['emoji']} **{aff['tag']} {e['name']}** - {aff['desc']}")
        if r.get("bounty"):
            title = D.BOUNTY_TITLES.get(e["type"], "Notorious")
            lines.append(f"-# 🏴 A **{title} {e['name']}** - a marked bounty. Tougher, "
                         f"but worth triple.")
        if delve.enemy_hp > 1 or (e.get("hp", 1) > 1 and delve.enemy_hp > 0):
            lines.append(f"-# {'🩸' * delve.enemy_hp} it will take {delve.enemy_hp} more "
                         f"telling blow{'s' if delve.enemy_hp != 1 else ''}")
        if e["type"] == "dragon" and not delve.grounded:
            lines.append("-# ☁️ **Airborne** - blades and fire barely reach it. Loose arrows, "
                         "or **Shout** it out of the sky.")
        if delve.grounded:
            lines.append("-# 🪨 The dragon is **grounded** - every style bites now. Press it!")
        if delve.venom:
            lines.append("-# 🟢 **Venom in your blood** - drink before you leave this room.")
        if delve.ambush:
            lines.append(f"-# 🥷 You are **hidden and in position** - strike at "
                         f"+{E.AMBUSH_BONUS}%, or slip past unseen.")
    else:
        ev = D.EVENTS[r["key"]]
        text = ev["text"]
        if r["key"] == "fallen" and r.get("corpse"):
            who = r["corpse"].get("name", "a fallen soul")
            tag = " (their end is fresh)" if r["corpse"].get("real") else ""
            text = f"A body slumps against the wall - **{who}**{tag}, satchel still in cold hands."
        lines.append(f"{ev['emoji']} {text}")
    hint = delve.next_hint()
    if hint:
        lines.append(f"-# {hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Delve view
# ---------------------------------------------------------------------------
def _btn(style, label, custom_id, cb, emoji=None, disabled=False):
    b = discord.ui.Button(style=style, label=label, custom_id=custom_id,
                          emoji=emoji, disabled=disabled)
    b.callback = cb
    return b


def build_delve_layout(delve: E.Delve, profile):
    """(view, files) for the delve's current state."""
    view = discord.ui.LayoutView(timeout=None)
    files = _gallery_files(view, _scene_art(delve))
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(_delve_text(delve, profile)))
    view.add_item(box)

    did = delve.delve_id
    if not delve.playing():
        row = discord.ui.ActionRow()
        row.add_item(_btn(discord.ButtonStyle.primary, "Delve Again", f"skyrim:{did}:again",
                          _make_cb(delve, "again"), emoji="🗺️"))
        row.add_item(_btn(discord.ButtonStyle.secondary, "Character", f"skyrim:{did}:sheet",
                          _make_cb(delve, "sheet"), emoji="👤"))
        row.add_item(_btn(discord.ButtonStyle.secondary, "Help", f"skyrim:{did}:help",
                          _make_cb(delve, "help"), emoji="📖"))
        view.add_item(row)
        return view, files

    r = delve.room
    row1 = discord.ui.ActionRow()
    row2 = discord.ui.ActionRow()
    if r["kind"] == "enemy":
        key = r["key"]
        e = D.ENEMIES[key]
        # three ways to hurt it - pick the tool that fits the foe (odds shown
        # already include the ambush bonus when you're hidden and in position)
        for skey, sd in D.STYLES.items():
            pct = E.fight_pct(profile, key, skey, delve)
            over = int(round(E.overkill_crit(profile, key, skey, delve) * 100))
            label = f"{sd['label']} {pct}%" + (f"  ⚡{over}" if over else "")
            row1.add_item(_btn(discord.ButtonStyle.danger, label,
                               f"skyrim:{did}:atk:{skey}", _make_cb(delve, f"atk:{skey}"),
                               emoji=sd["emoji"]))
        if delve.ambush:
            row1.add_item(_btn(discord.ButtonStyle.primary, "Slip past",
                               f"skyrim:{did}:slp", _make_cb(delve, "slp"), emoji="🥷"))
        else:
            p_snk = E.sneak_pct(profile, key)
            if p_snk is not None and not delve.engaged:
                row1.add_item(_btn(discord.ButtonStyle.primary, f"Sneak {p_snk}%",
                                   f"skyrim:{did}:snk", _make_cb(delve, "snk"), emoji="🥷"))
            p_per = E.persuade_pct(profile, key)
            if p_per is not None and not delve.engaged:
                row1.add_item(_btn(discord.ButtonStyle.primary, f"Persuade {p_per}%",
                                   f"skyrim:{did}:per", _make_cb(delve, "per"), emoji="💬"))
        shout_row = _shout_control(delve, profile, e)
        if profile["potions"] > 0 and (delve.hearts < E.heart_max(profile) or delve.venom) \
                and "namira" not in delve.pacts:
            row2.add_item(_btn(discord.ButtonStyle.secondary, f"Potion ({profile['potions']})",
                               f"skyrim:{did}:pot", _make_cb(delve, "pot"), emoji="🧪"))
        if "clavicus" not in delve.pacts:
            leave_label = "Flee" if delve.engaged else f"Leave ({delve.satchel:,})"
            row2.add_item(_btn(discord.ButtonStyle.secondary, leave_label,
                               f"skyrim:{did}:lve", _make_cb(delve, "lve"),
                               emoji="🏃" if delve.engaged else "🚪"))
    else:
        shout_row = None
        key = r["key"]
        if key == "chest" and r.get("locked"):
            choices = [("🔓", f"Pick the lock {E.lockpick_pct(profile)}%", "pick"),
                       ("🚶", "Move on", "skip")]
        else:
            choices = _EVENT_CHOICES[key]
        for emoji, label, act in choices:
            row1.add_item(_btn(discord.ButtonStyle.primary, label,
                               f"skyrim:{did}:evt:{act}", _make_cb(delve, f"evt:{act}"),
                               emoji=emoji))
        if key != "giant":
            row2.add_item(_btn(discord.ButtonStyle.secondary, f"Leave ({delve.satchel:,})",
                               f"skyrim:{did}:lve", _make_cb(delve, "lve"), emoji="🚪"))
    view.add_item(row1)
    if shout_row is not None:
        view.add_item(shout_row)
    if row2.children:
        view.add_item(row2)
    return view, files


# The Words of Power loadout. One known word = a single FUS button (in row2). Two or
# three = a select of the shouts you can afford, so the shared charge pool becomes a
# rationing choice without spending action-row buttons the fight can't spare.
_SHOUT_EFFECTS = {
    1: ("FUS", "Ground a dragon, or stagger a foe (1 charge)"),
    2: ("FUS RO", "Flatten a room; ground + chip a dragon (2 charges)"),
    3: ("FUS RO DAH", "The full Thu'um: 2 damage to anything (3 charges)"),
}


def _shout_control(delve: E.Delve, profile, e):
    words = profile.get("words", 0)
    if words <= 0 or delve.shout_charges <= 0:
        return None
    grounded_dragon = e["type"] == "dragon" and delve.grounded
    costs = [c for c in range(1, min(words, delve.shout_charges) + 1)
             if not (grounded_dragon and c == 1)]     # FUS is wasted on a grounded dragon
    if not costs:
        return None
    row = discord.ui.ActionRow()
    did = delve.delve_id
    if costs == [1]:
        row.add_item(_btn(discord.ButtonStyle.success, f"FUS  ({delve.shout_charges})",
                          f"skyrim:{did}:sht:1", _make_cb(delve, "sht:1"), emoji="🗣️"))
        return row
    select = discord.ui.Select(placeholder=f"🗣️ Shout - {delve.shout_charges} charge(s) in the Voice",
                               custom_id=f"skyrim:{did}:shtsel", min_values=1, max_values=1)
    for c in costs:
        name, desc = _SHOUT_EFFECTS[c]
        select.add_option(label=name, value=str(c), description=desc, emoji="🗣️")

    async def _on_shout(inter: Interaction):
        await _handle_delve_click(inter, delve, f"sht:{select.values[0]}")
    select.callback = _on_shout
    row.add_item(select)
    return row


_EVENT_CHOICES = {
    "chest": [("🧰", "Open it", "open"), ("🚶", "Move on", "skip")],
    "sweetroll": [("🍩", "Take the sweetroll", "take"), ("🚶", "Walk away", "skip")],
    "shrine": [("🙏", "Pray", "pray"), ("🚶", "Move on", "skip")],
    "satchel": [("🧪", "Take it", "take"), ("🚶", "Move on", "skip")],
    "maiq": [("💬", "Talk to M'aiq", "talk"), ("🚶", "Move on", "skip")],
    "wordwall": [("🗣️", "Approach the wall", "approach"), ("🚶", "Move on", "skip")],
    "giant": [("🚶", "Back away slowly", "retreat"), ("🧀", "About that cheese...", "approach")],
    "knee_trap": [("🚶", "Limp onward", "continue")],
    "fork": [("🪙", "The deep way", "deep"), ("🚶", "The safe way", "safe")],
    "fallen": [("💰", "Loot the satchel", "loot"), ("⚰️", "Lay them to rest", "honor")],
    "mudcrab": [("🦀", "Trade with the crab", "trade"), ("🚶", "Move on", "skip")],
    "nazeem": [("😤", "\"Yes, actually.\"", "yes"), ("😮‍💨", "Sigh deeply", "sigh")],
    "adoring_fan": [("🤩", "Let him follow", "adopt"), ("👉", "Send him home", "skip")],
}


# ---------------------------------------------------------------------------
# Delve interaction routing
# ---------------------------------------------------------------------------
def _make_cb(delve: E.Delve, action: str):
    async def _cb(interaction: Interaction):
        await _handle_delve_click(interaction, delve, action)
    return _cb


async def _rerender_delve(interaction: Interaction, delve: E.Delve, profile):
    view, files = build_delve_layout(delve, profile)
    try:
        await interaction.response.edit_message(view=view, attachments=files)
    except (discord.NotFound, discord.InteractionResponded):
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=view, attachments=files)
        except discord.HTTPException:
            logger.debug("skyrim delve fallback edit failed", exc_info=True)
    try:
        if delve.message_id:
            interaction.client.add_view(view, message_id=delve.message_id)
    except Exception:
        logger.debug("skyrim add_view failed", exc_info=True)


async def _handle_delve_click(interaction: Interaction, delve: E.Delve, action: str):
    if interaction.user.id != delve.player_id:
        await interaction.response.send_message(
            "This is not your adventure - run `/skyrim` to begin your own.", ephemeral=True)
        return

    # Buttons that work on finished boards (and never mutate the delve).
    if action == "help":
        await interaction.response.send_message(_help_text(), ephemeral=True)
        return
    if action == "sheet":
        profile = E.get_profile(interaction.user.id)
        if profile is None:
            await interaction.response.send_message("Run `/skyrim` first.", ephemeral=True)
            return
        await interaction.response.send_message(_sheet_text(profile), ephemeral=True)
        return
    if action == "again":
        await _open_location_picker(interaction)
        return

    profile = E.get_profile(interaction.user.id)
    if profile is None or profile.get("active_delve") != delve.message_id:
        await interaction.response.send_message(
            "This delve has ended - run `/skyrim` to set out again.", ephemeral=True)
        return
    if delve.busy or not delve.playing():
        await interaction.response.defer()
        return

    delve.busy = True
    try:
        if action == "atk":                       # legacy pre-styles button: best tool
            delve.act_attack(profile)
        elif action.startswith("atk:"):
            delve.act_attack(profile, action.split(":", 1)[1])
        elif action == "slp":
            delve.act_slip(profile)
        elif action == "snk":
            delve.act_sneak(profile)
        elif action == "per":
            delve.act_persuade(profile)
        elif action == "sht" or action.startswith("sht:"):
            cost = int(action.split(":", 1)[1]) if ":" in action else None
            delve.act_shout(profile, cost)
        elif action == "pot":
            delve.act_potion(profile)
        elif action == "lve":
            delve.act_leave(profile)
        elif action.startswith("evt:"):
            delve.act_event(profile, action.split(":", 1)[1])
        else:
            await interaction.response.defer()
            return

        E.save_profile(profile)
        if delve.playing():
            E.save_delve(delve)
        else:
            E.delete_delve(delve.message_id)
            if delve.daily:
                E.record_daily_result(profile, delve)
        await _rerender_delve(interaction, delve, profile)
    finally:
        delve.busy = False


# ---------------------------------------------------------------------------
# Ephemeral hub - one message that edits itself between panels
# ---------------------------------------------------------------------------
def _panel_view(text: str, rows, art_key: str = None):
    """(view, files) - a Container panel + button rows for the ephemeral hub."""
    view = discord.ui.LayoutView(timeout=900)
    files = _gallery_files(view, art_key) if art_key else []
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(text))
    view.add_item(box)
    for row in rows:
        view.add_item(row)
    return view, files


async def _edit_panel(interaction: Interaction, text: str, rows, art_key: str = None):
    view, files = _panel_view(text, rows, art_key)
    await interaction.response.edit_message(view=view, attachments=files)


def _hub_rows(profile):
    row1 = discord.ui.ActionRow()
    row1.add_item(_cb_btn(discord.ButtonStyle.success, "Adventure", "🗺️", _hub_adventure))
    row1.add_item(_cb_btn(discord.ButtonStyle.primary, "Character", "👤", _hub_character))
    row1.add_item(_cb_btn(discord.ButtonStyle.primary, "Belethor's", "🏪", _hub_shop))
    pts = E.perk_points(profile)
    row1.add_item(_cb_btn(discord.ButtonStyle.primary,
                          f"Perks ({pts})" if pts else "Perks", "📜", _hub_perks))
    row2 = discord.ui.ActionRow()
    daily_label = "Daily Delve" if E.daily_available(profile) else "Daily Results"
    row2.add_item(_cb_btn(discord.ButtonStyle.success if E.daily_available(profile)
                          else discord.ButtonStyle.secondary, daily_label, "📅", _hub_daily))
    # Factions + Expeditions light up when there's something to collect this week/day
    fac_ready = profile.get("allegiance") and E.faction_progress(profile)[2] \
        and not (profile.get("faction") or {}).get("claimed")
    row2.add_item(_cb_btn(discord.ButtonStyle.success if fac_ready else discord.ButtonStyle.secondary,
                          "Factions", "🏰", _hub_factions))
    exp_ready = E.expedition_ready(profile)
    row2.add_item(_cb_btn(discord.ButtonStyle.success if exp_ready else discord.ButtonStyle.secondary,
                          "Expedition", "🧭", _hub_expedition))
    row3 = discord.ui.ActionRow()
    row3.add_item(_cb_btn(discord.ButtonStyle.secondary, "Rankings", "🏆", _hub_rankings))
    row3.add_item(_cb_btn(discord.ButtonStyle.secondary, "How it works", "📖", _hub_help))
    return [row1, row2, row3]


def _cb_btn(style, label, emoji, cb):
    b = discord.ui.Button(style=style, label=label, emoji=emoji)
    b.callback = cb
    return b


def _back_row():
    row = discord.ui.ActionRow()
    row.add_item(_cb_btn(discord.ButtonStyle.secondary, "Back", "⬅️", _hub_root))
    return row


def _hub_text(profile) -> str:
    cls = D.STONES[profile["stone"]]
    left = E.delves_left(profile)
    into, need = D.xp_into_level(profile["xp"])
    daily_bit = ("📅 daily delve **available**" if E.daily_available(profile)
                 else "📅 daily delve done")
    return (
        f"## 🐉 Skyrim\n"
        f"{cls['emoji']} **{profile['name']}** - Level {E.level(profile)} "
        f"{E.archetype(profile)}  ·  💰 {profile['septims']:,} septims\n"
        f"-# XP {_bar(into, 0, need)} {into}/{need} to next level\n"
        f"-# {E.weather_line()}\n"
        f"-# 🛌 {left}/{getattr(config, 'SKYRIM_DELVES_PER_DAY', 3)} delves left today  ·  {daily_bit}\n\n"
        f"Delve the ruins of Skyrim, learn words of power, slay dragons. Levels, gear, "
        f"souls and skills are yours forever - only the **septims in your satchel** are at "
        f"stake when you die.\n"
        f"-# {D.pick(D.GUARD_LINES)}"
    )


async def _show_hub_root(interaction: Interaction, profile, *, first_response=False):
    profile["name"] = discord.utils.escape_markdown(interaction.user.display_name)
    E.save_profile(profile)
    view, files = _panel_view(_hub_text(profile), _hub_rows(profile), art_key="hub")
    if first_response:
        await interaction.response.send_message(view=view, files=files, ephemeral=True)
    else:
        await interaction.response.edit_message(view=view, attachments=files)


async def _hub_root(interaction: Interaction):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    await _show_hub_root(interaction, profile)


# --- class pick (first run) ----------------------------------------------------
async def _show_class_pick(interaction: Interaction, *, first_response=False):
    rows = []
    row = discord.ui.ActionRow()
    for key, stone in D.STONES.items():
        async def _pick(inter: Interaction, k=key):
            if E.get_profile(inter.user.id) is None:
                name = discord.utils.escape_markdown(inter.user.display_name)
                E.create_profile(inter.user.id, name, k)
            await _hub_root(inter)
        row.add_item(_cb_btn(discord.ButtonStyle.primary, stone["name"], stone["emoji"], _pick))
    rows.append(row)
    text = "## 🐉 Skyrim\n" + D.INTRO_TEXT + "\n\n" + "\n".join(
        f"{st['emoji']} **{st['name']}** - {st['blurb']}" for st in D.STONES.values())
    view, files = _panel_view(text, rows, art_key="intro")
    if first_response:
        await interaction.response.send_message(view=view, files=files, ephemeral=True)
    else:
        await interaction.response.edit_message(view=view, attachments=files)


# --- adventure / location picker ------------------------------------------------
async def _hub_adventure(interaction: Interaction):
    await _open_location_picker(interaction, edit_hub=True)


def _delve_jump_url(interaction: Interaction, delve: E.Delve) -> str | None:
    if interaction.guild_id is None or delve.channel_id is None or delve.message_id is None:
        return None
    return f"https://discord.com/channels/{interaction.guild_id}/{delve.channel_id}/{delve.message_id}"


async def _open_location_picker(interaction: Interaction, edit_hub: bool = False):
    """From the hub (edit in place) or a finished delve board (fresh ephemeral)."""
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        if edit_hub:
            await _show_class_pick(interaction)
        else:
            await interaction.response.send_message("Run `/skyrim` first.", ephemeral=True)
        return

    # A delve already underway? Offer the way back before anything else - starting
    # a new one silently walks out of the old one, which surprises people.
    live = E.load_delve(profile.get("active_delve")) if profile.get("active_delve") else None
    if live is not None and live.playing():
        loc = live.loc
        text = (f"## 🗺️ You are mid-delve\n"
                f"{loc['emoji']} **{loc['name']}** - room {live.idx + 1}/{len(live.rooms)}, "
                f"💰 {live.satchel:,} in the satchel.\n"
                f"Starting a new delve walks out of this one first (the satchel banks safely).")
        row = discord.ui.ActionRow()
        url = _delve_jump_url(interaction, live)
        if url:
            row.add_item(discord.ui.Button(style=discord.ButtonStyle.link,
                                           label="Return to delve", url=url, emoji="↩️"))

        async def _new(inter: Interaction):
            await _show_offers(inter, edit_hub=True)
        row.add_item(_cb_btn(discord.ButtonStyle.danger, "Abandon and delve anew", "🗺️", _new))
        rows = [row] + ([_back_row()] if edit_hub else [])
        if edit_hub:
            await _edit_panel(interaction, text, rows)
        else:
            view, files = _panel_view(text, rows)
            await interaction.response.send_message(view=view, files=files, ephemeral=True)
        return
    await _show_offers(interaction, edit_hub=edit_hub)


async def _show_offers(interaction: Interaction, edit_hub: bool = False):
    profile = E.get_profile(interaction.user.id)
    left = E.delves_left(profile)
    lines = ["## 🗺️ Where to, Dovahkiin?",
             f"-# {E.weather_line()}",
             f"-# 🛌 {left} delve{'s' if left != 1 else ''} left  ·  "
             f"💰 only the satchel is at stake  ·  ⛰️ new roads at dawn\n"]
    rows = []
    if left <= 0:
        lines.append("🛌 You need to rest - no delves left today. They reset at midnight "
                     "(UK time). The 📅 **Daily Delve** in the hub is separate, if you "
                     "haven't braved it yet.")
    else:
        row = discord.ui.ActionRow()
        for key in E.offer_locations(profile):
            loc = D.LOCATIONS[key]
            # the location line stays clean; every modifier lives on a small chip line
            lines.append(f"{loc['emoji']} **{loc['name']}** - {loc['desc']}")
            bits = [loc["difficulty"], f"{loc['rooms']} rooms"]
            drops = E.location_drops(key)
            if drops:
                bits.append(f"🧪{drops}")
            rc = E.route_condition(key)
            if rc:
                c = D.ROUTE_CONDITIONS[rc]
                bits.append(f"{c['emoji']} {c['name']}: {c['short']}")
            r = E.stirred_rank(profile, key)
            if r:
                bits.append(f"🔥 {E.stirred_name(r)}: -{D.STIRRED_FIGHT_PER_RANK * r}% / "
                            f"+{int(D.STIRRED_CLEAR_PER_RANK * r * 100)}%")
            lines.append("-# " + "  ·  ".join(bits))

            async def _go(inter: Interaction, k=key):
                await _launch_delve(inter, k)
            row.add_item(_cb_btn(
                discord.ButtonStyle.danger if D.LOCATIONS[key].get("dragon_lair")
                else discord.ButtonStyle.primary, loc["name"], loc["emoji"], _go))
        rows.append(row)
        if E.level(profile) >= E.PACT_MIN_LEVEL:
            sworn = profile.get("nextpacts") or []
            if sworn:
                names = ", ".join(D.PACTS[k]["name"] for k in sworn if k in D.PACTS)
                lines.append(f"\n⚖️ **Sworn for the next delve:** {names}")
            prow = discord.ui.ActionRow()
            prow.add_item(_cb_btn(discord.ButtonStyle.secondary,
                                  f"Pacts ({len(sworn)})" if sworn else "Pacts", "⚖️", _hub_pacts))
            rows.append(prow)

    # Skuldafn - shown only once earned; its attempt is daily and separate from stamina.
    ready, req_line = E.alduin_ready(profile)
    if E.alduin_available(profile):
        loc = D.LOCATIONS["skuldafn"]
        echo = E.alduin_echo(profile)
        echo_bit = f"  ·  🌑 Echo {echo}: he returns stronger" if echo else ""
        lines.append(f"\n🌑 **{loc['name']}**  ·  {loc['difficulty']} - {loc['desc']}{echo_bit}")
        arow = discord.ui.ActionRow()

        async def _alduin(inter: Interaction):
            await _launch_delve(inter, "skuldafn", kind="alduin")
        label = f"Face Alduin (Echo {echo})" if echo else "Face Alduin"
        arow.add_item(_cb_btn(discord.ButtonStyle.danger, label, "🌑", _alduin))
        rows.append(arow)
    elif ready:
        lines.append("\n-# 🌑 Alduin waits at Skuldafn - one attempt per day. Return tomorrow.")
    elif E.level(profile) >= 12:
        lines.append(f"\n-# 🌑 Greybeards' whisper: the World-Eater will meet you when you are "
                     f"ready - {req_line}.")

    # The Soul Cairn - unlocked once Alduin is down; one endless descent per day.
    if E.soulcairn_unlocked(profile):
        best = E.soulcairn_best(profile)
        best_str = f"  ·  deepest: **{best}**" if best else ""
        if E.soulcairn_available(profile):
            lines.append(f"\n💀 **The Soul Cairn**  ·  ENDLESS - how deep do you dare?{best_str}")
            srow = discord.ui.ActionRow()

            async def _cairn(inter: Interaction):
                await _launch_delve(inter, "soul_cairn", kind="soulcairn")
            srow.add_item(_cb_btn(discord.ButtonStyle.danger, "Descend the Soul Cairn", "💀", _cairn))
            rows.append(srow)
        else:
            lines.append(f"\n-# 💀 The Soul Cairn is spent for today{best_str}. Return tomorrow.")

    rows += [_back_row()] if edit_hub else []
    if edit_hub:
        # a button on an ephemeral panel (hub, or the mid-delve prompt): edit in place
        await _edit_panel(interaction, "\n".join(lines), rows)
    else:
        # a button on the PUBLIC delve board (Delve Again): never edit that message
        view, files = _panel_view("\n".join(lines), rows)
        await interaction.response.send_message(view=view, files=files, ephemeral=True)


async def _launch_delve(interaction: Interaction, loc_key: str, kind: str = "normal"):
    profile = E.get_profile(interaction.user.id)
    blocked = (profile is None
               or (kind == "normal" and E.delves_left(profile) <= 0)
               or (kind == "daily" and not E.daily_available(profile))
               or (kind == "alduin" and not E.alduin_available(profile))
               or (kind == "soulcairn" and not E.soulcairn_available(profile)))
    if blocked:
        await interaction.response.edit_message(
            view=_notice_view("🛌 Not today - that delve isn't available right now."),
            attachments=[])
        return
    if kind == "soulcairn":
        delve = E.start_soulcairn(profile, interaction.channel_id)
    else:
        delve = E.start_delve(profile, interaction.channel_id, loc_key, kind=kind)
    view, files = build_delve_layout(delve, profile)
    try:
        # the owner pill in the status line must render but never ping
        msg = await interaction.channel.send(
            view=view, files=files, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.error("skyrim: failed to post delve board", exc_info=True)
        await interaction.response.edit_message(
            view=_notice_view("Couldn't post your delve here - try another channel."),
            attachments=[])
        E.save_profile(profile)      # the attempt is already spent; keep the books straight
        return
    delve.message_id = msg.id
    profile["active_delve"] = msg.id
    E.save_profile(profile)
    E.save_delve(delve)
    try:
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.debug("skyrim add_view on launch failed", exc_info=True)
    loc = delve.loc
    send_off = {
        "daily": f"📅 Today's shared dungeon: **{loc['name']}**. Same rooms for everyone - your dice.",
        "alduin": "🌑 **Skuldafn.** The Greybeards are singing. Go.",
        "soulcairn": "💀 **The Soul Cairn.** Down you go. Leave with your haul before it takes you.",
    }.get(kind, f"{loc['emoji']} Off to **{loc['name']}** - good hunting, Dovahkiin.")
    await interaction.response.edit_message(view=_notice_view(send_off), attachments=[])


def _notice_view(text: str):
    view = discord.ui.LayoutView(timeout=60)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(text))
    view.add_item(box)
    return view


# --- character sheet -------------------------------------------------------------
def _sheet_text(profile) -> str:
    stone = D.STONES[profile["stone"]]
    s = profile["skills"]
    st = profile["stats"]
    into, need = D.xp_into_level(profile["xp"])
    words = " ".join(D.SHOUT_WORDS[:profile["words"]]) if profile["words"] else "not yet learned"
    boosted = set(stone["boost"])
    skill_rows = [("blade", "One-Handed"), ("marksman", "Marksman"),
                  ("destruction", "Destruction"), ("sneak", "Sneak"),
                  ("speech", "Speech"), ("lockpicking", "Lockpicking")]
    lines = [
        f"## {stone['emoji']} {profile['name']} - Level {E.level(profile)} {E.archetype(profile)}",
        f"-# Blessed by {stone['name']}  ·  XP {_bar(into, 0, need)} {into}/{need}",
        "",
        "**Skills** (improve by use; ✨ = stone-blessed, learns faster)",
    ] + [
        f"{label:<12} **{s[key]}** {_bar(s[key])}" + ("  ✨" if key in boosted else "")
        for key, label in skill_rows
    ] + [
        "",
        f"**Gear**: {E.gear_name(profile, 'weapon')}  ·  {E.gear_name(profile, 'armour')} "
        f"(soaks {E.soak_pct(profile)}% of hits)",
        f"**Hearts**: {'❤️' * E.heart_max(profile)}  ·  🧪 {profile['potions']}/{E.potion_cap(profile)} potions",
        f"**The Voice**: 🗣️ {words} ({profile['words']}/3 words)  ·  "
        f"breath {E.voice_charges(profile)}/{profile['words']}  ·  🐉 {profile['souls']} unspent "
        f"soul{'s' if profile['souls'] != 1 else ''}",
        f"**Septims**: 💰 {profile['septims']:,}",
    ]
    if profile.get("alduin_slain"):
        n = profile["alduin_slain"]
        lines.insert(2, f"⭐ **Slayer of Alduin**{f' (x{n})' if n > 1 else ''} - the "
                        f"World-Eater fell to this one.")
    if profile["perks"]:
        perk_bits = [f"{D.PERKS[k]['emoji']} {D.PERKS[k]['name']} {r}/{D.PERKS[k]['ranks']}"
                     for k, r in profile["perks"].items()]
        lines.append(f"**Perks**: {'  ·  '.join(perk_bits)}")
    if profile.get("home"):
        home_bits = [f"{D.HOME_ITEMS[k]['emoji']} {D.HOME_ITEMS[k]['name']}"
                     for k in profile["home"] if k in D.HOME_ITEMS]
        lines.append(f"**Property**: {'  ·  '.join(home_bits)}")
    temper = profile.get("temper") or {}
    if temper.get("weapon") or temper.get("armour"):
        lines.append(f"**Tempering**: ⚔️ +{temper.get('weapon', 0)}  ·  🛡️ +{temper.get('armour', 0)} "
                     f"(Grindstone grades)")
    doc = profile.get("doctrines") or {}
    if doc:
        doc_bits = [f"{D.DOCTRINES[sk][ch]['emoji']} {D.DOCTRINES[sk][ch]['name']}"
                    for sk, ch in doc.items() if ch in D.DOCTRINES.get(sk, {})]
        lines.append(f"**Doctrines**: {'  ·  '.join(doc_bits)}")
    if E.legendary_stars(profile):
        lines.append(f"**Legendary**: {'⭐' * min(6, E.legendary_stars(profile))} "
                     f"({E.legendary_stars(profile)} resets)")
    wall = profile.get("dragon_wall") or []
    if wall:
        names = ", ".join(D.DRAGON_ROSTER[k]["name"] for k in wall if k in D.DRAGON_ROSTER)
        lines.append(f"**🐲 Dragon Wall** ({len(wall)}/{len(D.DRAGON_ROSTER)}): {names}")
    lines += [
        "",
        f"**Deeds**: {st['delves']} delves · {st['clears']} cleared · {st['deaths']} deaths · "
        f"{st['kills']} kills · {st['dragons']} dragons · {st['sneaks']} sneaks · "
        f"{st['persuades']} persuasions · {st['sweetrolls']} sweetrolls",
    ]
    if st.get("launched"):
        lines.append(f"-# ...and launched into low orbit by a giant, {st['launched']} time(s).")
    return "\n".join(lines)


async def _hub_character(interaction: Interaction):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    row = discord.ui.ActionRow()
    open_n = len(E.doctrine_choices_open(profile))
    m_label = f"Masteries ({open_n})" if open_n else "Masteries"
    row.add_item(_cb_btn(discord.ButtonStyle.primary if open_n else discord.ButtonStyle.secondary,
                         m_label, "✨", _hub_masteries))
    row.add_item(_cb_btn(discord.ButtonStyle.secondary, "Back", "⬅️", _hub_root))
    await _edit_panel(interaction, _sheet_text(profile), [row])


# --- shop --------------------------------------------------------------------------
def _shop_text(profile) -> str:
    lines = [
        "## 🏪 Belethor's General Goods",
        "-# \"Everything's for sale, my friend! Everything! If I had a sister, I'd sell her in a second.\"",
        "",
        f"💰 Your septims: **{profile['septims']:,}**",
        f"🧪 **Health potion** - {D.POTION_PRICE} septims  ({profile['potions']}/{E.potion_cap(profile)} pockets)",
    ]
    for slot, scale in (("weapon", 1.0), ("armour", 0.8)):
        tier = profile[f"{slot}_tier"]
        if tier >= len(D.GEAR_TIERS) - 1:
            lines.append(f"{'⚔️' if slot == 'weapon' else '🛡️'} {E.gear_name(profile, slot)} - "
                         "nothing finer exists in Tamriel.")
        else:
            nxt = D.GEAR_TIERS[tier + 1]
            price = int(nxt["price"] * scale)
            req = f"  (needs {nxt['dragons']} dragons slain)" if nxt["dragons"] else ""
            lines.append(f"{'⚔️' if slot == 'weapon' else '🛡️'} Upgrade to **{nxt['emoji']} "
                         f"{nxt['name']}** - {price:,} septims{req}")
    style = profile.get("armour_style", "heavy")
    other = "light" if style == "heavy" else "heavy"
    lines.append(f"👕 Armour style: **{style}** - "
                 + ("full protection, worn loud." if style == "heavy"
                    else f"quieter (+{D.LIGHT_SNEAK_BONUS} sneak), thinner protection."))
    lines.append("")
    lines.append(f"-# Weapons add +{D.WEAPON_FIGHT_PER_TIER}% to all attack styles per tier; "
                 f"heavy armour soaks {D.ARMOUR_SOAK_PER_TIER}%/tier, light {D.LIGHT_SOAK_PER_TIER}%/tier. "
                 f"Switching to {other} is free.")
    return "\n".join(lines)


async def _hub_shop(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    text = _shop_text(profile)
    if notice:
        text += f"\n\n{notice}"
    row = discord.ui.ActionRow()

    async def _buy(inter: Interaction, what: str):
        p = E.get_profile(inter.user.id)
        if what == "potion":
            err = E.buy_potion(p)
            ok = "🧪 One health potion. \"Pleasure doing business!\""
        else:
            err = E.buy_gear(p, what)
            ok = f"{'⚔️' if what == 'weapon' else '🛡️'} Sold! You now carry {E.gear_name(p, what)}."
        if err is None:
            E.save_profile(p)
        await _hub_shop(inter, notice=f"-# {err or ok}")

    for label, emoji, what in (("Buy potion", "🧪", "potion"),
                               ("Upgrade weapon", "⚔️", "weapon"),
                               ("Upgrade armour", "🛡️", "armour")):
        async def _cb(inter: Interaction, w=what):
            await _buy(inter, w)
        row.add_item(_cb_btn(discord.ButtonStyle.primary, label, emoji, _cb))
    other = "light" if profile.get("armour_style", "heavy") == "heavy" else "heavy"

    async def _swap(inter: Interaction):
        p = E.get_profile(inter.user.id)
        new_style = E.toggle_armour_style(p)
        E.save_profile(p)
        await _hub_shop(inter, notice=f"-# 👕 Re-fitted: you now wear **{new_style}** armour.")
    row.add_item(_cb_btn(discord.ButtonStyle.secondary, f"Go {other}", "👕", _swap))
    row.add_item(_cb_btn(discord.ButtonStyle.secondary, "Property", "🏠", _hub_property))
    craft = discord.ui.ActionRow()
    craft.add_item(_cb_btn(discord.ButtonStyle.primary, "Grindstone", "🪓", _hub_grindstone))
    craft.add_item(_cb_btn(discord.ButtonStyle.primary, "Lab Bench", "⚗️", _hub_alchemy))
    await _edit_panel(interaction, text, [row, craft, _back_row()])


# --- property (Breezehome and furnishings) -------------------------------------------
async def _hub_property(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    lines = ["## 🏠 Property - Belethor's side business",
             "-# \"A house? I know a man who knows a Jarl. For a price.\"",
             "",
             f"💰 Your septims: **{profile['septims']:,}**", ""]
    row = discord.ui.ActionRow()
    for key, item in D.HOME_ITEMS.items():
        owned = E.home_owned(profile, key)
        tick = "✅ owned" if owned else f"{item['price']:,} septims"
        lines.append(f"{item['emoji']} **{item['name']}** ({tick}) - {item['desc']}")
        purchasable = (not owned
                       and (not item["requires"] or E.home_owned(profile, item["requires"])))
        if purchasable:
            async def _buy_home(inter: Interaction, k=key):
                p = E.get_profile(inter.user.id)
                err = E.buy_home(p, k)
                if err is None:
                    E.save_profile(p)
                    note = f"-# ✅ {D.HOME_ITEMS[k]['name']} is yours. \"Pleasure doing business!\""
                else:
                    note = f"-# {err}"
                await _hub_property(inter, notice=note)
            row.add_item(_cb_btn(discord.ButtonStyle.primary, f"Buy {item['name']}",
                                 item["emoji"], _buy_home))
    if notice:
        lines += ["", notice]

    async def _back_to_shop(inter: Interaction):
        await _hub_shop(inter)
    brow = discord.ui.ActionRow()
    brow.add_item(_cb_btn(discord.ButtonStyle.secondary, "Back to shop", "🏪", _back_to_shop))
    rows = ([row] if row.children else []) + [brow, _back_row()]
    await _edit_panel(interaction, "\n".join(lines), rows)


# --- perks -------------------------------------------------------------------------
async def _hub_perks(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    pts = E.perk_points(profile)
    lines = ["## 📜 Perks",
             f"-# One point per level. Points to spend: **{pts}**", ""]
    for key, perk in D.PERKS.items():
        have = E.perk_rank(profile, key)
        lines.append(f"{perk['emoji']} **{perk['name']}** {have}/{perk['ranks']} - {perk['desc']}")
    if profile.get("words", 0) > 0:
        lines.append(f"🧘 **Meditation** - spend a point to still the mind and restore the "
                     f"Voice in full (breath {E.voice_charges(profile)}/{profile['words']}). "
                     f"The Greybeards approve. ({int(profile.get('meditations') or 0)} so far)")
    if notice:
        lines += ["", notice]
    rows = []
    if pts > 0:
        select = discord.ui.Select(placeholder="Spend a perk point...")
        for key, perk in D.PERKS.items():
            if E.perk_rank(profile, key) < perk["ranks"]:
                select.add_option(label=f"{perk['name']} ({E.perk_rank(profile, key)}/{perk['ranks']})",
                                  value=key, emoji=perk["emoji"], description=perk["desc"][:100])

        if select.options:
            async def _on_pick(inter: Interaction):
                p = E.get_profile(inter.user.id)
                err = E.take_perk(p, select.values[0])
                if err is None:
                    E.save_profile(p)
                    perk = D.PERKS[select.values[0]]
                    await _hub_perks(inter, notice=f"-# ✅ {perk['name']} is now rank "
                                                   f"{E.perk_rank(p, select.values[0])}.")
                else:
                    await _hub_perks(inter, notice=f"-# {err}")
            select.callback = _on_pick
            srow = discord.ui.ActionRow()
            srow.add_item(select)
            rows.append(srow)
        if profile.get("words", 0) > 0 and E.voice_charges(profile) < profile["words"]:
            async def _meditate(inter: Interaction):
                p = E.get_profile(inter.user.id)
                err = E.meditate(p)
                if err is None:
                    E.save_profile(p)
                    await _hub_perks(inter, notice="-# 🧘 The mind stills. Your breath returns "
                                                   "in full.")
                else:
                    await _hub_perks(inter, notice=f"-# {err}")
            mrow = discord.ui.ActionRow()
            mrow.add_item(_cb_btn(discord.ButtonStyle.primary, "Meditate (1 pt)", "🧘", _meditate))
            rows.append(mrow)
    rows.append(_back_row())
    await _edit_panel(interaction, "\n".join(lines), rows)


# --- masteries (Capstone Doctrines + Legendary Skills) ------------------------------
_SKILL_LABELS = {"blade": "One-Handed", "marksman": "Marksman", "destruction": "Destruction",
                 "sneak": "Sneak", "speech": "Speech", "lockpicking": "Lockpicking"}


def _masteries_text(profile) -> str:
    lines = ["## ✨ Masteries",
             "-# Every skill you carry to **100** unlocks a permanent **Doctrine** - pick one of "
             "two. Make a mastered skill **Legendary** to reset it to 15 for a ⭐ (the Doctrine "
             "stays). This is how two maxed Dragonborn end up fighting differently.", ""]
    chosen = profile.get("doctrines") or {}
    if chosen:
        lines.append("**Your Doctrines**")
        for sk, ch in chosen.items():
            doc = D.DOCTRINES[sk][ch]
            lines.append(f"{doc['emoji']} **{doc['name']}** ({_SKILL_LABELS.get(sk, sk)}) - {doc['desc']}")
        lines.append("")
    stars = E.legendary_stars(profile)
    if stars:
        lines.append(f"⭐ **Legendary skills reset:** {stars}")
        lines.append("")
    open_choices = E.doctrine_choices_open(profile)
    if open_choices:
        lines.append("**Doctrines to choose** (a skill just hit 100):")
        for sk in open_choices:
            opts = D.DOCTRINES[sk]
            pair = "  vs  ".join(f"{d['emoji']} {d['name']}" for d in opts.values())
            lines.append(f"- {_SKILL_LABELS.get(sk, sk)}: {pair}")
    if not open_choices and not chosen:
        lines.append("-# No skill at 100 yet. Master one and its Doctrine unlocks here.")
    return "\n".join(lines)


async def _hub_masteries(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    text = _masteries_text(profile)
    if notice:
        text += f"\n\n{notice}"
    rows = []
    open_choices = E.doctrine_choices_open(profile)
    if open_choices:
        dsel = discord.ui.Select(placeholder="Choose a Doctrine (permanent)...")
        for sk in open_choices:
            for ch, doc in D.DOCTRINES[sk].items():
                dsel.add_option(label=f"{_SKILL_LABELS.get(sk, sk)}: {doc['name']}",
                                value=f"{sk}:{ch}", emoji=doc["emoji"],
                                description=doc["desc"][:100])

        async def _on_doctrine(inter: Interaction):
            p = E.get_profile(inter.user.id)
            sk, ch = dsel.values[0].split(":", 1)
            err = E.choose_doctrine(p, sk, ch)
            if err is None:
                E.save_profile(p)
                doc = D.DOCTRINES[sk][ch]
                await _hub_masteries(inter, notice=f"-# ✅ **{doc['name']}** learned - it is yours for good.")
            else:
                await _hub_masteries(inter, notice=f"-# {err}")
        dsel.callback = _on_doctrine
        drow = discord.ui.ActionRow()
        drow.add_item(dsel)
        rows.append(drow)
    ready = E.legendary_ready(profile)
    if ready:
        lsel = discord.ui.Select(placeholder="Make a skill Legendary (reset to 15 for a ⭐)...")
        for sk in ready:
            lsel.add_option(label=f"{_SKILL_LABELS.get(sk, sk)} → Legendary",
                            value=sk, emoji="⭐",
                            description="Resets this skill to 15. Keeps its Doctrine.")

        async def _on_legendary(inter: Interaction):
            p = E.get_profile(inter.user.id)
            sk = lsel.values[0]
            err = E.make_legendary(p, sk)
            if err is None:
                E.save_profile(p)
                await _hub_masteries(inter, notice=f"-# ⭐ **{_SKILL_LABELS.get(sk, sk)}** is now Legendary. "
                                                   f"The climb begins again.")
            else:
                await _hub_masteries(inter, notice=f"-# {err}")
        lsel.callback = _on_legendary
        lrow = discord.ui.ActionRow()
        lrow.add_item(lsel)
        rows.append(lrow)
    rows.append(_back_row())
    await _edit_panel(interaction, text, rows)


# --- the Lab Bench (brewing) --------------------------------------------------------
def _alchemy_text(profile) -> str:
    lines = ["## ⚗️ The Lab Bench",
             "-# Brew looted ingredients into potions and one-delve elixirs. Ingredients ride "
             "at risk in your satchel, so it pays to walk out alive.", ""]
    pouch = profile.get("ingredients") or {}
    if pouch:
        bits = [f"{D.INGREDIENTS[k]['emoji']} {D.INGREDIENTS[k]['name']} ×{n}"
                for k, n in sorted(pouch.items()) if k in D.INGREDIENTS]
        lines.append("**Your pouch:** " + "  ·  ".join(bits))
    else:
        lines.append("**Your pouch is empty.** Elites, bounties and dragons drop the good stuff.")
    lines.append("")
    if not E.home_owned(profile, "alchemy_lab"):
        lines.append("-# 🔒 You need an **Alchemy Lab** (a Breezehome upgrade in Property) to brew.")
        return "\n".join(lines)
    lines.append("**Recipes**")
    for key, r in D.RECIPES.items():
        cost = "  ".join(f"{D.INGREDIENTS[k]['emoji']}×{n}" for k, n in r["cost"].items())
        tick = "✅" if E.can_brew(profile, key) else "◻️"
        lines.append(f"{tick} {r['emoji']} **{r['name']}** - {r['desc']}  ({cost})")
    src = E.ingredient_sources()
    guide = "  ·  ".join(f"{D.INGREDIENTS[k]['emoji']} {', '.join(src[k])}"
                         for k in D.INGREDIENTS if k in src)
    lines.append(f"\n-# 🏹 Where to hunt: {guide}")
    return "\n".join(lines)


async def _hub_alchemy(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    text = _alchemy_text(profile)
    if notice:
        text += f"\n\n{notice}"
    rows = []
    if E.home_owned(profile, "alchemy_lab"):
        brewable = [k for k in D.RECIPES if E.can_brew(profile, k)]
        if brewable:
            sel = discord.ui.Select(placeholder="Brew a recipe...")
            for k in brewable:
                r = D.RECIPES[k]
                sel.add_option(label=r["name"], value=k, emoji=r["emoji"],
                               description=r["desc"][:100])

            async def _on_brew(inter: Interaction):
                p = E.get_profile(inter.user.id)
                err = E.brew(p, sel.values[0])
                if err is None:
                    E.save_profile(p)
                    r = D.RECIPES[sel.values[0]]
                    await _hub_alchemy(inter, notice=f"-# {r['emoji']} Brewed **{r['name']}**.")
                else:
                    await _hub_alchemy(inter, notice=f"-# {err}")
            sel.callback = _on_brew
            srow = discord.ui.ActionRow()
            srow.add_item(sel)
            rows.append(srow)
    back = discord.ui.ActionRow()
    back.add_item(_cb_btn(discord.ButtonStyle.secondary, "Back to shop", "🏪",
                          lambda i: _hub_shop(i)))
    rows += [back, _back_row()]
    await _edit_panel(interaction, text, rows)


# --- the Grindstone (tempering) -----------------------------------------------------
def _grindstone_text(profile) -> str:
    temper = profile.get("temper") or {"weapon": 0, "armour": 0}
    lines = ["## 🪓 The Grindstone",
             "-# Hone gear past its tier with septims and looted materials. Bonuses that the "
             "86% cap can't swallow: sharper weapons feed **Overkill**, tougher armour soaks more.", "",
             f"💰 Your septims: **{profile['septims']:,}**"]
    pouch = profile.get("ingredients") or {}
    if pouch:
        bits = [f"{D.INGREDIENTS[k]['emoji']}×{n}" for k, n in sorted(pouch.items()) if k in D.INGREDIENTS]
        lines.append("🎒 Materials: " + "  ".join(bits))
    lines.append("")
    for slot, emoji in (("weapon", "⚔️"), ("armour", "🛡️")):
        g = temper.get(slot, 0)
        star = "✦" * g + "·" * (E.TEMPER_MAX_GRADE - g)
        if g >= E.TEMPER_MAX_GRADE:
            lines.append(f"{emoji} **{slot.title()}** [{star}] - honed to perfection.")
        else:
            c = E.temper_cost(g)
            mats = "  ".join(f"{D.INGREDIENTS[k]['emoji']}×{n}" for k, n in c["mats"].items())
            eff = (f"+{E.TEMPER_FIGHT_PER_GRADE}% attack" if slot == "weapon"
                   else f"+{E.TEMPER_SOAK_PER_GRADE}% soak")
            lines.append(f"{emoji} **{slot.title()}** [{star}] → grade {g + 1} ({eff}): "
                         f"{c['septims']:,} septims + {mats}")
    src = E.ingredient_sources()
    mats_used = sorted({m for c in D.TEMPER_COSTS for m in c["mats"]})
    guide = "  ·  ".join(f"{D.INGREDIENTS[m]['emoji']} {D.INGREDIENTS[m]['name']} - "
                         f"{', '.join(src.get(m, ['?']))}" for m in mats_used)
    lines.append(f"\n-# 🏹 Where to hunt: {guide}")
    return "\n".join(lines)


async def _hub_grindstone(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    text = _grindstone_text(profile)
    if notice:
        text += f"\n\n{notice}"
    row = discord.ui.ActionRow()
    for slot, emoji in (("weapon", "⚔️"), ("armour", "🛡️")):
        if (profile.get("temper") or {}).get(slot, 0) < E.TEMPER_MAX_GRADE:
            async def _temper(inter: Interaction, s=slot):
                p = E.get_profile(inter.user.id)
                err = E.temper(p, s)
                if err is None:
                    E.save_profile(p)
                    await _hub_grindstone(inter, notice=f"-# 🪓 Your {s} rings sharper - grade "
                                                        f"{p['temper'][s]}.")
                else:
                    await _hub_grindstone(inter, notice=f"-# {err}")
            row.add_item(_cb_btn(discord.ButtonStyle.primary, f"Temper {slot}", emoji, _temper))
    back = discord.ui.ActionRow()
    back.add_item(_cb_btn(discord.ButtonStyle.secondary, "Back to shop", "🏪",
                          lambda i: _hub_shop(i)))
    rows = ([row] if row.children else []) + [back, _back_row()]
    await _edit_panel(interaction, text, rows)


# --- Daedric pacts ------------------------------------------------------------------
async def _hub_pacts(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    sworn = profile.get("nextpacts") or []
    lines = ["## ⚖️ Daedric Pacts",
             "-# Swear curses on your **next delve** for a multiplied satchel if you bank it. "
             "Death loses everything, as ever. Pacts don't bind the Daily, Skuldafn or the "
             "Cairn - the Princes want to watch you *choose* it.", ""]
    for key, pact in D.PACTS.items():
        tick = "⚖️" if key in sworn else "◻️"
        price = pact.get("mult_note") or f"x{pact['mult']:g}"
        lines.append(f"{tick} {pact['emoji']} **{pact['name']}** ({price}) - {pact['desc']}")
    if sworn:
        fake = E.Delve(profile["user_id"], "x", 0, "embershard",
                       [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}],
                       hearts=1, shout_charges=0, pacts=sworn)
        lines.append(f"\n**Sworn:** satchel **x{E.pact_mult(fake):g}** on your next delve "
                     f"(cap x{E.PACT_MULT_CAP:g}).")
    if notice:
        lines += ["", notice]
    rows = []
    sel = discord.ui.Select(placeholder="Swear your pacts (pick none to clear)...",
                            min_values=0, max_values=len(D.PACTS))
    for key, pact in D.PACTS.items():
        price = pact.get("mult_note") or f"x{pact['mult']:g}"
        sel.add_option(label=f"{pact['name']} ({price})", value=key,
                       emoji=pact["emoji"], description=pact["desc"][:100],
                       default=key in sworn)

    async def _swear(inter: Interaction):
        p = E.get_profile(inter.user.id)
        err = E.swear_pacts(p, list(sel.values))
        if err is None:
            E.save_profile(p)
            n = len(p.get("nextpacts") or [])
            note = (f"-# ⚖️ {n} pact{'s' if n != 1 else ''} sworn." if n
                    else "-# The Princes shrug. No pacts bound.")
            await _hub_pacts(inter, notice=note)
        else:
            await _hub_pacts(inter, notice=f"-# {err}")
    sel.callback = _swear
    srow = discord.ui.ActionRow()
    srow.add_item(sel)
    rows.append(srow)
    brow = discord.ui.ActionRow()
    brow.add_item(_cb_btn(discord.ButtonStyle.secondary, "To the roads", "🗺️",
                          lambda i: _show_offers(i, edit_hub=True)))
    rows.append(brow)
    rows.append(_back_row())
    await _edit_panel(interaction, "\n".join(lines), rows)


# --- NPC factions -------------------------------------------------------------------
def _factions_text(profile) -> str:
    lines = ["## 🏰 Factions of Skyrim",
             "-# Swear to a faction and each week they set you a task in a skill the endgame "
             "tends to forget. Finish it for favour, rank and coin.", ""]
    fac_key = profile.get("allegiance")
    if fac_key in D.FACTIONS:
        fac = D.FACTIONS[fac_key]
        goal, prog, done = E.faction_progress(profile)
        rank = E.faction_rank(profile)
        lines.append(f"{fac['emoji']} **{fac['name']}** - you are **{rank}** "
                     f"(favour {E.faction_favour(profile)})")
        bar = _bar(min(prog, goal), 0, goal, 10)
        state = "✅ ready to claim" if done else f"{prog}/{goal}"
        lines.append(f"-# This week: **{goal} {fac['verb']}**  {bar}  {state}")
        lines.append("")
        lines.append("**The word from the other guilds this week:**")
        for name, n in E.faction_rivals(profile):
            lines.append(f"-# {name} has been busy - {n} deeds done.")
    else:
        if E.level(profile) < int(getattr(E.config, "SKYRIM_DRAGON_MIN_LEVEL", 8)):
            lines.append("-# 🔒 The great factions only take proven adventurers (level 8+).")
        else:
            lines.append("**Choose an allegiance:**")
            for k, fac in D.FACTIONS.items():
                lines.append(f"{fac['emoji']} **{fac['name']}** ({fac['seat']}) - {fac['blurb']}  "
                             f"Weekly task: {fac['goal']} {fac['verb']}.")
    return "\n".join(lines)


async def _hub_factions(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    text = _factions_text(profile)
    if notice:
        text += f"\n\n{notice}"
    rows = []
    fac_key = profile.get("allegiance")
    can_join = E.level(profile) >= int(getattr(E.config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    if fac_key in D.FACTIONS:
        _g, _p, done = E.faction_progress(profile)
        if done:
            row = discord.ui.ActionRow()

            async def _claim(inter: Interaction):
                p = E.get_profile(inter.user.id)
                res = E.claim_faction(p)
                E.save_profile(p)
                await _hub_factions(inter, notice=f"-# 🏅 {res}" if res and "favour" in res
                                    else f"-# {res}")
            row.add_item(_cb_btn(discord.ButtonStyle.success, "Claim this week's favour", "🏅", _claim))
            rows.append(row)
    elif can_join:
        sel = discord.ui.Select(placeholder="Swear an allegiance...")
        for k, fac in D.FACTIONS.items():
            sel.add_option(label=fac["name"], value=k, emoji=fac["emoji"],
                           description=f"Weekly: {fac['goal']} {fac['verb']}"[:100])

        async def _join(inter: Interaction):
            p = E.get_profile(inter.user.id)
            err = E.join_faction(p, sel.values[0])
            if err is None:
                E.save_profile(p)
                await _hub_factions(inter, notice=f"-# 🤝 You run with {D.FACTIONS[sel.values[0]]['name']} now.")
            else:
                await _hub_factions(inter, notice=f"-# {err}")
        sel.callback = _join
        srow = discord.ui.ActionRow()
        srow.add_item(sel)
        rows.append(srow)
    rows.append(_back_row())
    await _edit_panel(interaction, text, rows)


# --- idle expeditions ---------------------------------------------------------------
def _expedition_text(profile) -> str:
    lines = ["## 🧭 Expeditions",
             "-# Send your housecarl on an errand for a day or more, then collect the haul when "
             "you next open the hub. It runs while you're away - no delves spent.", ""]
    e = E.expedition(profile)
    if e:
        exp = D.EXPEDITIONS[e["key"]]
        carl = e.get("carl", "Your housecarl")
        if E.expedition_ready(profile):
            lines.append(f"✅ **{carl}** is back from **{exp['name']}** - collect the haul below.")
        else:
            lines.append(f"⏳ **{carl}** is away on **{exp['name']}** - returns **{e['return']}** (UK).")
        full = E.expedition_log(profile, limit=0)
        log = full[-E.EXPEDITION_LOG_SHOW:]
        if log:
            lines.append("")
            lines.append("📜 **Word from the road:**")
            if len(full) > len(log):
                lines.append(f"-# ...{len(full) - len(log)} earlier dispatches, lost to the wind.")
            lines += [f"-# {entry}" for entry in log]
    elif E.level(profile) < int(getattr(E.config, "SKYRIM_DRAGON_MIN_LEVEL", 8)):
        lines.append("-# 🔒 You earn a housecarl to send at level 8.")
    else:
        lines.append("**Send your housecarl:**")
        for k, exp in D.EXPEDITIONS.items():
            ing = f"  ·  {D.INGREDIENTS[exp['ingredient']]['emoji']} {D.INGREDIENTS[exp['ingredient']]['name']}" \
                  if exp.get("ingredient") else ""
            lines.append(f"{exp['emoji']} **{exp['name']}** ({exp['days']}d) - "
                         f"~{exp['septims']} septims, {exp['xp']} XP{ing}. {exp['desc']}")
    ledger = profile.get("exp_log") or []
    if ledger:
        lines.append("")
        lines.append("📒 **The ledger** - last returns:")
        for entry in reversed(ledger):
            exp = D.EXPEDITIONS.get(entry.get("key"), {})
            ing_bit = f", {D.INGREDIENTS[entry['ing']]['emoji']}" if entry.get("ing") else ""
            lines.append(f"-# {exp.get('emoji', '🧭')} {entry['date']} · {entry['carl']}, "
                         f"{exp.get('name', 'an errand')}: +{entry['septims']:,} septims, "
                         f"+{entry['xp']} XP{ing_bit}")
        tot = profile.get("exp_totals") or {}
        if tot.get("count"):
            lines.append(f"-# 📦 All time: {tot['count']} errand{'s' if tot['count'] != 1 else ''} · "
                         f"+{tot['septims']:,} septims · +{tot['xp']:,} XP")
    return "\n".join(lines)


async def _hub_expedition(interaction: Interaction, notice: str = ""):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    text = _expedition_text(profile)
    if notice:
        text += f"\n\n{notice}"
    rows = []
    e = E.expedition(profile)
    can = E.level(profile) >= int(getattr(E.config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    if e and E.expedition_ready(profile):
        row = discord.ui.ActionRow()

        async def _collect(inter: Interaction):
            p = E.get_profile(inter.user.id)
            res = E.collect_expedition(p)
            E.save_profile(p)
            await _hub_expedition(inter, notice=f"-# 🎁 {res}")
        row.add_item(_cb_btn(discord.ButtonStyle.success, "Collect the haul", "🎁", _collect))
        rows.append(row)
    elif not e and can:
        sel = discord.ui.Select(placeholder="Send an expedition...")
        for k, exp in D.EXPEDITIONS.items():
            sel.add_option(label=f"{exp['name']} ({exp['days']}d)", value=k, emoji=exp["emoji"],
                           description=exp["desc"][:100])

        async def _send(inter: Interaction):
            p = E.get_profile(inter.user.id)
            err = E.start_expedition(p, sel.values[0])
            if err is None:
                E.save_profile(p)
                await _hub_expedition(inter, notice=f"-# 🧭 Off they go on **{D.EXPEDITIONS[sel.values[0]]['name']}**.")
            else:
                await _hub_expedition(inter, notice=f"-# {err}")
        sel.callback = _send
        srow = discord.ui.ActionRow()
        srow.add_item(sel)
        rows.append(srow)
    rows.append(_back_row())
    await _edit_panel(interaction, text, rows)


# --- the daily delve ---------------------------------------------------------------
def _daily_marked_line() -> str:
    affs = E.daily_affixes()
    if not affs:
        return ""
    bits = "  ".join(f"{D.AFFIXES[a]['emoji']} {D.AFFIXES[a]['tag']}" for a in affs)
    return f"\n-# 🗡️ Word from inside - marked foes today: {bits}"


def _daily_results_text() -> str:
    loc = E.daily_location()
    lines = [f"## 📅 Daily Delve - {loc['emoji']} {loc['name']}",
             f"-# {E.weather_line()}  ·  same rooms for everyone, one attempt each, "
             f"{E.DAILY_CLEAR_MULT:g}x clear bonus" + _daily_marked_line(), ""]
    results = E.daily_results()
    if not results:
        lines.append("No attempts yet today. The dungeon waits.")
        return "\n".join(lines)

    def sort_key(r):
        cleared = r["state"] == "cleared"
        return (not cleared, -r["satchel"] if cleared else -r["rooms"], -r["kills"])
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(sorted(results.values(), key=sort_key)[:12]):
        cls = D.STONES.get(r.get("stone", r.get("class")), D.STONES["warrior"])
        rank = medals[i] if i < len(medals) else f"`{i + 1:>2}.`"
        if r["state"] == "cleared":
            outcome = f"✅ cleared  ·  💰 {r['satchel']:,}"
        elif r["state"] == "dead":
            outcome = f"💀 died in room {r['rooms'] + 1}"
        elif r["state"] == "launched":
            outcome = "🦣 launched into orbit"
        else:
            outcome = f"🚪 left after room {r['rooms']}"
        lines.append(f"{rank} {cls['emoji']} **{r['name']}** - {outcome}  ·  ⚔️ {r['kills']}")
    return "\n".join(lines)


async def _hub_daily(interaction: Interaction):
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction)
        return
    if not E.daily_available(profile):
        await _edit_panel(interaction, _daily_results_text(), [_back_row()])
        return
    loc = E.daily_location()
    text = (f"## 📅 Daily Delve - {loc['emoji']} {loc['name']}\n"
            f"-# {E.weather_line()}{_daily_marked_line()}\n\n"
            f"{loc['desc']}\n"
            f"One shared dungeon per day: **everyone faces the same rooms**, the dice are "
            f"your own. One attempt, separate from your normal delves, and the clear bonus "
            f"pays {E.DAILY_CLEAR_MULT:g}x. Results land on the daily board.")
    row = discord.ui.ActionRow()

    async def _go(inter: Interaction):
        await _launch_delve(inter, loc["key"], kind="daily")
    row.add_item(_cb_btn(discord.ButtonStyle.success, "Set out", "📅", _go))

    async def _board(inter: Interaction):
        await _edit_panel(inter, _daily_results_text(), [_back_row()])
    row.add_item(_cb_btn(discord.ButtonStyle.secondary, "Today's board", "📋", _board))
    await _edit_panel(interaction, text, [row, _back_row()])


# --- rankings ------------------------------------------------------------------------
async def _hub_rankings(interaction: Interaction):
    profiles = sorted(E.all_profiles().values(), key=lambda p: p["xp"], reverse=True)[:10]
    lines = ["## 🏆 Legends of Skyrim", ""]
    if not profiles:
        lines.append("No adventurers yet. The ruins wait.")
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(profiles):
        cls = D.STONES[p["stone"]]
        rank = medals[i] if i < len(medals) else f"`{i + 1:>2}.`"
        st = p["stats"]
        flair = ""
        if p.get("alduin_slain"):
            flair += " ⭐"
        if E.home_owned(p, "trophy_room"):
            flair += " 🏆"
        if E.legendary_stars(p):
            flair += f" ✨{E.legendary_stars(p)}"
        best = E.soulcairn_best(p)
        cairn = f"  ·  💀 {best}" if best else ""
        lines.append(f"{rank} {cls['emoji']} **{p['name']}**{flair} - Lv {E.level(p)}  ·  "
                     f"🐉 {st['dragons']}  ·  🏰 {st['clears']}  ·  "
                     f"💰 {p['septims']:,}{cairn}")
    obit = E.latest_obituary()
    if obit:
        lines += ["", obit]
    await _edit_panel(interaction, "\n".join(lines), [_back_row()])


# --- help -------------------------------------------------------------------------
def _help_text() -> str:
    return (
        "## 📖 Skyrim - How it works\n"
        "A persistent adventure: your character, skills, gear and dragon souls are kept "
        "forever. Run `/skyrim` for your hub, then **Adventure** to delve.\n\n"
        "**Delves** - a run of rooms ending in a boss. Each enemy shows your odds up front:\n"
        "- ⚔️🏹🔥 **Attack** - three styles (One-Handed, Marksman, Destruction), each its "
        "own skill, each better against some foes (fire purges draugr; arrows bounce off "
        "bones). The style you use is the skill that grows.\n"
        "- 🥷 **Sneak** - slip into hiding. Then choose: **ambush** for a big attack bonus, "
        "or slip past for XP. Get spotted and the fight is on.\n"
        "- 💬 **Persuade** - humans only. Talk your way through, sometimes at a profit.\n"
        "- 🗣️ **Shout** - the Voice flattens a room (dragons get grounded instead). "
        "Charges = words you know.\n"
        "- 🧪 **Potion** / 🚪 **Leave** - patch up, or walk out with your satchel. "
        "Fleeing mid-fight spills a third of it.\n"
        "- 🔓 **Master-locked chests** - Lockpicking territory: double loot, one careful try.\n\n"
        "**The stakes** - XP, skill-ups, gear, souls and potions bank instantly. The "
        "**septims in your satchel** only bank when you leave or clear - die and they stay "
        "in the dungeon.\n\n"
        "**No classes - you become what you practise.** Every skill levels by use; your "
        "Guardian Stone just makes its skills learn faster. Your title (Stealth Archer, "
        "Spellsword...) is earned, not picked. Armour comes **heavy** (tougher) or "
        "**light** (quieter) - switch free at Belethor's. Perks stack on top.\n\n"
        "**Dragons** - sighted once you're strong enough. Slay one and its **soul** is "
        "yours; spend souls at **Word Walls** to learn FUS, then RO, then DAH.\n\n"
        "**The wilds** - each UK day has a shared **weather** roll that tilts the odds for "
        "everyone; a clean strike **crits** for double; rare 🏴 **bounty** enemies are "
        "tougher but pay triple.\n\n"
        "**📅 The Daily Delve** - one shared dungeon per day, same rooms for everyone, one "
        "attempt each, fatter clear bonus, and a board to compare scars on. Doesn't use "
        "your normal delves.\n\n"
        "**🏠 Property** - Belethor sells Breezehome and furnishings for septims. Comforts, "
        "not power: a blessing and a brewed potion on your first delve each day.\n\n"
        "**🌑 Alduin** - at level 20, with the full Shout and 5 dragons down, Skuldafn "
        "opens. One attempt per day. He takes wing mid-fight; only the Voice brings him "
        "back down. Slay him for the ⭐ title - but the World-Eater **echoes**: every "
        "victory returns him a heart stronger, harder to face, and demanding more dragons "
        "slain before he'll meet you again. His soul pays richer each time.\n\n"
        "**⚡ Overkill** - once your odds are pinned at the cap, the surplus becomes bonus "
        "**crit** (shown on the button). Gear, tempering, affinity and grounding still matter "
        "at the top.\n"
        "**Elite foes** - rare **affixed** enemies (Warded needs Fire, Bonebound shrugs off "
        "arrows, Venomous bleeds into the next room...) are telegraphed a room ahead. Bounties "
        "still pay triple.\n"
        "**The Voice** - **FUS** grounds/staggers (1 charge), **FUS RO** flattens a room (2), "
        "**FUS RO DAH** deals 2 damage to anything (3). Your breath is **persistent**: it no "
        "longer refills at every door - you regain 1 charge at dawn (UK), and **absorbing a "
        "dragon soul renews it in full**. Skuldafn alone grants a full Voice at the gate. "
        "A **week's dragon** rotates the roster; airborne dragons need a **bow** or a shout.\n"
        "**Masteries** - carry a skill to 100 for a permanent **Doctrine** (pick one of two); "
        "make it **Legendary** to reset it to 15 for a ⭐.\n"
        "**Crafting** - kills drop **ingredients** into your at-risk satchel; brew them at the "
        "**Lab Bench**, or feed them to the **Grindstone** to temper gear past its tier. Drops "
        "follow the foe: undead shed the smithing salts, monsters the fats and claws, men and "
        "beasts the herbs, dragons their scales - the 🧪 icons on the map picker show what "
        "hunts where, so the easy roads still have a job once you've outgrown them.\n"
        "**More to do** - swear to a **Faction** for weekly tasks, send a housecarl on an "
        "**Expedition**, loot **Fallen Adventurers**, brave the **Forks** and **Mimics**, and "
        "once Alduin is down, descend the endless **Soul Cairn** (one attempt a day).\n\n"
        f"-# {getattr(config, 'SKYRIM_DELVES_PER_DAY', 3)} delves per day, reset at "
        "midnight (UK). No UKPence involved anywhere - glory only."
    )


async def _hub_help(interaction: Interaction):
    await _edit_panel(interaction, _help_text(), [_back_row()])


# ---------------------------------------------------------------------------
# Command entry + restart recovery
# ---------------------------------------------------------------------------
async def handle_skyrim_command(interaction: Interaction):
    if not getattr(config, "SKYRIM_ENABLED", True):
        await interaction.response.send_message(
            "The roads to Skyrim are closed for now.", ephemeral=True)
        return
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        await _show_class_pick(interaction, first_response=True)
    else:
        await _show_hub_root(interaction, profile, first_response=True)


def reattach_skyrim_view(client, key, value):
    """Re-register routing for an in-play delve after a restart; prune anything
    terminal or malformed so it can't wedge future boots."""
    try:
        delve = E.Delve.from_dict(value)
    except Exception as e:
        logger.error(f"Pruning malformed skyrim entry {key}: {e}", exc_info=True)
        E.delete_delve(key)
        return
    profile = E.get_profile(delve.player_id)
    if not delve.playing() or profile is None or profile.get("active_delve") != int(key):
        E.delete_delve(key)
        return
    try:
        delve.message_id = int(key)
        view, _files = build_delve_layout(delve, profile)
        client.add_view(view, message_id=int(key))
    except Exception as e:
        logger.error(f"Failed to reattach skyrim view {key}: {e}", exc_info=True)
