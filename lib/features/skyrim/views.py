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
        return D.ENEMIES[r["key"]]["art"]
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
    cls = D.CLASSES[profile["class"]]
    bits = [f"{cls['emoji']} <@{delve.player_id}> Lv {E.level(profile)}",
            _hearts_str(delve, profile), f"🧪 {profile['potions']}"]
    if profile["words"] > 0:
        bits.append(f"🗣️ {delve.shout_charges}")
    bits.append(f"💰 {delve.satchel:,} in satchel")
    return "  ·  ".join(bits)


def _delve_text(delve: E.Delve, profile) -> str:
    loc = delve.loc
    n = len(delve.rooms)
    if delve.playing():
        r = delve.room
        if r["kind"] == "enemy" and r["boss"]:
            head = f"## {loc['emoji']} {loc['name']} - the final chamber"
        else:
            head = f"## {loc['emoji']} {loc['name']} - room {delve.idx + 1}/{n}"
    else:
        head = f"## {loc['emoji']} {loc['name']}"
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
        if delve.engaged:
            lines.append(f"{e['emoji']} The **{e['name']}** presses the attack!")
        else:
            lines.append(f"{e['emoji']} {D.pick(e['intro'])}")
        if e.get("hp", 1) > 1:
            lines.append(f"-# {'🩸' * delve.enemy_hp} it will take {delve.enemy_hp} more "
                         f"telling blow{'s' if delve.enemy_hp != 1 else ''}")
        if delve.grounded:
            lines.append("-# The dragon is **grounded** - now is your chance!")
    else:
        ev = D.EVENTS[r["key"]]
        lines.append(f"{ev['emoji']} {ev['text']}")
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
        row1.add_item(_btn(discord.ButtonStyle.danger, f"Attack {E.fight_pct(profile, key, delve)}%",
                           f"skyrim:{did}:atk", _make_cb(delve, "atk"), emoji="⚔️"))
        p_snk = E.sneak_pct(profile, key)
        if p_snk is not None and not delve.engaged:
            row1.add_item(_btn(discord.ButtonStyle.primary, f"Sneak {p_snk}%",
                               f"skyrim:{did}:snk", _make_cb(delve, "snk"), emoji="🥷"))
        p_per = E.persuade_pct(profile, key)
        if p_per is not None and not delve.engaged:
            row1.add_item(_btn(discord.ButtonStyle.primary, f"Persuade {p_per}%",
                               f"skyrim:{did}:per", _make_cb(delve, "per"), emoji="💬"))
        if profile["words"] > 0 and delve.shout_charges > 0 and \
                not (e["type"] == "dragon" and delve.grounded):
            shout = " ".join(D.SHOUT_WORDS[:profile["words"]])
            row2.add_item(_btn(discord.ButtonStyle.success, f"{shout}  ({delve.shout_charges})",
                               f"skyrim:{did}:sht", _make_cb(delve, "sht"), emoji="🗣️"))
        if profile["potions"] > 0 and delve.hearts < E.heart_max(profile):
            row2.add_item(_btn(discord.ButtonStyle.secondary, f"Potion ({profile['potions']})",
                               f"skyrim:{did}:pot", _make_cb(delve, "pot"), emoji="🧪"))
        leave_label = "Flee" if delve.engaged else f"Leave ({delve.satchel:,})"
        row2.add_item(_btn(discord.ButtonStyle.secondary, leave_label,
                           f"skyrim:{did}:lve", _make_cb(delve, "lve"),
                           emoji="🏃" if delve.engaged else "🚪"))
    else:
        key = r["key"]
        choices = {
            "chest": [("🧰", "Open it", "open"), ("🚶", "Move on", "skip")],
            "sweetroll": [("🍩", "Take the sweetroll", "take"), ("🚶", "Walk away", "skip")],
            "shrine": [("🙏", "Pray", "pray"), ("🚶", "Move on", "skip")],
            "satchel": [("🧪", "Take it", "take"), ("🚶", "Move on", "skip")],
            "maiq": [("💬", "Talk to M'aiq", "talk"), ("🚶", "Move on", "skip")],
            "wordwall": [("🗣️", "Approach the wall", "approach"), ("🚶", "Move on", "skip")],
            "giant": [("🚶", "Back away slowly", "retreat"), ("🧀", "About that cheese...", "approach")],
            "knee_trap": [("🚶", "Limp onward", "continue")],
        }[key]
        for emoji, label, act in choices:
            row1.add_item(_btn(discord.ButtonStyle.primary, label,
                               f"skyrim:{did}:evt:{act}", _make_cb(delve, f"evt:{act}"),
                               emoji=emoji))
        if key != "giant":
            row2.add_item(_btn(discord.ButtonStyle.secondary, f"Leave ({delve.satchel:,})",
                               f"skyrim:{did}:lve", _make_cb(delve, "lve"), emoji="🚪"))
    view.add_item(row1)
    if row2.children:
        view.add_item(row2)
    return view, files


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
        if action == "atk":
            delve.act_attack(profile)
        elif action == "snk":
            delve.act_sneak(profile)
        elif action == "per":
            delve.act_persuade(profile)
        elif action == "sht":
            delve.act_shout(profile)
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
    row2.add_item(_cb_btn(discord.ButtonStyle.secondary, "Rankings", "🏆", _hub_rankings))
    row2.add_item(_cb_btn(discord.ButtonStyle.secondary, "How it works", "📖", _hub_help))
    return [row1, row2]


