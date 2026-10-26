"""EE code hunters get one playful reply.

Call of Duty runs promotions where EE customers unlock exclusive in-game items with a code, and
people from abroad turn up in UK servers asking for one. Asking for codes is against the rules
here, so the first time someone asks, Vic answers with one of these lines, times them out and
drops a short note in the police station with a Remove timeout button. Only actual requests count ("anyone got ee", "ee codes", "who has EE"), not someone saying
they're on EE. One reply per person, ever; staff and bots are left alone. Gated on
config.EE_CODE_REPLIES_ENABLED so it can be switched off when the promotion ends.

Two checks. The patterns below run on everyone and cost nothing. Members who joined in the last
week also get their first few messages read by Jev, which catches the asks that never say "EE"
("any UK lads able to get me the new cod skin?"). That's where the code-hunters are, and it keeps
the Jev calls to a few dozen a day rather than one per message on the server.
"""

import logging
import random
import re
from datetime import timedelta

import discord

import config
from config import CHANNELS, ROLES
from lib.core.file_operations import load_json_file, save_json_file

log = logging.getLogger(__name__)

_ASKS_FOR_EE = re.compile(
    r"\bee\s*(?:codes?|promo|skins?|rewards?|offer|operator)\b"
    r"|\b(?:any\s*one|anybody|some\s*one|somebody|who|any\s+(?:brits?|uk))\b[^.?!\n]{0,30}?\b(?:have|has|got|with|on|uses?)\s+(?:an?\s+)?ee\b"
    r"|\bif\s+(?:you|u)\b[^.?!\n]{0,15}?\b(?:have|got|on|with|use)\s+ee\b",
    re.IGNORECASE,
)

_STAFF_ROLES = {ROLES.DEPUTY_PM, ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE, ROLES.PCSO,
                ROLES.DEPUTY_MINISTER_OF_COMMUNITY}

