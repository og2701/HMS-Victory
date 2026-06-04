"""HMS Victory - National Lottery (shared pooled draw).

Players buy tickets (LOTTERY_TICKET_PRICE each) into a shared round. A round draws when
it sells out (LOTTERY_TICKET_CAP) OR at the weekly time (Sunday 8pm UK), whichever comes
first - but a sold-out round never reopens until the next weekly tick. The winner is
drawn weighted by ticket count and takes the pot minus a LOTTERY_RAKE_PCT bank cut.

Economy (UKP conserved; bank is the house): each ticket -> bank via remove_bb; the prize
is paid from the bank via credit_from_bank; the rake is simply the slice of the pot the
bank never pays back. The board + winner announcement live in CHANNELS.VOTING.

State lives in two tables (database.py): lottery_rounds (one per round) and
lottery_entries (aggregated tickets per user per round). draw_round is idempotent (only a
status='open' round draws), so the sellout draw and the weekly draw can never double-pay.
"""

import io
import time
import random
import logging
from datetime import datetime, timedelta

import discord
from discord import Interaction
import pytz

from database import DatabaseManager
from lib.economy.economy_manager import get_bb, remove_bb
from commands.economy.casino_base import credit_from_bank
from lib.core.file_operations import read_html_template

logger = logging.getLogger(__name__)
ACCENT = discord.Colour(0xD4AF37)  # brass

_ROUND_COLS = ("id,status,ticket_price,ticket_cap,rake_pct,draw_ts,created_at,drawn_at,"
               "winner_id,winning_ticket,pot,prize,message_id,channel_id")
_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


# ---------------------------------------------------------------------------
# Round lifecycle / DB helpers
# ---------------------------------------------------------------------------
def _lottery_channel(client):
    from config import CHANNELS
    ch = client.get_channel(CHANNELS.VOTING)
    return ch  # may be None; caller fetches if needed


def _next_draw_ts() -> int:
    """Unix ts of the next weekly draw (e.g. next Sunday 20:00 Europe/London)."""
    import config
    tz = pytz.timezone("Europe/London")
    now = datetime.now(tz)
    target = _DOW.get(getattr(config, "LOTTERY_DRAW_DOW", "sun"), 6)
    hour = getattr(config, "LOTTERY_DRAW_HOUR", 20)
    minute = getattr(config, "LOTTERY_DRAW_MINUTE", 0)
    days_ahead = (target - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return int(candidate.timestamp())


def _row_to_round(row):
    if not row:
        return None
    return dict(zip(_ROUND_COLS.split(","), row))


def get_round(round_id):
    return _row_to_round(DatabaseManager.fetch_one(
        f"SELECT {_ROUND_COLS} FROM lottery_rounds WHERE id = ?", (round_id,)))


def get_open_round():
    return _row_to_round(DatabaseManager.fetch_one(
        f"SELECT {_ROUND_COLS} FROM lottery_rounds WHERE status = 'open' ORDER BY id DESC LIMIT 1"))


def latest_round():
    return _row_to_round(DatabaseManager.fetch_one(
        f"SELECT {_ROUND_COLS} FROM lottery_rounds ORDER BY id DESC LIMIT 1"))


def tickets_sold(round_id) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT COALESCE(SUM(tickets), 0) FROM lottery_entries WHERE round_id = ?", (round_id,))
    return int(row[0]) if row else 0


def user_tickets(round_id, user_id) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT tickets FROM lottery_entries WHERE round_id = ? AND user_id = ?",
        (round_id, str(user_id)))
    return int(row[0]) if row else 0


def create_round():
    import config
    now = int(time.time())
    rid = DatabaseManager.execute_insert(
        "INSERT INTO lottery_rounds (status, ticket_price, ticket_cap, rake_pct, draw_ts, created_at) "
        "VALUES ('open', ?, ?, ?, ?, ?)",
        (config.LOTTERY_TICKET_PRICE, config.LOTTERY_TICKET_CAP, config.LOTTERY_RAKE_PCT,
         _next_draw_ts(), now))
    return get_round(rid)


