"""Rock Paper Scissors: the round logic, and the two ways it could quietly cheat someone.

The dangerous bugs here are not "who beats what". They are a pick becoming visible before
both are in, a tie being scored as a win, and the pot being paid twice or not at all.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from commands.economy import rps as R

P1, P2 = 111, 222
STAKE = 100


class FakeMessage:
    def __init__(self):
        self.id = 987654321
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


def _match(monkeypatch):
    """A match with the money and persistence stubbed out, recording what it would pay."""
    paid = []
    monkeypatch.setattr(R, "credit_from_bank",
                        lambda uid, amt, reason: paid.append(("credit", uid, amt)))
    monkeypatch.setattr(R, "settle_pvp_pot",
                        lambda w, l, pot, reason, own_stake=None: paid.append(("pot", w, pot)))
    monkeypatch.setattr(R, "delete_state", lambda mid: paid.append(("escrow_dropped", mid, 0)))
    recorded = []
    # _finish imports pvp_stats inside the function, so the stub has to go on the real
    # module rather than on a name in rps
    from lib.economy import pvp_stats
    monkeypatch.setattr(pvp_stats, "record_result", lambda *a: recorded.append(a))
    m = R.RPSMatch(P1, "Alice", P2, "Bob", STAKE, channel_id=1)
    m.message = FakeMessage()
    return m, paid, recorded


class FakeInteraction:
    """Enough of an Interaction to drive a HandPicker button, logging what it was sent."""

    def __init__(self, log):
        self.log = log
        self.response = types.SimpleNamespace(edit_message=self._ack)
        self.followup = types.SimpleNamespace(send=self._followup)

    async def _ack(self, **kwargs):
        self.log.append(("acked", kwargs.get("content")))

    async def _followup(self, **kwargs):
        self.log.append(("followup", kwargs.get("content")))


def _press(picker, hand, log):
    """Press one of the ephemeral buttons for real, callback and all."""
    index = R.HANDS.index(hand)
    asyncio.get_event_loop().run_until_complete(
        picker.children[index].callback(FakeInteraction(log)))


def _pick(match, uid, hand):
    """Submit a hand the way HandPicker does, without a real interaction."""
    asyncio.get_event_loop().run_until_complete(match.submit(None, uid, hand))


def _fresh_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def test_the_hands_beat_each_other_correctly(monkeypatch):
    _fresh_loop()
    for winner, loser in (("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")):
        m, _paid, _rec = _match(monkeypatch)
        m._start_timer = lambda: None
        _pick(m, P1, winner)
        _pick(m, P2, loser)
        assert m.scores[P1] == 1, f"{winner} should beat {loser}"
        assert m.scores[P2] == 0


def test_nothing_is_revealed_until_both_have_picked(monkeypatch):
    """The whole game. If the first pick shows, the second player just wins."""
    _fresh_loop()
    m, _paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "rock")
    assert m.last is None, "the round was revealed with one hand in"
    body = m._embed().description
    assert "rock" not in body.lower() and R.EMOJI["rock"] not in body, \
        f"the pick leaked into the public embed: {body!r}"
    assert "Bob" in body, "the embed should say who is still to pick"
    _pick(m, P2, "paper")
    assert m.last is not None


def test_you_cannot_change_your_pick_or_pick_twice(monkeypatch):
    _fresh_loop()
    m, _paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "rock")
    _pick(m, P1, "paper")          # second attempt must be ignored
    assert m.picks[P1] == "rock"
    _pick(m, P2, "scissors")
    assert m.scores[P1] == 1, "the first pick should have stood"


def test_a_tie_is_replayed_and_does_not_score(monkeypatch):
    _fresh_loop()
    m, _paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "rock")
    _pick(m, P2, "rock")
    assert m.scores == {P1: 0, P2: 0}
    assert m.round == 1, "a tie should not advance the round"
    assert m.picks == {}, "the tie should clear both hands for a replay"
    assert not m.game_over


def test_best_of_three_settles_on_the_second_win(monkeypatch):
    _fresh_loop()
    m, paid, recorded = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")
    assert not m.game_over, "one win is not a match"
    _pick(m, P1, "paper"); _pick(m, P2, "rock")
    assert m.game_over
    assert ("pot", P1, STAKE * 2) in paid, f"the winner was not paid the pot: {paid}"
    assert recorded and recorded[0][:3] == ("rps", P1, P2)
    assert recorded[0][4] == "win"


def test_the_escrow_record_is_dropped_before_any_payout(monkeypatch):
    """Delete-before-credit: a crash mid-settle must not let the next boot pay again."""
    _fresh_loop()
    m, paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")
    kinds = [p[0] for p in paid]
    assert kinds.index("escrow_dropped") < kinds.index("pot"), \
        f"paid out before dropping the escrow record: {paid}"


def test_a_finished_match_cannot_be_played_on(monkeypatch):
    _fresh_loop()
    m, paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")
    payouts = len([p for p in paid if p[0] == "pot"])
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")
    assert len([p for p in paid if p[0] == "pot"]) == payouts, "the pot was paid twice"


def test_running_the_clock_down_forfeits_to_whoever_picked(monkeypatch):
    loop = _fresh_loop()
    m, paid, recorded = _match(monkeypatch)
    monkeypatch.setattr(R, "_round_seconds", lambda: 0)

    async def play():
        await m.submit(None, P1, "rock")       # P2 never picks
        await asyncio.sleep(0.05)

    loop.run_until_complete(play())
    assert m.game_over, "the forfeit clock did not fire"
    assert ("pot", P1, STAKE * 2) in paid
    assert recorded[0][4] == "forfeit"


def test_neither_player_picking_voids_rather_than_guessing(monkeypatch):
    loop = _fresh_loop()
    m, paid, recorded = _match(monkeypatch)
    monkeypatch.setattr(R, "_round_seconds", lambda: 0)

    async def wait_it_out():
        m._start_timer()
        await asyncio.sleep(0.05)

    loop.run_until_complete(wait_it_out())
    assert m.game_over
    assert ("credit", P1, STAKE) in paid and ("credit", P2, STAKE) in paid, \
        f"both stakes should come back: {paid}"
    assert not [p for p in paid if p[0] == "pot"], "nobody should win a pot nobody played for"
    assert recorded[0][4] == "draw"


def test_the_click_is_answered_before_any_slow_work(monkeypatch):
    """Settling hits the database and edits the public message, both easily past Discord's 3
    second window. If the click isn't acknowledged first the player gets a red "didn't respond
    in time" even though their pick landed - which is exactly what happened in play."""
    _fresh_loop()
    m, _paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    log = []

    async def slow_render():
        log.append(("rendered", None))
        return True

    m._render = slow_render
    _press(R.HandPicker(m, P1), "rock", log)
    assert log, "the player was told nothing at all"
    assert log[0][0] == "acked", f"the interaction was not answered first: {log}"
    assert m.picks[P1] == "rock", "the pick did not register"


