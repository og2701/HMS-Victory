import logging
import discord
from discord import app_commands, Interaction
from config import ROLES

logger = logging.getLogger(__name__)

MEMBER_ROLE_ID = 1142491622563643442


class ServerVerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Click to Enter Server",
        style=discord.ButtonStyle.success,
        emoji="🇬🇧",
        custom_id="server_verification_button"
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        role = interaction.guild.get_role(MEMBER_ROLE_ID)
        if not role:
            await interaction.response.send_message(
                "❌ Configuration error: Member role not found. Please contact staff.",
                ephemeral=True
            )
            return

        if role in member.roles:
            await interaction.response.send_message(
                "✅ You are already verified and have full access to the server!",
                ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason="Verification button in rules")
            await interaction.response.send_message(
                "🇬🇧 **Welcome to UK place!** You have been verified and now have access to all channels. Enjoy your stay!",
                ephemeral=True
            )
        except Exception as e:
            logger.error("Failed to assign Member role to %s: %s", member.id, e)
            await interaction.response.send_message(
                f"❌ Failed to assign role: {e}. Please contact a moderator.",
                ephemeral=True
            )


def setup_verification_commands(tree: app_commands.CommandTree, client: discord.Client):
    @tree.command(
        name="setup-verification-card",
        description="Post the official server access verification embed & button (Staff only)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup_verification_card(interaction: Interaction, channel: discord.TextChannel = None):
        target_channel = channel or interaction.channel
        staff_roles = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE}
        if not any(r.id in staff_roles for r in interaction.user.roles):
            await interaction.response.send_message(
                "❌ Only staff members can post the verification card.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🇬🇧 Welcome to UK place!",
            description=(
                "Welcome to the official **UK place** community server!\n\n"
                "📜 **Server Access**\n"
                "To prevent automated spam accounts and protect the server, please click the button below to confirm you are human and unlock all server channels.\n\n"
                "By clicking below, you agree to follow the server rules and Discord Terms of Service."
            ),
            color=0x012169  # Royal Navy Blue
        )
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_footer(text="UK place • Click below to enter")

        view = ServerVerificationView()
        await target_channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            f"✅ Verification card posted in {target_channel.mention}!",
            ephemeral=True
        )
