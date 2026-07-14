"""Ephemeral, owner-only Discord UI for the durable notification inbox."""

import logging
import re

import discord

from lib.features.inbox import (
    clear_read_notifications,
    count_notifications,
    count_unread_notifications,
    list_notifications,
    mark_all_notifications_read,
)


log = logging.getLogger(__name__)
PAGE_SIZE = 5
ACCENT = discord.Colour(0x5865F2)
_DISCORD_JUMP_URL = re.compile(
    r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/"
    r"channels/(?:@me|\d+)/\d+/\d+/?",
    re.IGNORECASE,
)


def _safe_text(value, limit: int, *, single_line: bool = False) -> str:
    """Escape stored text so it cannot create mentions or alter inbox markdown."""
    text = "".join(
        character
        for character in str(value or "")
        if character in "\n\t" or ord(character) >= 32
    ).replace("\r", "")
    if single_line:
        text = " ".join(text.split())
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _safe_jump_url(value) -> str | None:
    """Return only canonical Discord message links, never arbitrary stored URLs."""
    if not value:
        return None
    candidate = str(value).strip()
    return candidate if _DISCORD_JUMP_URL.fullmatch(candidate) else None


class InboxView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.page = 0
        self.total = 0
        self.unread = 0
        self.total_pages = 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who opened this inbox can use it.",
                ephemeral=True,
            )
            return False
        return True

    def build_embed(self) -> discord.Embed:
        self.total = count_notifications(self.owner_id)
        self.unread = count_unread_notifications(self.owner_id)
        self.total_pages = max(1, (self.total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, self.total_pages - 1))

        notifications = list_notifications(
            self.owner_id,
            limit=PAGE_SIZE,
            offset=self.page * PAGE_SIZE,
        )
        self._sync_buttons()

        embed = discord.Embed(
            title="📬 Notification Inbox",
            description=f"**{self.unread:,} unread** · {self.total:,} total",
            colour=ACCENT,
        )
        if not notifications:
            embed.description += "\n\nYour inbox is empty. New notices will appear here even if a DM cannot be delivered."

        for notification in notifications:
            state = "🔵 Unread" if notification.is_unread else "⚪ Read"
            category = _safe_text(notification.category, 80, single_line=True) or "General"
            title = _safe_text(notification.title, 180, single_line=True) or "Notification"
            body = _safe_text(notification.body, 680) or "No details provided."
            created = max(0, notification.created_at)
            value = (
                f"**{title}**\n{body}\n"
                f"-# <t:{created}:f> · <t:{created}:R>"
            )
            jump_url = _safe_jump_url(notification.jump_url)
            if jump_url:
                value += f" · [Jump to message]({jump_url})"
            embed.add_field(
                name=f"{state} · {category}"[:256],
                value=value[:1024],
                inline=False,
            )

        embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages}")
        return embed

    def _sync_buttons(self) -> None:
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1
        self.mark_all_button.disabled = self.unread == 0
        self.clear_read_button.disabled = self.total == self.unread

    async def _edit(self, interaction: discord.Interaction) -> None:
        try:
            embed = self.build_embed()
        except Exception:
            log.exception("Failed to refresh notification inbox")
            await interaction.response.send_message(
                "The inbox could not be refreshed. Please try again in a moment.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=0)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        await self._edit(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self._edit(interaction)

    @discord.ui.button(label="Mark all read", style=discord.ButtonStyle.primary, row=0)
    async def mark_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            mark_all_notifications_read(self.owner_id)
        except Exception:
            log.exception("Failed to mark notification inbox read")
            await interaction.response.send_message(
                "Those notifications could not be updated. Please try again.",
                ephemeral=True,
            )
            return
        await self._edit(interaction)

    @discord.ui.button(label="Clear read", style=discord.ButtonStyle.danger, row=0)
    async def clear_read_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            clear_read_notifications(self.owner_id)
        except Exception:
            log.exception("Failed to clear read notifications")
            await interaction.response.send_message(
                "Read notifications could not be cleared. Please try again.",
                ephemeral=True,
            )
            return
        await self._edit(interaction)


async def handle_inbox_command(interaction: discord.Interaction) -> None:
    """Open the invoking user's inbox as an ephemeral message."""
    view = InboxView(interaction.user.id)
    try:
        embed = view.build_embed()
    except Exception:
        log.exception("Failed to open notification inbox")
        await interaction.response.send_message(
            "Your notification inbox is temporarily unavailable.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
