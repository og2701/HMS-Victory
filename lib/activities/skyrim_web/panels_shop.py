"""Belethor's and the crafting benches in the activity: the shop, property, rumours, the lab bench, the grindstone
and the Daedric pacts. Each is a port of its views.py screen as SkPanel data (docs/skyrim-activity-contract.md).
"""

from __future__ import annotations

from lib.activities.skyrim_web import common
from lib.activities.skyrim_web.common import Refuse
from lib.activities.skyrim_web.panels_character import _fresh, _one, _values
from lib.activities.skyrim_web.registry import act, view
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E

_QUOTE = "\"Everything's for sale, my friend! Everything! If I had a sister, I'd sell her in a second.\""


async def _saved(ctx, profile, panel_fn, toast):
    """Save, tell Discord, answer with the panel redrawn from what was saved."""
    E.save_profile(profile)
    await common.after(ctx, profile)
    return common.result(panel_fn(profile, ctx), profile, toast=toast)


def _fail(err):
    raise Refuse(common.clean(err))


# ---- shop --------------------------------------------------------------------------------------------------

@view("shop")
def shop(profile, ctx):
    cap = E.potion_cap(profile)
    full = profile["potions"] >= cap
    buy = [common.tile("Potion", icon="a:potion|i:flask", cost=E.shop_price(profile, D.POTION_PRICE),
                       pips=(profile["potions"], cap), state="max" if full else None, act=None if full else "potion",
                       info="Your potion pockets are full." if full else None)]
    for slot, scale in (("weapon", 1.0), ("armour", 0.8)):
        tier = profile[f"{slot}_tier"]
        if tier >= len(D.GEAR_TIERS) - 1:
            top = D.GEAR_TIERS[tier]
            buy.append(common.tile(top["name"], icon=f"a:{slot}_{top['name'].lower()}|i:{'sword' if slot == 'weapon' else 'shield'}",
                                   sub=f"best {slot}", state="max", info="Nothing finer exists in Tamriel."))
            continue
        nxt = D.GEAR_TIERS[tier + 1]
        short = profile["stats"]["dragons"] < nxt["dragons"]
        buy.append(common.tile(nxt["name"], icon=f"a:{slot}_{nxt['name'].lower()}|i:{'sword' if slot == 'weapon' else 'shield'}",
                               cost=E.shop_price(profile, int(nxt["price"] * scale)),
                               sub=f"{nxt['dragons']} dragons first" if short else f"next {slot}",
                               state="locked" if short else None, act=None if short else slot,
                               info=f"Slay {nxt['dragons']} dragons first ({profile['stats']['dragons']}/{nxt['dragons']})." if short else None))
    style = profile.get("armour_style", "heavy")
    fit = [common.tile("Heavy", icon="i:shield", sub="more armour", state="done" if style == "heavy" else None,
                       act=None if style == "heavy" else "style", info="You're in heavy armour."),
           common.tile("Light", icon="i:hood", sub=f"+{D.LIGHT_SNEAK_BONUS}% sneak", state="done" if style == "light" else None,
                       act=None if style == "light" else "style", info="You're in light armour.")]
    rooms = [common.tile("Property", icon="s:property", nav="property"),
             common.tile("Grindstone", icon="s:grindstone", nav="grindstone"),
             common.tile("Lab bench", icon="s:alchemy", nav="alchemy"),
             common.tile("Rumours", icon="s:rumours", nav="rumours")]
    note = (f"Weapons add +{D.WEAPON_FIGHT_PER_TIER}% to every attack per tier. Heavy armour soaks "
            f"{D.ARMOUR_SOAK_PER_TIER}% a tier, light {D.LIGHT_SOAK_PER_TIER}%. Switching is free.")
    return common.panel("shop", "🏪 Belethor's", back="town", blurb=[_QUOTE, note],
                        sections=[common.section("Buy", tiles=buy, cols=3), common.section("Armour", tiles=fit),
                                  common.section("In the back", tiles=rooms)])