def _cb_btn(style, label, emoji, cb):
    b = discord.ui.Button(style=style, label=label, emoji=emoji)
    b.callback = cb
    return b


def _back_row():
    row = discord.ui.ActionRow()
    row.add_item(_cb_btn(discord.ButtonStyle.secondary, "Back", "⬅️", _hub_root))
    return row


def _hub_text(profile) -> str:
    cls = D.CLASSES[profile["class"]]
    left = E.delves_left(profile)
    into, need = D.xp_into_level(profile["xp"])
    return (
        f"## 🐉 Skyrim\n"
        f"{cls['emoji']} **{profile['name']}** - Level {E.level(profile)} {cls['name']}"
        f"  ·  💰 {profile['septims']:,} septims  ·  🛌 {left}/{getattr(config, 'SKYRIM_DELVES_PER_DAY', 3)} delves left today\n"
        f"-# XP {_bar(into, 0, need)} {into}/{need} to next level\n\n"
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
    for key, cls in D.CLASSES.items():
        async def _pick(inter: Interaction, k=key):
            if E.get_profile(inter.user.id) is None:
                name = discord.utils.escape_markdown(inter.user.display_name)
                E.create_profile(inter.user.id, name, k)
            await _hub_root(inter)
        row.add_item(_cb_btn(discord.ButtonStyle.primary, cls["name"], cls["emoji"], _pick))
    rows.append(row)
    text = "## 🐉 Skyrim\n" + D.INTRO_TEXT + "\n\n" + "\n".join(
        f"{c['emoji']} **{c['name']}** - {c['blurb']}" for c in D.CLASSES.values())
    view, files = _panel_view(text, rows, art_key="intro")
    if first_response:
        await interaction.response.send_message(view=view, files=files, ephemeral=True)
    else:
        await interaction.response.edit_message(view=view, attachments=files)


# --- adventure / location picker ------------------------------------------------
async def _hub_adventure(interaction: Interaction):
    await _open_location_picker(interaction, edit_hub=True)


async def _open_location_picker(interaction: Interaction, edit_hub: bool = False):
    """From the hub (edit in place) or a finished delve board (fresh ephemeral)."""
    profile = E.get_profile(interaction.user.id)
    if profile is None:
        if edit_hub:
            await _show_class_pick(interaction)
        else:
            await interaction.response.send_message("Run `/skyrim` first.", ephemeral=True)
        return
    left = E.delves_left(profile)
    if left <= 0:
        msg = ("🛌 You need to rest - no delves left today. "
               "They reset at midnight (UK time).")
        if edit_hub:
            await _edit_panel(interaction, f"## 🗺️ Adventure\n{msg}", [_back_row()])
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    offers = E.offer_locations(profile)
    lines = [f"## 🗺️ Where to, Dovahkiin?",
             f"-# 🛌 {left} delve{'s' if left != 1 else ''} left today  ·  "
             f"the satchel is at stake, everything else is forever\n"]
    row = discord.ui.ActionRow()
    for key in offers:
        loc = D.LOCATIONS[key]
        lines.append(f"{loc['emoji']} **{loc['name']}**  ·  {loc['difficulty']}  ·  "
                     f"{loc['rooms']} rooms - {loc['desc']}")

        async def _go(inter: Interaction, k=key):
            await _launch_delve(inter, k)
        row.add_item(_cb_btn(
            discord.ButtonStyle.danger if D.LOCATIONS[key].get("dragon_lair")
            else discord.ButtonStyle.primary, loc["name"], loc["emoji"], _go))
    rows = [row] + ([_back_row()] if edit_hub else [])
    if edit_hub:
        await _edit_panel(interaction, "\n".join(lines), rows)
    else:
        view, files = _panel_view("\n".join(lines), rows)
        await interaction.response.send_message(view=view, files=files, ephemeral=True)


async def _launch_delve(interaction: Interaction, loc_key: str):
    profile = E.get_profile(interaction.user.id)
    if profile is None or E.delves_left(profile) <= 0:
        await interaction.response.edit_message(
            view=_notice_view("🛌 You need to rest - no delves left today."), attachments=[])
        return
    delve = E.start_delve(profile, interaction.channel_id, loc_key)
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
        E.save_profile(profile)      # stamina already spent; keep the books straight
        return
    delve.message_id = msg.id
    profile["active_delve"] = msg.id
    E.save_profile(profile)
    E.save_delve(delve)
    try:
        interaction.client.add_view(view, message_id=msg.id)
    except Exception:
        logger.debug("skyrim add_view on launch failed", exc_info=True)
    loc = D.LOCATIONS[loc_key]
    await interaction.response.edit_message(
        view=_notice_view(f"{loc['emoji']} Off to **{loc['name']}** - good hunting, Dovahkiin."),
        attachments=[])


def _notice_view(text: str):
    view = discord.ui.LayoutView(timeout=60)
    box = discord.ui.Container(accent_colour=ACCENT)
    box.add_item(discord.ui.TextDisplay(text))
    view.add_item(box)
    return view


# --- character sheet -------------------------------------------------------------
def _sheet_text(profile) -> str:
    cls = D.CLASSES[profile["class"]]
    s = profile["skills"]
    st = profile["stats"]
    into, need = D.xp_into_level(profile["xp"])
    words = " ".join(D.SHOUT_WORDS[:profile["words"]]) if profile["words"] else "not yet learned"
    lines = [
        f"## {cls['emoji']} {profile['name']} - Level {E.level(profile)} {cls['name']}",
        f"-# {cls['stone']}  ·  XP {_bar(into, 0, need)} {into}/{need}",
        "",
        "**Skills** (improve by use)",
        f"{cls['weapon_skill']:<12} **{s['weapon']}** {_bar(s['weapon'])}",
        f"{'Sneak':<12} **{s['sneak']}** {_bar(s['sneak'])}",
        f"{'Speech':<12} **{s['speech']}** {_bar(s['speech'])}",
        "",
        f"**Gear**: {E.gear_name(profile, 'weapon')}  ·  {E.gear_name(profile, 'armour')} "
        f"(soaks {E.soak_pct(profile)}% of hits)",
        f"**Hearts**: {'❤️' * E.heart_max(profile)}  ·  🧪 {profile['potions']}/{E.potion_cap(profile)} potions",
        f"**The Voice**: 🗣️ {words} ({profile['words']}/3 words)  ·  🐉 {profile['souls']} unspent "
        f"soul{'s' if profile['souls'] != 1 else ''}",
        f"**Septims**: 💰 {profile['septims']:,}",
    ]
    if profile["perks"]:
        perk_bits = [f"{D.PERKS[k]['emoji']} {D.PERKS[k]['name']} {r}/{D.PERKS[k]['ranks']}"
                     for k, r in profile["perks"].items()]
        lines.append(f"**Perks**: {'  ·  '.join(perk_bits)}")
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
    await _edit_panel(interaction, _sheet_text(profile), [_back_row()])


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
    lines.append("")
    lines.append(f"-# Weapons add +{D.WEAPON_FIGHT_PER_TIER}% attack per tier; armour adds "
                 f"+{D.ARMOUR_SOAK_PER_TIER}% wound absorption per tier.")
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
    await _edit_panel(interaction, text, [row, _back_row()])


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
    if notice:
        lines += ["", notice]
    rows = []
    if pts > 0:
        select = discord.ui.Select(placeholder="Spend a perk point...")
        for key, perk in D.PERKS.items():
            if E.perk_rank(profile, key) < perk["ranks"]:
                select.add_option(label=f"{perk['name']} ({E.perk_rank(profile, key)}/{perk['ranks']})",
                                  value=key, emoji=perk["emoji"], description=perk["desc"][:100])

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
    rows.append(_back_row())
    await _edit_panel(interaction, "\n".join(lines), rows)


# --- rankings ------------------------------------------------------------------------
async def _hub_rankings(interaction: Interaction):
    profiles = sorted(E.all_profiles().values(), key=lambda p: p["xp"], reverse=True)[:10]
    lines = ["## 🏆 Legends of Skyrim", ""]
    if not profiles:
        lines.append("No adventurers yet. The ruins wait.")
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(profiles):
        cls = D.CLASSES[p["class"]]
        rank = medals[i] if i < len(medals) else f"`{i + 1:>2}.`"
        st = p["stats"]
        lines.append(f"{rank} {cls['emoji']} **{p['name']}** - Lv {E.level(p)}  ·  "
                     f"🐉 {st['dragons']}  ·  🏰 {st['clears']} cleared  ·  "
                     f"💰 {p['septims']:,}")
    await _edit_panel(interaction, "\n".join(lines), [_back_row()])


# --- help -------------------------------------------------------------------------
def _help_text() -> str:
    return (
        "## 📖 Skyrim - How it works\n"
        "A persistent adventure: your character, skills, gear and dragon souls are kept "
        "forever. Run `/skyrim` for your hub, then **Adventure** to delve.\n\n"
        "**Delves** - a run of rooms ending in a boss. Each enemy shows your odds up front:\n"
        "- ⚔️ **Attack** - kill for full loot and XP. Fail and you take a wound.\n"
        "- 🥷 **Sneak** - slip past for XP (no loot). Get spotted and the fight is on.\n"
        "- 💬 **Persuade** - humans only. Talk your way through, sometimes at a profit.\n"
        "- 🗣️ **Shout** - the Voice flattens a room (dragons get grounded instead). "
        "Charges = words you know.\n"
        "- 🧪 **Potion** / 🚪 **Leave** - patch up, or walk out with your satchel. "
        "Fleeing mid-fight spills a third of it.\n\n"
        "**The stakes** - XP, skill-ups, gear, souls and potions bank instantly. The "
        "**septims in your satchel** only bank when you leave or clear - die and they stay "
        "in the dungeon.\n\n"
        "**Skills level by use** (swing to get better at swinging), your class shapes the "
        "odds, and perks stack on top - spend points in the hub.\n\n"
        "**Dragons** - sighted once you're strong enough. Slay one and its **soul** is "
        "yours; spend souls at **Word Walls** to learn FUS, then RO, then DAH.\n\n"
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
