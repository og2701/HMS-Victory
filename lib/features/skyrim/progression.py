"""Small persistent progression rules; callers own profile/store persistence.

Hunt rewards use immutable receipts. A receipt is never deleted when delivered:
the receipt ID and its balance credit are saved together on the profile. This
allows either JSON file to be retried after a restart without double payment.
"""

import copy
import datetime
import time

from lib.features.skyrim import data as D


def _engine():
    from lib.features.skyrim import engine
    return engine


def _notice(profile, text):
    notices = profile.setdefault("reward_notices", [])
    notices.append(text)
    del notices[:-4]


def _week_date(week):
    try:
        year, number = str(week).split("-", 1)
        return datetime.date.fromisocalendar(int(year), int(number), 1).isoformat()
    except (TypeError, ValueError):
        return None


def settle_task_rollover(profile, new_week):
    """Credit completed old tasks BEFORE task_state clears them; no file I/O.

    Both the old claim marks and resulting balances belong to the same profile
    save. A retry from an unsaved profile therefore recomputes the same balance.
    """
    state = profile.get("tasks") or {}
    old_week = state.get("week")
    date = _week_date(old_week)
    if not old_week or old_week == new_week or not date:
        return None
    E = _engine()
    keys = E.weekly_tasks(date)
    claimed = set(state.get("claimed") or [])
    progress = state.get("prog") or {}
    completed = [k for k in keys if progress.get(k, 0) >= D.TASKS[k]["n"]]
    pending = [k for k in completed if k not in claimed]
    swept = bool(keys) and len(completed) == len(keys) and not state.get("bonus")
    if not pending and not swept:
        return None
    coin = sum(D.TASK_REWARDS[D.TASKS[k]["band"]][0] for k in pending)
    xp = sum(D.TASK_REWARDS[D.TASKS[k]["band"]][1] for k in pending)
    if swept:
        coin += D.TASK_ALL_BONUS[0]
        xp += D.TASK_ALL_BONUS[1]
        state["bonus"] = True
    state["claimed"] = sorted(claimed | set(pending))
    paid = E._septims(profile, coin)
    profile["septims"] += paid
    gained, _ = E.add_xp(profile, xp)
    stats = profile.setdefault("stats", {})
    stats["tasks_done"] = int(stats.get("tasks_done", 0)) + len(pending)
    line = f"Saved bounties: +{paid:,} septims · +{gained} XP."
    _notice(profile, line)
    return line


def capture_hunt_rewards(store):
    """Snapshot newly earned share increments into durable immutable receipts.

    Call after calculating ALL a wave's shares (including its killing bonus),
    before saving the hunt, and on reads to migrate legacy unclaimed shares.
    """
    changed = False
    mailbox = store.setdefault("reward_mailbox", {})
    sequences = store.setdefault("reward_sequence", {})
    for uid, share in (store.get("shares") or {}).items():
        if share.get("claimed"):
            continue
        recorded = share.get("mailbox_recorded") or {}
        coin = max(0, int(share.get("septims", 0)) - int(recorded.get("septims", 0)))
        xp = max(0, int(share.get("xp", 0)) - int(recorded.get("xp", 0)))
        if not coin and not xp:
            continue
        seq = int(sequences.get(uid, 0)) + 1
        sequences[uid] = seq
        receipt_id = f"hunt:{store.get('week', 'legacy')}:{uid}:{seq}"
        mailbox.setdefault(uid, {})[receipt_id] = {
            "septims": coin, "xp": xp, "week": store.get("week"),
            "boss": store.get("boss"),
        }
        share["mailbox_recorded"] = {
            "septims": int(share.get("septims", 0)), "xp": int(share.get("xp", 0)),
        }
        changed = True
    return changed


def preserve_hunt_mailbox(old_store, new_store):
    """Carry unpaid and already-delivered receipt evidence through week reset."""
    capture_hunt_rewards(old_store)
    new_store["reward_mailbox"] = copy.deepcopy(old_store.get("reward_mailbox") or {})
    new_store["reward_sequence"] = dict(old_store.get("reward_sequence") or {})