REPLIES = (
    "Ah, another pilgrim seeking the sacred EE code. Sadly the vault is empty, and asking for it is against the rules.",
    "Let me guess: you've come for an EE code. Afraid the cupboard's bare, and asking for one breaks the server rules.",
    "Welcome, brave traveller. The EE code you seek does not exist here, and asking for it is against the rules.",
    "You've got the look of someone hunting EE codes. We haven't got any, and begging for them is against the rules.",
    "Rumour has it there's an EE code in this server. Rumour is wrong, and asking for one is against the rules.",
    "Another one for the EE code queue. The queue leads nowhere, I'm afraid, and asking is against the rules.",
    "If you're after an EE code, you've sailed into the wrong harbour. None here, and asking for them breaks the rules.",
    "The great EE code migration continues. Sadly this stop has none, and asking for them is against the rules.",
    "Ah, EE code season is upon us. We don't stock them, and asking for one is against the rules.",
    "I sense an EE code request. The answer is no, and asking is against the server rules anyway.",
    "Many have come seeking EE codes. None have found them. Asking for one is against the rules, so best not to.",
    "Your quest for an EE code ends here, unsuccessfully. We have none, and asking is against the rules.",
    "Sorry, the EE code fairy doesn't visit this server. Asking for codes is against the rules too.",
    "Here for an EE code? Bold move. We haven't got any, and asking for them is against the rules.",
    "This is a tea and chat establishment, not an EE code dispensary. Asking for codes is against the rules.",
    "Spoiler: nobody here has a spare EE code. Also, asking for one is against the server rules.",
    "The EE code you're looking for is in another castle. Asking for one here is against the rules.",
    "Ah, a fellow treasure hunter. Unfortunately the EE code chest is empty, and asking is against the rules.",
    "We admire the dedication, but there are no EE codes here, and asking for them breaks the server rules.",
    "Every day a new seeker arrives asking for EE codes. Every day the answer is no. Asking is against the rules, too.",
    "No EE codes in this port, captain. Asking for them is against the rules, so lower the sails on that one.",
    "If EE codes grew on trees, our tree would be bare. Asking for them is against the server rules.",
    "You've wandered in looking for an EE code, haven't you? We've none to give, and asking breaks the rules.",
    "Ah yes, the annual EE code pilgrimage. This shrine is empty, and asking for codes is against the rules.",
    "Breaking news: local server has no EE codes. Asking for them remains against the rules.",
    "Unfortunately the EE code shop is permanently closed. Asking for one is against the rules here.",
    "Have you tried asking EE? Because we haven't got codes, and asking for them here is against the rules.",
    "A noble quest, but the EE code is not in this kingdom. Asking for one is against the rules.",
    "Hold your horses, we don't hand out EE codes. Asking for them is against the server rules.",
    "We've had a few of you today. Still no EE codes, and asking for them is still against the rules.",
    "Ah, you must be here for the legendary EE code. Legend is all it is, I'm afraid, and asking is against the rules.",
    'We checked behind the sofa. No EE codes, just crumbs. Asking for them is against the rules, by the way.',
    'The EE code department is closed for refurbishment, indefinitely. Asking for one is against the rules.',
    "Sorry, the last EE code was spotted in 2019 and hasn't been seen since. Asking for one is against the rules.",
    "If I had an EE code, I'd keep it myself. I don't, and asking for one is against the rules.",
    "You're the third person this hour hunting EE codes. The prize is still nothing, and asking is against the rules.",
    'Our EE code reserves are at zero, much like your chances here. Asking for one is against the rules.',
    "An EE code? In this economy? We've none, and asking for them is against the rules.",
    'This server runs on tea and complaining about the weather, not EE codes. Asking for them is against the rules.',
    "The EE code you're after has left the building. Asking for one here is against the rules.",
    "Brave of you to ask for an EE code in a room full of people who can't help. It's also against the rules.",
    "Somewhere out there is an EE code with your name on it. It isn't here, and asking for it is against the rules.",
    'We consulted the tea leaves. They say no EE codes, and that asking for them is against the rules.',
    "I've asked around. Nobody has an EE code, and everybody agrees asking for one is against the rules.",
    "You've reached the EE code helpline. All our operators are pretending they didn't see this. Asking is against the rules.",
    'Unfortunately the EE code was eaten by the server cat. Asking for one is against the rules anyway.',
    'No EE codes, no Call of Duty skins, no dice. Asking for them is against the server rules.',
    'Plenty of chat, plenty of tea, zero EE codes. Asking for them is against the rules.',
    "We're a UK server, not a UK phone shop. No EE codes here, and asking for them is against the rules.",
    "That EE code isn't going to find itself, and it won't be found here either. Asking is against the rules.",
    "Kindly put the EE code request back where you found it. It's against the rules here.",
    'The EE code lottery has no winners today. Or ever. Asking for one is against the rules.',
    'One does not simply walk into this server and get an EE code. Asking for one is against the rules.',
    "I'd love to help, but I'm a bot with no phone contract. No EE codes here, and asking is against the rules.",
    'Ask not what the server can do for your EE code. The answer is nothing, and asking is against the rules.',
    "Sadly there's no EE code raffle, giveaway or secret handshake here. Asking for codes is against the rules.",
    "We've searched every channel. The EE code isn't hiding in any of them. Asking for one is against the rules.",
    'Bad news: no EE codes. Worse news: asking for them is against the rules.',
    "You've come a long way for an EE code, and it ends here, empty-handed. Asking is against the rules.",
    'The EE code ship has sailed, sunk and been forgotten. Asking for one here is against the rules.',
)


def _next_reply(store: dict) -> str:
    """Deal the lines like a shuffled deck, so nobody watching sees a repeat until all of them are used."""
    deck = [i for i in store.get("_deck", []) if isinstance(i, int) and 0 <= i < len(REPLIES)]
    if not deck:
        deck = list(range(len(REPLIES)))
        random.shuffle(deck)
    i = deck.pop()
    store["_deck"] = deck
    return REPLIES[i]


def asks_for_ee_code(content: str) -> bool:
    return bool(content) and bool(_ASKS_FOR_EE.search(content))


NEW_JOINER_DAYS = 7
NEW_JOINER_MESSAGES = 10
JEV_THRESHOLD = 0.7
# Jev only reads a new joiner's message once it's plausibly about this at all. A bare link with
# "then your this" in front scored 0.64 and got someone timed out, so links are stripped and the
# words left over have to touch the topic: EE, codes, skins, CoD, phones, or the UK.
_URL_RE = re.compile(r"https?://\S+")
_ON_TOPIC = re.compile(
    r"\b(?:ee|codes?|promo\w*|skins?|operators?|rewards?|cod|call\s+of\s+duty|mw\s?4|warzone|black\s+ops|"
    r"phones?|contracts?|network|sim|mobile|uk|brits?|british|england|english|euros?|claim)\b",
    re.IGNORECASE,
)
_jev_checked: dict[int, int] = {}   # member id -> messages Jev has read; in memory, a restart only grants a few more


