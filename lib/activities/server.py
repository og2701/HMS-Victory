"""The HTTP side of ukplace activities, served from inside the bot.

Listens on localhost only. A Cloudflare Tunnel publishes it as ukplace.ogme.dev, and the
activity's URL mapping in the Developer Portal sends the page's /api calls there through
Discord's proxy, so the server never opens a port to the internet.

Routes are registered both bare and under /api, because whether the proxy strips the
mapping's prefix isn't worth betting the launch on:
    POST /token          {code, instance_id} -> {access_token, session, user}
    GET  /wordle         today's board for the session's player
    POST /wordle/guess   {guess} -> the board after it
    GET  /crossword, POST /crossword/answer, POST /crossword/hint
    GET  /climb          Climb HMS Victory: today's seed and the player's best and pay so far
    POST /climb/start, POST /climb/finish {height, time, bounces, seed}
    GET  /home           balance, today's puzzles, the casino in last-played order
    GET  /casino/<game>  a casino table (the hand in play, or the last one)
    POST /casino/<game>/<action>   deal {bet} or a move -> the table after it
    POST /casino/<game>/here|leave the open table checking in, or closing (for #casino)
    GET  /casino/watch/<uid>/<game>   someone else's table, for a spectator
    GET  /health
"""

import asyncio
import logging
import time

from aiohttp import web

import config
from lib.activities import auth, casino, climb, crossword_api, wordle_api

log = logging.getLogger(__name__)

_runner: web.AppRunner | None = None
_sweeper: asyncio.Task | None = None
CLIENT = web.AppKey("client", object)
_last_guess: dict[int, float] = {}
_token_hits: dict[str, list[float]] = {}
GUESS_GAP = 0.4          # seconds between one player's guesses
TOKENS_PER_MINUTE = 20   # sign-ins per address per minute


def _json(data, status=200):
    return web.json_response(data, status=status)


def _error(message: str, status: int):
    return _json({"error": message}, status)


def _today():
    from lib.features.wordle import _today as today
    return today()


def _allowed_channels() -> set[int]:
    return {int(c) for c in getattr(config, "ACTIVITIES_ALLOWED_CHANNELS", []) or []}


def _gate(client, uid: int, game: str = "wordle"):
    """Why this player can't play right now, or None. Same rules as the slash commands."""
    if getattr(client, "maintenance_mode", False):
        return _error("The bot is restarting. Try again in a minute.", 503)
    guild = client.get_guild(config.GUILD_ID)
    if guild is None:
        return _error("The bot is still starting. Try again in a moment.", 503)
    if guild.get_member(uid) is None:
        return _error("Join the HMS Victory server to play for UKPence.", 403)
    from lib.core import restrictions
    tier = restrictions.is_blocked(uid, game)
    if tier:
        return _error(restrictions.refusal_message(tier).replace("**", ""), 403)
    return None