def hunt_rewards_waiting(profile, store):
    """Read pending durable rewards without consuming or acknowledging them."""
    delivered = set(profile.get("hunt_receipts") or [])
    mailbox = (store.get("reward_mailbox") or {}).get(str(profile["user_id"])) or {}
    pending = {key: value for key, value in mailbox.items() if key not in delivered}
    if not pending:
        return None
    return {"septims": sum(int(r.get("septims", 0)) for r in pending.values()),
            "xp": sum(int(r.get("xp", 0)) for r in pending.values()),
            "receipts": list(pending), "claimed": False}


def claim_hunt_rewards(profile, store):
    """Apply receipts to a profile once; caller must save the profile afterwards.

    The shared mailbox is intentionally not acknowledged/deleted. The existing
    share flag is advisory only, and never determines receipt eligibility.
    """
    capture_hunt_rewards(store)
    pending = hunt_rewards_waiting(profile, store)
    if not pending:
        return None
    E = _engine()
    paid = E._septims(profile, pending["septims"])
    profile["septims"] += paid
    gained, _ = E.add_xp(profile, pending["xp"])
    profile["hunt_receipts"] = sorted(set(profile.get("hunt_receipts") or []) |
                                      set(pending["receipts"]))
    share = (store.get("shares") or {}).get(str(profile["user_id"]))
    if share:
        share["claimed"] = True
    line = f"Hunt rewards: +{paid:,} septims · +{gained} XP."
    _notice(profile, line)
    return line


HUNT_ROLES = {
    "attack": {"label": "Attack", "emoji": "⚔️", "attack": 0,
               "hint": "Deal your full damage."},
    "expose": {"label": "Expose", "emoji": "🎯", "attack": -10,
               "hint": "−10% hit; the next ally gains +12% hit."},
    "protect": {"label": "Protect", "emoji": "🛡️", "attack": -10,
                "hint": "−10% hit; the next ally gains +12% guard."},
}
SUPPORT_SECONDS = 24 * 60 * 60


def consume_hunt_support(store, profile, role="attack", now=None):
    """Consume at most one effect of each kind, only for an accepted march.

    The provider cannot consume their own help. A pair of allies cannot stack
    unlimited effects: the store holds only one expose and one protect slot.
    """
    if role not in HUNT_ROLES:
        raise ValueError("Choose attack, expose or protect.")
    now = int(time.time() if now is None else now)
    support = store.setdefault("ally_support", {})
    result = {"attack_bonus": HUNT_ROLES[role]["attack"], "guard_bonus": 0,
              "lines": [], "helpers": []}
    for key, field in (("expose", "attack_bonus"), ("protect", "guard_bonus")):
        effect = support.get(key)
        if not effect:
            continue
        if int(effect.get("expires", 0)) <= now:
            support.pop(key, None)
            continue
        if str(effect.get("uid")) == str(profile["user_id"]):
            continue
        result[field] += 12
        result["helpers"].append(str(effect["uid"]))
        who = effect.get("name", "An ally")
        benefit = "+12% hit" if key == "expose" else "+12% guard"
        result["lines"].append(f"{who} helps you: {benefit}.")
        support.pop(key, None)
    return result


def finish_hunt_support(store, profile, role="attack", now=None):
    """Leave bounded help for another hunter after resolving this march."""
    if role not in HUNT_ROLES:
        raise ValueError("Choose attack, expose or protect.")
    if role == "attack":
        return None
    now = int(time.time() if now is None else now)
    store.setdefault("ally_support", {})[role] = {
        "uid": str(profile["user_id"]), "name": profile.get("name", "An ally"),
        "expires": now + SUPPORT_SECONDS,
    }
    return ("Weakness exposed for the next ally." if role == "expose"
            else "Your shield will cover the next ally.")