@act("shop")
async def shop_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action == "potion":
        err = E.buy_potion(profile)
        toast = "🧪 One health potion. \"Pleasure doing business!\""
    elif action in ("weapon", "armour"):
        err = E.buy_gear(profile, action)
        toast = f"{'⚔️' if action == 'weapon' else '🛡️'} Sold! You now carry {E.gear_name(profile, action)}."
    elif action == "style":
        err = None
        toast = f"👕 Re-fitted: you now wear **{E.toggle_armour_style(profile)}** armour."
    else:
        raise Refuse("You can't do that here.")
    if err:
        _fail(err)
    return await _saved(ctx, profile, shop, toast)


# ---- property ----------------------------------------------------------------------------------------------

@view("property")
def property_(profile, ctx):
    lines, opts = [], []
    for key, item in D.HOME_ITEMS.items():
        owned = E.home_owned(profile, key)
        tick = "✅ owned" if owned else f"{item['price']:,} septims"
        lines.append(f"{item['emoji']} **{item['name']}** ({tick}) - {item['desc']}")
        if not owned and (not item["requires"] or E.home_owned(profile, item["requires"])):
            opts.append(common.option(key, f"Buy {item['name']} ({item['price']:,})", item["desc"], item["emoji"]))
    return common.panel(
        "property", "🏠 Property - Belethor's side business", back="shop",
        blurb=["\"A house? I know a man who knows a Jarl. For a price.\""],
        stats=[common.stat("Septims", f"{profile['septims']:,}", "💰")],
        sections=[common.section("On the market", lines)],
        selects=[common.select("buy", "Buy a property or furnishing...", opts)])


@act("property")
async def property_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action != "buy":
        raise Refuse("You can't do that here.")
    key = _one(body)
    err = E.buy_home(profile, key)
    if err:
        _fail(err)
    return await _saved(ctx, profile, property_, f"✅ {D.HOME_ITEMS[key]['name']} is yours. \"Pleasure doing business!\"")


# ---- rumours -----------------------------------------------------------------------------------------------

@view("rumours")
def rumours(profile, ctx):
    state = E.rumours_of(profile)
    lines = []
    for key, r in D.RUMOURS.items():
        loc = D.LOCATIONS[r["loc"]]
        if state.get(key) == "slain":
            lines.append(f"✅ {r['emoji']} **{r['name'].capitalize()}** - settled. {loc['name']} stands quiet, "
                         f"because of you.")
        elif state.get(key) == "heard":
            lines.append(f"🗺️ {r['emoji']} **{r['name'].capitalize()}** - heard. **{loc['name']}** waits on your "
                         f"Adventure map.")
        else:
            lines.append(f"❔ {r['emoji']} **{r['name'].capitalize()}** ({r['price']:,} septims, level "
                         f"{r['min_level']}+) - {r['blurb']}")
    opts = [common.option(k, f"{r['name'].capitalize()} ({r['price']:,})", r["blurb"], r["emoji"])
            for k, r in D.RUMOURS.items() if not state.get(k) and E.level(profile) >= r["min_level"]]
    return common.panel(
        "rumours", "🗣️ Rumours - Belethor leans in", back="shop",
        blurb=["\"For a few septims I'll tell you where the LEGENDS sleep. One-time hunts, friend - the kind you "
               "tell grandchildren about. If you get to have any.\""],
        stats=[common.stat("Septims", f"{profile['septims']:,}", "💰")],
        sections=[common.section("Whispers", lines)],
        selects=[common.select("buy", "Buy a whisper...", opts)])


@act("rumours")
async def rumours_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action != "buy":
        raise Refuse("You can't do that here.")
    key = _one(body)
    err = E.buy_rumour(profile, key)
    if err:
        _fail(err)
    loc = D.LOCATIONS[D.RUMOURS[key]["loc"]]
    return await _saved(ctx, profile, rumours,
                        f"🗺️ Belethor marks your map: **{loc['name']}**. \"Pleasure doing business. Try to come back.\"")


# ---- the lab bench -----------------------------------------------------------------------------------------

