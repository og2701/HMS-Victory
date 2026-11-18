"""Run the activity's API on this machine against a throwaway database, for testing the
HMS Games page against the real game code without Discord.

    PYTHONPATH=. python scripts/dev_activity_api.py [port]

Everything it writes goes to a temp folder: a fresh database with one funded player, and the
puzzle and casino state files. It prints a session token; open the page with
http://localhost:5174/?local=<token> (the Vite dev server proxies /api here). Nothing here
can touch the live bot's data, and #casino posts go nowhere (no Discord connection).

There are two funded players, Tester and Rival, so a Broadside duel can be played from two tabs:
open each printed link in its own tab, with &game=duel to go straight to the duels. The #casino
posts a duel makes are saved as pictures (and their text printed) in the data folder's posts/."""

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")
os.environ["ACTIVITIES_SESSION_SECRET"] = "dev-activity-secret"

PLAYER = 4242
RIVAL = 4343
THIRD = 4444
WORKSHOP = 1141037835445616640


def _post_to_folder(launcher, folder: str) -> None:
    """Instead of Discord: each post and edit saved as a picture, with its text and channel printed."""
    os.makedirs(folder, exist_ok=True)
    count = {"posts": 0, "saves": 0}

    def text_of(view) -> str:
        out, todo = [], list(view.children)
        while todo:
            item = todo.pop(0)
            if getattr(item, "content", None):
                out.append(item.content)
            todo[:0] = list(getattr(item, "children", []) or [])
        return " / ".join(out)

    def save(mid: int, view, files, channel_id=None) -> None:
        count["saves"] += 1
        path = None
        for f in files or []:
            path = os.path.join(folder, f"post{mid}-{count['saves']:03d}.png")
            f.fp.seek(0)
            with open(path, "wb") as out:
                out.write(f.fp.read())
        print(f"post {mid} in channel {channel_id}: {text_of(view)}" + (f"\n  picture: {path}" if path else ""), flush=True)

    async def post_view(channel_id, view, files=None, ping=None):
        count["posts"] += 1
        save(count["posts"], view, files, channel_id)
        return count["posts"]

    async def edit_view(channel_id, message_id, view, files=None):
        save(message_id, view, files, channel_id)

    launcher.post_view, launcher.edit_view = post_view, edit_view


def _sandbox(app, heist) -> None:
    """Bank Job's puzzle sandbox, this script only: one mini-game at a time, at any size, with no crew or job
    around it (the page's ?local=<token>&sandbox=role:game:size). It plays the game through heist.py exactly as a
    job would, and says what it scored when it's done."""
    import importlib
    import random
    import traceback
    from aiohttp import web
    from lib.activities import heist_games
    puzzles: dict[str, tuple[str, str, dict]] = {}

    def reload(role):
        """Pick up a role's games as they're edited, with no restart (a broken file keeps the last good ones)."""
        try:
            mod = importlib.reload(importlib.import_module(f"lib.activities.heist_games.{role}"))
            heist_games.GAMES[role] = dict(mod.GAMES)
        except Exception:
            traceback.print_exc()
    story = heist._story(random.Random())

    def view(sid):
        role, size, p = puzzles[sid]
        done = heist._done(role, p)
        return {"sid": sid, "size": size, "puzzle": {"role": role, "ob": sid, "name": "SANDBOX", "stepped": False, "done": done,
                                                    **heist._public(role, p)},
                "score": round(heist._score(role, p), 3) if done else None, "story": story}

    async def games(request):
        for role in heist_games.ROLES:
            reload(role)
        from lib.activities.heist_games.loud import LOUD
        return web.json_response({role: [{"key": k, "title": (heist_games.FIRST_TITLES.get(k) or heist_games.GAMES[role][k].title)}
                                         for k in heist_games.keys(role)] + [{"key": "loud", "title": f"LOUD: {LOUD[role].title}"}]
                                  for role in heist_games.ROLES})

    async def make(request):
        body = await request.json()
        role, game, size = body.get("role"), body.get("game"), body.get("size") or "s"
        if role in heist_games.ROLES:
            reload(role)
        if role not in heist_games.ROLES or (game not in heist_games.keys(role) and game != "loud"):
            return web.json_response({"error": "No such game."}, status=400)
        sid = f"sb{len(puzzles) + 1}"
        try:
            puzzles[sid] = (role, size, heist._make(role, size, random.Random(), story, game))
        except Exception as e:
            traceback.print_exc()
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
        return web.json_response(view(sid))

    async def move(request):
        body = await request.json()
        sid = body.get("sid")
        if sid not in puzzles:
            return web.json_response({"error": "That puzzle's gone."}, status=400)
        role, _, p = puzzles[sid]
        try:
            heist._move(role, p, body.get("body") or {})
        except heist.Refuse as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:                                   # a game being built: say what broke
            traceback.print_exc()
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
        return web.json_response(view(sid))

    app.router.add_get("/api/heist/sandbox/games", games)
    app.router.add_post("/api/heist/sandbox/make", make)
    app.router.add_post("/api/heist/sandbox/move", move)


