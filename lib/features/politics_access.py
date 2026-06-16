"""Native permission control for the #politics channel.

Visibility is handled by Discord's own channel permissions now (no JSON whitelist, no
listening for and deleting unauthorised messages):

- /politics-toggle <user> smart-toggles a *member-specific* view_channel overwrite. If
  the toggle would put the member back to their natural/default state (whatever their
  roles already grant), the overwrite is deleted entirely so the channel settings stay
  clean.
- The Deputy PM types `polcontrol` / `politics` / `pol` to flip the *@everyone* overwrite,
  opening/closing the channel for the whole server (behind a confirm prompt).
"""

import discord

from config import ROLES, CHANNELS

CONTROL_TRIGGERS = {"polcontrol", "politics", "pol"}


def has_natural_access(channel: discord.abc.GuildChannel, member: discord.Member) -> bool:
    """Whether ``member`` can view ``channel`` from roles + @everyone alone, IGNORING any
    member-specific overwrite.

    This is the member's "default" state. /politics-toggle uses it to decide whether a
    toggle should write an explicit overwrite or simply clear one - clearing is correct
    exactly when the desired visibility already matches this natural state.

    Mirrors Discord's permission resolution (base role perms -> @everyone overwrite ->
    aggregated role overwrites), but deliberately stops short of the member overwrite.
    """
    guild = channel.guild
    if member.id == guild.owner_id:
        return True
    base = member.guild_permissions
    if base.administrator:
        return True

    allow = bool(base.view_channel)  # guild-level, from the member's roles incl. @everyone
    overwrites = channel.overwrites

    everyone_ow = overwrites.get(guild.default_role)
    if everyone_ow is not None and everyone_ow.view_channel is not None:
        allow = everyone_ow.view_channel

    # Aggregate the member's role overwrites: Discord applies all denies then all allows,
    # so an allow on any of their roles wins over a deny on another.
    member_role_ids = {r.id for r in member.roles}
    role_allow = role_deny = False
    for target, ow in overwrites.items():
        if not isinstance(target, discord.Role) or target.id == guild.default_role.id:
            continue
        if target.id not in member_role_ids:
            continue
        if ow.view_channel is True:
            role_allow = True
        elif ow.view_channel is False:
            role_deny = True
    allow = (allow and not role_deny) or role_allow
    return bool(allow)


async def toggle_member_access(channel, member, *, actor=None):
    """Flip ``member``'s effective visibility of ``channel`` via a member overwrite.

    Returns ``(granted, natural, cleared)``:
      granted - the member's new effective visibility (True = can see the channel)
      natural - whether they have natural access from roles alone
      cleared - whether we removed the overwrite (back to natural) rather than setting one
    """
    natural = has_natural_access(channel, member)
    ow = channel.overwrites_for(member)
    current = ow.view_channel  # True / False / None(=inherit natural)
    current_effective = natural if current is None else current
    granted = not current_effective

    reason = f"politics-toggle by {actor}" if actor is not None else "politics-toggle"

    if granted == natural:
        # Returning them to their default - drop the explicit overwrite. Only delete the
        # whole overwrite if nothing else lives on it, so unrelated perms are preserved.
        ow.update(view_channel=None)
        if ow.is_empty():
            await channel.set_permissions(member, overwrite=None, reason=reason)
        else:
            await channel.set_permissions(member, overwrite=ow, reason=reason)
        return granted, natural, True

    ow.update(view_channel=granted)
    await channel.set_permissions(member, overwrite=ow, reason=reason)
    return granted, natural, False


def is_channel_open(channel) -> bool:
    """Whether @everyone can currently view the channel (the Deputy PM 'unlocked' state)."""
    return channel.overwrites_for(channel.guild.default_role).view_channel is True


async def set_everyone_access(channel, *, unlock: bool, actor=None):
    """Set the @everyone view_channel overwrite to open (True) or close (False) the channel."""
    everyone = channel.guild.default_role
    ow = channel.overwrites_for(everyone)
    ow.update(view_channel=True if unlock else False)
    reason = (f"politics {'unlock' if unlock else 'lock'} by {actor}"
              if actor is not None else None)
    await channel.set_permissions(everyone, overwrite=ow, reason=reason)


class PoliticsControlView(discord.ui.View):
    """Confirm prompt for the Deputy PM unlock/lock command. Only the invoker can act."""

    def __init__(self, channel, author_id: int, *, unlock: bool):
        super().__init__(timeout=60)
        self.channel = channel
        self.author_id = author_id
        self.unlock = unlock
        self.message = None
        confirm = discord.ui.Button(
            label="Unlock" if unlock else "Lock",
            style=discord.ButtonStyle.success if unlock else discord.ButtonStyle.danger,
        )
        confirm.callback = self._confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the Deputy PM who ran this can confirm it.", ephemeral=True)
            return False
        return True

    def _disable(self):
        for child in self.children:
            child.disabled = True

    async def _confirm(self, interaction: discord.Interaction):
        try:
            await set_everyone_access(self.channel, unlock=self.unlock, actor=interaction.user)
        except discord.Forbidden:
            self._disable()
            await interaction.response.edit_message(
                content="I don't have permission to edit that channel's overrides.", view=self)
            self.stop()
            return
        self._disable()
        verb = "unlocked" if self.unlock else "locked"
        await interaction.response.edit_message(
            content=f"✅ {self.channel.mention} has been **{verb}** for the whole server.",
            view=self, allowed_mentions=discord.AllowedMentions.none())
        self.stop()

    async def _cancel(self, interaction: discord.Interaction):
        self._disable()
        await interaction.response.edit_message(content="Cancelled - no changes made.", view=self)
        self.stop()

    async def on_timeout(self):
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(content="⏲️ Timed out - no changes made.", view=self)
            except discord.HTTPException:
                pass


async def handle_politics_control_command(client, message) -> bool:
    """Deputy-PM-only prefix command. Returns True if it handled the message.

    Triggers on an exact 'polcontrol' / 'politics' / 'pol' message and opens a confirm
    prompt to flip the channel open/closed for @everyone.
    """
    if message.author.bot or message.guild is None:
        return False
    if message.content.strip().lower() not in CONTROL_TRIGGERS:
        return False
    if not any(r.id == ROLES.DEPUTY_PM for r in getattr(message.author, "roles", [])):
        return False

    channel = message.guild.get_channel(CHANNELS.POLITICS)
    if channel is None:
        return False

    # Delete the trigger message instantly (like ukpadd/roleadd) so the channel stays clean.
    try:
        await message.delete()
    except discord.HTTPException:
        pass

    currently_open = is_channel_open(channel)
    unlock = not currently_open
    state = "Open" if currently_open else "Restricted"
    action = "Unlock" if unlock else "Lock"
    view = PoliticsControlView(channel, message.author.id, unlock=unlock)
    # allowed_mentions.none() so the wording can never ping anyone (never @everyone).
    view.message = await message.channel.send(
        f"🏛️ {channel.mention} is currently **{state}**. {action} it for **the whole server**?",
        view=view, allowed_mentions=discord.AllowedMentions.none())
    return True
