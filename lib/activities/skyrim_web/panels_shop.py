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

_HOME_ICONS = {"breezehome": "s:homestead_1", "alchemy_lab": "i:flask", "trophy_room": "i:crown"}


@view("property")
def property_(profile, ctx):
    tiles = []
    for key, item in D.HOME_ITEMS.items():
        icon = _HOME_ICONS.get(key, "i:diamond")
        if E.home_owned(profile, key):
            tiles.append(common.tile(item["name"], icon=icon, state="done", info=item["desc"]))
        elif item["requires"] and not E.home_owned(profile, item["requires"]):
            need = D.HOME_ITEMS[item["requires"]]["name"]
            tiles.append(common.tile(item["name"], icon=icon, cost=item["price"], sub=f"needs {need}", state="locked",
                                     info=f"Buy {need} first. {item['desc']}"))
        else:
            tiles.append(common.tile(item["name"], icon=icon, cost=item["price"],
                                     state="ready" if profile["septims"] >= item["price"] else None,
                                     act="buy", body={"value": key}, info=item["desc"]))
    return common.panel("property", "🏠 Property", back="shop",
                        blurb=["\"A house? I know a man who knows a Jarl. For a price.\""],
                        sections=[common.section("On the market", tiles=tiles, cols=3)])


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
    lvl = E.level(profile)
    tiles = []
    for key, r in D.RUMOURS.items():
        loc = D.LOCATIONS[r["loc"]]
        icon = f"s:{loc['art']}" if loc.get("art") else "i:skull"
        name = r["name"].capitalize()
        if state.get(key) == "slain":
            tiles.append(common.tile(name, icon=icon, sub="settled", state="done",
                                     info=f"{loc['name']} stands quiet, because of you."))
        elif state.get(key) == "heard":
            tiles.append(common.tile(name, icon=icon, sub=loc["name"], state="ready", nav="map"))
        elif lvl < r["min_level"]:
            tiles.append(common.tile(name, icon=icon, cost=r["price"], sub=f"level {r['min_level']}", state="locked",
                                     info=f"Come back at level {r['min_level']}."))
        else:
            tiles.append(common.tile(name, icon=icon, cost=r["price"],
                                     state="ready" if profile["septims"] >= r["price"] else None,
                                     act="buy", body={"value": key}, info=r["blurb"]))
    return common.panel(
        "rumours", "🗣️ Rumours", back="shop",
        blurb=["\"For a few septims I'll tell you where the LEGENDS sleep. One-time hunts, friend - the kind you "
               "tell grandchildren about. If you get to have any.\""],
        sections=[common.section("Whispers", tiles=tiles, cols=3)])


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

_RECIPE_SUB = {"healing": "+1 potion", "vigor": "+1 heart", "fortitude": "+10% soak", "fury": "+6% attack",
               "true_shot": "+6% crit"}
_RECIPE_ICON = {"healing": "i:flask", "vigor": "i:heart", "fortitude": "i:shield", "fury": "i:flame", "true_shot": "i:eye"}


def _ing_icon(key):
    return f"a:ingredient_{key}|i:flask"