def _player(request):
    """The session's player, or None. The token is the only thing trusted here."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return auth.read_session(header[7:])


async def token(request):
    client = request.app[CLIENT]
    ip = request.headers.get("CF-Connecting-IP") or request.remote or "?"
    now = time.time()
    hits = [t for t in _token_hits.get(ip, []) if now - t < 60]
    if len(hits) >= TOKENS_PER_MINUTE:
        return _error("Too many sign-ins. Wait a minute.", 429)
    _token_hits[ip] = hits + [now]

    try:
        body = await request.json()
    except Exception:
        return _error("Bad request.", 400)
    code, instance_id = str(body.get("code") or ""), str(body.get("instance_id") or "")
    if not code:
        return _error("Bad request.", 400)
    session = client.session
    try:
        access = await auth.exchange_code(session, code)
        # Who it is and where the activity is running don't depend on each other, so both
        # questions go to Discord at once - sign-in is the wait every player sits through.
        user, instance = await asyncio.gather(
            auth.fetch_user(session, access),
            auth.fetch_instance(session, instance_id) if instance_id else _none())
    except auth.AuthError as e:
        return _error(str(e), 401)
    uid = int(user["id"])

    channel = auth.instance_channel(instance, uid) if instance_id else None
    allowed = _allowed_channels()
    if allowed and channel not in allowed:
        return _error("This is still being tested, and only opens in #bot-workshop for now.", 403)

    game = _game_for(uid, body)
    gated = _gate(client, uid, _gate_name(game))
    if gated is not None:
        return gated
    return _json({"session": auth.make_session(uid, channel),
                  "user": {"id": str(uid), "name": user.get("global_name") or user.get("username"),
                           "avatar": user.get("avatar")},
                  **_opening(client, uid, game, channel)})


_STATE = {"wordle": wordle_api, "crossword": crossword_api, "climb": climb}


def _game_for(uid: int, body: dict) -> str:
    """Which screen to open: the one this person just asked for with a command or Play
    button, else the one the page names (an activity link's custom_id), else Home."""
    from lib.activities import launcher
    asked = launcher.take_requested(uid) or str(body.get("game") or "")
    if asked in _STATE or asked == "home":
        return asked
    if asked.startswith("casino:") and casino.adapter(asked[7:]) is not None:
        return asked
    if _watched(asked) is not None:
        return asked
    return "home"


def _watched(game: str) -> tuple[int, str] | None:
    """(player, table) for a watch:<uid>:<game> screen, if it's one that can be watched."""
    parts = game.split(":")
    if len(parts) != 3 or parts[0] != "watch" or not parts[1].isdigit():
        return None
    a = casino.adapter(parts[2])
    return (int(parts[1]), parts[2]) if a is not None and a.watchable else None


def _named(client, viewer: int, table: dict) -> dict:
    """A watched table with its spectators' names, and which of them is the one asking."""
    table["spectators"] = [{"uid": w, "name": _name(client, int(w)) or "Someone"} for w in table.get("spectators", [])]
    table["you"] = str(viewer)
    return table


def _name(client, uid: int) -> str | None:
    guild = client.get_guild(config.GUILD_ID)
    member = guild.get_member(int(uid)) if guild else None
    return getattr(member, "display_name", None)


def _gate_name(game: str) -> str:
    """The name lib.core.restrictions knows a screen by."""
    if game.startswith("watch:"):
        return "home"       # watching isn't playing
    if game.startswith("casino:"):
        a = casino.adapter(game[7:])
        return a.command if a else "casino"
    return game     # "home" isn't a command, so only a full block keeps someone off it


def _casino_open(channel) -> bool:
    allowed = {int(c) for c in getattr(config, "ACTIVITIES_CASINO_CHANNELS", []) or []}
    return not allowed or (channel is not None and int(channel) in allowed)


def _opening(client, uid: int, game: str, channel=None) -> dict:
    """The screen's data, sent with the sign-in to save the page a second round trip."""
    if game == "home":
        return {"game": game, "home": _home(client, uid, channel)}
    if game.startswith("casino:"):
        if not _casino_open(channel):
            return {"game": "home", "home": _home(client, uid, channel)}
        return {"game": game, "casino": _named(client, uid, casino.table(uid, game[7:]))}
    if game.startswith("watch:"):
        player, key = _watched(game)
        if not _casino_open(channel):
            return {"game": "home", "home": _home(client, uid, channel)}
        return {"game": game, "casino": _named(client, uid, casino.watch(player, key, _name(client, player), uid))}
    date = _today()
    _STATE[game].opened(client, uid, date)
    return {"game": game, game: _STATE[game].state(uid, date)}


def _home(client, uid: int, channel) -> dict:
    from lib.activities import home
    return {**home.state(client, uid), "casinoOpen": _casino_open(channel)}


async def _none():
    return None


async def resume(request):
    """Reopen with a remembered session instead of Discord's sign-in, which takes seconds.

    The remembered token says who; Discord's own record of this activity instance says
    where, and that the same person is genuinely in it. Anything that doesn't check out
    gets a 401 and the page falls back to the full sign-in."""
    client = request.app[CLIENT]
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    try:
        body = await request.json()
    except Exception:
        return _error("Bad request.", 400)
    instance_id = str(body.get("instance_id") or "")
    channel = None
    if instance_id:
        channel = auth.instance_channel(await auth.fetch_instance(client.session, instance_id), who["uid"])
        if channel is None:
            return _error("Sign in again.", 401)
    allowed = _allowed_channels()
    if allowed and channel not in allowed:
        return _error("This is still being tested, and only opens in #bot-workshop for now.", 403)
    game = _game_for(who["uid"], body)
    gated = _gate(client, who["uid"], _gate_name(game))
    if gated is not None:
        return gated
    return _json({"session": auth.make_session(who["uid"], channel),
                  **_opening(client, who["uid"], game, channel)})


async def timing(request):
    """The page reports how long each sign-in step took, so a slow launch can be pinned down,
    and anything that went wrong on the device (as `problem`), so it can be diagnosed."""
    who = _player(request)
    try:
        body = await request.json()
        steps = {k: int(v) for k, v in (body.get("steps") or {}).items() if isinstance(v, (int, float))}
    except Exception:
        return _error("Bad request.", 400)
    problem = body.get("problem")
    if isinstance(problem, str) and problem:
        # something that went wrong on the player's device (a 3D view lost, say), for diagnosing
        log.warning("activity problem for %s: %s", who["uid"] if who else "?", problem[:600])
    elif steps:
        log.info("activity launch timing for %s: %s", who["uid"] if who else "?", steps)
    return _json({"ok": True})


async def wordle_state(request):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    gated = _gate(request.app[CLIENT], who["uid"])
    if gated is not None:
        return gated
    date = _today()
    wordle_api.opened(request.app[CLIENT], who["uid"], date)
    return _json(wordle_api.state(who["uid"], date))


async def wordle_guess(request):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    client = request.app[CLIENT]
    gated = _gate(client, who["uid"])
    if gated is not None:
        return gated
    now = time.time()
    if now - _last_guess.get(who["uid"], 0) < GUESS_GAP:
        return _error("Slow down a little.", 429)
    _last_guess[who["uid"]] = now
    try:
        body = await request.json()
    except Exception:
        return _error("Bad request.", 400)
    board, err, solved_now = await wordle_api.guess(client, who["uid"], _today(), str(body.get("guess") or ""))
    if err:
        return _error(err, 422)
    if solved_now and who["ch"]:
        import asyncio
        from lib.activities import launcher
        asyncio.create_task(launcher.announce(who["ch"], wordle_api.solve_message(who["uid"], board), "wordle"))
    return _json(board)


def _slow_down(uid: int):
    now = time.time()
    if now - _last_guess.get(uid, 0) < GUESS_GAP:
        return _error("Slow down a little.", 429)
    _last_guess[uid] = now
    return None


async def crossword_state(request):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    client = request.app[CLIENT]
    gated = _gate(client, who["uid"], "crossword")
    if gated is not None:
        return gated
    date = _today()
    crossword_api.opened(client, who["uid"], date)
    return _json(crossword_api.state(who["uid"], date))


async def _crossword_move(request, move):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    client = request.app[CLIENT]
    gated = _gate(client, who["uid"], "crossword") or _slow_down(who["uid"])
    if gated is not None:
        return gated
    try:
        body = await request.json()
    except Exception:
        return _error("Bad request.", 400)
    board, msg, finished_now = await move(client, who["uid"], _today(), body)
    if board is None:
        return _error(msg or "That wasn't accepted.", 422)
    if finished_now and who["ch"]:
        from lib.activities import launcher
        asyncio.create_task(launcher.announce(
            who["ch"], crossword_api.finish_message(who["uid"], board), "crossword"))
    return _json({**board, "message": msg})


async def crossword_answer(request):
    return await _crossword_move(request, lambda client, uid, date, body: crossword_api.answer(
        client, uid, date, str(body.get("entry") or ""), str(body.get("guess") or "")))


async def crossword_hint(request):
    return await _crossword_move(request, lambda client, uid, date, body: crossword_api.hint(client, uid, date))


async def climb_state(request):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    gated = _gate(request.app[CLIENT], who["uid"], "climb")
    if gated is not None:
        return gated
    return _json(climb.state(who["uid"], _today()))


async def climb_run(request):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    gated = _gate(request.app[CLIENT], who["uid"], "climb")
    if gated is not None:
        return gated
    action = request.match_info["action"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        if action == "start":
            return _json(climb.start(who["uid"], _today()))
        if action == "finish":
            board, result = climb.finish(who["uid"], _today(), body if isinstance(body, dict) else {})
            if result["newBest"] and who["ch"]:
                climb.schedule_post(who["uid"], who["ch"], board["date"])
            return _json({**board, "result": result})
    except climb.Refuse as e:
        return _error(str(e), 422)
    except Exception:
        log.error("climb %s failed", action, exc_info=True)
        return _error("Something went wrong saving that climb.", 500)
    return _error("Not found.", 404)


async def home_state(request):
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    client = request.app[CLIENT]
    gated = _gate(client, who["uid"], "home")
    if gated is not None:
        return gated
    return _json(_home(client, who["uid"], who["ch"]))


def _casino_request(request):
    """(who, adapter key, error response) for a casino route."""
    who = _player(request)
    if who is None:
        return None, None, _error("Sign in again.", 401)
    key = request.match_info["game"]
    a = casino.adapter(key)
    if a is None:
        return who, None, _error("That game isn't here.", 404)
    if not _casino_open(who["ch"]):
        return who, None, _error("The casino only opens in #bot-workshop while it's being tested.", 403)
    gated = _gate(request.app[CLIENT], who["uid"], a.command)
    return who, key, gated


async def casino_state(request):
    who, key, err = _casino_request(request)
    if err is not None:
        return err
    return _json(_named(request.app[CLIENT], who["uid"], casino.table(who["uid"], key)))


async def casino_move(request):
    who, key, err = _casino_request(request)
    if err is not None:
        return err
    presence = {"here": casino.sessions.here, "leave": casino.sessions.leave}.get(request.match_info["action"])
    if presence is not None:            # the open table checking in (and hearing who's watching), or closing
        presence(who["uid"], key)
        out = {"ok": True, "spectators": [str(w) for w in casino.spectators(who["uid"], key) if w]}
        return _json(_named(request.app[CLIENT], who["uid"], out))
    try:
        body = await request.json()
    except Exception:
        body = {}
    client = request.app[CLIENT]
    guild = client.get_guild(config.GUILD_ID)
    member = guild.get_member(who["uid"]) if guild else None
    name = member.display_name if member else "Player"
    try:
        return _json(_named(client, who["uid"], await casino.move(who["uid"], name, key, request.match_info["action"],
                                                                  body if isinstance(body, dict) else {})))
    except casino.Busy as e:
        return _error(str(e), 429)
    except casino.Refuse as e:
        return _error(str(e), 422)
    except Exception:
        log.error("casino move failed (%s %s)", key, request.match_info["action"], exc_info=True)
        return _error("Something went wrong at the table. Your stake is safe; try again.", 500)


async def casino_watch(request):
    """Someone else's table, for a spectator."""
    who = _player(request)
    if who is None:
        return _error("Sign in again.", 401)
    if not _casino_open(who["ch"]):
        return _error("The casino isn't open in this channel.", 403)
    try:
        player = int(request.match_info["uid"])
    except ValueError:
        return _error("That isn't a player.", 404)
    try:
        client = request.app[CLIENT]
        return _json(_named(client, who["uid"], casino.watch(player, request.match_info["game"],
                                                             _name(client, player), who["uid"])))
    except casino.Refuse as e:
        return _error(str(e), 404)


async def health(_request):
    return _json({"ok": True})


def build_app(client) -> web.Application:
    app = web.Application(client_max_size=16 * 1024)
    app[CLIENT] = client
    for prefix in ("", "/api"):
        app.router.add_post(f"{prefix}/token", token)
        app.router.add_get(f"{prefix}/wordle", wordle_state)
        app.router.add_post(f"{prefix}/wordle/guess", wordle_guess)
        app.router.add_get(f"{prefix}/health", health)
        app.router.add_post(f"{prefix}/timing", timing)
        app.router.add_post(f"{prefix}/resume", resume)
        app.router.add_get(f"{prefix}/crossword", crossword_state)
        app.router.add_post(f"{prefix}/crossword/answer", crossword_answer)
        app.router.add_post(f"{prefix}/crossword/hint", crossword_hint)
        app.router.add_get(f"{prefix}/home", home_state)
        app.router.add_get(f"{prefix}/climb", climb_state)
        app.router.add_post(f"{prefix}/climb/{{action}}", climb_run)
        app.router.add_get(f"{prefix}/casino/{{game}}", casino_state)
        app.router.add_get(f"{prefix}/casino/watch/{{uid}}/{{game}}", casino_watch)
        app.router.add_post(f"{prefix}/casino/{{game}}/{{action}}", casino_move)
    return app


async def _name_entry_command(client) -> None:
    """Give the app's launch command the name we want people typing (/test-wordle while
    testing). Discord creates it as /launch when Activities are switched on; renaming it is
    one idempotent call, so it's simply checked on every boot."""
    want = getattr(config, "ACTIVITIES_ENTRY_COMMAND", "")
    if not want:
        return
    app_id = auth._env("ACTIVITIES_CLIENT_ID")
    headers = {"Authorization": f"Bot {auth._env('ACTIVITIES_BOT_TOKEN')}"}
    try:
        async with client.session.get(f"{auth.API}/applications/{app_id}/commands", headers=headers) as r:
            commands = await r.json() if r.status == 200 else []
        entry = next((c for c in commands if c.get("type") == 4), None)
        description = getattr(config, "ACTIVITIES_ENTRY_DESCRIPTION", "")
        if entry is None or (entry.get("name") == want and entry.get("description") == description):
            return
        async with client.session.patch(
                f"{auth.API}/applications/{app_id}/commands/{entry['id']}", headers=headers,
                json={"name": want, "description": description}) as r:
            log.info("activity launch command set to /%s (%s)", want, r.status)
    except Exception:
        log.warning("couldn't name the activity launch command", exc_info=True)


async def start(client) -> bool:
    """Start the API if it's switched on and the app's secrets are in .env."""
    global _runner
    if _runner is not None or not getattr(config, "ACTIVITIES_API_ENABLED", False):
        return False
    if not auth.configured():
        log.info("ukplace activities API not started: ACTIVITIES_* secrets aren't in .env")
        return False
    casino.base.CLIENT = client          # badge awards need the bot's client
    _runner = web.AppRunner(build_app(client), access_log=None)
    await _runner.setup()
    port = int(getattr(config, "ACTIVITIES_API_PORT", 8787))
    await web.TCPSite(_runner, "127.0.0.1", port).start()
    log.info("ukplace activities API listening on 127.0.0.1:%s", port)
    await _name_entry_command(client)
    from lib.activities import launcher
    await launcher.start(client.session)
    global _sweeper
    _sweeper = asyncio.create_task(casino.sessions.run_sweeper())
    return True


async def stop() -> None:
    global _runner, _sweeper
    from lib.activities import launcher
    if _sweeper is not None:
        _sweeper.cancel()
        _sweeper = None
    try:
        await casino.sessions.close_all()
    except Exception:
        log.warning("couldn't close the casino session lines", exc_info=True)
    await launcher.stop()
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