def test_a_picker_left_over_from_an_earlier_round_cannot_submit(monkeypatch):
    """The old ephemeral stays on screen with live-looking buttons. Pressing one must not
    quietly count as this round's hand."""
    _fresh_loop()
    m, _paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    stale = R.HandPicker(m, P1)
    _pick(m, P1, "rock"); _pick(m, P2, "scissors")      # round 1 resolves, seq moves on
    log = []
    _press(stale, "paper", log)
    assert m.picks == {}, "a stale picker counted towards the live round"
    assert log and "already been played" in (log[0][1] or ""), log


def test_the_history_says_whose_hand_was_whose(monkeypatch):
    """"📄 vs 🪨" tells you nothing about who played what."""
    _fresh_loop()
    m, _paid, _rec = _match(monkeypatch)
    m._start_timer = lambda: None
    _pick(m, P1, "paper"); _pick(m, P2, "rock")
    body = m._embed().description
    assert "paper beats" in body and "Alice" in body, body
    assert "1 - 0" in body, f"the score should be readable at a glance: {body!r}"


def test_the_stake_ceiling_is_lower_than_connect_fours():
    """A match takes seconds, so the same ceiling would move money far faster."""
    assert config.RPS_MAX_BET < config.CONNECT4_MAX_BET


def _run_all():
    class MP:
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value, raising=True):
            self._undo.append((target, name, getattr(target, name, None),
                               hasattr(target, name)))
            setattr(target, name, value)

        def undo(self):
            for target, name, old, existed in reversed(self._undo):
                if existed:
                    setattr(target, name, old)
                else:
                    delattr(target, name)
            self._undo.clear()

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        mp = MP()
        try:
            fn(mp) if fn.__code__.co_argcount else fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