# ---------------------------------------------------------------------------
# Buying tickets
# ---------------------------------------------------------------------------
def buy_tickets(rnd, user_id, qty: int):
    """Charge for and record ``qty`` tickets. Returns (ok, message, sold_out, new_sold).

    Clamps to the tickets remaining in the pool. Money only moves on success.
    """
    price = rnd["ticket_price"]
    cap = rnd["ticket_cap"]
    sold = tickets_sold(rnd["id"])
    remaining = cap - sold
    if remaining <= 0:
        return False, "🎟️ This round is **sold out** - the draw is on its way!", False, sold
    qty = max(1, min(int(qty), remaining))
    cost = qty * price
    if get_bb(user_id) < cost:
        return False, f"You need **{cost:,} UKPence** for {qty} ticket(s) - you don't have enough.", False, sold
    if not remove_bb(user_id, cost, reason="Lottery ticket"):
        return False, "Couldn't take your payment - nothing was charged.", False, sold
    DatabaseManager.execute(
        "INSERT INTO lottery_entries (round_id, user_id, tickets) VALUES (?, ?, ?) "
        "ON CONFLICT(round_id, user_id) DO UPDATE SET tickets = tickets + ?",
        (rnd["id"], str(user_id), qty, qty))
    new_sold = sold + qty
    mine = user_tickets(rnd["id"], user_id)
    word = "ticket" if qty == 1 else "tickets"
    msg = (f"✅ Bought **{qty} {word}** for **{cost:,} UKPence**. "
           f"You now hold **{mine}** ticket(s) this round - good luck! 🍀")
    return True, msg, new_sold >= cap, new_sold


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _pick_winner(round_id):
    """Weighted pick across all tickets. Returns (winner_id, winning_ticket, total) or
    (None, 0, 0) if no tickets were sold."""
    entries = DatabaseManager.fetch_all(
        "SELECT user_id, tickets FROM lottery_entries WHERE round_id = ? ORDER BY rowid", (round_id,))
    total = sum(int(t) for _, t in entries)
    if total <= 0:
        return None, 0, total
    winning_ticket = random.randint(1, total)
    cum = 0
    for uid, t in entries:
        cum += int(t)
        if winning_ticket <= cum:
            return uid, winning_ticket, total
    return entries[-1][0], winning_ticket, total  # rounding safety


async def draw_round(client, round_id):
    """Pick a winner, pay the prize and announce. Idempotent: only an 'open' round draws,
    so the sellout path and the weekly job can't double-pay. Does NOT open a new round."""
    rnd = get_round(round_id)
    if not rnd or rnd["status"] != "open":
        return rnd
    price = rnd["ticket_price"]
    rake = rnd["rake_pct"]
    winner_id, winning_ticket, total = _pick_winner(round_id)
    now = int(time.time())

    if total <= 0:
        DatabaseManager.execute(
            "UPDATE lottery_rounds SET status='drawn', drawn_at=?, pot=0, prize=0 WHERE id=?", (now, round_id))
        rnd = get_round(round_id)
        await _refresh_board(client, round_id)
        await _announce(client, rnd, no_winner=True)
        return rnd

    pot = total * price
    prize = pot * (100 - rake) // 100  # rake stays in the bank
    DatabaseManager.execute(
        "UPDATE lottery_rounds SET status='drawn', drawn_at=?, winner_id=?, winning_ticket=?, pot=?, prize=? WHERE id=?",
        (now, str(winner_id), winning_ticket, pot, prize, round_id))
    credit_from_bank(int(winner_id), prize, reason="Lottery win")
    rnd = get_round(round_id)
    await _refresh_board(client, round_id)
    await _announce(client, rnd, no_winner=False)
    logger.info("Lottery round %s drawn: winner %s, ticket %s/%s, prize %s.",
                round_id, winner_id, winning_ticket, total, prize)
    return rnd


async def open_round(client, *, post: bool = True):
    rnd = create_round()
    if post:
        await _post_board(client, rnd)
    return rnd


async def weekly_draw_job(client):
    """Weekly: draw the open round (if any) then open a fresh one for next week."""
    import config
    if not getattr(config, "LOTTERY_ENABLED", True):
        return
    rnd = get_open_round()
    if rnd:
        await draw_round(client, rnd["id"])
    await open_round(client, post=True)


