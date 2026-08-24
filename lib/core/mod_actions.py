"""Ban / time out / analyse / ignore buttons for automated moderation reports.

Every automated report has the same problem: it is a judgement made by a machine, it has a
shelf life, and if acting on it means going somewhere else then it expires unanswered. So
the actions sit on the report itself.

One set of buttons serves every report. The custom_id carries both the member and a KIND -
`mod:ban:dmspam:123` - and the kind is only used to pick the wording of the ban notice. That
keeps it to four DynamicItem classes registered once at boot, rather than four more for each
new detector, and it means a report posted before the last restart still works: there is no
state anywhere, only what is written in the custom_id.

Banning always DMs the appeal first. These bans are issued on a detector's judgement rather
than on anything a moderator watched happen, and the usual innocent explanation - a hijacked
account, someone who just clicked quickly - is one only the person themselves can give. The
DM has to go out BEFORE the ban: afterwards we no longer share a guild and Discord refuses to
deliver it.
"""

import logging
from datetime import timedelta
from typing import Any

import discord

logger = logging.getLogger(__name__)

TIMEOUT_HOURS = 24
NO_PINGS = discord.AllowedMentions.none()


class Kind:
    """What a report is about: the audit-log reason, and what the banned member is told."""

    def __init__(self, slug: str, audit: str, ban_dm: str):
        self.slug, self.audit, self.ban_dm = slug, audit, ban_dm


DM_SPAM = Kind(
    "dmspam",
    "Discord unusual DM activity flag",
    "## You've been banned from UK Place\n"
    "Discord's own systems flagged your account for sending an unusual volume of direct "
    "messages. That is almost always either a scam account or an account somebody else has "
    "taken control of.\n\n"
    "**If your account was hacked, say so.** Change your password and turn on two-factor "
    "authentication first, then appeal below - we would rather have you back than leave a "
    "hijacked account banned.")

ONBOARDING = Kind(
    "onboard",
    "Automated onboarding selections",
    "## You've been banned from UK Place\n"
    "Your account answered the server's onboarding questions the way a script does - taking "
    "every option in a question that only has one answer, or getting through the lot in a "
    "couple of seconds.\n\n"
    "**If you just clicked quickly, say so.** This was a judgement about how the answers "
    "arrived rather than about anything you said, and it can be wrong - appeal below and a "
    "person will read it.")

JOIN_WATCH = Kind(
    "joinwatch",
    "Join-watch screening",
    "## You've been banned from UK Place\n"
    "Your first messages in the server were screened before anyone had met you, and they "
    "matched what the server blocks outright - recruiting strangers for off-platform work, "
    "scam offers, or hostility aimed at the place itself.\n\n"
    "**If that has read you wrong, say so.** Appeal below and a person will read it "
    "properly rather than a screener.")

KINDS = {k.slug: k for k in (DM_SPAM, ONBOARDING, JOIN_WATCH)}
_FALLBACK = Kind("unknown", "Automated moderation report", DM_SPAM.ban_dm)


def _kind(slug) -> Kind:
    return KINDS.get(str(slug), _FALLBACK)


def _is_staff(user) -> bool:
    from lib.core.behaviour_watch import is_staff
    return is_staff(user)


async def _settle(interaction, note: str, kind: str, user_id: int) -> None:
    """Write what was done onto the report and grey the buttons out.

    Greyed rather than removed: a handled report that has lost its buttons reads as though
    it was never actionable, and you can no longer see what the other options had been.
    """
    embeds = list(getattr(interaction.message, "embeds", None) or [])
    if not embeds:
        # A Components V2 report keeps its text in the layout rather than an embed, so
        # there is nothing to annotate and no way to rebuild somebody else's view from
        # here. Say it in the channel instead, hung off the report so it reads as part
        # of it rather than as a loose message.
        try:
            await interaction.message.reply(note, allowed_mentions=NO_PINGS)
        except Exception:
            logger.debug("could not note the outcome on the report", exc_info=True)
        return
    try:
        embed = embeds[0]
        embed.add_field(name="Handled", value=note, inline=False)
        embed.colour = 0x95A5A6
        await interaction.message.edit(embed=embed,
                                       view=action_view(kind, user_id, disabled=True))
    except Exception:
        logger.debug("could not settle the moderation report", exc_info=True)


