"""The felt card games besides Blackjack: Higher or Lower, Video Poker, Red Dog and Three
Card Poker. Each adapter drives the bot's own game class and settles through that module's
own payout function, so stakes, reason strings and casino_results match the slash command.

Checks the bot only makes in its button handlers (a direction with no odds, cashing out
before the first step, affording a raise) are made here too, before anything moves."""

from commands.economy import casino_base as cb
from commands.economy import higher_lower as HL
from commands.economy import red_dog as RD
from commands.economy import three_card_poker as TCP
from commands.economy import video_poker as VP
from lib.activities.casino.base import Adapter, Refuse, Round, after_save, badge
from lib.economy.economy_manager import get_bb, remove_bb
from lib.economy.game_badges import award_higherlower_badges


def _refund_on_failure(uid, bet, reason, make):
    try:
        return make()
    except Exception:
        cb.credit_from_bank(uid, bet, reason)
        raise


# --- Higher or Lower -----------------------------------------------------------------------
class HigherLower(Adapter):
    key = "higherlower"
    command = "higher-lower"
    label = "Higher or Lower"
    unit = "runs"
    actions = frozenset({"higher", "lower", "cashout"})
    enabled_cfg = "HIGHERLOWER_ENABLED"
    min_cfg = "HIGHERLOWER_MIN_BET"
    max_cfg = "HIGHERLOWER_MAX_BET"

    def deal(self, uid, name, bet, body):
        if not remove_bb(uid, bet, reason="Higher-Lower bet"):
            raise Refuse("You don't have enough UKPence.")
        return _refund_on_failure(uid, bet, "Higher-Lower stake refund (deal failed)",
                                  lambda: HL.HigherLowerGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "player":
            raise Refuse("That run is over.")
        if action == "cashout":
            if not game.can_cash_out():
                raise Refuse("Win at least one guess before you cash out.")
            game.cash_out()
        else:
            mult = game.mult_higher if action == "higher" else game.mult_lower
            if mult is None:
                raise Refuse(f"{action.title()} isn't on offer for this card.")
            game.guess(action)
        if game.state == "over":
            after_save(HL._payout, game)
        badge(award_higherlower_badges, game)

    def over(self, game):
        return game.state == "over"

    def result(self, game):
        text = f"{game.cumulative:.2f}x after {game.steps}" if game.outcome == "win" else "wrong call"
        return Round(game.bet, game.payout, text)

    def view(self, game):
        run = list(game.history) + [game.current]
        return {
            "id": game.game_id, "bet": game.bet, "current": game.current, "run": run[-8:],
            "steps": game.steps, "cumulative": round(game.cumulative, 4),
            "value": game.current_value() if game.state == "player" else game.payout,
            "higher": game.mult_higher, "lower": game.mult_lower,
            "canCashOut": game.can_cash_out(), "left": len(game.deck),
            "over": game.state == "over", "outcome": game.outcome,
            "payout": game.payout, "net": game.net if game.state == "over" else 0,
        }

    def load(self, data):
        return HL.HigherLowerGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "A card is shown. Guess whether the next card is higher or lower. Aces are high."),
            ("Odds", "Each button shows the multiplier it pays if you're right: the longer the "
                     "odds, the bigger the multiplier."),
            ("Climbing", "A correct guess multiplies your banked value and deals the next card. "
                         "Keep going, or cash out to collect."),
            ("Ties", "A wrong guess loses the whole stake. A tie is a push and the run carries "
                     "on from the new card."),
            ("Edge", "Each multiplier is shaved slightly below the true odds, so the further you "
                     "climb, the more the house edges in. Cash out to lock it."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- Video Poker ---------------------------------------------------------------------------
class VideoPoker(Adapter):
    key = "videopoker"
    command = "video-poker"
    label = "Video Poker"
    unit = "hands"
    actions = frozenset({"draw"})
    enabled_cfg = "VIDEOPOKER_ENABLED"
    min_cfg = "VIDEOPOKER_MIN_BET"
    max_cfg = "VIDEOPOKER_MAX_BET"
    max_multiplier = 800.0

    def deal(self, uid, name, bet, body):
        if not remove_bb(uid, bet, reason="Video Poker bet"):
            raise Refuse("You don't have enough UKPence.")
        return _refund_on_failure(uid, bet, "Video Poker stake refund (deal failed)",
                                  lambda: VP.VideoPokerGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "draw_decision":
            raise Refuse("That hand is over.")
        held = body.get("held") or []
        if not isinstance(held, list) or any(not isinstance(i, int) or not 0 <= i < 5 for i in held):
            raise Refuse("Pick the cards to hold again.")
        game.held = [i in held for i in range(5)]
        game.draw()
        VP._decide(game)
        after_save(VP._pay, game)

    def over(self, game):
        return game.state == "over"

    def result(self, game):
        return Round(game.bet, game.payout, "" if game.outcome == "nothing" else game.outcome)

    def view(self, game):
        over = game.state == "over"
        cat = VP._paying_category(game.cards)
        return {
            "id": game.game_id, "bet": game.bet, "cards": list(game.cards),
            "held": list(game.held) if over else [False] * 5,
            "hand": cb.five_card_name(game.cards),
            "pays": VP._pays(cat) if cat is not None else 0,
            "row": cat,
            "over": over, "outcome": game.outcome if over else None,
            "payout": game.payout if over else 0, "net": game.net if over else 0,
        }

    def load(self, data):
        return VP.VideoPokerGame.from_dict(data)

    def extras(self, uid):
        return {"paytable": [{"row": cat, "label": label, "pays": VP._pays(cat)}
                             for cat, label in VP._PAY_ROWS]}

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "You're dealt five cards. Tap the cards you want to hold, then draw to "
                    "replace the rest. You're paid on the poker rank of your final hand."),
            ("Paytable", " · ".join(f"{label} {VP._pays(cat)}x" for cat, label in VP._PAY_ROWS)),
            ("Losing", "Anything below a pair of Jacks loses. A pair of Jacks or better gives "
                       "your bet back."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- Red Dog -------------------------------------------------------------------------------
_RD_TEXT = {"push": "push", "trips": "three of a kind 11:1", "win": "", "lose": ""}


class RedDog(Adapter):
    key = "reddog"
    command = "red-dog"
    label = "Red Dog"
    unit = "hands"
    actions = frozenset({"raise", "call"})
    enabled_cfg = "REDDOG_ENABLED"
    min_cfg = "REDDOG_MIN_BET"
    max_cfg = "REDDOG_MAX_BET"

    def deal(self, uid, name, bet, body):
        if not remove_bb(uid, bet, reason="Red Dog bet"):
            raise Refuse("You don't have enough UKPence.")
        game = _refund_on_failure(uid, bet, "Red Dog stake refund (deal failed)",
                                  lambda: RD.RedDogGame.new(uid, name, None, bet))
        if game.state == "over":            # consecutive or a pair: decided on the deal
            RD._decide_initial(game)
            after_save(RD._pay, game)
        return game

    def act(self, game, action, body):
        if game.state != "raise_decision":
            raise Refuse("That hand is over.")
        if action == "raise":
            if get_bb(game.player_id) < game.bet or not remove_bb(game.player_id, game.bet,
                                                                   reason="Red Dog bet"):
                raise Refuse(f"You need {game.bet:,} more UKPence to raise.")
            game.raise_bet()
        else:
            game.call_bet()
        RD._decide_spread(game)
        after_save(RD._pay, game)

    def over(self, game):
        return game.state == "over"

    def result(self, game):
        text = _RD_TEXT.get(game.outcome, "")
        if game.outcome == "win":
            text = f"spread {game.spread} at {game.odds}:1"
        return Round(game.total_staked, game.payout, text)

    def view(self, game):
        over = game.state == "over"
        return {
            "id": game.game_id, "bet": game.bet, "staked": game.total_staked,
            "first": game.first_card, "second": game.second_card, "third": game.third_card,
            "spread": None if (game.is_pair or game.is_consecutive) else game.spread,
            "odds": None if (game.is_pair or game.is_consecutive) else game.odds,
            "pair": game.is_pair, "consecutive": game.is_consecutive,
            "over": over, "outcome": game.outcome if over else None,
            "payout": game.payout if over else 0, "net": game.net if over else 0,
        }

    def load(self, data):
        return RD.RedDogGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "Two cards are dealt face up. Aces are high. Bet on the third card landing "
                    "strictly between them."),
            ("Pushes", "Consecutive ranks (7 and 8, K and A) push and your bet comes back."),
            ("Pairs", "A pair deals a third card at once: match it for three of a kind at 11:1, "
                      "otherwise it's a push."),
            ("Raise, Call", "Otherwise a spread opens up. Raise to double your bet, or Call to "
                            "keep it, then the third card is dealt."),
            ("Spreads", "1 pays 5:1, 2 pays 4:1, 3 pays 2:1, 4 or more pays 1:1. The wider the "
                        "gap, the likelier the hit, so the smaller the payout."),
            ("Tip", "Raise on a wide spread, where you're very likely to win. Only call on a "
                    "narrow one."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]


# --- Three Card Poker ----------------------------------------------------------------------
_TCP_TEXT = {"dealer_no_qualify": "dealer didn't qualify", "win": "beat the dealer",
             "push": "push", "lose": "", "fold": "fold"}


class ThreeCard(Adapter):
    key = "tcp"
    command = "three-card-poker"
    label = "3-Card Poker"
    unit = "hands"
    actions = frozenset({"play", "fold"})
    enabled_cfg = "TCP_ENABLED"
    min_cfg = "TCP_MIN_BET"
    max_cfg = "TCP_MAX_BET"

    def deal(self, uid, name, bet, body):
        if not remove_bb(uid, bet, reason="Three Card Poker bet"):
            raise Refuse("You don't have enough UKPence.")
        return _refund_on_failure(uid, bet, "Three Card Poker stake refund (deal failed)",
                                  lambda: TCP.TcpGame.new(uid, name, None, bet))

    def act(self, game, action, body):
        if game.state != "play_decision":
            raise Refuse("That hand is over.")
        if action == "play":
            if get_bb(game.player_id) < game.bet or not remove_bb(game.player_id, game.bet,
                                                                   reason="Three Card Poker bet"):
                raise Refuse(f"You need {game.bet:,} more UKPence to play.")
            game.play()
            TCP._decide_play(game)
        else:
            game.fold()
            TCP._decide_fold(game)
        after_save(TCP._pay, game)

    def over(self, game):
        return game.state == "over"

    def result(self, game):
        text = _TCP_TEXT.get(game.outcome, "")
        bonus = TCP._ante_bonus(game) if game.outcome != "fold" else 0
        if bonus:
            text = (text + ", " if text else "") + cb.three_card_name(game.player_cards)
        return Round(game.total_staked, game.payout, text)

    def view(self, game):
        over = game.state == "over"
        return {
            "id": game.game_id, "bet": game.bet, "staked": game.total_staked,
            "player": list(game.player_cards),
            "dealer": list(game.dealer_cards) if over else [None, None, None],
            "playerHand": cb.three_card_name(game.player_cards),
            "dealerHand": cb.three_card_name(game.dealer_cards) if over else None,
            "bonus": TCP._ante_bonus(game) // game.bet if game.bet else 0,
            "over": over, "outcome": game.outcome if over else None,
            "payout": game.payout if over else 0, "net": game.net if over else 0,
        }

    def load(self, data):
        return TCP.TcpGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "You and the dealer get three cards each, yours face up. Aces are high and, "
                    "with three cards, a straight beats a flush."),
            ("Fold", "Fold to give up your bet."),
            ("Play", "Play to match your bet, so two equal bets are riding. Then the dealer reveals."),
            ("Dealer", "The dealer only qualifies with Queen-high or better. If they don't, your "
                       "first bet pays 1:1 and the Play bet pushes."),
            ("Showdown", "Beat a qualifying dealer and both bets pay 1:1. Lose and both go. A tie "
                         "pushes both."),
            ("Bonus", "Paid on your first bet whatever the dealer holds: Straight +1x, Three of "
                      "a Kind +4x, Straight Flush +5x."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]
