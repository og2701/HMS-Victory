"""Run the activity's API on this machine against a throwaway database, for testing the
HMS Games page against the real game code without Discord.

    PYTHONPATH=. python scripts/dev_activity_api.py [port]

Everything it writes goes to a temp folder: a fresh database with one funded player, and the
puzzle and casino state files. It prints a session token; open the page with
http://localhost:5174/?local=<token> (the Vite dev server proxies /api here). Nothing here
can touch the live bot's data, and #casino posts go nowhere (no Discord connection)."""

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")
os.environ["ACTIVITIES_SESSION_SECRET"] = "dev-activity-secret"

PLAYER = 4242
WORKSHOP = 1141037835445616640


def main(port: int) -> None:
    tmp = tempfile.mkdtemp(prefix="hms-activity-dev-")
    import config
    config.JSON_DATA_DIR = tmp
    config.WORDLE_STATE_FILE = os.path.join(tmp, "wordle.json")
    if hasattr(config, "CROSSWORD_STATE_FILE"):
        config.CROSSWORD_STATE_FILE = os.path.join(tmp, "crossword.json")
    config.PERSISTENT_VIEWS_FILE = os.path.join(tmp, "persistent_views.json")
    from lib.core import file_operations
    file_operations.PERSISTENT_VIEWS_FILE = config.PERSISTENT_VIEWS_FILE

    import database
    database.DB_FILE = os.path.join(tmp, "dev.db")
    database.init_db()
    from lib.economy import economy_manager as em
    em.add_bb(PLAYER, 50_000, reason="dev seed", taxable=False)

    from lib.activities import auth, server
    from lib.activities.casino import base
    base._FILE = os.path.join(tmp, "activity_casino.json")
    from lib.core import restrictions
    restrictions.is_blocked = lambda uid, cmd: None

    member = SimpleNamespace(display_name="Tester")
    guild = SimpleNamespace(get_member=lambda uid: member)
    client = SimpleNamespace(maintenance_mode=False, session=None, get_guild=lambda gid: guild)

    async def run():
        from aiohttp import web
        runner = web.AppRunner(server.build_app(client))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        token = auth.make_session(PLAYER, WORKSHOP)
        print(f"activity API on http://127.0.0.1:{port} (data in {tmp})")
        print(f"open: http://localhost:5174/?local={token}", flush=True)
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8788)
