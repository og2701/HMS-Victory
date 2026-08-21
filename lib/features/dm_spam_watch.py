"""Watch for members Discord has caught sending unusual volumes of DMs.

Discord stamps `unusual_dm_activity_until` on the guild member object when its own spam
detection trips on an account - the same thing that puts the "unusual DM activity" warning
in the client. It is a strong signal: measured across the guild, the accounts carrying it
are overwhelmingly the bulk-generated dating and crypto scam sort.

Two awkward facts shape this module.

discord.py drops the field. It is not in the library's Member at all, so the parsed objects
can never show it and there is no gateway event we can read it from. The only way to see it
is to ask the API for the raw member JSON ourselves, which is why this sweeps over HTTP
rather than reading from cache.

The flag runs 24 hours and is set AFTER the spamming starts, not on arrival. Across every
flagged member in the guild the earliest any flag expired was exactly 24 hours after that
member joined, so nobody turns up already carrying one. That rules it out as a join gate -
it is a "this member has just DM-spammed your people" alarm, and the value is in mods seeing
it while the account is still around to act on.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import discord

import config
from lib.core.file_operations import load_json_file, save_json_file

logger = logging.getLogger(__name__)

STATE_FILE = config.DM_SPAM_FLAG_FILE

# A first run that would announce a backlog is noise, not news. Above this many unseen
# flags, seed quietly and let the next sweep report genuine new ones.
FIRST_RUN_ALERT_LIMIT = 10
PAGE = 1000
TIMEOUT_HOURS = 24        # matches how long Discord's own flag runs

# The ban goes out on Discord's judgement rather than on anything a moderator watched
# happen, and a hijacked account belongs to a real person who did not do it - so this ban
# gets the same appeal route as the cluster bans, with wording that fits the actual reason.
BAN_DM_TEXT = (
    "## You've been banned from UK Place\n"
    "Discord's own systems flagged your account for sending an unusual volume of direct "
    "messages. That is almost always either a scam account or an account somebody else has "
    "taken control of.\n\n"
    "**If your account was hacked, say so.** Change your password and turn on two-factor "
    "authentication first, then appeal below - we would rather have you back than leave a "
    "hijacked account banned.")


def _parse(stamp):
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


async def fetch_flagged_members(client, now=None):
    """Every member whose unusual DM flag is live right now, straight from the API.

    Returns {user id: {name, until, joined, roles}}. Goes through client.http so the
    library's rate limiter owns the pacing - a full sweep of a 12k guild is 13 requests.
    """
    now = now or datetime.now(timezone.utc)
    found, after, pages = {}, 0, 0
    while True:
        # get_members hands back the untouched JSON - it is the parsing into Member that
        # loses the field, not the fetch.
        batch = await client.http.get_members(config.GUILD_ID, PAGE, after)
        if not batch:
            break
        pages += 1
        for m in batch:
            until = _parse(m.get("unusual_dm_activity_until"))
            if until is None or until <= now:
                continue
            user = m.get("user") or {}
            found[str(user.get("id"))] = {
                "name": user.get("username"),
                "until": m["unusual_dm_activity_until"],
                "joined": m.get("joined_at"),
                "roles": len(m.get("roles") or []),
            }
        after = max(int(m["user"]["id"]) for m in batch)
        if len(batch) < PAGE:
            break
    logger.debug(f"dm spam sweep: {pages} pages, {len(found)} live flags")
    return found


def _embed(uid, info, now=None):
    now = now or datetime.now(timezone.utc)
    until = _parse(info.get("until"))
    joined = _parse(info.get("joined"))
    created = discord.utils.snowflake_time(int(uid))

    embed = discord.Embed(
        title="📨 Discord flagged this member for unusual DM activity",
        description=f"<@{uid}> `{uid}`" + (f" · `{info['name']}`" if info.get("name") else ""),
        colour=0xE74C3C)
    if until is not None:
        hours = max(0, (until - now).total_seconds() / 3600)
        embed.add_field(name="Flag lifts", value=f"<t:{int(until.timestamp())}:R> ({hours:.0f}h)",
                        inline=True)
    if joined is not None:
        embed.add_field(name="Joined", value=f"<t:{int(joined.timestamp())}:R>", inline=True)
    embed.add_field(name="Account made", value=f"<t:{int(created.timestamp())}:R>", inline=True)
    embed.set_footer(text="Discord's own spam detection · the flag lasts 24h and is set "
                          "after the DMs go out")
    return embed


async def sweep(client, now=None):
    """One pass: find live flags, report the ones we have not reported, remember them.

    Expired flags are dropped from state rather than kept, so an account caught a second
    time weeks later is reported again instead of being silently remembered as old news.
    """
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        live = await fetch_flagged_members(client, now=now)
    except discord.HTTPException as e:
        logger.warning(f"dm spam sweep failed: {e}")
        return {}
    except Exception:
        logger.error("dm spam sweep blew up", exc_info=True)
        return {}

    state = load_json_file(STATE_FILE) or {}
    first_run = not state
    # Re-flagged with a later expiry counts as new; the same stamp does not.
    new = {uid: info for uid, info in live.items()
           if state.get(uid, {}).get("until") != info["until"]}
    save_json_file(STATE_FILE, live)

    took = time.monotonic() - started
    logger.info(f"dm spam sweep: {len(live)} live, {len(new)} new, {took:.1f}s")
    if not new:
        return {}
    if first_run and len(new) > FIRST_RUN_ALERT_LIMIT:
        logger.warning(f"dm spam watch seeded with {len(new)} existing flags, not announcing")
        return {}

    channel = client.get_channel(config.CHANNELS.POLICE_STATION)
    if channel is None:
        logger.warning("dm spam watch has flags to report but no police station channel")
        return new
    for uid, info in sorted(new.items(), key=lambda kv: kv[1]["until"]):
        try:
            await channel.send(embed=_embed(uid, info, now=now),
                               view=action_view(uid))
        except Exception:
            logger.error(f"could not report the DM flag on {uid}", exc_info=True)
        await asyncio.sleep(1)
    return new


# --- staff actions on the alert ---------------------------------------------------------
# The flag lasts 24 hours, so an alert nobody can act on from where they are reading it is
# an alert that expires unanswered. Buttons are DynamicItems keyed on the user id: no state
# to keep, and they still work on an alert posted before the last restart.

def _is_staff(user):
    from lib.core.behaviour_watch import is_staff
    return is_staff(user)


async def _settle(interaction, note):
    """Write what was done onto the alert itself and retire the buttons, so the next mod to
    scroll past can see it has been dealt with and by whom."""
    try:
        embed = interaction.message.embeds[0]
        embed.add_field(name="Handled", value=note, inline=False)
        embed.colour = 0x95A5A6
        await interaction.message.edit(embed=embed, view=None)
    except Exception:
        logger.debug("could not settle the DM flag alert", exc_info=True)


class _DMFlagAction(discord.ui.DynamicItem[discord.ui.Button], template=r"$"):
    """Shared plumbing for the buttons on an unusual-DM alert."""

    def __init__(self, user_id, prefix, label, emoji, style):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label=label, emoji=emoji, style=style,
            custom_id=f"{prefix}:{self.user_id}"))

    async def _reject_non_staff(self, interaction):
        if _is_staff(interaction.user):
            return False
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return True

    async def _member(self, interaction):
        guild = interaction.guild
        member = guild.get_member(self.user_id) if guild else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(self.user_id)
            except discord.HTTPException:
                member = None
        return member


class DMFlagBanButton(_DMFlagAction, template=r"dmflag:ban:(?P<uid>\d+)"):
    def __init__(self, user_id=0):
        super().__init__(user_id, "dmflag:ban", "Ban", "🔨", discord.ButtonStyle.danger)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["uid"])

    async def callback(self, interaction):
        if await self._reject_non_staff(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        reason = f"Discord unusual DM activity flag · banned by {interaction.user}"
        # Tell them before the ban lands. Afterwards we no longer share a guild and Discord
        # will not deliver the DM, so the appeal route silently disappears.
        told = False
        member = await self._member(interaction)
        if member is not None:
            from commands.moderation.join_clusters import send_ban_appeal_dm
            told = await send_ban_appeal_dm(member, BAN_DM_TEXT)
        try:
            # By id rather than by member: they may well have been kicked or left already,
            # and a ban is still worth having in that case.
            await interaction.guild.ban(discord.Object(id=self.user_id),
                                        reason=reason[:500], delete_message_seconds=0)
        except Exception as e:
            await interaction.followup.send(f"Could not ban them: {e}", ephemeral=True)
            return
        await _settle(interaction, f"🔨 Banned by {interaction.user.mention}"
                                   + ("" if told else " · could not DM them the appeal"))
        await interaction.followup.send(
            "Banned." + (" They have the appeal button." if told
                         else " Their DMs are closed, so no appeal notice reached them."),
            ephemeral=True)


class DMFlagTimeoutButton(_DMFlagAction, template=r"dmflag:to:(?P<uid>\d+)"):
    def __init__(self, user_id=0):
        super().__init__(user_id, "dmflag:to", f"Time out {TIMEOUT_HOURS}h", "⏳",
                         discord.ButtonStyle.primary)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["uid"])

    async def callback(self, interaction):
        if await self._reject_non_staff(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = await self._member(interaction)
        if member is None:
            await interaction.followup.send("They are not in the server any more.",
                                            ephemeral=True)
            return
        until = discord.utils.utcnow() + timedelta(hours=TIMEOUT_HOURS)
        reason = f"Discord unusual DM activity flag · timed out by {interaction.user}"
        try:
            await member.timeout(until, reason=reason[:500])
        except Exception as e:
            await interaction.followup.send(f"Could not time them out: {e}", ephemeral=True)
            return
        await _settle(interaction,
                      f"⏳ Timed out {TIMEOUT_HOURS}h by {interaction.user.mention}")
        await interaction.followup.send(f"Timed out for {TIMEOUT_HOURS}h.", ephemeral=True)


class DMFlagAnalyseButton(_DMFlagAction, template=r"dmflag:analyse:(?P<uid>\d+)"):
    def __init__(self, user_id=0):
        super().__init__(user_id, "dmflag:analyse", "Analyse", "🔎",
                         discord.ButtonStyle.secondary)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["uid"])

    async def callback(self, interaction):
        member = await self._member(interaction)
        if member is None:
            await interaction.response.send_message(
                "They are not in the server any more, so there is nothing to read.",
                ephemeral=True)
            return
        # handle_analyse_user does its own permission check and its own defer.
        from commands.moderation.user_analysis import handle_analyse_user
        await handle_analyse_user(interaction, member)


class DMFlagDismissButton(_DMFlagAction, template=r"dmflag:dismiss:(?P<uid>\d+)"):
    def __init__(self, user_id=0):
        super().__init__(user_id, "dmflag:dismiss", "Ignore", "✅",
                         discord.ButtonStyle.success)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["uid"])

    async def callback(self, interaction):
        if await self._reject_non_staff(interaction):
            return
        await interaction.response.defer()
        await _settle(interaction, f"✅ Left alone by {interaction.user.mention}")


def action_view(user_id):
    view = discord.ui.View(timeout=None)
    for button in (DMFlagBanButton(user_id), DMFlagTimeoutButton(user_id),
                   DMFlagAnalyseButton(user_id), DMFlagDismissButton(user_id)):
        view.add_item(button)
    return view
