"""The push-your-luck games: Mines, Chest Upgrade, The Glass Bridge, Blockade Run, Darts
and Penalty Shootout. Each drives its own game class from commands/economy and pays with
the same reason strings and casino_results rows as the slash command.

All of them settle the same way the bot does: the finished game leaves the store before
the payout is credited (after_save), so a crash can't leave a paid game that still
resumes. Cash-out guards the bot only enforces in its buttons (at least one coin, panel or
goal first; Stand only after a dart) are enforced here."""

import math

import config
from commands.economy import blockade as BR
from commands.economy import chest as CH
from commands.economy import darts as DA
from commands.economy import glass_bridge as GB
from commands.economy import mines as MI
from commands.economy import penalty as PE
from commands.economy.casino_base import credit_from_bank
from lib.activities.casino.base import Adapter, Refuse, Round, after_save, badge
from lib.economy.casino_stats import record_result
from lib.economy.economy_manager import remove_bb
from lib.economy.game_badges import award_blockade_badges, award_chest_badges, award_darts_badges


def _stake(uid, bet, game_word, make):
    if not remove_bb(uid, bet, reason=f"{game_word} bet"):
        raise Refuse("You don't have enough UKPence.")
    try:
        return make()
    except Exception:
        credit_from_bank(uid, bet, f"{game_word} stake refund (deal failed)")
        raise


def _won(uid, key, bet, payout, reason):
    if payout > 0:
        credit_from_bank(uid, payout, reason=reason)
    record_result(uid, key, bet, bet, payout, "win")


def _lost(uid, key, bet):
    record_result(uid, key, bet, bet, 0, "lose")


