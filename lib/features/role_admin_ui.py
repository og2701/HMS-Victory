"""'roleadd' Deputy-PM tool: give a role to one or more members via a searchable
role dropdown. Mirrors the ukpadd flow (trigger in event_handlers, UI here)."""

import logging
from typing import List, Optional

import discord

from config import ROLES

logger = logging.getLogger(__name__)


def _is_deputy_pm(user) -> bool:
    roles = getattr(user, "roles", None)
    return bool(roles) and any(role.id == ROLES.DEPUTY_PM for role in roles)


def _bot_can_assign(guild: discord.Guild, role: discord.Role) -> Optional[str]:
    """None if the bot can give this role, else a human-readable reason it can't."""
    me = guild.me
    if role.is_default():
        return "that's the @everyone role"
    if role.managed:
        return "it's managed by an integration (bot/booster/subscription role)"
    if not me.guild_permissions.manage_roles:
        return "I don't have the Manage Roles permission"
    if role >= me.top_role:
        return "it's higher than my own top role"
    return None


class RoleAddView(discord.ui.View):
    """UserSelect (who) + searchable RoleSelect (which) + an Add button. Members can
    be pre-filled from message mentions, in which case the user picker is dropped."""

    def __init__(self, author_id: int, members: Optional[List[discord.Member]] = None,
                 role: Optional[discord.Role] = None):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.members: List[discord.Member] = list(members) if members else []
        self.role: Optional[discord.Role] = role
        if self.members:
            # Recipients already chosen via mentions - no need for the picker.
            self.remove_item(self.user_select)
        self._sync()

    def _sync(self):
        self.apply.disabled = not (self.members and self.role)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the command initiator can use this menu.", ephemeral=True)
            return False
        if not _is_deputy_pm(interaction.user):
            await interaction.response.send_message(
                "❌ Only the Deputy PM can add roles.", ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Select members",
                       min_values=1, max_values=25, row=0)
    async def user_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.members = list(select.values)
        self._sync()
        await interaction.response.edit_message(view=self)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Search for a role to add…",
                       min_values=1, max_values=1, row=1)
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.role = select.values[0]
        self._sync()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Add role", style=discord.ButtonStyle.success, row=2, disabled=True)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role = self.role
        reason = _bot_can_assign(guild, role)
        if reason:
            await interaction.response.send_message(
                f"❌ I can't assign **{role.name}** — {reason}.", ephemeral=True)
            return

        # Adding to up to 25 members is several API calls; ack first to dodge the 3s timeout.
        await interaction.response.edit_message(
            content=f"Adding **{role.name}** to {len(self.members)} member(s)…", view=None)

        added, already, failed = [], [], []
        for m in self.members:
            member = guild.get_member(m.id) or m
            if role in getattr(member, "roles", []):
                already.append(member)
                continue
            try:
                await member.add_roles(role, reason=f"roleadd by {interaction.user} (Deputy PM)")
                added.append(member)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning("roleadd: failed to give %s to %s: %s", role.name, member.id, e)
                failed.append(member)

        def _names(members):
            shown = ", ".join(m.mention for m in members[:10])
            if len(members) > 10:
                shown += f" and {len(members) - 10} more"
            return shown or "—"

        embed = discord.Embed(
            title="✅ Role Added" if added else "⚠️ No roles added",
            description=f"**{role.name}** → **{len(added)}** member(s).",
            color=role.color if (added and role.color.value) else (0x00FF00 if added else 0xF59E0B),
        )
        if added:
            embed.add_field(name="Added", value=_names(added), inline=False)
        if already:
            embed.add_field(name="Already had it", value=_names(already), inline=False)
        if failed:
            embed.add_field(name="Failed", value=_names(failed), inline=False)
        embed.set_footer(text=f"Authorised by Deputy PM {interaction.user.display_name}")

        await interaction.edit_original_response(
            content=None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
        self.stop()
