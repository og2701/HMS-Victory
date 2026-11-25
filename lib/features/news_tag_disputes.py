"""When Jev disagrees with a tag a member put on their own news forum post, Vic posts a card with buttons so a person
decides: take the tag off, or keep it (Oggers, 25 Nov 2026). Jev never removes a member's tag by itself.

The card goes to config.NEWSFEED_TAG_DISPUTE_CHANNEL_ID and pings config.NEWSFEED_TAG_DISPUTE_PING. The thread and tag
ids ride in each button's custom_id, so a card posted before a restart still works."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Tuple

import discord

import config

logger = logging.getLogger(__name__)

MAX_REMOVE_BUTTONS = 3  # plus Remove all and Keep makes one row of 5
DECIDERS = (404634271861571584, 198144909583056898)  # Oggers, Hadidas; anyone who can manage threads also may


def disputed_tags(own: Iterable[str], probs: Optional[Dict[str, float]],
                  below: Optional[float] = None) -> List[Tuple[str, float]]:
    """The member's tags Jev scores under the line, lowest first. A tag Jev has no question for (Megathread, or one
    it didn't answer) is never disputed."""
    if not probs:
        return []
    line = config.NEWSFEED_TAG_DISPUTE_BELOW if below is None else below
    out = [(t, probs[t]) for t in own if t in probs and probs[t] < line]
    return sorted(out, key=lambda x: x[1])


def card_body(thread, author_id, disputes: List[Tuple[str, float]], added: List[str],
              tag_ids: Dict[str, int], ping: Optional[int]) -> dict:
    """The Components V2 message for one disputed post."""
    lines = [f"### 🏷️ Jev disagrees with a member's tag",
             f"{f'<@{ping}> ' if ping and int(ping) != int(author_id) else ''}<@{author_id}> posted <#{thread.id}> \"{thread.name[:120]}\""]
    lines.append("Jev scores " + ", ".join(f"**{t}** at {p:.2f}" for t, p in disputes) + ".")
    if added:
        lines.append(f"-# Vic also added: {', '.join(added)}")
    buttons = [{"type": 2, "style": 4, "label": f"Remove {t}"[:80], "emoji": {"name": "🗑️"},
                "custom_id": f"njt:rm:{thread.id}:{tag_ids[t]}"} for t, _ in disputes[:MAX_REMOVE_BUTTONS]
               if t in tag_ids]
    if len(buttons) > 1:
        buttons.append({"type": 2, "style": 4, "label": "Remove all", "emoji": {"name": "🧹"},
                        "custom_id": f"njt:all:{thread.id}:0"})
    buttons.append({"type": 2, "style": 2, "label": "Keep as is", "emoji": {"name": "👍"},
                    "custom_id": f"njt:keep:{thread.id}:0"})
    container = {"type": 17, "accent_color": 0xF0B232, "components": [
        {"type": 10, "content": "\n".join(lines)},
        {"type": 1, "components": buttons}]}
    return {"flags": 1 << 15, "components": [container],
            "allowed_mentions": {"users": [str(ping)] if ping else []}}  # the author is never pinged (unless it's ping)


async def post_dispute(client, thread, disputes: List[Tuple[str, float]], added: List[str]) -> None:
    if not disputes:
        return
    channel_id = getattr(config, "NEWSFEED_TAG_DISPUTE_CHANNEL_ID", None)
    if not channel_id:
        return
    from discord.http import Route
    tag_ids = {t.name: t.id for t in getattr(thread.parent, "available_tags", [])}
    body = card_body(thread, thread.owner_id, disputes, added, tag_ids,
                     getattr(config, "NEWSFEED_TAG_DISPUTE_PING", None))
    await client.http.request(Route("POST", "/channels/{channel_id}/messages", channel_id=int(channel_id)), json=body)


def _may_decide(user) -> bool:
    if getattr(user, "id", None) in DECIDERS:
        return True
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and (perms.manage_threads or perms.administrator))


