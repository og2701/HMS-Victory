"""Blackjack in the activity, on commands/economy/blackjack.py's own game and money paths."""

from commands.economy import blackjack as B
from lib.activities.casino.base import Adapter, Refuse, Round, after_save
from lib.economy.economy_manager import remove_bb

OUTCOMES = {"blackjack": "Blackjack 3:2", "win": "Win", "push": "Push", "lose": "Loss"}


class Blackjack(Adapter):
    key = "blackjack"
    command = "blackjack"
    label = "Blackjack"
    unit = "hands"
    actions = frozenset({"hit", "stand", "double"})
    enabled_cfg = "BLACKJACK_ENABLED"
    min_cfg = "BLACKJACK_MIN_BET"
    max_cfg = "BLACKJACK_MAX_BET"
    default_max = 250_000
    max_multiplier = 2.5

    def deal(self, uid, name, bet, body):
        if not remove_bb(uid, bet, reason="Blackjack bet"):
            raise Refuse("You don't have enough UKPence.")
        try:
            game = B.BlackjackGame.new(uid, name, None, bet)
        except Exception:
            B._credit(uid, bet, "Blackjack stake refund (deal failed)")
            raise
        if game.state == "over":          # a natural on either side settles on the deal
            B._decide(game)
            after_save(B._payout, game)
        return game

    def act(self, game, action, body):
        if game.state != "player":
            raise Refuse("That hand is over.")
        if action == "hit":
            game.hit_player()
        elif action == "stand":
            game.dealer_play()
        elif action == "double":
            if not game.can_double():
                raise Refuse("You can only double down on your opening two cards.")
            if not remove_bb(game.player_id, game.bet, reason="Blackjack double down"):
                raise Refuse(f"You need {game.bet:,} more UKPence to double down.")
            game.total_staked += game.bet
            game.doubled = True
            game.player_cards.append(game.deck.pop())
            if game.player_busted():
                game.hole_revealed = True
                game.state = "over"
            else:
                game.dealer_play()
        if game.state == "over" and not game.settled:
            B._decide(game)
            after_save(B._payout, game)

    def over(self, game):
        return game.state == "over"

    def result(self, game):
        B._decide(game)
        text = OUTCOMES.get(game.outcome, "")
        if game.outcome == "win" and game.dealer_total() > 21:
            text = "dealer bust"
        if game.doubled and game.outcome in ("win", "blackjack"):
            text += ", doubled"
        return Round(game.total_staked, game.payout, text)

    def view(self, game):
        up = game.dealer_cards[0]
        dealer = list(game.dealer_cards) if game.hole_revealed else [up, None]
        total, soft = B.hand_value(game.player_cards)
        over = game.state == "over"
        if over:
            B._decide(game)
        return {
            "id": game.game_id,
            "player": list(game.player_cards),
            "dealer": dealer,
            "playerTotal": total,
            "playerSoft": soft and total < 21,
            "dealerTotal": game.dealer_total() if game.hole_revealed else B.hand_value([up])[0],
            "over": over,
            "bet": game.bet,
            "staked": game.total_staked,
            "doubled": game.doubled,
            "canDouble": game.can_double(),
            "outcome": game.outcome if over else None,
            "payout": game.payout if over else 0,
            "net": game.net if over else 0,
        }

    def load(self, data):
        return B.BlackjackGame.from_dict(data)

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "Beat the dealer by getting closer to 21 than they do, without going over."),
            ("Cards", "2 to 10 count as shown, J Q K are 10, and an Ace is 1 or 11, whichever "
                      "is better for your hand."),
            ("Blackjack", "An Ace and a 10-value card as your first two cards. Pays 3:2."),
            ("Hit, Stand", "Hit draws another card, Stand locks in your total. Reach 21 and you "
                           "stand automatically."),
            ("Double", "On your opening two cards only. Doubles your stake for exactly one more "
                       "card, then stands."),
            ("Dealer", "Reveals the hole card and draws to 17, standing on all 17s (soft 17 "
                       "included)."),
            ("Ties", "Going over 21 loses at once. Matching totals push and your stake comes back."),
            ("Bets", f"{low:,} to {high:,} UKPence. Stakes go to the house bank and wins are "
                     f"paid from it."),
        ]