@view("alchemy")
def alchemy(profile, ctx):
    pouch = profile.get("ingredients") or {}
    sections = []
    bag = [common.tile(D.INGREDIENTS[k]["name"], icon=_ing_icon(k), value=f"x{n}")
           for k, n in sorted(pouch.items()) if k in D.INGREDIENTS and n > 0]
    if bag:
        sections.append(common.section("Pouch", tiles=bag, cols=4))
    stock = E.elixir_stock(profile)
    if stock:
        sections.append(common.section("Elixirs", tiles=[
            common.tile(D.RECIPES[k]["name"], icon=f"a:recipe_{k}|{_RECIPE_ICON.get(k, 'i:flask')}", value=f"x{n}",
                        sub=_RECIPE_SUB.get(k)) for k, n in sorted(stock.items()) if k in D.RECIPES], cols=3))
    if not E.home_owned(profile, "alchemy_lab"):
        sections.append(common.section("Brew", tiles=[
            common.tile("Alchemy Lab", icon="s:alchemy", sub="not yet", state="locked", nav="property",
                        info="Buy the Alchemy Lab in Property to brew.")], cols=1))
    else:
        recipes = []
        for key, r in D.RECIPES.items():
            icon = f"a:recipe_{key}|{_RECIPE_ICON.get(key, 'i:flask')}"
            missing = [f"{n - pouch.get(k, 0)}x {D.INGREDIENTS[k]['name']}" for k, n in r["cost"].items()
                       if pouch.get(k, 0) < n]
            if E.can_brew(profile, key):
                recipes.append(common.tile(r["name"], icon=icon, sub=_RECIPE_SUB.get(key), state="ready",
                                           act="brew", body={"value": key}, info=r["desc"]))
            else:
                recipes.append(common.tile(r["name"], icon=icon, sub=_RECIPE_SUB.get(key), state="locked",
                                           info="Need " + ", ".join(missing) + "." if missing else r["desc"]))
        sections.append(common.section("Brew", tiles=recipes, cols=2))
    return common.panel(
        "alchemy", "⚗️ Lab Bench", back="shop",
        blurb=["Brew looted ingredients into potions and one-delve elixirs. Ingredients ride at risk in your "
               "satchel, so it pays to walk out alive. Elites, bounties and dragons drop the good stuff. Pick "
               "elixirs on the Adventure picker before you set out - one of each type per delve."],
        sections=sections)


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
    tiles = []
    for slot, fallback in (("weapon", "i:sword"), ("armour", "i:shield")):
        g = temper.get(slot, 0)
        tier = D.GEAR_TIERS[profile[f"{slot}_tier"]]["name"].lower()
        icon = f"a:{slot}_{tier}|{fallback}"
        pips = (g, E.TEMPER_MAX_GRADE)
        if g >= E.TEMPER_MAX_GRADE:
            tiles.append(common.tile(slot.title(), icon=icon, pips=pips, state="max", info="Honed to perfection."))
            continue
        c = E.temper_cost(g)
        eff = f"+{E.TEMPER_FIGHT_PER_GRADE}% attack" if slot == "weapon" else f"+{E.TEMPER_SOAK_PER_GRADE}% soak"
        mats = ", ".join(f"{n}x {D.INGREDIENTS[k]['name']}" for k, n in c["mats"].items())
        ok = profile["septims"] >= c["septims"] and all(pouch.get(k, 0) >= n for k, n in c["mats"].items())
        tiles.append(common.tile(slot.title(), icon=icon, pips=pips, cost=c["septims"], sub=eff,
                                 state="ready" if ok else None, act=slot, info=f"Needs {mats}."))
    need = {}
    for g in range(E.TEMPER_MAX_GRADE):
        for k in E.temper_cost(g)["mats"]:
            need[k] = pouch.get(k, 0)
    sections = [common.section("The stone", tiles=tiles, cols=2)]
    mats = [common.tile(D.INGREDIENTS[k]["name"], icon=_ing_icon(k), value=f"x{n}") for k, n in sorted(need.items())
            if k in D.INGREDIENTS]
    if mats:
        sections.append(common.section("Materials", tiles=mats, cols=4))
    return common.panel(
        "grindstone", "🪓 Grindstone", back="shop",
        blurb=["Hone gear past its tier with septims and looted materials. Bonuses that the 86% cap can't "
               "swallow: sharper weapons feed **Overkill**, tougher armour soaks more."],
        sections=sections)


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

_PACT_ICON = {"boethiah": "i:sword", "namira": "i:skull", "dagon": "i:flame", "clavicus": "i:door"}
_PACT_CURSE = {"boethiah": "can miss", "namira": "no potions", "dagon": "crushing hits", "clavicus": "no fleeing"}


@view("pacts")
def pacts(profile, ctx):
    sworn = profile.get("nextpacts") or []
    locked = E.level(profile) < E.PACT_MIN_LEVEL
    tiles = []
    for key, pact in D.PACTS.items():
        icon = f"a:pact_{key}|{_PACT_ICON.get(key, 'i:skull')}"
        mult = f"x{pact['mult']:g}" + ("+" if pact.get("per_other") else "")
        curse = _PACT_CURSE.get(key, "a curse")
        if locked:
            tiles.append(common.tile(pact["name"].split("'")[0], icon=icon, value=mult, sub=curse, state="locked",
                                     info=f"The Princes don't bargain with the unproven (level {E.PACT_MIN_LEVEL}+)."))
            continue
        now = [k for k in sworn if k != key] if key in sworn else sworn + [key]
        tiles.append(common.tile(pact["name"].split("'")[0], icon=icon, value=mult, sub=curse,
                                 state="done" if key in sworn else None, act="swear", body={"values": now},
                                 info=pact["desc"]))
    stats = []
    if sworn:
        fake = E.Delve(profile["user_id"], "x", 0, "embershard",
                       [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}],
                       hearts=1, shout_charges=0, pacts=sworn)
        stats.append(common.stat("Satchel", f"x{E.pact_mult(fake):g}", "💰"))
    return common.panel(
        "pacts", "⚖️ Daedric Pacts", back="town",
        blurb=["Swear curses on your **next delve** for a multiplied satchel if you bank it (cap "
               f"x{E.PACT_MULT_CAP:g}). Death loses everything, as ever. Pacts don't bind the Daily, Skuldafn or "
               "the Cairn - the Princes want to watch you *choose* it. Tap a pact again to lift it."],
        stats=stats, sections=[common.section("Pacts", tiles=tiles, cols=2)],
        actions=[common.action("adventure", "To the roads", "🗺️", nav="adventure")])


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