def _card_buttons(message) -> Tuple[Optional[discord.ui.LayoutView], List[discord.ui.Button]]:
    try:
        view = discord.ui.LayoutView.from_message(message)
        return view, [c for c in view.walk_children() if isinstance(c, discord.ui.Button)]
    except Exception:
        logger.debug("[NEWSFEED] could not rebuild the tag dispute card", exc_info=True)
        return None, []


def _rm_tag_id(custom_id: str) -> Optional[int]:
    parts = (custom_id or "").split(":")
    return int(parts[3]) if len(parts) == 4 and parts[1] == "rm" else None


class TagDisputeButton(discord.ui.DynamicItem[discord.ui.Button],
                       template=r"njt:(?P<action>rm|all|keep):(?P<thread>\d+):(?P<tag>\d+)"):
    """Remove <tag> takes that one tag off and greys out only its own button, so several can be removed one by one.
    Remove all takes every tag still offered on the card. Keep as is, or nothing left to remove, closes the card."""

    def __init__(self, action: str = "keep", thread_id: int = 0, tag_id: int = 0, label: str = "Keep as is"):
        self.action, self.thread_id, self.tag_id = action, int(thread_id), int(tag_id)
        super().__init__(discord.ui.Button(label=label[:80], custom_id=f"njt:{action}:{self.thread_id}:{self.tag_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["thread"]), int(match["tag"]), getattr(item, "label", None) or "")

    async def callback(self, interaction):
        if not _may_decide(interaction.user):
            await interaction.response.send_message("Only mods can decide this one.", ephemeral=True)
            return
        # Ack first: editing the post's tags can take longer than Discord's 3 seconds.
        await interaction.response.defer()
        view, buttons = _card_buttons(interaction.message)
        open_rm = [b for b in buttons if not b.disabled and _rm_tag_id(b.custom_id) is not None]
        if self.action == "rm":
            targets = {self.tag_id}
        elif self.action == "all":
            targets = {_rm_tag_id(b.custom_id) for b in open_rm} or set()
        else:
            targets = set()
        note = f"👍 {interaction.user.mention} kept the tags on <#{self.thread_id}>."
        done: set = set()
        if targets:
            try:
                thread = interaction.client.get_channel(self.thread_id) or \
                    await interaction.client.fetch_channel(self.thread_id)
                keep = [t for t in thread.applied_tags if t.id not in targets]
                gone = [t for t in thread.applied_tags if t.id in targets]
                if not gone:
                    note = f"Already off <#{self.thread_id}>."
                    done = targets
                elif not keep:
                    await interaction.followup.send(
                        "That would leave the post with no tags, and the forum needs one. Add another tag to it first, "
                        "or remove fewer.", ephemeral=True)
                    return
                else:
                    await thread.edit(applied_tags=keep, reason=f"Tag dispute: removed by {interaction.user}")
                    done = targets
                    note = (f"🗑️ {interaction.user.mention} removed {', '.join(f'**{t.name}**' for t in gone)} "
                            f"from <#{self.thread_id}>.")
            except discord.NotFound:
                note, done = "That post is gone.", targets
                targets = {_rm_tag_id(b.custom_id) for b in open_rm}
            except Exception:
                logger.warning("[NEWSFEED] tag dispute removal failed for %s", self.thread_id, exc_info=True)
                await interaction.followup.send("Couldn't change the tags. Check the logs.", ephemeral=True)
                return
        if view is not None:
            still_open = [b for b in open_rm if _rm_tag_id(b.custom_id) not in done]
            close = self.action == "keep" or not still_open
            for b in buttons:
                tid = _rm_tag_id(b.custom_id)
                if tid is not None and tid in done:
                    b.disabled, b.style = True, discord.ButtonStyle.success
                elif close:
                    b.disabled = True
                    if b.custom_id == self.custom_id:
                        b.style = discord.ButtonStyle.success
            try:
                await interaction.edit_original_response(view=view)
            except Exception:
                logger.debug("[NEWSFEED] could not update the tag dispute card", exc_info=True)
        await interaction.followup.send(note, allowed_mentions=discord.AllowedMentions.none())


BUTTONS = (TagDisputeButton,)