async def _jev_ee_ask(text: str) -> float | None:
    from lib.features.mention_signals import judge_yes_no
    p = await judge_yes_no(
        text,
        instructions=("Is `message` asking for a Call of Duty x EE promotional code, or for someone with an EE phone "
                      "contract or account to claim the in-game reward for them?"),
        true_examples=["anyone got an ee code", "any uk lads able to get me the new cod skin?", "does anyone here have ee mobile",
                       "need someone with a uk phone contract for the cod promo, will pay", "can someone claim the operator skin for me",
                       "american looking for a euro with EE"],
        false_what="Anything else: greetings, chatting about EE as a phone network, Call of Duty in general, other games",
        context={"server": "a UK community Discord server",
                 "promotion": "EE customers can claim exclusive Call of Duty items by code, so people from abroad ask UK members for them"},
    )
    return p


def _new_joiner(member) -> bool:
    joined = getattr(member, "joined_at", None)
    return joined is not None and discord.utils.utcnow() - joined <= timedelta(days=NEW_JOINER_DAYS)


async def handle_ee_code_ask(message: discord.Message) -> None:
    if not getattr(config, "EE_CODE_REPLIES_ENABLED", False) or message.author.bot or message.guild is None:
        return
    if any(r.id in _STAFF_ROLES for r in getattr(message.author, "roles", [])):
        return
    seen = load_json_file(config.EE_CODE_REPLIES_FILE) or {}
    if str(message.author.id) in seen:
        return
    how = "the EE patterns"
    if not asks_for_ee_code(message.content):
        text = _URL_RE.sub("", message.content or "").strip()
        uid = message.author.id
        if len(text.split()) < 3 or not _ON_TOPIC.search(text):
            return
        if not _new_joiner(message.author) or _jev_checked.get(uid, 0) >= NEW_JOINER_MESSAGES:
            return
        _jev_checked[uid] = _jev_checked.get(uid, 0) + 1
        p = await _jev_ee_ask(text)
        if p is None or p < JEV_THRESHOLD:
            return
        how = f"Jev ({p:.2f}, new joiner)"
    line = _next_reply(seen)
    seen[str(message.author.id)] = int(message.created_at.timestamp())
    save_json_file(config.EE_CODE_REPLIES_FILE, seen)
    log.info("EE code ask from %s (%s): %r", message.author, message.author.id, (message.content or "")[:120])
    try:
        await message.reply(line, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        log.debug("EE code reply failed", exc_info=True)

    minutes = getattr(config, "EE_CODE_TIMEOUT_MINUTES", 60)
    try:
        await message.author.timeout(timedelta(minutes=minutes), reason="Asked for EE codes")
        timed_out = True
    except discord.HTTPException:
        log.warning("couldn't time out EE code asker %s", message.author.id, exc_info=True)
        timed_out = False
    await _report(message, how, minutes if timed_out else None)


async def _report(message: discord.Message, how: str, minutes: int | None) -> None:
    """A short note in the police station: Remove timeout for the false positives, Ban (which DMs the
    appeal first) for the ones who mean it."""
    from lib.core.mod_actions import EE_CODES, ModBanButton, ModUntimeoutButton, action_view
    channel = message.guild.get_channel(CHANNELS.POLICE_STATION)
    if channel is None:
        return
    snippet = (message.content or "").replace("\n", " ")[:200]
    done = f"Timed out for {minutes} min" if minutes else "Timeout FAILED"
    embed = discord.Embed(
        title="🎮 EE code ask",
        description=(f"{message.author.mention} (`{message.author.id}`) in {message.channel.mention}\n"
                     f"> {snippet}\n{done} · caught by {how} · [jump]({message.jump_url})"),
        colour=0xE67E22,
    )
    try:
        buttons = (ModUntimeoutButton, ModBanButton) if minutes else (ModBanButton,)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(),
                           view=action_view(EE_CODES, message.author.id, only=buttons))
    except discord.HTTPException:
        log.warning("couldn't post the EE code report", exc_info=True)