# Four promotions per faction. Favour unlocks the examination; varied play passes it.
MISSIONS = {
    "companions": [
        {"label": "Defeat 10 foes", "kind": "kill", "n": 10, "elixir": "vigor"},
        {"label": "Clear a Hard delve", "kind": "clear", "n": 1,
         "diff": ["Hard"], "elixir": "fortitude"},
        {"label": "Slay a dragon", "kind": "kill", "n": 1,
         "dragon": True, "elixir": "fury"},
        {"label": "Win 2 Pit bouts", "kind": "pit_win", "n": 2,
         "elixir": "true_shot"},
    ],
    "thieves": [
        {"label": "Slip past 5 foes", "kind": "sneak", "n": 5, "elixir": "vigor"},
        {"label": "Loot 3 chests", "kind": "chest", "n": 3, "elixir": "fortitude"},
        {"label": "Clear without a potion", "kind": "clear", "n": 1,
         "no_potion": True, "elixir": "fury"},
        {"label": "Clear the deep route", "kind": "clear", "n": 1,
         "deep": True, "elixir": "true_shot"},
    ],
    "college": [
        {"label": "Win 4 parleys", "kind": "persuade", "n": 4, "elixir": "vigor"},
        {"label": "Defeat 6 foes with fire", "kind": "kill", "n": 6,
         "style": "destruction", "elixir": "fortitude"},
        {"label": "Clear using only fire", "kind": "clear", "n": 1,
         "style_only": "destruction", "elixir": "fury"},
        {"label": "Clear a Stirred rank 2+ delve", "kind": "clear", "n": 1,
         "stirred_min": 2, "elixir": "true_shot"},
    ],
}
FACTION_TITLES = {"companions": "Shield of Jorrvaskr", "thieves": "Shadow of Riften",
                  "college": "Voice of Winterhold"}


