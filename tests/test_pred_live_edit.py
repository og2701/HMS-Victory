"""Editing a live prediction from /pred-admin: bets follow their outcome through
renames, reorders, additions and removals, and the modal applies the edit."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.economy.prediction_system import (  # noqa: E402
    LivePredictionEditModal, Prediction, _remap_live_options,
)


def _pred():
    p = Prediction(1, "Fenerbache Vs Liverpool", ["Fenerbache", "Draw", "Liverpool"], end_ts=0)
    p.bets = {1: {10: 100}, 2: {}, 3: {30: 300, 31: 50}}
    return p


def test_rename_keeps_bets_in_place():
    bets, err = _remap_live_options(_pred(), ["Fenerbahce", "Draw", "Liverpool"])
    assert err is None
    assert bets == {1: {10: 100}, 2: {}, 3: {30: 300, 31: 50}}


def test_reorder_moves_bets_with_their_outcome():
    bets, err = _remap_live_options(_pred(), ["liverpool", "Draw", "Fenerbache"])
    assert err is None
    assert bets == {1: {30: 300, 31: 50}, 2: {}, 3: {10: 100}}


def test_add_outcome_gets_empty_pool():
    bets, err = _remap_live_options(_pred(), ["Fenerbache", "Draw", "Liverpool", "Abandoned"])
    assert err is None
    assert bets[4] == {} and bets[3] == {30: 300, 31: 50}


def test_remove_outcome_without_bets():
    bets, err = _remap_live_options(_pred(), ["Fenerbache", "Liverpool"])
    assert err is None
    assert bets == {1: {10: 100}, 2: {30: 300, 31: 50}}


def test_remove_outcome_with_bets_is_refused():
    bets, err = _remap_live_options(_pred(), ["Fenerbache", "Draw"])
    assert bets is None and "Liverpool" in err


def _interaction():
    msg = MagicMock()
    msg.edit = AsyncMock()
    msg.channel.history = MagicMock(return_value=_aiter([]))
    inter = MagicMock()
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter, msg


async def _aiter_gen(items):
    for i in items:
        yield i


def _aiter(items):
    return _aiter_gen(items)


def _submit(pred, title, options, close):
    client = SimpleNamespace(predictions={pred.msg_id: pred}, user=SimpleNamespace(id=999))
    admin_view = SimpleNamespace(pred=pred, client=client, stop=MagicMock())
    modal = LivePredictionEditModal(admin_view)
    modal.title_input._value = title
    modal.options_input._value = options
    modal.close_input._value = close
    inter, msg = _interaction()
    with patch("lib.economy.prediction_system._save") as save, \
         patch("lib.economy.prediction_system._fetch_pred_message", AsyncMock(return_value=msg)), \
         patch("lib.economy.prediction_system._edit_prediction_message", AsyncMock()) as edit_card:
        asyncio.run(modal.on_submit(inter))
    return inter, save, edit_card


def test_modal_pushes_close_time_back_and_rerenders():
    import time
    pred = _pred()
    pred.end_ts = time.time() + 600
    inter, save, edit_card = _submit(pred, "Fenerbache Vs Liverpool",
                                     "Fenerbache\nDraw\nLiverpool", "1440")
    assert abs(pred.end_ts - (time.time() + 1440 * 60)) < 5
    assert pred.bets[3] == {30: 300, 31: 50}
    save.assert_called_once()
    edit_card.assert_awaited_once()
    assert edit_card.await_args.kwargs["interactive"] is True
    inter.response.edit_message.assert_awaited_once()
    assert "Updated" in inter.followup.send.await_args.args[0]


def test_modal_keeps_past_close_time_when_untouched_on_locked_pred():
    pred = _pred()
    pred.locked = True
    pred.end_ts = 1_700_000_000  # long gone; the field defaults to it
    from datetime import datetime
    import pytz
    orig = datetime.fromtimestamp(pred.end_ts, tz=pytz.timezone("Europe/London")).strftime("%d/%m/%Y %H:%M")
    inter, _, edit_card = _submit(pred, "Fenerbahce Vs Liverpool",
                                  "Fenerbahce\nDraw\nLiverpool", orig)
    assert pred.end_ts == 1_700_000_000
    assert pred.title == "Fenerbahce Vs Liverpool" and pred.options[0] == "Fenerbahce"
    assert edit_card.await_args.kwargs["interactive"] is False
    assert "Unlock" in inter.followup.send.await_args.args[0]


def test_modal_refuses_settled_prediction():
    pred = _pred()
    client = SimpleNamespace(predictions={}, user=SimpleNamespace(id=999))
    modal = LivePredictionEditModal(SimpleNamespace(pred=pred, client=client, stop=MagicMock()))
    inter, _ = _interaction()
    asyncio.run(modal.on_submit(inter))
    assert "already been settled" in inter.response.send_message.await_args.args[0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
