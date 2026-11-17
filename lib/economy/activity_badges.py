"""Badges for the activity's games: UKP Kart, Paperboy, Countdown, Bank Job, HMS Wordle and the crossword, the penalty
shootout, and the daily games together.

Like game_badges, every check is best-effort: a failure here must never touch a race, a run or a payout, so each
one is wrapped and only logs. Awards go through award_badge_notify (idempotent, so a badge already held is a no-op,
and safe from synchronous code: it schedules the DM on the running loop). The two secret ones are named only in the
encrypted secret config, by neutral key, as the others are.
"""

import logging

logger = logging.getLogger(__name__)

# the daily games, for All-Rounder: every one of them played in the same day
DAILY = ("wordle", "crossword", "climb", "spitfire", "paperboy")


def _award(uid, badge_id) -> None:
    from lib.bot.event_handlers import award_badge_notify
    award_badge_notify(int(uid), badge_id)


# ---- UKP Kart ----------------------------------------------------------------------------------------
def kart_race(room: dict) -> None:
    """A race against people, settled: who got home, who won, who set the fastest lap, and whether a winner
    has now won on every track. Call after the race is kept (kart._record), so the win counts."""
    try:
        if room.get("how") != "race":
            return
        from lib.activities import kart
        fin, ranked = room.get("finish", {}), kart.order(room)
        people = {g["id"] for g in room.get("grid", []) if not g["cpu"]}
        for kid in people & set(fin):
            _award(kid, "kart_learner")
        first = ranked[0] if ranked else None
        if first in people and first in fin:
            if len(room.get("players", [])) >= 2:
                _award(first, "kart_chequered")
            _grand_slam(first)
        laps = {k: t for k, t in room.get("laps", {}).items() if k in ranked}
        if laps:
            quickest = min(laps, key=laps.get)
            if quickest in people:
                _award(quickest, "kart_purple")
    except Exception:
        logger.error("kart race badges failed for %s", room.get("id"), exc_info=True)


def _grand_slam(uid) -> None:
    from database import DatabaseManager
    from lib.activities import kart
    rows = DatabaseManager.fetch_all(
        "SELECT DISTINCT r.track FROM kart_results k JOIN kart_races r ON r.id = k.race_id "
        "WHERE k.user_id = ? AND k.place = 1 AND r.mode = 'race' AND r.outcome = 'race'", (str(uid),)) or []
    if set(kart.TRACKS) <= {t for (t,) in rows}:
        _award(uid, "kart_grand_slam")


def kart_practice(uid, finished: bool) -> None:
    """A practice race the page ran: getting home counts for Learner Plates."""
    try:
        if finished:
            _award(uid, "kart_learner")
    except Exception:
        logger.error("kart practice badges failed for %s", uid, exc_info=True)


def kart_ouch(room: dict, uid, what: str) -> None:
    """Something on the road got someone's kart in a race (the page says what). Kept count of per race."""
    try:
        from lib.economy import secret_config as sc
        counts = room.setdefault("ouch", {}).setdefault(str(uid), {})
        counts[what] = int(counts.get(what, 0)) + 1
        need, badge = sc.param("a10"), sc.bid("a10")
        if badge and need and what == "sheep" and counts[what] >= int(need):
            _award(uid, badge)
    except Exception:
        logger.error("kart ouch badge failed for %s", uid, exc_info=True)


# ---- the daily games ---------------------------------------------------------------------------------
def daily_run(uid, game: str, iso: str, played: dict | None) -> None:
    """A counted run of a daily game (Climb, Spitfire, Paperboy): Paperboy's dog, and All-Rounder."""
    try:
        if game == "paperboy" and played and played.get("crashedOn") == "dog":
            _award(uid, "paperboy_dogs")
    except Exception:
        logger.error("paperboy badge failed for %s", uid, exc_info=True)
    all_rounder(uid, iso)


def all_rounder(uid, iso: str) -> None:
    """Every daily game played on the day `iso`: a guess at the Wordle, an answer or a hint in the crossword, and
    a counted run of each of the others."""
    try:
        from lib.activities.daily_score import GAMES
        from lib.features import crossword as X
        from lib.features import wordle as W
        if not W._player(iso, int(uid)).get("guesses"):
            return
        p = X._player(iso, int(uid))
        if not (p.get("solved") or p.get("revealed") or int(p.get("wrong", 0))):
            return
        if all(GAMES[g].day(int(uid), iso)["runs"] > 0 for g in DAILY if g in GAMES):
            _award(uid, "all_rounder")
    except Exception:
        logger.error("all-rounder check failed for %s", uid, exc_info=True)


def wordle_solved(uid, guesses: int, iso: str) -> None:
    try:
        if guesses == 6:
            _award(uid, "wordle_phew")
    except Exception:
        logger.error("wordle badge failed for %s", uid, exc_info=True)
    all_rounder(uid, iso)


def crossword_finished(uid, p: dict, iso: str) -> None:
    try:
        if not p.get("revealed"):
            _award(uid, "crossword_no_peeking")
    except Exception:
        logger.error("crossword badge failed for %s", uid, exc_info=True)
    all_rounder(uid, iso)


# ---- Countdown ---------------------------------------------------------------------------------------
def countdown_round(results: dict) -> None:
    """A letters round, checked: anyone with a good nine-letter word."""
    try:
        for uid, r in results.items():
            if r.get("valid") and len(r.get("word") or "") == 9:
                _award(uid, "countdown_nine")
    except Exception:
        logger.error("countdown badges failed", exc_info=True)


# ---- Bank Job -----------------------------------------------------------------------------------------
def heist_over(room: dict) -> None:
    """A staked job, settled (and kept, so Banged Up's count includes it): who got out with something,
    a clean vault job, a crew that did its job properly, anyone greedy enough, and anyone nicked once too often.
    Free jobs earn nothing here, so a badge's reward can't be farmed without a buy-in at stake."""
    try:
        if not room.get("stake") or not room.get("results"):
            return
        results = room["results"]
        for uid, r in results.items():
            if r["how"] != "nicked" and r["payout"] > 0:
                _award(uid, "heist_first")
            if r["how"] != "nicked" and r["take"] >= 8:
                _award(uid, "heist_greedy")
        if room.get("target") == "vault" and room.get("how") == "clean":
            for uid in results:
                _award(uid, "heist_clean")
        if len(results) >= 4 and (room.get("score") or 0) >= 0.9:
            for uid in results:
                _award(uid, "heist_mastermind")
        nicked = [uid for uid, r in results.items() if r["how"] == "nicked"]
        if nicked:
            from database import DatabaseManager
            for uid in nicked:
                row = DatabaseManager.fetch_one(
                    "SELECT COUNT(*) FROM game_record_players p JOIN game_records r ON r.game = p.game AND r.id = p.id "
                    "WHERE p.game = 'heist' AND p.user_id = ? AND p.result = 'nicked' AND r.stake > 0", (str(uid),))
                if row and int(row[0]) >= 5:
                    _award(uid, "heist_banged_up")
    except Exception:
        logger.error("bank job badges failed for %s", room.get("id"), exc_info=True)


# ---- the penalty shootout ----------------------------------------------------------------------------
def penalty_saved(uid, goals: int) -> None:
    """A kick saved, ending the shootout with `goals` scored."""
    try:
        from lib.economy import secret_config as sc
        badge, at = sc.bid("a11"), sc.param("a11")
        if badge and at is not None and goals == int(at):
            _award(uid, badge)
    except Exception:
        logger.error("penalty badge failed for %s", uid, exc_info=True)
