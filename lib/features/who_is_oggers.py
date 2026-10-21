"""Ask "who is oggers?" and the bot answers in your DMs with a coordinate and nothing else.

Started as a creepypasta joke in #general. Each asker gets a random spot somewhere in the
UK, a few seconds later, with no explanation. The spot comes from boxes drawn well inside
the coastline and the Irish border, weighted by area, so it never lands at sea or abroad
(lakes and rivers are fair game).
"""

import asyncio
import logging
import math
import random
import re
import time

import discord

from config import USERS

logger = logging.getLogger(__name__)

# "who is oggers", "who's oggers", "whos oggers", "who tf is oggers", "who even is this
# oggers?", "who is @oggers". Filler words are listed rather than wildcarded so "who was
# talking to oggers" doesn't count.
_FILLER = r"(?:s|is|was|tf|the|hell|heck|fuck|even|actually|exactly|really|this|that)"
WHO_IS_OGGERS = re.compile(
    rf"\bwho(?:s|se|z)?(?:[\s'’]+{_FILLER})*[\s'’,]+(?:og+ers?|<@!?{USERS.OGGERS}>)(?!\w)",
    re.IGNORECASE,
)

# (lat_min, lat_max, lon_min, lon_max), each fully on UK land.
_LAND_BOXES = (
    (51.00, 51.55, -2.50, 0.10),    # southern England
    (51.75, 53.20, -2.90, -0.20),   # Midlands
    (52.10, 52.70, 0.30, 1.45),     # East Anglia
    (52.95, 53.55, -0.60, -0.10),   # Lincolnshire
    (53.20, 54.80, -2.60, -1.40),   # Pennines and the north
    (53.80, 54.40, -1.40, -0.60),   # East Yorkshire
    (54.90, 55.60, -2.40, -1.80),   # Northumberland
    (51.70, 52.90, -3.85, -3.10),   # Wales
    (55.10, 55.80, -4.20, -2.20),   # Southern Uplands and the Borders
    (56.30, 57.40, -4.80, -3.50),   # Highlands
    (56.90, 57.50, -3.40, -2.30),   # Aberdeenshire
    (57.95, 58.35, -4.80, -4.00),   # Sutherland
    (54.50, 54.95, -7.25, -6.00),   # Northern Ireland
)
_WEIGHTS = [(b - a) * (d - c) * math.cos(math.radians((a + b) / 2)) for a, b, c, d in _LAND_BOXES]

COOLDOWN_SECONDS = 600
_last_sent: dict[int, float] = {}


def asks_who_oggers_is(content: str) -> bool:
    return bool(content) and bool(WHO_IS_OGGERS.search(content))


def random_uk_coordinate(rng: random.Random = random) -> str:
    lat_min, lat_max, lon_min, lon_max = rng.choices(_LAND_BOXES, weights=_WEIGHTS)[0]
    return f"{rng.uniform(lat_min, lat_max):.3f}, {rng.uniform(lon_min, lon_max):.3f}"


async def handle_who_is_oggers(client, message):
    if message.author.bot or not asks_who_oggers_is(message.content):
        return
    now = time.time()
    if now - _last_sent.get(message.author.id, 0) < COOLDOWN_SECONDS:
        return
    _last_sent[message.author.id] = now
    # A short pause so it arrives as its own thing rather than an obvious bot reply.
    await asyncio.sleep(random.uniform(3, 10))
    try:
        await message.author.send(random_uk_coordinate())
    except (discord.Forbidden, discord.HTTPException):
        logger.info("who-is-oggers DM to %s failed (DMs closed?)", message.author.id)
