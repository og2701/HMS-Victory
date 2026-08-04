"""Host a generated image on Discord's CDN so ephemeral messages can show it quickly.

Discord loads image ATTACHMENTS on ephemeral messages badly - they sit on a grey
placeholder for seconds however good the connection, and a puzzle board that redraws on
every answer pays that cost every single time. An embed pointing at a normal CDN URL has
no such problem, because by then it's just an image on a link like any other.

So: post the PNG once into a quiet channel, keep the attachment URL, and put that in the
ephemeral's embed. The player sees the picture arrive with the message.

The hosting message is left in place on purpose. Discord's attachment URLs are signed and
time-limited, and deleting the message can invalidate them early - so the post stays, in a
channel nobody reads, rather than risking a board that renders as a broken image.
"""

import io
import logging

import discord

logger = logging.getLogger(__name__)


def _host_channel_id() -> int:
    import config
    explicit = int(getattr(config, "IMAGE_HOST_CHANNEL_ID", 0) or 0)
    if explicit:
        return explicit
    return int(getattr(getattr(config, "CHANNELS", None), "BOT_USAGE_LOG", 0) or 0)


_logged_destination = False


async def host_image(client, data: io.BytesIO, filename: str = "board.png") -> str | None:
    """Return a CDN URL for `data`, or None if it couldn't be hosted.

    Falls back to None rather than raising: the caller should then send the image as a
    plain attachment (slower, but a slow board beats no board).
    """
    global _logged_destination
    try:
        cid = _host_channel_id()
        if not cid or client is None:
            return None
        ch = client.get_channel(cid)
        if ch is None:
            ch = await client.fetch_channel(cid)

        # Say once, loudly, where these are actually going. Getting this wrong is silent
        # otherwise - the images just turn up somewhere unexpected and nothing errors.
        if not _logged_destination:
            _logged_destination = True
            parent = getattr(ch, "parent", None)
            logger.info(
                "[IMAGE HOST] id=%s -> %s #%s%s",
                cid, type(ch).__name__, getattr(ch, "name", "?"),
                f" (thread under #{getattr(parent, 'name', '?')})" if parent else
                " (NOT a thread - this is a channel)",
            )

        data.seek(0)
        msg = await ch.send(file=discord.File(data, filename))
        if msg.attachments:
            return msg.attachments[0].url
    except Exception:
        logger.warning("image hosting failed; falling back to an attachment", exc_info=True)
    return None
