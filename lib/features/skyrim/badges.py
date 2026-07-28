"""Server badges for /skyrim - the bridge between the game and the wider economy.

Septims are a closed loop; badges are not. Every one of these pays UKPence out of
the bank on first award (config.BADGE_REWARDS, by rarity), so this module is the
only place in the feature that can move real money and it is deliberately dull:
pure predicates over a profile, no side effects of its own, everything wrapped so a
failure here can never break a delve.

This module is the AUTHORITY on the thresholds. The rows seeded in database.py carry
the player-facing descriptions - keep the two in step.

Awards go through award_badge_with_notify (idempotent: a badge already held is a
no-op), and profile["badges"] caches what's been handed out so the common case -
a player who has already earned everything they're going to earn today - does no
database work at all. The cache is only written AFTER a successful award, so a
failed one simply retries on the next click.
"""

import logging

logger = logging.getLogger(__name__)

TOP_FACTION_FAVOUR = 8      # favour // 2 indexes FACTION_RANKS; 8 lands on Harbinger
DRAGON_HUNTER_KILLS = 10
CAIRN_DEPTH = 20
STREAK_DAYS = 14


def _earned(profile) -> set:
    """Every badge id this profile currently qualifies for. Pure - no writes."""
    from lib.features.skyrim import engine as E
    from lib.features.skyrim import data as D

    st = profile.get("stats") or {}
    skills = profile.get("skills") or {}
    rec = E.records_of(profile)
    seen = set((profile.get("log") or {}).get("events") or [])
    out = set()

    def win(badge_id, ok):
        if ok:
            out.add(badge_id)

    # --- Bronze: you turned up ---------------------------------------------------
    win("sky_dovahkiin", int(st.get("delves", 0)) > 0)
    win("sky_arrow_knee", "knee_trap" in seen)
    win("sky_sweetroll", int(st.get("sweetrolls", 0)) > 0)
    win("sky_cloud_district", "nazeem" in seen)
    win("sky_low_orbit", int(st.get("launched", 0)) > 0)
    win("sky_unmarked_grave", int(st.get("deaths", 0)) > 0)
    win("sky_stray", bool(profile.get("companions")))
    win("sky_maiq", "maiq" in seen)

    # --- Silver: you committed to something --------------------------------------
    win("sky_locksmith", int(skills.get("lockpicking", 0)) >= 100)
    win("sky_thuum", int(profile.get("words", 0)) >= len(D.SHOUT_WORDS))
    win("sky_dragon_hunter", int(st.get("dragons", 0)) >= DRAGON_HUNTER_KILLS)
    win("sky_harbinger", max([int(v) for v in (profile.get("favours") or {}).values()]
                             or [0]) >= TOP_FACTION_FAVOUR)
    win("sky_pit_champion", int(rec.get("pit_rank", 0)) >= len(D.PIT_TITLES))
    win("sky_into_the_dark", E.soulcairn_best(profile) >= CAIRN_DEPTH)
    win("sky_long_road", int(rec.get("streak", 0)) >= STREAK_DAYS)
    win("sky_thane", E.homestead_built(profile, "hall"))
    win("sky_wonder", bool(profile.get("wonders")))

    # --- Gold: the endgame -------------------------------------------------------
    win("sky_world_eater", int(profile.get("alduin_slain", 0) or 0) > 0)
    # a Legendary skill resets to 15, so the star counts as having mastered it
    win("sky_master_of_all", all(int(skills.get(s, 0)) >= 100 or E.legendary_count(profile, s)
                                 for s in E.SKILLS))
    win("sky_hall_of_legends", E.legacy_rank(profile) >= D.LEGACY_MAX)
    win("sky_no_stone_unturned", E.collection_pct(profile) >= 100)
    return out


ID_PREFIX = "sky_"


def roster() -> list:
    """[(id, name, description, icon)] for every seeded Skyrim badge, in seed order.
    Read from the database so database.py stays the single source of the copy -
    this module only ever owns the thresholds. Empty list if the table isn't there."""
    try:
        from database import DatabaseManager
        return DatabaseManager.fetch_all(
            "SELECT id, name, description, icon_path FROM badges WHERE id LIKE ?",
            (ID_PREFIX + "%",)) or []
    except Exception:
        logger.debug("skyrim badge roster read failed", exc_info=True)
        return []


def progress(profile) -> tuple:
    """(earned, total, [names still to chase]) for the Hall of Records line."""
    rows = roster()
    if not rows:
        return (0, 0, [])
    got = _earned(profile)
    missing = [f"{icon} {name}" for bid, name, _desc, icon in rows if bid not in got]
    return (len(rows) - len(missing), len(rows), missing)


async def award_skyrim_badges(client, profile):
    """Hand out any newly earned badges. Call after a profile is saved - it never
    mutates anything except the profile's own awarded-badge cache, and it swallows
    every error, because no badge is worth losing a delve over."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        cache = profile.setdefault("badges", [])
        fresh = _earned(profile) - set(cache)
        if not fresh:
            return
        awarded = []
        for badge_id in sorted(fresh):
            try:
                await award_badge_with_notify(client, profile["user_id"], badge_id)
                awarded.append(badge_id)
            except Exception:
                # leave it out of the cache so the next click tries again
                logger.error("skyrim badge %s failed for %s", badge_id,
                             profile.get("user_id"), exc_info=True)
        if awarded:
            cache.extend(awarded)
            from lib.features.skyrim import engine as E
            E.save_profile(profile)
    except Exception:
        logger.error("skyrim badge award failed", exc_info=True)