def main(port: int) -> None:
    # DEV_ACTIVITY_DATA reuses an earlier run's folder, so a restart keeps the test saves
    tmp = os.environ.get("DEV_ACTIVITY_DATA") or tempfile.mkdtemp(prefix="hms-activity-dev-")
    import config
    config.JSON_DATA_DIR = tmp
    config.WORDLE_STATE_FILE = os.path.join(tmp, "wordle.json")
    if hasattr(config, "CROSSWORD_STATE_FILE"):
        config.CROSSWORD_STATE_FILE = os.path.join(tmp, "crossword.json")
    config.PERSISTENT_VIEWS_FILE = os.path.join(tmp, "persistent_views.json")
    # the stand-in players all try UKP Kart while it's only open to its testers
    config.KART_TESTERS = [*getattr(config, "KART_TESTERS", []), PLAYER, RIVAL, THIRD]
    config.GUESSR_TESTERS = [*getattr(config, "GUESSR_TESTERS", []), PLAYER, RIVAL, THIRD]
    config.GUESSR_TEST_SPOTS = True         # UKGuessr: three known spots with test panoramas, without a Mapillary token
    config.HEIST_TESTERS = [*getattr(config, "HEIST_TESTERS", []), PLAYER, RIVAL, THIRD]
    # Skyrim's saves too, so playing it here never touches this checkout's data/json
    for name in ("PROFILES", "DAILY", "GRAVEYARD", "WORLDBOSS"):
        setattr(config, f"SKYRIM_{name}_FILE", os.path.join(tmp, f"skyrim_{name.lower()}.json"))
    from lib.core import file_operations
    file_operations.PERSISTENT_VIEWS_FILE = config.PERSISTENT_VIEWS_FILE

    import database
    database.DB_FILE = os.path.join(tmp, "dev.db")
    database.init_db()
    from lib.economy import economy_manager as em
    em.add_bb(PLAYER, 50_000, reason="dev seed", taxable=False)
    em.add_bb(RIVAL, 50_000, reason="dev seed", taxable=False)
    em.add_bb(THIRD, 50_000, reason="dev seed", taxable=False)
    import dev_heist_bots                                   # Bank Job's stand-in crew, this script only
    for uid in dev_heist_bots.BOTS:
        em.add_bb(uid, 50_000, reason="dev seed", taxable=False)

    from lib.activities import auth, server
    from lib.activities.casino import base
    base._FILE = os.path.join(tmp, "activity_casino.json")
    from lib.activities import countdown, countdown_posts, duel, duel_posts, heist, heist_posts, kart, kart_posts, launcher
    duel._FILE = os.path.join(tmp, "activity_duels.json")
    countdown._FILE = os.path.join(tmp, "activity_countdown.json")
    kart._FILE = os.path.join(tmp, "activity_kart.json")
    heist._FILE = os.path.join(tmp, "activity_heist.json")
    _post_to_folder(launcher, os.path.join(tmp, "posts"))
    duel.listeners.append(duel_posts.on_event)
    countdown.listeners.append(countdown_posts.on_event)
    kart.listeners.append(kart_posts.on_event)
    heist.listeners.append(heist_posts.on_event)
    from lib.core import restrictions
    restrictions.is_blocked = lambda uid, cmd: None

    class Picture:
        """A stand-in profile picture (Broadside's move art); Pooja has none, to show the initial."""
        def __init__(self, path):
            self.path = path

        def replace(self, **kwargs):
            return self

        async def read(self):
            with open(self.path, "rb") as f:
                return f.read()

    art = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "broadside")
    members = {PLAYER: SimpleNamespace(id=PLAYER, display_name="Tester", bot=False,
                                       display_avatar=Picture(os.path.join(art, "ram.webp"))),
               RIVAL: SimpleNamespace(id=RIVAL, display_name="Rival", bot=False,
                                      display_avatar=Picture(os.path.join(art, "fireship.webp"))),
               THIRD: SimpleNamespace(id=THIRD, display_name="Pooja", bot=False)}
    members.update({uid: SimpleNamespace(id=uid, display_name=name, bot=False) for uid, name in dev_heist_bots.BOTS.items()})
    guild = SimpleNamespace(get_member=lambda uid: members.get(int(uid)), members=list(members.values()))
    client = SimpleNamespace(maintenance_mode=False, session=None, get_guild=lambda gid: guild)
    base.CLIENT = client
    heist.CLIENT = client

    async def run():
        from aiohttp import web
        app = server.build_app(client)
        _sandbox(app, heist)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        print(f"activity API on http://127.0.0.1:{port} (data in {tmp})")
        print(f"open: http://localhost:5174/?local={auth.make_session(PLAYER, WORKSHOP)}")
        print(f"rival (a second tab, for duels): http://localhost:5174/?local={auth.make_session(RIVAL, WORKSHOP)}&game=duel",
              flush=True)
        print(f"rival on Countdown: http://localhost:5174/?local={auth.make_session(RIVAL, WORKSHOP)}&game=countdown",
              flush=True)
        print(f"third player (Countdown): http://localhost:5174/?local={auth.make_session(THIRD, WORKSHOP)}&game=countdown",
              flush=True)
        print(f"paperboy: http://localhost:5174/?local={auth.make_session(PLAYER, WORKSHOP)}&game=paperboy", flush=True)
        for who, uid in (("tester", PLAYER), ("rival", RIVAL), ("pooja", THIRD)):
            print(f"kart ({who}): http://localhost:5174/?local={auth.make_session(uid, WORKSHOP)}&game=kart", flush=True)
        asyncio.get_running_loop().create_task(duel.run_sweeper())
        asyncio.get_running_loop().create_task(countdown.run_sweeper())
        for who, uid in (("tester", PLAYER), ("rival", RIVAL), ("pooja", THIRD)):
            print(f"bank job ({who}): http://localhost:5174/?local={auth.make_session(uid, WORKSHOP)}&game=heist", flush=True)
        asyncio.get_running_loop().create_task(kart.run_sweeper())
        asyncio.get_running_loop().create_task(heist.run_sweeper())
        print(f"bank job sandbox (one mini-game on its own): http://localhost:5174/?local={auth.make_session(PLAYER, WORKSHOP)}"
              "&sandbox=hacker:keypad:s", flush=True)
        if dev_heist_bots.FILL > 1:
            print(f"bank job: bots join any crew you open, up to {dev_heist_bots.FILL} strong (DEV_HEIST_BOTS=0 for none)", flush=True)
            asyncio.get_running_loop().create_task(dev_heist_bots.run(heist, {PLAYER, RIVAL, THIRD}))
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8788)