# --- Mines ---------------------------------------------------------------------------------
class Mines(Adapter):
    key = "mines"
    command = "mines"
    label = "Mines"
    unit = "rounds"
    actions = frozenset({"reveal", "cashout"})
    enabled_cfg = "MINES_ENABLED"
    min_cfg = "MINES_MIN_BET"
    max_cfg = "MINES_MAX_BET"

    def deal(self, uid, name, bet, body):
        mines = int(getattr(config, "MINES_DEFAULT_MINES", 3))
        return _stake(uid, bet, "Mines", lambda: MI.MinesGame.new(uid, name, None, bet, mines))

    def act(self, game, action, body):
        if game.state != "playing":
            raise Refuse("That board is over.")
        if action == "cashout":
            if game.revealed_count < 1:
                raise Refuse("Find at least one coin before cashing out.")
            game.cash_out()
            after_save(_won, game.player_id, "mines", game.bet, game.payout, "Mines cashout")
            return
        try:
            idx = int(body.get("tile"))
        except (TypeError, ValueError):
            raise Refuse("Pick a tile.")
        result = game.reveal(idx)
        if result == "ignore":
            raise Refuse("That tile's already turned over.")
        if result == "win":
            after_save(_won, game.player_id, "mines", game.bet, game.payout, "Mines win (board cleared)")
        elif result == "mine":
            after_save(_lost, game.player_id, "mines", game.bet)

    def over(self, game):
        return game.state != "playing"

    def result(self, game):
        text = f"{game.multiplier():.2f}x after {game.revealed_count} coins" if game.outcome == "win" else ""
        return Round(game.bet, game.payout if game.outcome == "win" else 0, text)

    def view(self, game):
        over = game.state != "playing"
        return {
            "id": game.game_id, "bet": game.bet, "mines": len(game.mine_positions),
            "tiles": MI.TILES, "cols": MI.COLS, "revealed": list(game.revealed),
            "now": round(game.multiplier(), 4), "value": game.current_payout(),
            "next": round(game.multiplier(game.revealed_count + 1), 4)
            if game.revealed_count < game.safe_tiles else None,
            "nextValue": game.payout_for(game.revealed_count + 1),
            "over": over, "outcome": game.outcome, "hit": game.hit_mine,
            # The layout only leaves the server once the board is finished.
            "minesAt": list(game.mine_positions) if over else None,
            "payout": game.payout if game.outcome == "win" else 0,
            "net": (game.payout - game.bet if game.outcome == "win" else -game.bet) if over else 0,
        }

    def load(self, data):
        return MI.MinesGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        mines = int(getattr(config, "MINES_DEFAULT_MINES", 3))
        return [
            ("Aim", f"A {MI.TILES}-tile grid hides {mines} cannonballs. Find coins to build "
                    "your multiplier, then cash out before you hit one."),
            ("Coins", "Each coin raises your cash-out multiplier, but any tile could be a "
                      "cannonball."),
            ("Cash out", "Cash out any time after your first coin to take stake x multiplier. Hit "
                         "a cannonball and you lose the stake."),
            ("Clear it", "Clear every safe tile and you cash out automatically at the top multiplier."),
            ("Edge", "The house keeps about 2% whatever you do."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- Chest Upgrade -------------------------------------------------------------------------
class Chest(Adapter):
    key = "chest"
    command = "chest"
    label = "Chest Upgrade"
    unit = "rounds"
    actions = frozenset({"upgrade", "cashout"})
    enabled_cfg = "CHEST_ENABLED"
    min_cfg = "CHEST_MIN_BET"
    max_cfg = "CHEST_MAX_BET"

    def deal(self, uid, name, bet, body):
        return _stake(uid, bet, "Chest", lambda: CH.ChestGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "playing":
            raise Refuse("That chest is gone.")
        if action == "cashout":
            game.cash_out()
            after_save(_won, game.player_id, "chest", game.bet, game.payout, "Chest cashout")
        else:
            result = game.upgrade()
            if result == "ignore":
                raise Refuse("That's the top chest.")
            if result == "top":
                after_save(_won, game.player_id, "chest", game.bet, game.payout, "Chest win (max tier)")
            elif result == "break":
                after_save(_lost, game.player_id, "chest", game.bet)
        if game.state != "playing":
            badge(award_chest_badges, game)

    def over(self, game):
        return game.state != "playing"

    def result(self, game):
        name = CH._tiers()[game.tier][0]
        return Round(game.bet, game.payout if game.outcome == "win" else 0,
                     f"{name} chest, {game.multiplier():g}x" if game.outcome == "win" else "")

    def view(self, game):
        over = game.state != "playing"
        tiers = CH._tiers()
        return {
            "id": game.game_id, "bet": game.bet, "tier": game.tier,
            "tiers": [{"name": n, "mult": m, "chance": round(CH._success_prob(i - 1), 4) if i else None}
                      for i, (n, _e, m) in enumerate(tiers)],
            "value": game.current_payout(),
            "chance": round(CH._success_prob(game.tier), 4) if not game.at_top() else None,
            "nextValue": game.payout_for(game.tier + 1) if game.tier + 1 < len(tiers) else None,
            "over": over, "outcome": game.outcome,
            "payout": game.payout if game.outcome == "win" else 0,
            "net": (game.payout - game.bet if game.outcome == "win" else -game.bet) if over else 0,
        }

    def load(self, data):
        return CH.ChestGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        tiers = CH._tiers()
        ladder = " · ".join(
            f"{n} {m:g}x" + (f" ({CH._success_prob(i - 1):.0%} to reach)" if i else " (free)")
            for i, (n, _e, m) in enumerate(tiers))
        return [
            ("Aim", "Open the free Wood chest, then choose tier by tier whether to risk it to "
                    "upgrade. Succeed and you hold a richer chest; fail and it shatters and you "
                    "lose the lot."),
            ("Chests", ladder),
            ("Cash out", "Cash out any time to take stake x multiplier. Cashing the Wood chest "
                         "just returns your stake."),
            ("Diamond", "Reach Diamond and it cashes out automatically at the top multiplier."),
            ("Edge", "Every upgrade carries the same small house edge, so there's no clever "
                     "stopping point."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- The Glass Bridge ----------------------------------------------------------------------
class Glass(Adapter):
    key = "glass"
    command = "glassbridge"
    label = "Glass Bridge"
    unit = "crossings"
    actions = frozenset({"left", "right", "cashout"})
    enabled_cfg = "GLASS_ENABLED"
    min_cfg = "GLASS_MIN_BET"
    max_cfg = "GLASS_MAX_BET"

    def deal(self, uid, name, bet, body):
        return _stake(uid, bet, "Glass Bridge", lambda: GB.GlassBridgeGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "playing":
            raise Refuse("That crossing is over.")
        if action == "cashout":
            if game.step < 1:
                raise Refuse("Cross the first panel before cashing out.")
            game.cash_out()
            after_save(_won, game.player_id, "glass", game.bet, game.payout, "Glass Bridge cashout")
            return
        result = game.take_step(GB.LEFT if action == "left" else GB.RIGHT)
        if result == "across":
            after_save(_won, game.player_id, "glass", game.bet, game.payout, "Glass Bridge win (crossed)")
        elif result == "fell":
            after_save(_lost, game.player_id, "glass", game.bet)

    def over(self, game):
        return game.state != "playing"

    def result(self, game):
        return Round(game.bet, game.payout if game.outcome == "win" else 0,
                     f"{game.step} panels, {game.multiplier():.2f}x" if game.outcome == "win" else "")

    def board(self, game) -> str:
        """The bot's own Glass Bridge scene for this crossing, as an SVG."""
        from lib.economy import glass_scene
        total = GB._steps()
        lost = game.state == "over" and game.outcome == "lose"
        known = game.step + 1 if lost else game.step
        return glass_scene.bridge_svg(
            steps=total, step=game.step, safe_sides=game.bridge[:known],
            multipliers=[GB.multiplier_for(i + 1) for i in range(total)],
            playing=game.state == "playing", fell_on=game.fell_on if lost else None)

    def view(self, game):
        over = game.state != "playing"
        lost = over and game.outcome == "lose"
        known = game.step + 1 if lost else game.step
        total = GB._steps()
        return {
            "id": game.game_id, "bet": game.bet, "step": game.step, "steps": total,
            "safe": game.bridge[:known], "fellOn": game.fell_on if lost else None,
            "multipliers": [round(GB.multiplier_for(i + 1), 2) for i in range(total)],
            "value": game.current_payout(),
            "nextValue": game.payout_for(game.step + 1) if game.step < total else None,
            "scene": self.board(game),
            "over": over, "outcome": game.outcome,
            "payout": game.payout if game.outcome == "win" else 0,
            "net": (game.payout - game.bet if game.outcome == "win" else -game.bet) if over else 0,
        }

    def load(self, data):
        return GB.GlassBridgeGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        total = GB._steps()
        cap = int(getattr(config, "GLASS_MAX_WIN", 0) or 0)
        ladder = " · ".join(f"{n}: {GB.multiplier_for(n):.2f}x" for n in range(1, total + 1))
        out = [
            ("Aim", f"{total} pairs of panels span the canyon. One of each pair holds your "
                    "weight; the other shatters. Pick a side, step, and hope."),
            ("Panels", ladder),
            ("Cash out", "Cash out any time after the first panel to take stake x multiplier. "
                         f"Cross all {total} and it banks automatically."),
            ("Falling", "Pick the fragile panel and the stake is gone."),
            ("Edge", "Every panel is a straight 50/50 and the house takes the same cut off each "
                     "one, so there's no clever place to stop."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]
        if cap > 0:
            out.append(("Cap", f"Wins are capped at {cap:,} UKPence."))
        return out


# --- Blockade Run --------------------------------------------------------------------------
class Blockade(Adapter):
    key = "blockade"
    command = "blockade"
    label = "Blockade Run"
    unit = "runs"
    actions = frozenset({"sail", "anchor"})
    enabled_cfg = "CRASH_ENABLED"
    min_cfg = "CRASH_MIN_BET"
    max_cfg = "CRASH_MAX_BET"

    def deal(self, uid, name, bet, body):
        return _stake(uid, bet, "Blockade Run", lambda: BR.CrashGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "running":
            raise Refuse("That run is over.")
        if action == "anchor":
            game.cash_out()
            after_save(BR._settle_cash, game, "Blockade Run cashout")
        else:
            result = game.sail_on()
            if result == "cap":
                after_save(BR._settle_cash, game, "Blockade Run cashout (ceiling)")
            elif result == "bust":
                after_save(BR._settle_bust, game)
        if game.state != "running":
            badge(award_blockade_badges, game)

    def over(self, game):
        return game.state != "running"

    def result(self, game):
        return Round(game.bet, game.payout if game.state == "cashed" else 0,
                     f"anchored at {game.mult:.2f}x" if game.state == "cashed" else "")

    def view(self, game):
        over = game.state != "running"
        # The multiplier one more push would show, worked out the way sail_on() does (floored to
        # the penny), so the button's promise and the next screen agree.
        nxt = min(math.floor(BR._growth() ** (game.ticks + 1) * 100) / 100.0, BR._cap())
        return {
            "id": game.game_id, "bet": game.bet, "ticks": game.ticks, "mult": game.mult,
            "value": game.payout_now(), "next": nxt,
            "cap": BR._cap(), "state": game.state,
            "caught": game.crash_display() if game.state == "busted" else None,
            "over": over, "outcome": None if not over else ("win" if game.state == "cashed" else "lose"),
            "payout": game.payout if game.state == "cashed" else 0,
            "net": (game.payout - game.bet if game.state == "cashed" else -game.bet) if over else 0,
        }

    def load(self, data):
        return BR.CrashGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "Run the enemy blockade. Each Sail On pushes deeper and lifts the "
                    "multiplier, but a hidden point along the route will sink you and the "
                    "whole stake. Drop Anchor any time to bank stake x multiplier."),
            ("Pace", "There's no timer. It's your nerve against the odds."),
            ("Ceiling", f"The run banks automatically if you reach {BR._cap():g}x."),
            ("Fair", "The catch point is rolled at the start, so the house keeps the same small "
                     "edge however far you push."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- Darts ---------------------------------------------------------------------------------
class Darts(Adapter):
    key = "darts"
    command = "darts"
    label = "Darts"
    unit = "rounds"
    actions = frozenset({"throw", "stand"})
    enabled_cfg = "DARTS_ENABLED"
    min_cfg = "DARTS_MIN_BET"
    max_cfg = "DARTS_MAX_BET"

    def deal(self, uid, name, bet, body):
        return _stake(uid, bet, "Darts", lambda: DA.DartsGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "playing":
            raise Refuse("That round is over.")
        if action == "stand":
            if game.darts < 1:
                raise Refuse("Throw your first dart before you stand.")
            game.stand()
        else:
            game.throw_dart()
        if game.state != "playing":
            after_save(DA._settle, game)
            badge(award_darts_badges, game)

    def over(self, game):
        return game.state != "playing"

    def result(self, game):
        return Round(game.bet, game.payout,
                     f"{game.total} with {game.darts} darts" if game.payout > 0 else "")

    def view(self, game):
        over = game.state != "playing"
        return {
            "id": game.game_id, "bet": game.bet,
            "throws": [{"label": l, "value": v} for l, v in game.throws],
            "total": game.total, "darts": int(getattr(config, "DARTS_DARTS", 3)),
            "bust": int(getattr(config, "DARTS_BUST", 60)),
            "bands": [{"low": a, "high": b, "mult": m} for a, b, m in config.DARTS_PAYOUTS],
            "value": game.payout_now(), "result": game.result,
            "over": over, "payout": game.payout if over else 0,
            "net": game.payout - game.bet if over else 0,
        }

    def load(self, data):
        return DA.DartsGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        bust = int(getattr(config, "DARTS_BUST", 60))
        darts = int(getattr(config, "DARTS_DARTS", 3))
        bands = " · ".join(f"{a}-{b}: {m:g}x" for a, b, m in config.DARTS_PAYOUTS)
        return [
            ("Aim", f"Blackjack with darts: get as close to {bust} as you can without going "
                    "over. Throw to build your score, then Stand to bank it."),
            ("Darts", f"Throw up to {darts} darts. After the last one you stand automatically."),
            ("Payouts", f"{bands}. Finish under {config.DARTS_PAYOUTS[0][0]} and you lose; go "
                        f"over {bust} and you bust."),
            ("The board", "Each dart lands on a real, area-weighted board. Singles are common; "
                          "trebles and doubles are rare but worth far more."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- Penalty Shootout ----------------------------------------------------------------------
class Penalty(Adapter):
    key = "penalty"
    command = "penalty"
    label = "Penalties"
    unit = "shootouts"
    actions = frozenset({"shoot", "cashout"})
    enabled_cfg = "PENALTY_ENABLED"
    min_cfg = "PENALTY_MIN_BET"
    max_cfg = "PENALTY_MAX_BET"

    def deal(self, uid, name, bet, body):
        return _stake(uid, bet, "Penalty", lambda: PE.PenaltyGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "aiming":
            raise Refuse("That shootout is over.")
        if action == "cashout":
            if game.goals < 1:
                raise Refuse("Score at least once before cashing out.")
            game.cash_out()
            after_save(_won, game.player_id, "penalty", game.bet, game.payout, "Penalty cashout")
            return
        spot = str(body.get("spot") or "")
        if spot not in PE.SPOTS:
            raise Refuse("Pick a spot to shoot at.")
        result = game.kick(spot)
        if result == "perfect":
            after_save(_won, game.player_id, "penalty", game.bet, game.payout, "Penalty five from five")
        elif result == "save":
            after_save(_lost, game.player_id, "penalty", game.bet)

    def over(self, game):
        return game.state != "aiming"

    def result(self, game):
        return Round(game.bet, game.payout if game.outcome == "win" else 0,
                     f"{game.goals} goals, {game.multiplier():.2f}x" if game.outcome == "win" else "")

    def view(self, game):
        over = game.state != "aiming"
        return {
            "id": game.game_id, "bet": game.bet, "goals": game.goals, "shots": PE.MAX_GOALS,
            "ladder": [round(game.multiplier(k), 2) for k in range(1, PE.MAX_GOALS + 1)],
            "now": round(game.multiplier(), 4), "value": game.current_payout(),
            "nextValue": game.payout_for(game.goals + 1) if game.goals < PE.MAX_GOALS else None,
            "lastKick": game.last_kick, "lastDove": game.last_dove, "lastResult": game.last_result,
            "over": over, "outcome": game.outcome,
            "payout": game.payout if game.outcome == "win" else 0,
            "net": (game.payout - game.bet if game.outcome == "win" else -game.bet) if over else 0,
        }

    def load(self, data):
        return PE.PenaltyGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "Step up and take up to five penalties against the keeper."),
            ("Shooting", "Pick a spot to shoot. Beat the keeper and you score, and every goal "
                         "lifts your multiplier. Guess wrong and he saves it, and your stake is gone."),
            ("Cash out", "Cash out after any goal to take your winnings, or push your luck. "
                         "Five from five is the top prize."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]
