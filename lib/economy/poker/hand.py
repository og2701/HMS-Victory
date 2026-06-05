"""One hand of no-limit Texas Hold'em as a driveable state machine.

The Discord layer reads `stage`, `board`, `current_player()`, `legal_actions()` and calls
`act(kind, amount)` for whoever is to act. When `finished` is True, `final_stack` holds each
seat's chips after the hand (remaining stack + winnings) and `payouts` the gross won per seat.

Known MVP simplifications: no card burns (cosmetic only); an all-in raise for less than a
full raise still reopens the betting (rare, harmless to chip integrity).
"""

import random

from lib.economy.poker.engine import full_deck, settle


def _shuffled(rng=None):
    deck = full_deck()
    (rng or random).shuffle(deck)
    return deck


class Hand:
    def __init__(self, seat_ids, stacks, button, sb, bb, *, deck=None, rng=None):
        self.seats = list(seat_ids)
        self.n = len(self.seats)
        if self.n < 2:
            raise ValueError("need at least 2 seats")
        self.stack = {s: int(stacks[s]) for s in self.seats}
        self.button = button % self.n
        self.sb = sb
        self.bb = bb
        self.folded = set()
        self.allin = set()
        self.committed = {s: 0 for s in self.seats}   # this betting round
        self.total = {s: 0 for s in self.seats}        # whole hand
        self.hole = {s: [] for s in self.seats}
        self.board = []
        self.stage = "preflop"
        self.current_bet = 0
        self.min_raise = bb
        self.acted = set()                              # acted-and-square since last aggression
        self.to_act = None
        self.last_aggressor = None
        self.finished = False
        self.payouts = None
        self.final_stack = None
        self._deck = list(deck) if deck is not None else _shuffled(rng)
        self._deal_and_blinds()

    # --- setup -----------------------------------------------------------------
    def _post(self, idx, amount):
        sid = self.seats[idx]
        amt = min(amount, self.stack[sid])
        self.stack[sid] -= amt
        self.committed[sid] += amt
        self.total[sid] += amt
        if self.stack[sid] == 0:
            self.allin.add(sid)
        return amt

    def _deal_and_blinds(self):
        if self.n == 2:
            sb_idx, bb_idx = self.button, (self.button + 1) % 2
        else:
            sb_idx, bb_idx = (self.button + 1) % self.n, (self.button + 2) % self.n
        self._post(sb_idx, self.sb)
        self._post(bb_idx, self.bb)
        self.current_bet = self.bb
        self.min_raise = self.bb
        self.last_aggressor = self.seats[bb_idx]
        for _ in range(2):
            for i in range(self.n):
                self.hole[self.seats[i]].append(self._deck.pop())
        self.to_act = self._advance(bb_idx)
        if self.to_act is None:        # everyone all-in from blinds (tiny stacks)
            self._run_out()

    # --- turn order ------------------------------------------------------------
    def _needs_action(self, sid):
        return (sid not in self.folded and sid not in self.allin
                and (self.committed[sid] < self.current_bet or sid not in self.acted))

    def _advance(self, from_idx):
        for k in range(1, self.n + 1):
            i = (from_idx + k) % self.n
            if self._needs_action(self.seats[i]):
                return i
        return None

    # --- public read -----------------------------------------------------------
    def current_player(self):
        return None if self.to_act is None or self.finished else self.seats[self.to_act]

    def pot(self):
        return sum(self.total.values())

    def legal_actions(self):
        """Action options for the current actor, or {} if none."""
        sid = self.current_player()
        if sid is None:
            return {}
        to_call = self.current_bet - self.committed[sid]
        stack = self.stack[sid]
        max_to = self.committed[sid] + stack            # all-in raise-to level
        min_to = self.current_bet + self.min_raise
        can_raise = stack > to_call                     # has chips beyond a call
        return {
            "fold": True,
            "check": to_call == 0,
            "call": to_call if (to_call > 0 and stack > 0) else 0,
            "call_amount": min(to_call, stack),
            "can_raise": can_raise,
            "min_raise_to": min(min_to, max_to) if can_raise else 0,
            "max_raise_to": max_to if can_raise else 0,
        }

    # --- actions ---------------------------------------------------------------
    def act(self, kind, amount=None):
        sid = self.current_player()
        if sid is None:
            raise ValueError("no one to act")
        idx = self.to_act
        to_call = self.current_bet - self.committed[sid]

        if kind == "fold":
            self.folded.add(sid)
            self.acted.add(sid)
        elif kind == "check":
            if to_call != 0:
                raise ValueError("cannot check facing a bet")
            self.acted.add(sid)
        elif kind == "call":
            self._put(sid, min(to_call, self.stack[sid]))
            self.acted.add(sid)
        elif kind in ("raise", "allin"):
            if kind == "allin":
                target = self.committed[sid] + self.stack[sid]
            else:
                target = int(amount)
            target = min(target, self.committed[sid] + self.stack[sid])
            if target <= self.current_bet and not (kind == "allin"):
                raise ValueError("raise must exceed the current bet")
            raise_size = target - self.current_bet
            self._put(sid, target - self.committed[sid])
            if raise_size >= self.min_raise:
                self.min_raise = raise_size
            if target > self.current_bet:
                self.current_bet = target
                self.last_aggressor = sid
                self.acted = {sid}                       # reopen: others must respond
            else:
                self.acted.add(sid)                      # all-in call for less than current bet
        else:
            raise ValueError(f"unknown action {kind}")

        self._after_action(idx)

    def _put(self, sid, amt):
        amt = min(amt, self.stack[sid])
        self.stack[sid] -= amt
        self.committed[sid] += amt
        self.total[sid] += amt
        if self.stack[sid] == 0:
            self.allin.add(sid)

    # --- progression -----------------------------------------------------------
    def _after_action(self, idx):
        if len([s for s in self.seats if s not in self.folded]) == 1:
            self._finish()
            return
        nxt = self._advance(idx)
        if nxt is not None:
            self.to_act = nxt
            return
        self._next_street()

    def _open_street(self):
        """Deal the next street; return True if betting can happen on it."""
        self.committed = {s: 0 for s in self.seats}
        self.current_bet = 0
        self.min_raise = self.bb
        self.acted = set()
        self.last_aggressor = None
        if self.stage == "preflop":
            self.board += [self._deck.pop() for _ in range(3)]
            self.stage = "flop"
        elif self.stage == "flop":
            self.board.append(self._deck.pop())
            self.stage = "turn"
        elif self.stage == "turn":
            self.board.append(self._deck.pop())
            self.stage = "river"
        else:
            return False
        self.to_act = self._advance(self.button)
        actable = [s for s in self.seats if s not in self.folded and s not in self.allin]
        return self.to_act is not None and len(actable) >= 2

    def _next_street(self):
        if self.stage == "river":
            self._finish()
            return
        while True:
            if self._open_street():
                return                  # betting continues on the new street
            if self.stage == "river":
                self._finish()
                return                  # ran out of streets

    def _run_out(self):
        while self.stage != "river":
            self._open_street()
        self._finish()

    # --- resolution ------------------------------------------------------------
    def _contrib_in_order(self):
        order = [(self.button + 1 + k) % self.n for k in range(self.n)]
        return {self.seats[i]: self.total[self.seats[i]]
                for i in order if self.total[self.seats[i]] > 0}

    def _finish(self):
        if self.finished:
            return
        live = [s for s in self.seats if s not in self.folded]
        if len(live) == 1:
            winnings = {s: 0 for s in self.seats}
            winnings[live[0]] = self.pot()
        else:
            while len(self.board) < 5:        # complete the board for a showdown
                self.board.append(self._deck.pop())
            self.stage = "showdown"
            winnings = settle(self._contrib_in_order(), self.folded, self.hole, self.board)
        self.finished = True
        self.to_act = None
        self.payouts = {s: winnings.get(s, 0) for s in self.seats}
        self.final_stack = {s: self.stack[s] + winnings.get(s, 0) for s in self.seats}
