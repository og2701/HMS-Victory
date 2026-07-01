"""Badge award helpers for the games (Connect 4, Higher/Lower, Blackjack, casino-wide).

Every function is best-effort: a failure here must never break a payout or a game, so
callers fire-and-forget and everything is wrapped to swallow + log errors. Awards go
through award_badge_with_notify, which is idempotent (a badge already held is a no-op),
so it's safe to call the low-threshold checks on every win.
"""

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connect 4
# ---------------------------------------------------------------------------
async def award_connect4_badges(client, winner_id, stake):
    """Award the winner's Connect 4 badges from their lifetime wins + this match's stake.
    Call AFTER recording the result (via pvp_stats) so the count includes this win."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        from lib.economy import pvp_stats
        wins = pvp_stats.win_count("connect4", winner_id)
        if wins >= 1:
            await award_badge_with_notify(client, winner_id, "first_blood")
        if wins >= 10:
            await award_badge_with_notify(client, winner_id, "four_in_a_row")
        if wins >= 100:
            await award_badge_with_notify(client, winner_id, "grandmaster")
        if int(stake) >= 1000:
            await award_badge_with_notify(client, winner_id, "trash_talker")
    except Exception:
        logger.error("connect4 badge award failed", exc_info=True)


# ---------------------------------------------------------------------------
# Higher or Lower
# ---------------------------------------------------------------------------
async def award_higherlower_badges(client, game):
    """on_the_up: win 3 guesses in one game (steps). vertigo: cash out at >= 5x."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        if getattr(game, "steps", 0) >= 3:
            await award_badge_with_notify(client, game.player_id, "on_the_up")
        if getattr(game, "outcome", None) == "win" and getattr(game, "cumulative", 0) >= 5.0:
            await award_badge_with_notify(client, game.player_id, "vertigo")
    except Exception:
        logger.error("higher/lower badge award failed", exc_info=True)


# ---------------------------------------------------------------------------
# Cross-game "pinnacle" tracking for the secret cross-game badge: hit the TOP
# tier in all three new games. The badge id/name lives only in the encrypted
# secret config (neutral key "a8"); we resolve it via secret_config.bid so the
# open repo never names it.
# ---------------------------------------------------------------------------
_PINNACLES = {"chest", "blockade", "darts"}


async def _record_pinnacle(client, user_id, which):
    """Note a top-tier win in one of the three new games; award the secret cross-game badge once
    all three are done. Best-effort; a per-user list persists in config.GAME_PINNACLE_FILE."""
    try:
        import config
        from lib.core.file_operations import load_json_file, save_json_file
        from lib.bot.event_handlers import award_badge_with_notify
        store = load_json_file(config.GAME_PINNACLE_FILE) or {}
        got = set(store.get(str(user_id), []))
        if which not in got:
            got.add(which)
            store[str(user_id)] = sorted(got)
            save_json_file(config.GAME_PINNACLE_FILE, store)
        if _PINNACLES <= got:
            from lib.economy import secret_config as _sc
            if (_b := _sc.bid("a8")):
                await award_badge_with_notify(client, user_id, _b)
    except Exception:
        logger.error("pinnacle record failed", exc_info=True)


# ---------------------------------------------------------------------------
# Chest Upgrade
# ---------------------------------------------------------------------------
async def award_chest_badges(client, game):
    """diamond_chest: reach Diamond (win at the top tier). cold_feet: cash out the Wood chest.
    so_close: shatter on the Diamond attempt (fail the upgrade while holding Gold). Call once the
    game is over."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        uid = game.player_id
        if game.outcome == "win" and game.at_top():
            await award_badge_with_notify(client, uid, "diamond_chest")
            await _record_pinnacle(client, uid, "chest")
        elif game.outcome == "win" and game.tier == 0:
            await award_badge_with_notify(client, uid, "cold_feet")
        elif game.outcome == "lose":
            from commands.economy.chest import _top_tier
            if game.tier == _top_tier() - 1:            # failed the Gold -> Diamond upgrade
                await award_badge_with_notify(client, uid, "so_close")
    except Exception:
        logger.error("chest badge award failed", exc_info=True)


# ---------------------------------------------------------------------------
# Blockade Run
# ---------------------------------------------------------------------------
async def award_blockade_badges(client, game):
    """ran_the_gauntlet: bank at the max-multiplier ceiling. steady_nerves: anchor at 10x+.
    davy_jones: get sunk. Call once the game is over."""
    try:
        import config
        from lib.bot.event_handlers import award_badge_with_notify
        uid = game.player_id
        cap = float(getattr(config, "CRASH_MAX_MULT", 25.0))
        if game.state == "cashed":
            if game.mult >= cap:
                await award_badge_with_notify(client, uid, "ran_the_gauntlet")
                await _record_pinnacle(client, uid, "blockade")
            elif game.mult >= 10.0:
                await award_badge_with_notify(client, uid, "steady_nerves")
        elif game.state == "busted":
            await award_badge_with_notify(client, uid, "davy_jones")
    except Exception:
        logger.error("blockade badge award failed", exc_info=True)


# ---------------------------------------------------------------------------
# Darts
# ---------------------------------------------------------------------------
async def award_darts_badges(client, game):
    """bullseye: land a Bullseye (50) with any dart. on_the_wire: stand on 59-60 (top band).
    Call once the game is over."""
    try:
        from lib.bot.event_handlers import award_badge_with_notify
        uid = game.player_id
        if any(lbl == "Bullseye" for lbl, _v in game.throws):
            await award_badge_with_notify(client, uid, "bullseye")
        if game.result == "win" and game.total >= 59:
            await award_badge_with_notify(client, uid, "on_the_wire")
            await _record_pinnacle(client, uid, "darts")
    except Exception:
        logger.error("darts badge award failed", exc_info=True)