async def ensure_started(client):
    """On startup: if no lottery round has ever existed, open the first one. A sold-out
    round that's waiting for the weekly tick is left alone (no open round = intended)."""
    import config
    if not getattr(config, "LOTTERY_ENABLED", True):
        return
    if latest_round() is None:
        await open_round(client, post=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _draw_value(draw_ts) -> str:
    tz = pytz.timezone("Europe/London")
    return datetime.fromtimestamp(draw_ts, tz).strftime("%a %-d %b · %H:%M")


async def render_board(rnd) -> io.BytesIO:
    from lib.core.image_processing import screenshot_html
    price = rnd["ticket_price"]
    cap = rnd["ticket_cap"]
    rake = rnd["rake_pct"]
    drawn = rnd["status"] == "drawn"

    if drawn:
        pot = rnd["pot"] or 0
        sold = pot // price if price else 0
    else:
        sold = tickets_sold(rnd["id"])
        pot = sold * price
    remain = max(0, cap - sold)
    pct = min(100, round(sold * 100 / cap)) if cap else 0

    if drawn and rnd["winner_id"]:
        winner = (f'<div class="winner"><div class="h">🎉 We have a winner!</div>'
                  f'<div class="who">Ticket #{rnd["winning_ticket"]:,} of {sold:,}</div>'
                  f'<div class="sub">Prize: {rnd["prize"]:,} UKPence</div></div>')
        label, note = "Final Jackpot", f"Drawn · {100 - rake}% to the winner"
        draw_label, draw_value = "Drawn", _draw_value(rnd["drawn_at"] or rnd["draw_ts"])
    elif drawn:
        winner = ('<div class="winner"><div class="h">No entries</div>'
                  '<div class="sub">No tickets were sold this round.</div></div>')
        label, note = "Jackpot", "Round closed"
        draw_label, draw_value = "Drawn", _draw_value(rnd["drawn_at"] or rnd["draw_ts"])
    else:
        winner = ""
        label, note = "This Week's Jackpot", f"Winner takes {100 - rake}% · {rake}% to the house"
        draw_label, draw_value = "Draws", _draw_value(rnd["draw_ts"])

    html = (read_html_template("templates/lottery.html")
            .replace("{{JACKPOT_LABEL}}", label)
            .replace("{{POT}}", f"{pot:,}")
            .replace("{{POT_NOTE}}", note)
            .replace("{{SOLD}}", f"{sold:,}")
            .replace("{{CAP}}", f"{cap:,}")
            .replace("{{REMAIN}}", f"{remain:,}")
            .replace("{{PROGRESS_PCT}}", str(pct))
            .replace("{{PRICE}}", f"{price:,}")
            .replace("{{DRAW_LABEL}}", draw_label)
            .replace("{{DRAW_VALUE}}", draw_value)
            .replace("{{WINNER}}", winner))
    return await screenshot_html(html, size=(820, 1200), element_selector=".board")


def _native_text(rnd) -> str:
    price = rnd["ticket_price"]
    cap = rnd["ticket_cap"]
    if rnd["status"] == "drawn":
        if rnd["winner_id"]:
            return (f"## 🎟️ National Lottery — Round #{rnd['id']} (Drawn)\n"
                    f"🎉 <@{rnd['winner_id']}> won **{rnd['prize']:,} UKPence** "
                    f"(ticket #{rnd['winning_ticket']:,}).")
        return f"## 🎟️ National Lottery — Round #{rnd['id']} (Drawn)\nNo tickets were sold."
    sold = tickets_sold(rnd["id"])
    pot = sold * price
    return (f"## 🎟️ National Lottery — Round #{rnd['id']}\n"
            f"**Jackpot: {pot:,} UKPence** · {sold:,}/{cap:,} tickets sold\n"
            f"Tickets **{price:,} UKPence** each · draw <t:{rnd['draw_ts']}:R>")


def _board_text(rnd) -> str:
    """Live Components V2 text under the image (countdown that the picture can't show)."""
    price = rnd["ticket_price"]
    if rnd["status"] == "drawn":
        if rnd["winner_id"]:
            return (f"🎉 **Round #{rnd['id']} drawn** — <@{rnd['winner_id']}> won "
                    f"**{rnd['prize']:,} UKPence** with ticket **#{rnd['winning_ticket']:,}**.\n"
                    f"-# A new round opens at the next weekly draw.")
        return f"**Round #{rnd['id']} drawn** — no tickets were sold.\n-# A new round opens at the next weekly draw."
    sold = tickets_sold(rnd["id"])
    pot = sold * price
    return (f"🎟️ **Round #{rnd['id']}** · Jackpot **{pot:,} UKPence** · "
            f"draw <t:{rnd['draw_ts']}:R> (or when it sells out).\n"
            f"-# Tickets {price:,} UKPence each. Buy below — good luck! 🇬🇧")


def _action_row(rnd) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    rid = rnd["id"]
    if rnd["status"] == "open":
        buy = discord.ui.Button(label="Buy Tickets", emoji="🎟️", style=discord.ButtonStyle.success,
                                custom_id=f"lottery:{rid}:buy")
        buy.callback = _make_cb(rid, "buy")
        row.add_item(buy)
        mine = discord.ui.Button(label="My Tickets", emoji="🎫", style=discord.ButtonStyle.secondary,
                                 custom_id=f"lottery:{rid}:mine")
        mine.callback = _make_cb(rid, "mine")
        row.add_item(mine)
    else:
        done = discord.ui.Button(label="Round Drawn", emoji="🏁", style=discord.ButtonStyle.secondary,
                                 custom_id=f"lottery:{rid}:done", disabled=True)
        row.add_item(done)
    odds = discord.ui.Button(label="Odds", emoji="📖", style=discord.ButtonStyle.secondary,
                             custom_id=f"lottery:{rid}:odds")
    odds.callback = _make_cb(rid, "odds")
    row.add_item(odds)
    return row


async def build_board_layout(rnd):
    import config
    view = discord.ui.LayoutView(timeout=None)
    files = []
    used_image = False
    if getattr(config, "LOTTERY_IMAGE_ENABLED", True):
        try:
            img = await render_board(rnd)
            files = [discord.File(img, filename="lottery.png")]
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media="attachment://lottery.png")
            view.add_item(gallery)
            used_image = True
        except Exception:
            logger.warning("Lottery board render failed; using native layout.", exc_info=True)
    container = discord.ui.Container(accent_colour=ACCENT)
    container.add_item(discord.ui.TextDisplay(_board_text(rnd) if used_image else _native_text(rnd)))
    view.add_item(container)
    view.add_item(_action_row(rnd))
    return view, files