class _MemberAction(discord.ui.DynamicItem[discord.ui.Button], template=r"$"):
    """Shared plumbing. Subclasses supply the template, the look and the callback."""

    def __init__(self, kind: str, user_id: Any, verb: str, label: str, emoji: str,
                 style: discord.ButtonStyle):
        self.kind = str(kind)
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label=label, emoji=emoji, style=style,
            custom_id=f"mod:{verb}:{self.kind}:{self.user_id}"))

    async def _reject_non_staff(self, interaction) -> bool:
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

    async def _done(self, interaction, note: str) -> None:
        await _settle(interaction, note, self.kind, self.user_id)


class ModBanButton(_MemberAction, template=r"mod:ban:(?P<kind>\w+):(?P<uid>\d+)"):
    def __init__(self, kind="", user_id=0):
        super().__init__(kind, user_id, "ban", "Ban", "🔨", discord.ButtonStyle.danger)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["kind"], match["uid"])

    async def callback(self, interaction):
        if await self._reject_non_staff(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        kind = _kind(self.kind)

        told = False
        member = await self._member(interaction)
        if member is not None:
            from commands.moderation.join_clusters import send_ban_appeal_dm
            told = await send_ban_appeal_dm(member, kind.ban_dm)

        reason = f"{kind.audit} · banned by {interaction.user}"
        try:
            # By id rather than by member object: they are often gone by the time anyone
            # looks at the report, and the ban is still worth having.
            await interaction.guild.ban(discord.Object(id=self.user_id),
                                        reason=reason[:500], delete_message_seconds=0)
        except Exception as e:
            await interaction.followup.send(f"Could not ban them: {e}", ephemeral=True)
            return
        await self._done(interaction, f"🔨 Banned by {interaction.user.mention}"
                                      + ("" if told else " · could not DM them the appeal"))
        await interaction.followup.send(
            "Banned." + (" They have the appeal button." if told
                         else " Their DMs are closed, so no appeal notice reached them."),
            ephemeral=True)


class ModTimeoutButton(_MemberAction, template=r"mod:to:(?P<kind>\w+):(?P<uid>\d+)"):
    def __init__(self, kind="", user_id=0):
        super().__init__(kind, user_id, "to", f"Time out {TIMEOUT_HOURS}h", "⏳",
                         discord.ButtonStyle.primary)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["kind"], match["uid"])

    async def callback(self, interaction):
        if await self._reject_non_staff(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = await self._member(interaction)
        if member is None:
            await interaction.followup.send("They are not in the server any more.",
                                            ephemeral=True)
            return
        reason = f"{_kind(self.kind).audit} · timed out by {interaction.user}"
        try:
            await member.timeout(discord.utils.utcnow() + timedelta(hours=TIMEOUT_HOURS),
                                 reason=reason[:500])
        except Exception as e:
            await interaction.followup.send(f"Could not time them out: {e}", ephemeral=True)
            return
        await self._done(interaction,
                         f"⏳ Timed out {TIMEOUT_HOURS}h by {interaction.user.mention}")
        await interaction.followup.send(f"Timed out for {TIMEOUT_HOURS}h.", ephemeral=True)


class ModAnalyseButton(_MemberAction, template=r"mod:analyse:(?P<kind>\w+):(?P<uid>\d+)"):
    def __init__(self, kind="", user_id=0):
        super().__init__(kind, user_id, "analyse", "Analyse", "🔎",
                         discord.ButtonStyle.secondary)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["kind"], match["uid"])

    async def callback(self, interaction):
        member = await self._member(interaction)
        if member is None:
            await interaction.response.send_message(
                "They are not in the server any more, so there is nothing to read.",
                ephemeral=True)
            return
        # handle_analyse_user runs its own permission check and its own defer, and it is a
        # narrower permission than staff - leave both to it.
        from commands.moderation.user_analysis import handle_analyse_user
        await handle_analyse_user(interaction, member)


class ModIgnoreButton(_MemberAction, template=r"mod:ignore:(?P<kind>\w+):(?P<uid>\d+)"):
    def __init__(self, kind="", user_id=0):
        super().__init__(kind, user_id, "ignore", "Ignore", "✅",
                         discord.ButtonStyle.success)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["kind"], match["uid"])

    async def callback(self, interaction):
        if await self._reject_non_staff(interaction):
            return
        await interaction.response.defer()
        await self._done(interaction, f"✅ Left alone by {interaction.user.mention}")


BUTTONS = (ModBanButton, ModTimeoutButton, ModAnalyseButton, ModIgnoreButton)


def action_view(kind, user_id, disabled: bool = False) -> discord.ui.View:
    """The action row for one member on one kind of report."""
    slug = kind.slug if isinstance(kind, Kind) else str(kind)
    view = discord.ui.View(timeout=None)
    for cls in BUTTONS:
        button = cls(slug, user_id)
        button.item.disabled = disabled
        view.add_item(button)
    return view