@view("alchemy")
def alchemy(profile, ctx):
    pouch = profile.get("ingredients") or {}
    sections = []
    if pouch:
        bits = [f"{D.INGREDIENTS[k]['emoji']} {D.INGREDIENTS[k]['name']} ×{n}"
                for k, n in sorted(pouch.items()) if k in D.INGREDIENTS]
        sections.append(common.section("Your pouch", ["  ·  ".join(bits)]))
    else:
        sections.append(common.section("Your pouch", ["Your pouch is empty. Elites, bounties and dragons drop the "
                                                      "good stuff."]))
    stock = E.elixir_stock(profile)
    if stock:
        shelf = "  ·  ".join(f"{D.RECIPES[k]['emoji']} {D.RECIPES[k]['name']} ×{n}" for k, n in sorted(stock.items()))
        sections.append(common.section("🧪 Your elixir shelf", [
            shelf, "Pick which to drink on the Adventure picker before you set out - one of each type per delve, "
                   "effects stack."]))
    lab = E.home_owned(profile, "alchemy_lab")
    selects = []
    if not lab:
        sections.append(common.section("Locked", ["🔒 You need an **Alchemy Lab** (a Breezehome upgrade in "
                                                  "Property) to brew."]))
    else:
        rows = []
        for key, r in D.RECIPES.items():
            cost = "  ".join(f"{D.INGREDIENTS[k]['emoji']}×{n}" for k, n in r["cost"].items())
            rows.append(f"{'✅' if E.can_brew(profile, key) else '◻️'} {r['emoji']} **{r['name']}** - {r['desc']}  ({cost})")
        sections.append(common.section("Recipes", rows))
        src = E.ingredient_sources()
        guide = "  ·  ".join(f"{D.INGREDIENTS[k]['emoji']} {', '.join(src[k])}" for k in D.INGREDIENTS if k in src)
        sections.append(common.section("🏹 Where to hunt", [guide]))
        opts = [common.option(k, D.RECIPES[k]["name"], D.RECIPES[k]["desc"], D.RECIPES[k]["emoji"])
                for k in D.RECIPES if E.can_brew(profile, k)]
        selects.append(common.select("brew", "Brew a recipe...", opts))
    stats = [common.stat("Potions", f"{profile['potions']}/{E.potion_cap(profile)}", "🧪"),
             common.stat("Elixirs on the shelf", sum(stock.values()), "⚗️"),
             common.stat("Alchemy Lab", "yes" if lab else "not yet", "🏠")]
    return common.panel(
        "alchemy", "⚗️ The Lab Bench", back="shop",
        blurb=["Brew looted ingredients into potions and one-delve elixirs. Ingredients ride at risk in your "
               "satchel, so it pays to walk out alive."],
        stats=stats, sections=sections, selects=selects)


@act("alchemy")
async def alchemy_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action != "brew":
        raise Refuse("You can't do that here.")
    key = _one(body)
    err = E.brew(profile, key)
    if err:
        _fail(err)
    r = D.RECIPES[key]
    toast = f"{r['emoji']} Brewed **{r['name']}**."
    if r["makes"] != "potion":
        toast += (f" On the shelf (×{E.elixir_stock(profile).get(key, 0)}) - pick it on the Adventure picker "
                  f"when you want it.")
    return await _saved(ctx, profile, alchemy, toast)


# ---- the grindstone ----------------------------------------------------------------------------------------

@view("grindstone")
def grindstone(profile, ctx):
    temper = profile.get("temper") or {"weapon": 0, "armour": 0}
    pouch = profile.get("ingredients") or {}
    lines, actions = [], []
    for slot, emoji in (("weapon", "⚔️"), ("armour", "🛡️")):
        g = temper.get(slot, 0)
        star = "✦" * g + "·" * (E.TEMPER_MAX_GRADE - g)
        if g >= E.TEMPER_MAX_GRADE:
            lines.append(f"{emoji} **{slot.title()}** [{star}] - honed to perfection.")
            actions.append(common.action(slot, f"Temper {slot}", emoji, "primary", disabled=True,
                                         hint="Honed to perfection."))
            continue
        c = E.temper_cost(g)
        mats = "  ".join(f"{D.INGREDIENTS[k]['emoji']}×{n}" for k, n in c["mats"].items())
        eff = f"+{E.TEMPER_FIGHT_PER_GRADE}% attack" if slot == "weapon" else f"+{E.TEMPER_SOAK_PER_GRADE}% soak"
        lines.append(f"{emoji} **{slot.title()}** [{star}] → grade {g + 1} ({eff}): {c['septims']:,} septims + {mats}")
        actions.append(common.action(slot, f"Temper {slot}", emoji, "primary",
                                     hint=f"{c['septims']:,} septims + {mats}"))
    sections = [common.section("The stone", lines)]
    if pouch:
        sections.append(common.section("🎒 Materials", ["  ".join(
            f"{D.INGREDIENTS[k]['emoji']}×{n}" for k, n in sorted(pouch.items()) if k in D.INGREDIENTS)]))
    src = E.ingredient_sources()
    mats_used = sorted({m for c in D.TEMPER_COSTS for m in c["mats"]})
    sections.append(common.section("🏹 Where to hunt", ["  ·  ".join(
        f"{D.INGREDIENTS[m]['emoji']} {D.INGREDIENTS[m]['name']} - {', '.join(src.get(m, ['?']))}"
        for m in mats_used)]))
    stats = [common.stat("Septims", f"{profile['septims']:,}", "💰"),
             common.stat("Weapon temper", f"{temper.get('weapon', 0)}/{E.TEMPER_MAX_GRADE}", "⚔️"),
             common.stat("Armour temper", f"{temper.get('armour', 0)}/{E.TEMPER_MAX_GRADE}", "🛡️")]
    return common.panel(
        "grindstone", "🪓 The Grindstone", back="shop",
        blurb=["Hone gear past its tier with septims and looted materials. Bonuses that the 86% cap can't "
               "swallow: sharper weapons feed **Overkill**, tougher armour soaks more."],
        stats=stats, sections=sections, actions=actions)