def build_board_controls(rnd) -> discord.ui.LayoutView:
    """Buttons-only view for reattaching the persistent board after a restart."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(_action_row(rnd))
    return view


# ---------------------------------------------------------------------------
# Board posting / refreshing
# ---------------------------------------------------------------------------
async def _post_board(client, rnd):
    from config import CHANNELS
    channel = client.get_channel(CHANNELS.VOTING)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNELS.VOTING)
        except Exception:
            logger.warning("Lottery: VOTING channel unavailable; cannot post board.")
            return
    try:
        view, files = await build_board_layout(rnd)
        msg = await channel.send(view=view, files=files)
        DatabaseManager.execute(
            "UPDATE lottery_rounds SET message_id=?, channel_id=? WHERE id=?",
            (str(msg.id), str(channel.id), rnd["id"]))
        try:
            client.add_view(view, message_id=msg.id)
        except Exception:
            pass
    except Exception:
        logger.error("Lottery: failed to post board for round %s.", rnd["id"], exc_info=True)


async def _refresh_board(client, round_id):
    """Re-render the stored board message for a round (after a buy or a draw)."""
    rnd = get_round(round_id)
    if not rnd or not rnd["message_id"] or not rnd["channel_id"]:
        return
    try:
        channel = client.get_channel(int(rnd["channel_id"])) or await client.fetch_channel(int(rnd["channel_id"]))
        msg = await channel.fetch_message(int(rnd["message_id"]))
        view, files = await build_board_layout(rnd)
        await msg.edit(view=view, attachments=files)
        try:
            client.add_view(view, message_id=msg.id)
        except Exception:
            pass
    except Exception:
        logger.warning("Lottery: could not refresh board for round %s.", round_id, exc_info=True)


async def _announce(client, rnd, *, no_winner: bool):
    from config import CHANNELS
    channel = client.get_channel(CHANNELS.VOTING)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNELS.VOTING)
        except Exception:
            return
    if no_winner:
        await channel.send(
            f"🎟️ **National Lottery — Round #{rnd['id']}** drew with **no tickets sold**, so there's no winner. "
            f"A fresh round is open — get your tickets in!")
        return
    sold = (rnd["pot"] // rnd["ticket_price"]) if rnd["ticket_price"] else 0
    try:
        await channel.send(
            content=(f"🎉🎟️ **NATIONAL LOTTERY — Round #{rnd['id']}** 🎟️🎉\n"
                     f"Congratulations <@{rnd['winner_id']}>, you won **{rnd['prize']:,} UKPence** "
                     f"with ticket **#{rnd['winning_ticket']:,}** of {sold:,}! 🍀\n"
                     f"-# A new round opens at the next weekly draw (Sunday 8pm UK). 🇬🇧"),
            allowed_mentions=discord.AllowedMentions(users=True))
    except Exception:
        logger.error("Lottery: failed to announce winner for round %s.", rnd["id"], exc_info=True)


# ---------------------------------------------------------------------------
# Interaction handling
# ---------------------------------------------------------------------------
def _make_cb(round_id, action):
    async def _cb(interaction: Interaction):
        await _handle_action(interaction, round_id, action)
    return _cb


async def _handle_action(interaction: Interaction, round_id, action):
    if action == "odds":
        await _show_odds(interaction, round_id)
        return
    if action == "mine":
        n = user_tickets(round_id, interaction.user.id)
        sold = tickets_sold(round_id)
        odds = (f"{n / sold * 100:.1f}%" if sold else "—")
        await interaction.response.send_message(
            f"🎫 You hold **{n}** ticket(s) in round #{round_id}"
            + (f" — about a **{odds}** chance of winning right now." if n else " — buy some below to enter!"),
            ephemeral=True)
        return
    if action == "buy":
        rnd = get_round(round_id)
        if not rnd or rnd["status"] != "open":
            await interaction.response.send_message("🎟️ This round is closed. Run `/lottery` for the live one.", ephemeral=True)
            return
        await interaction.response.send_modal(BuyTicketsModal(round_id))
        return


async def _show_odds(interaction: Interaction, round_id):
    rnd = get_round(round_id) or {}
    import config
    price = rnd.get("ticket_price", config.LOTTERY_TICKET_PRICE)
    cap = rnd.get("ticket_cap", config.LOTTERY_TICKET_CAP)
    rake = rnd.get("rake_pct", config.LOTTERY_RAKE_PCT)
    await interaction.response.send_message(
        "## 🎟️ National Lottery — How it works\n"
        f"- Tickets are **{price:,} UKPence** each — buy as many as you like.\n"
        f"- Every ticket is one entry: more tickets = higher chance. Your odds are "
        f"**your tickets ÷ all tickets sold**.\n"
        f"- The round draws when it **sells out ({cap:,} tickets)** or at the **weekly draw "
        f"(Sunday 8pm UK)** — whichever comes first.\n"
        f"- The winner takes **{100 - rake}%** of the pot; **{rake}%** goes to the house bank.\n"
        f"- A sold-out round won't reopen until the next weekly draw.\n"
        "-# Please gamble responsibly. 🇬🇧",
        ephemeral=True)


class BuyTicketsModal(discord.ui.Modal, title="Buy lottery tickets"):
    def __init__(self, round_id):
        super().__init__()
        self.round_id = round_id
        self.qty = discord.ui.TextInput(label="How many tickets?", placeholder="e.g. 5",
                                        required=True, max_length=6)
        self.add_item(self.qty)

    async def on_submit(self, interaction: Interaction):
        raw = str(self.qty.value).replace(",", "").strip()
        try:
            qty = int(raw)
        except ValueError:
            await interaction.response.send_message("Please enter a whole number of tickets.", ephemeral=True)
            return
        if qty <= 0:
            await interaction.response.send_message("You must buy at least one ticket.", ephemeral=True)
            return
        rnd = get_round(self.round_id)
        if not rnd or rnd["status"] != "open":
            await interaction.response.send_message("🎟️ This round is closed. Run `/lottery` for the live one.", ephemeral=True)
            return
        ok, msg, sold_out, _ = buy_tickets(rnd, interaction.user.id, qty)
        await interaction.response.send_message(msg, ephemeral=True)
        if ok:
            await _refresh_board(interaction.client, self.round_id)
            if sold_out:
                # Sellout draw - does NOT open a new round (weekly tick does that).
                await draw_round(interaction.client, self.round_id)


# ---------------------------------------------------------------------------
# Slash command
# ---------------------------------------------------------------------------
async def handle_lottery_command(interaction: Interaction):
    import config
    if not getattr(config, "LOTTERY_ENABLED", True):
        await interaction.response.send_message("🎟️ The lottery is currently closed.", ephemeral=True)
        return
    rnd = get_open_round()
    if rnd is None:
        last = latest_round()
        if last and last["status"] == "drawn" and last["winner_id"]:
            await interaction.response.send_message(
                f"🎟️ The last round was won by <@{last['winner_id']}> "
                f"(**{last['prize']:,} UKPence**). The next round opens at the weekly draw "
                f"(Sunday 8pm UK).", ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(
                "🎟️ No lottery round is open right now — the next one opens at the weekly draw (Sunday 8pm UK).",
                ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    view, files = await build_board_layout(rnd)
    await interaction.followup.send(view=view, files=files, ephemeral=True)
