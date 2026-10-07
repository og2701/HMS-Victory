"""The /skyrim routes for the activity API (docs/skyrim-activity-contract.md), wired in from server.py.

Each handler signs the player in and gates them like every other game, then hands over to core (the town, the
map, delves), bouts (the Pit and duels) or a registered panel. The functions those modules provide:

    await core.home(ctx) -> SkHome                    core.create(ctx, stone) -> SkHome
    core.offers(ctx) -> SkOffers                      core.offers_action(ctx, action, body) -> SkOffers
    await core.launch(ctx, loc, kind) -> SkTurn
    core.current(ctx) -> SkTurn | None                await core.act(ctx, action, rev) -> SkTurn
    bouts.bout(ctx, arena) -> SkBout                  await bouts.bout_action(ctx, arena, action, body) -> SkBout

`ctx` is {"uid", "name", "client", "ch"} (ch: the channel the activity was opened in). Any of them may raise
common.Refuse (422) or common.Stale (409, carrying the current turn as .turn). Whatever changes the game awaits
common.after(ctx, profile) once it's saved, for the Discord side: the game log, Wonder announcements, badges.
"""

from __future__ import annotations

import inspect
import logging

from aiohttp import web

from lib.activities.skyrim_web import common
from lib.activities.skyrim_web.registry import PANELS, load_all

log = logging.getLogger(__name__)
GAME = "skyrim"


def _json(data, status=200):
    return web.json_response(data, status=status)


def _error(message: str, status: int, **extra):
    return _json({"error": message, **extra}, status)


def _who(request):
    """(ctx, error response): the signed-in player and what they may do, as server.py checks it."""
    from lib.activities import server
    who = server._player(request)
    if who is None:
        return None, _error("Sign in again.", 401)
    client = request.app[server.CLIENT]
    err = server._gate(client, who["uid"], GAME)
    if err is not None:
        return None, err
    return {"uid": int(who["uid"]), "name": server._name(client, who["uid"]), "client": client, "ch": who.get("ch")}, None


async def _body(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def _run(fn, *args):
    """Call a handler's function, answering its refusals as the contract says."""
    try:
        out = fn(*args)
        if inspect.isawaitable(out):
            out = await out
        return _json(out)
    except common.Refuse as e:
        return _error(str(e), 422)
    except common.Stale as e:
        return _error(str(e) or "That turn has already moved on.", 409, turn=getattr(e, "turn", None))
    except ValueError as e:                 # the sessions layer refuses with a player-readable ValueError
        return _error(str(e), 422)
    except Exception:
        log.error("skyrim %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return _error("Something went wrong. Your character is safe; try again.", 500)


def _core():
    from lib.activities.skyrim_web import core
    return core


def _bouts():
    from lib.activities.skyrim_web import bouts
    return bouts


async def home(request):
    ctx, err = _who(request)
    return err or await _run(_core().home, ctx)


async def create(request):
    ctx, err = _who(request)
    if err:
        return err
    body = await _body(request)
    return await _run(_core().create, ctx, str(body.get("stone") or ""))


async def offers(request):
    ctx, err = _who(request)
    return err or await _run(_core().offers, ctx)


async def offers_action(request):
    ctx, err = _who(request)
    if err:
        return err
    return await _run(_core().offers_action, ctx, request.match_info["action"], await _body(request))


async def launch(request):
    ctx, err = _who(request)
    if err:
        return err
    body = await _body(request)
    return await _run(_core().launch, ctx, str(body.get("loc") or ""), str(body.get("kind") or "normal"))


async def delve(request):
    ctx, err = _who(request)
    if err:
        return err
    try:
        turn = _core().current(ctx)
    except common.Refuse as e:
        return _error(str(e), 422)
    return _json(turn) if turn else _error("You're not on an adventure.", 404)


async def act(request):
    ctx, err = _who(request)
    if err:
        return err
    body = await _body(request)
    rev = body.get("rev")
    return await _run(_core().act, ctx, str(body.get("action") or ""), int(rev) if rev is not None else None)


async def panel(request):
    ctx, err = _who(request)
    if err:
        return err
    entry = PANELS.get(request.match_info["key"])
    if not entry or "view" not in entry:
        return _error("There's nothing there.", 404)
    try:
        profile = common.need(ctx["uid"])
    except common.Refuse as e:
        return _error(str(e), 422)
    return await _run(entry["view"], profile, ctx)


async def panel_action(request):
    ctx, err = _who(request)
    if err:
        return err
    entry = PANELS.get(request.match_info["key"])
    if not entry or "act" not in entry:
        return _error("You can't do that here.", 404)
    try:
        profile = common.need(ctx["uid"])
    except common.Refuse as e:
        return _error(str(e), 422)
    return await _run(entry["act"], profile, ctx, request.match_info["action"], await _body(request))


async def bout(request):
    ctx, err = _who(request)
    return err or await _run(_bouts().bout, ctx, request.match_info["arena"])


async def bout_action(request):
    ctx, err = _who(request)
    if err:
        return err
    return await _run(_bouts().bout_action, ctx, request.match_info["arena"], request.match_info["action"],
                      await _body(request))


def register(app: web.Application, prefix: str):
    load_all()
    app.router.add_get(f"{prefix}/skyrim", home)
    app.router.add_post(f"{prefix}/skyrim/create", create)
    app.router.add_get(f"{prefix}/skyrim/offers", offers)
    app.router.add_post(f"{prefix}/skyrim/offers/{{action}}", offers_action)
    app.router.add_post(f"{prefix}/skyrim/launch", launch)
    app.router.add_get(f"{prefix}/skyrim/delve", delve)
    app.router.add_post(f"{prefix}/skyrim/act", act)
    app.router.add_get(f"{prefix}/skyrim/panel/{{key}}", panel)
    app.router.add_post(f"{prefix}/skyrim/panel/{{key}}/{{action}}", panel_action)
    app.router.add_get(f"{prefix}/skyrim/bout/{{arena}}", bout)
    app.router.add_post(f"{prefix}/skyrim/bout/{{arena}}/{{action}}", bout_action)
