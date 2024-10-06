from discord import Embed, ButtonStyle, Interaction, Forbidden, InteractionType
from discord.ui import View, Button
from config import ROLE_BUTTONS


async def persistantRoleButtons(interaction: Interaction):
    role_embed = Embed(
        title="Get Roles",
        description="Select the buttons below to assign yourself a role.",
        color=0xFFA500,  # Orange color
    )

    view = View()
    for role_id, role_info in ROLE_BUTTONS.items():
        role_embed.add_field(
            name=role_info["name"],
            value=role_info.get("description", "No description available"),
            inline=False,
        )
        button = Button(
            style=ButtonStyle.primary,
            label=role_info["name"],
            custom_id=f"role_{role_id}",
        )
        view.add_item(button)

    await interaction.response.send_message(
        embed=role_embed, view=view, ephemeral=False
    )


async def handleRoleButtonInteraction(interaction: Interaction):
    # Check if the interaction is of type component and access the component's custom ID via interaction.data
    if (
        interaction.type == InteractionType.component
        and "custom_id" in interaction.data
    ):
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            role_id = custom_id.split("_")[1]
            role = interaction.guild.get_role(int(role_id))
            if role:
                try:
                    if role in interaction.user.roles:
                        await interaction.user.remove_roles(role)
                        await interaction.response.send_message(
                            f"Role {role.name} removed.", ephemeral=True
                        )
                    else:
                        await interaction.user.add_roles(role)
                        await interaction.response.send_message(
                            f"Role {role.name} assigned.", ephemeral=True
                        )
                except Forbidden:
                    await interaction.response.send_message(
                        "I do not have permission to assign this role.", ephemeral=True
                    )
                except Exception as e:
                    await interaction.response.send_message(
                        "An error occurred while assigning the role.", ephemeral=True
                    )
                    print(e)
            else:
                await interaction.response.send_message(
                    "Role not found.", ephemeral=True
                )
