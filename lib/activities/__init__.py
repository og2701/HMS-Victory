"""The ukplace activities API: the server side of the games that run as Discord Activities.

The game pages live on Vercel and run inside Discord's activity frame; everything that
matters - who you are, whether a guess is right, what it pays - happens here, inside the
bot's own process, so it shares the database lock and the JSON state files with the slash
commands rather than racing them from a second program. See server.py for the routes and
auth.py for how a player is identified.
"""