@act("grindstone")
async def grindstone_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action not in ("weapon", "armour"):
        raise Refuse("You can't do that here.")
    err = E.temper(profile, action)
    if err:
        _fail(err)
    return await _saved(ctx, profile, grindstone,
                        f"🪓 Your {action} rings sharper - grade {profile['temper'][action]}.")


# ---- Daedric pacts -----------------------------------------------------------------------------------------

@view("pacts")
def pacts(profile, ctx):
    sworn = profile.get("nextpacts") or []
    lines = []
    for key, pact in D.PACTS.items():
        price = pact.get("mult_note") or f"x{pact['mult']:g}"
        lines.append(f"{'⚖️' if key in sworn else '◻️'} {pact['emoji']} **{pact['name']}** ({price}) - {pact['desc']}")
    sections = [common.section("The Princes' terms", lines)]
    stats = [common.stat("Sworn", len(sworn), "⚖️")]
    if sworn:
        fake = E.Delve(profile["user_id"], "x", 0, "embershard",
                       [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}],
                       hearts=1, shout_charges=0, pacts=sworn)
        mult = f"x{E.pact_mult(fake):g}"
        sections.append(common.section("Sworn", [
            f"Satchel **{mult}** on your next delve (cap x{E.PACT_MULT_CAP:g})."]))
        stats.append(common.stat("Satchel", mult, "💰"))
    locked = E.level(profile) < E.PACT_MIN_LEVEL
    if locked:
        sections.append(common.section("Locked", [f"🔒 The Princes don't bargain with the unproven "
                                                   f"(level {E.PACT_MIN_LEVEL}+)."]))
    opts = [common.option(key, f"{p['name']} ({p.get('mult_note') or 'x' + format(p['mult'], 'g')})", p["desc"],
                          p["emoji"], chosen=key in sworn) for key, p in D.PACTS.items()]
    selects = [] if locked else [common.select("swear", "Swear your pacts (pick none to clear)...", opts,
                                               min=0, max=len(D.PACTS))]
    return common.panel(
        "pacts", "⚖️ Daedric Pacts", back="town",
        blurb=["Swear curses on your **next delve** for a multiplied satchel if you bank it. Death loses "
               "everything, as ever. Pacts don't bind the Daily, Skuldafn or the Cairn - the Princes want to "
               "watch you *choose* it."],
        stats=stats, sections=sections,
        actions=[common.action("adventure", "To the roads", "🗺️", nav="adventure")], selects=selects)


@act("pacts")
async def pacts_act(profile, ctx, action, body):
    profile = _fresh(profile, ctx)
    if action != "swear":
        raise Refuse("You can't do that here.")
    err = E.swear_pacts(profile, _values(body))
    if err:
        _fail(err)
    n = len(profile.get("nextpacts") or [])
    toast = f"⚖️ {n} pact{'s' if n != 1 else ''} sworn." if n else "The Princes shrug. No pacts bound."
    return await _saved(ctx, profile, pacts, toast)
