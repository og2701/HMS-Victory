"""EE code hunters get one playful reply.

Call of Duty runs promotions where EE customers unlock exclusive in-game items with a code, and
people from abroad turn up in UK servers asking for one. Asking for codes is against the rules
here, so the first time someone asks, Vic answers with one of these lines instead of staff having
to. Only actual requests count ("anyone got ee", "ee codes", "who has EE"), not someone saying
they're on EE. One reply per person, ever; staff and bots are left alone. Gated on
config.EE_CODE_REPLIES_ENABLED so it can be switched off when the promotion ends.
"""

import logging
import random
import re

import discord

import config
from config import ROLES
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
)


def asks_for_ee_code(content: str) -> bool:
    return bool(content) and bool(_ASKS_FOR_EE.search(content))


async def handle_ee_code_ask(message: discord.Message) -> None:
    if not getattr(config, "EE_CODE_REPLIES_ENABLED", False) or message.author.bot or message.guild is None:
        return
    if not asks_for_ee_code(message.content):
        return
    if any(r.id in _STAFF_ROLES for r in getattr(message.author, "roles", [])):
        return
    seen = load_json_file(config.EE_CODE_REPLIES_FILE) or {}
    if str(message.author.id) in seen:
        return
    seen[str(message.author.id)] = int(message.created_at.timestamp())
    save_json_file(config.EE_CODE_REPLIES_FILE, seen)
    log.info("EE code ask from %s (%s): %r", message.author, message.author.id, (message.content or "")[:120])
    try:
        await message.reply(random.choice(REPLIES), allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        log.debug("EE code reply failed", exc_info=True)
