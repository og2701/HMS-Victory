"""The HTTP side of ukplace activities, served from inside the bot.

Listens on localhost only. A Cloudflare Tunnel publishes it as ukplace.ogme.dev, and the
activity's URL mapping in the Developer Portal sends the page's /api calls there through
Discord's proxy, so the server never opens a port to the internet.

Routes are registered both bare and under /api, because whether the proxy strips the
mapping's prefix isn't worth betting the launch on:
    POST /token          {code, instance_id} -> {access_token, session, user}
    GET  /wordle         today's board for the session's player
    POST /wordle/guess   {guess} -> the board after it
    GET  /health
"""

import asyncio
import logging
import time

from aiohttp import web

import config
from lib.activities import auth, wordle_api

log = logging.getLogger(__name__)

_runner: web.AppRunner | None = None
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


def _gate(client, uid: int):
    """Why this player can't play right now, or None. Same rules as the slash command."""
    if getattr(client, "maintenance_mode", False):
        return _error("The bot is restarting. Try again in a minute.", 503)
    guild = client.get_guild(config.GUILD_ID)
    if guild is None:
        return _error("The bot is still starting. Try again in a moment.", 503)
    if guild.get_member(uid) is None:
        return _error("Join the HMS Victory server to play for UKPence.", 403)
    from lib.core import restrictions
    tier = restrictions.is_blocked(uid, "wordle")
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

    gated = _gate(client, uid)
    if gated is not None:
        return gated
    # The board rides along with the sign-in, saving the page a second round trip.
    date = _today()
    wordle_api.opened(client, uid, date)
    return _json({"session": auth.make_session(uid, channel),
                  "user": {"id": str(uid), "name": user.get("global_name") or user.get("username"),
                           "avatar": user.get("avatar")},
                  "wordle": wordle_api.state(uid, date)})


async def _none():
    return None


async def timing(request):
    """The page reports how long each sign-in step took, so a slow launch can be pinned down."""
    who = _player(request)
    try:
        body = await request.json()
        steps = {k: int(v) for k, v in (body.get("steps") or {}).items() if isinstance(v, (int, float))}
    except Exception:
        return _error("Bad request.", 400)
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
        asyncio.create_task(launcher.announce(who["ch"], wordle_api.solve_message(who["uid"], board)))
    return _json(board)


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
        if entry is None or entry.get("name") == want:
            return
        async with client.session.patch(
                f"{auth.API}/applications/{app_id}/commands/{entry['id']}", headers=headers,
                json={"name": want, "description": getattr(config, "ACTIVITIES_ENTRY_DESCRIPTION", "")}) as r:
            log.info("activity launch command renamed /%s -> /%s (%s)", entry.get("name"), want, r.status)
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
    _runner = web.AppRunner(build_app(client), access_log=None)
    await _runner.setup()
    port = int(getattr(config, "ACTIVITIES_API_PORT", 8787))
    await web.TCPSite(_runner, "127.0.0.1", port).start()
    log.info("ukplace activities API listening on 127.0.0.1:%s", port)
    await _name_entry_command(client)
    from lib.activities import launcher
    await launcher.start(client.session)
    return True


async def stop() -> None:
    global _runner
    from lib.activities import launcher
    await launcher.stop()
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