def ensure_promotions(profile):
    """Grandfather already-earned guild ranks once, before changing favour."""
    favours = profile.get("favours") or {}
    promotions = profile.setdefault("promotions", {})
    for key in D.FACTIONS:
        promotions.setdefault(key, {
            "grandfathered": min(4, max(0, int(favours.get(key, 0))) // 2),
            "claimed": [], "progress": {},
        })
    return promotions


def faction_rank_index(profile, key=None):
    key = key or profile.get("allegiance")
    record = ensure_promotions(profile).get(key) or {}
    return min(4, max([int(record.get("grandfathered", 0))] +
                      [int(x) for x in record.get("claimed", [])]))


def faction_rank(profile, key=None):
    return D.FACTION_RANKS[faction_rank_index(profile, key)]


def promotion(profile):
    key = profile.get("allegiance")
    if key not in MISSIONS:
        return None
    tier = faction_rank_index(profile, key) + 1
    if tier > len(MISSIONS[key]):
        return None
    mission = MISSIONS[key][tier - 1]
    record = ensure_promotions(profile)[key]
    progress = min(mission["n"], int(record.get("progress", {}).get(str(tier), 0)))
    eligible = int((profile.get("favours") or {}).get(key, 0)) >= tier * 2
    complete = progress >= mission["n"]
    title = FACTION_TITLES[key] if tier == 4 else ""
    return {"faction": key, "tier": tier, "rank": D.FACTION_RANKS[tier],
            "label": mission["label"], "progress": progress, "goal": mission["n"],
            "complete": complete, "eligible": eligible, "claimable": complete and eligible,
            "reward": "2 elixirs" + (f" · {title}" if title else ""), "title": title,
            "favour_needed": tier * 2}


def promotion_event(profile, kind, **ctx):
    current = promotion(profile)
    if not current or current["complete"]:
        return
    key, tier = current["faction"], current["tier"]
    mission = MISSIONS[key][tier - 1]
    if _engine()._task_matches(mission, kind, ctx):
        record = ensure_promotions(profile)[key]
        progress = record.setdefault("progress", {})
        progress[str(tier)] = min(mission["n"], int(progress.get(str(tier), 0)) + 1)


def claim_promotion(profile):
    current = promotion(profile)
    if not current:
        return "No promotion is waiting."
    if not current["eligible"]:
        return f"Earn {current['favour_needed']} favour to take this rank."
    if not current["complete"]:
        return f"Finish the trial: {current['label']} ({current['progress']}/{current['goal']})."
    key, tier = current["faction"], current["tier"]
    record = ensure_promotions(profile)[key]
    record.setdefault("claimed", []).append(tier)
    elixir = MISSIONS[key][tier - 1]["elixir"]
    stock = profile.setdefault("elixirs", {})
    stock[elixir] = int(stock.get(elixir, 0)) + 2
    if current["title"]:
        titles = profile.setdefault("titles", [])
        if current["title"] not in titles:
            titles.append(current["title"])
    _notice(profile, f"Promoted to {current['rank']} · {current['reward']}.")
    return None


def inheritance_options(profile):
    E = _engine()
    return [{"skill": skill, "choice": choice,
             "label": f"{skill.title()}: {D.DOCTRINES[skill][choice]['name']}"}
            for skill in D.DOCTRINES for choice in E.doctrine_keys(profile, skill)
            if choice in D.DOCTRINES[skill]]


def inherit(profile, skill, choice):
    if not any(o["skill"] == skill and o["choice"] == choice
               for o in inheritance_options(profile)):
        return "Choose an ability you have already learned."
    profile["inheritance"] = {"skill": skill, "choice": choice}
    return None


def prepare_inheritance(profile):
    """Validate and copy the selected learned doctrine BEFORE rebirth resets it."""
    selected = profile.get("inheritance") or {}
    options = inheritance_options(profile)
    return next(({"skill": o["skill"], "choice": o["choice"]} for o in options
                 if (o["skill"], o["choice"]) ==
                 (selected.get("skill"), selected.get("choice"))), None)


def apply_inheritance(profile, selected):
    """Restore exactly one validated choice AFTER rebirth; retain no other doctrine."""
    profile["doctrines"] = {}
    profile.pop("inheritance", None)
    if not selected:
        return
    skill, choice = selected.get("skill"), selected.get("choice")
    if choice not in D.DOCTRINES.get(skill, {}):
        return
    profile["doctrines"] = {skill: [choice]}
    profile["inheritance"] = {"skill": skill, "choice": choice}


def next_goal(profile):
    """One concise achievable objective, routed through existing hub panels."""
    E = _engine()
    if getattr(E, "tutorial_available", lambda p: False)(profile):
        return {"text": "Learn the road in a free first adventure.", "action": "tutorial"}
    if profile.get("active_delve"):
        return {"text": "Finish your current adventure.", "action": "adventure"}
    if E.perk_points(profile) and any(E.perk_rank(profile, k) < p["ranks"]
                                    for k, p in D.PERKS.items()):
        return {"text": "Spend a perk point on your next advantage.", "action": "perks"}
    current = promotion(profile)
    if current and current["claimable"]:
        return {"text": f"Claim your {current['rank']} promotion.", "action": "factions"}
    if E.retire_ready(profile)[0]:
        return {"text": "Choose an inherited ability and enter the Hall.", "action": "hall"}
    tier = min(int(profile.get("weapon_tier", 0)) + 1, len(D.GEAR_TIERS) - 1)
    gear = D.GEAR_TIERS[tier]
    if tier > profile.get("weapon_tier", 0) and profile.get("septims", 0) >= E.shop_price(profile, gear["price"]) \
            and profile.get("stats", {}).get("dragons", 0) >= gear["dragons"]:
        return {"text": f"Upgrade your weapon to {gear['name']}.", "action": "shop"}
    if current and not current["complete"]:
        return {"text": f"{current['label']} ({current['progress']}/{current['goal']}).",
                "action": "factions"}
    if E.level(profile) >= 8 and not profile.get("allegiance"):
        return {"text": "Join a faction and begin its first trial.", "action": "factions"}
    if E.tasks_claimable(profile):
        return {"text": "Collect your completed bounties.", "action": "notice"}
    if E.daily_available(profile):
        return {"text": "Try today's shared adventure.", "action": "notice"}
    if E.level(profile) < 8:
        return {"text": f"Adventure toward level 8 ({E.level(profile)}/8).", "action": "adventure"}
    if profile.get("words", 0) < 3:
        return {"text": "Hunt dragons and learn the next Word.", "action": "adventure"}
    if not profile.get("alduin_slain"):
        return {"text": "Prepare for Alduin at level 20.", "action": "adventure"}
    return {"text": "Beat your best Soul Cairn depth.", "action": "adventure"}
