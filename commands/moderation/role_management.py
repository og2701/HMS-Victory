from discord import Embed, utils, ButtonStyle, ui
import asyncio

from commands.moderation.anti_raid import QUARANTINE_ROLE_ID


def _is_quarantined(member) -> bool:
    return any(getattr(r, "id", None) == QUARANTINE_ROLE_ID for r in getattr(member, "roles", ()))


async def updateRoleAssignments(interaction, role_name: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You do not have permission to use this command."
        )
        return

    guild = interaction.guild
    role = utils.get(guild.roles, name=role_name)
    if role is None:
        await interaction.response.send_message(f"Role '{role_name}' not found.")
        return

    # Quarantined members are being held on purpose - handing them a role in bulk is the
    # one thing that can undo that, and the whole point of a bulk grant is that nobody
    # reads the list first.
    candidates = [member for member in guild.members if role not in member.roles]
    skipped = [member for member in candidates if _is_quarantined(member)]
    members_without_role = [member for member in candidates if member not in skipped]
    member_count = len(members_without_role)

    if member_count == 0:
        note = "All members already have this role."
        if skipped:
            note += f" ({len(skipped)} quarantined member(s) skipped.)"
        await interaction.response.send_message(note)
        return

    if member_count > 50:
        initial_description = (
            f"There are {member_count} members without the role {role_name}."
        )
    else:
        initial_description = " | ".join(
            [str(member) for member in members_without_role]
        )
        if len(initial_description) > 4096:
            initial_description = (
                f"There are {member_count} members without the role {role_name}."
            )

    if skipped:
        initial_description += (
            f"\n\n-# Skipping {len(skipped)} quarantined member(s); "
            "release them first if they should get this role."
        )

    initial_embed = Embed(
        title=f"Members without role __{role.name}__",
        description=initial_description,
        color=0xFFA500,
    )

    button = ui.Button(label="Give Role", style=ButtonStyle.green)

    async def button_callback(interaction):
        await interaction.response.defer()

        button.label = "Processing..."
        button.style = ButtonStyle.grey
        button.disabled = True

        processing_view = ui.View()
        processing_view.add_item(button)

        processing_embed = Embed(
            title="Processing...",
            description="Assigning roles, please wait...",
            color=0x808080,
        )

        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=processing_embed,
            view=processing_view,
        )

        batch_size = 10
        delay = 1.2
        members_given_role = []

        for i in range(0, len(members_without_role), batch_size):
            batch = members_without_role[i : i + batch_size]

            for member in batch:
                # Re-checked here as well as when the list was built: a bulk grant can sit
                # unpressed for a while, and somebody quarantined in the meantime must not
                # be handed the role anyway.
                if _is_quarantined(member):
                    continue
                await member.add_roles(role)
                members_given_role.append(member)

            await asyncio.sleep(delay)

        final_description = (
            f"Done. Given role __{role.name}__ to {len(members_given_role)} members."
        )
        if skipped:
            final_description += f"\nSkipped {len(skipped)} quarantined member(s)."

        final_embed = Embed(
            title="Role Assignment Complete",
            description=final_description,
            color=0x00FF00,
        )

        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=final_embed, view=None
        )

    button.callback = button_callback

    view = ui.View()
    view.add_item(button)

    await interaction.response.send_message(embed=initial_embed, view=view)
