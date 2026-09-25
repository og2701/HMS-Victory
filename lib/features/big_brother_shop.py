"""Big Brother shop tasks.

One fixed catalogue (categories of items with prices), entered by the host. Each task is a
theme on top of it: a brief, a shared budget, a time limit and a secret shopping list. The
shop lives in the house channel with a Browse button; every purchase comes out of the shared
pot and is announced in the open. When the shop closes (timer, host, or nothing affordable
left) the basket is judged against the secret list and the result posted.

Part of the temporary Big Brother event: gated on config.BIG_BROTHER_ENABLED via the
main module, and the tables sit alongside the other bb_* tables.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import random
import re
from typing import Optional

import discord

import config
from database import DatabaseManager
from lib.features import big_brother as bb

log = logging.getLogger(__name__)

_tables_ready = False
_close_tasks: dict[int, asyncio.Task] = {}
_buying_tasks: dict[int, asyncio.Task] = {}
STATE_VIEWING_SECONDS = "shop_viewing_seconds"  # the host's last choice, pre-filled next time
SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "data", "big_brother_catalogue.txt")
_PRICE_LINE = re.compile(r"^(?P<name>.+?)\s*[-–—:]+\s*£?\s*(?P<price>\d+(?:[.,]\d{1,2})?)\s*$")
_PRICE_FIRST_LINE = re.compile(r"^£?\s*(?P<price>\d+(?:[.,]\d{1,2})?)\s*[-–—:]+\s*(?P<name>.+?)\s*$")
_ROGUE_NAMES = {
    "1000 ukpence",
    "diary leak",
    "immunity token",
    "force nominate",
    "george's catsuit",
    "raw pigeon",
}


def shop_channel_id() -> int:
    """Where the shop posts: the house, unless config points elsewhere (used to test the shop
    in the control channel without touching the live house)."""
    override = getattr(config, "BIG_BROTHER_SHOP_CHANNEL", None)
    return int(override) if override else bb.house_channel_id()


def shop_is_in_the_house() -> bool:
    return shop_channel_id() == bb.house_channel_id()


def shop_in_thread() -> bool:
    """The shop board gets its own thread, so browsing doesn't push the house chat along.
    Purchases and the closing verdict still land in the channel itself, in the open."""
    return bool(getattr(config, "BIG_BROTHER_SHOP_IN_THREAD", True)) and shop_is_in_the_house()


async def shop_channel(client: discord.Client):
    return await bb._channel(client, shop_channel_id())


def can_shop(user_id: int) -> bool:
    return bb.is_housemate(user_id) or bb.is_operator(user_id)


def viewing_seconds() -> int:
    """How long the house can look before it can buy: the host's last choice, else config."""
    saved = bb.get_state(STATE_VIEWING_SECONDS)
    if saved is not None:
        return max(0, int(saved))
    return max(0, int(getattr(config, "BIG_BROTHER_SHOP_VIEWING_SECONDS", 60)))


def ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    with DatabaseManager.transaction() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bb_shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL,
            price INTEGER NOT NULL, position INTEGER NOT NULL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_shop_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, brief TEXT NOT NULL, budget INTEGER NOT NULL,
            required TEXT, status TEXT NOT NULL DEFAULT 'open', opened_at INTEGER NOT NULL,
            closes_at INTEGER, closed_at INTEGER, channel_id TEXT, message_id TEXT, result TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_shop_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
            user_id TEXT NOT NULL, price INTEGER NOT NULL, at INTEGER NOT NULL,
            UNIQUE(task_id, item_id))""")
        try:
            c.execute("ALTER TABLE bb_shop_tasks ADD COLUMN buy_from INTEGER")
        except Exception:
            pass
    _tables_ready = True


# ---------------------------------------------------------------------------
# Money and parsing
# ---------------------------------------------------------------------------

def pounds(pence: int) -> str:
    return f"£{pence / 100:,.2f}"


def parse_money(text: str) -> Optional[int]:
    m = re.match(r"^\s*£?\s*(\d+(?:[.,]\d{1,2})?)\s*$", text or "")
    if not m:
        return None
    return int(round(float(m.group(1).replace(",", ".")) * 100))


def parse_catalogue(text: str) -> list[tuple[str, str, int]]:
    """Lines like "Sausages — £3.00" are items; lines without a price start a category.
    Returns (category, name, pence) in the order given."""
    out, category = [], "Other"
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•* ").strip()
        if not line:
            continue
        m = _PRICE_LINE.match(line)
        if m:
            pence = int(round(float(m.group("price").replace(",", ".")) * 100))
            out.append((category, m.group("name").strip(), pence))
        else:
            m1 = _PRICE_FIRST_LINE.match(line)
            if m1:
                pence = int(round(float(m1.group("price").replace(",", ".")) * 100))
                out.append((category, m1.group("name").strip(), pence))
            else:
                category = line.rstrip(":.,;- ").strip()[:60] or "Other"
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def set_catalogue(entries: list[tuple[str, str, int]], *, replace: bool) -> int:
    ensure_tables()
    with DatabaseManager.transaction() as c:
        if replace:
            c.execute("DELETE FROM bb_shop_items")
            start = 0
        else:
            row = c.execute("SELECT COALESCE(MAX(position), -1) FROM bb_shop_items").fetchone()
            start = int(row[0]) + 1
        for i, (cat, name, pence) in enumerate(entries):
            # Same name in the same category updates the price rather than duplicating.
            existing = c.execute("SELECT id FROM bb_shop_items WHERE category = ? AND lower(name) = lower(?)",
                                 (cat, name)).fetchone()
            if existing:
                c.execute("UPDATE bb_shop_items SET price = ? WHERE id = ?", (pence, existing[0]))
            else:
                c.execute("INSERT INTO bb_shop_items (category, name, price, position) VALUES (?, ?, ?, ?)",
                          (cat, name, pence, start + i))
    return len(entries)


def add_hidden_item(category: str, name: str, pence: int) -> None:
    """One hand-placed item, dropped at a random spot inside its aisle rather than on the end,
    so a special (a diary leak among the cheeses) doesn't stand out as the newest addition."""
    ensure_tables()
    with DatabaseManager.transaction() as c:
        existing = c.execute("SELECT id FROM bb_shop_items WHERE category = ? AND lower(name) = lower(?)",
                             (category, name)).fetchone()
        if existing:
            c.execute("UPDATE bb_shop_items SET price = ? WHERE id = ?", (pence, existing[0]))
            return
        spots = [r[0] for r in c.execute("SELECT position FROM bb_shop_items WHERE category = ?", (category,))]
        if spots:
            pos = random.choice(spots)
            c.execute("UPDATE bb_shop_items SET position = position + 1 WHERE position >= ?", (pos,))
        else:
            pos = int(c.execute("SELECT COALESCE(MAX(position), -1) FROM bb_shop_items").fetchone()[0]) + 1
        c.execute("INSERT INTO bb_shop_items (category, name, price, position) VALUES (?, ?, ?, ?)",
                  (category, name, pence, pos))


def catalogue() -> list[dict]:
    ensure_tables()
    rows = DatabaseManager.fetch_all(
        "SELECT id, category, name, price FROM bb_shop_items ORDER BY position, id")
    return [{"id": r[0], "category": r[1], "name": r[2], "price": int(r[3])} for r in rows]


def categories() -> list[str]:
    seen = []
    for it in catalogue():
        if it["category"] not in seen:
            seen.append(it["category"])
    return seen


def catalogue_text() -> str:
    lines, cat = [], None
    for it in catalogue():
        if it["category"] != cat:
            cat = it["category"]
            lines.append(f"\n**{cat}**")
        lines.append(f"{it['name']} — {pounds(it['price'])}")
    return "\n".join(lines).strip() or "*Empty. Press Add items and paste the list.*"


# ---------------------------------------------------------------------------
# Tasks and purchases
# ---------------------------------------------------------------------------

def _task_row(r) -> Optional[dict]:
    if not r:
        return None
    try:
        required = json.loads(r[3]) if r[3] else []
    except (TypeError, ValueError):
        required = []
    return {"id": r[0], "brief": r[1], "budget": int(r[2]), "required": required, "status": r[4],
            "opened_at": r[5], "closes_at": r[6], "closed_at": r[7],
            "channel_id": int(r[8]) if r[8] else None, "message_id": int(r[9]) if r[9] else None,
            "result": r[10], "buy_from": r[11]}


_TASK_COLS = ("id, brief, budget, required, status, opened_at, closes_at, closed_at, channel_id, message_id, "
              "result, buy_from")


def viewing(task: Optional[dict]) -> bool:
    """Still in the viewing period: the shelves can be browsed but nothing bought."""
    return bool(task and task.get("buy_from") and bb._now() < task["buy_from"])


def current_task() -> Optional[dict]:
    ensure_tables()
    return _task_row(DatabaseManager.fetch_one(
        f"SELECT {_TASK_COLS} FROM bb_shop_tasks WHERE status = 'open' ORDER BY id DESC LIMIT 1"))


def get_task(task_id: int) -> Optional[dict]:
    ensure_tables()
    return _task_row(DatabaseManager.fetch_one(f"SELECT {_TASK_COLS} FROM bb_shop_tasks WHERE id = ?", (int(task_id),)))


def create_task(brief: str, budget: int, required: list[str], closes_at: Optional[int],
                buy_from: Optional[int] = None) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_shop_tasks (brief, budget, required, status, opened_at, closes_at, buy_from) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?)",
        (brief, int(budget), json.dumps(required), bb._now(), closes_at, buy_from))


def set_buy_from(task_id: int, buy_from: Optional[int]) -> None:
    DatabaseManager.execute("UPDATE bb_shop_tasks SET buy_from = ? WHERE id = ?", (buy_from, int(task_id)))


def set_task_message(task_id: int, channel_id: int, message_id: int) -> None:
    DatabaseManager.execute("UPDATE bb_shop_tasks SET channel_id = ?, message_id = ? WHERE id = ?",
                            (str(channel_id), str(message_id), int(task_id)))


def purchases(task_id: int) -> list[dict]:
    rows = DatabaseManager.fetch_all(
        "SELECT p.id, p.item_id, i.name, i.category, p.user_id, p.price, p.at FROM bb_shop_purchases p "
        "JOIN bb_shop_items i ON i.id = p.item_id WHERE p.task_id = ? ORDER BY p.at", (int(task_id),))
    return [{"id": r[0], "item_id": r[1], "name": r[2], "category": r[3], "user_id": int(r[4]),
             "price": int(r[5]), "at": r[6]} for r in rows]


def spent(task_id: int) -> int:
    row = DatabaseManager.fetch_one("SELECT COALESCE(SUM(price), 0) FROM bb_shop_purchases WHERE task_id = ?",
                                    (int(task_id),))
    return int(row[0]) if row else 0


def remaining(task: dict) -> int:
    return task["budget"] - spent(task["id"])


def bought_item_ids(task_id: int) -> set[int]:
    rows = DatabaseManager.fetch_all("SELECT item_id FROM bb_shop_purchases WHERE task_id = ?", (int(task_id),))
    return {int(r[0]) for r in rows}


def buy(task_id: int, item_id: int, user_id: int) -> tuple[bool, str, Optional[dict], int]:
    """Atomic: one of each item, and never over budget. Returns (ok, reason, item, remaining)."""
    ensure_tables()
    with DatabaseManager.transaction() as c:
        t = c.execute("SELECT budget, status, buy_from FROM bb_shop_tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not t or t[1] != "open":
            return False, "The shop is closed.", None, 0
        if t[2] and bb._now() < t[2]:
            return False, f"No buying yet. The tills open <t:{t[2]}:R>.", None, 0
        it = c.execute("SELECT id, category, name, price FROM bb_shop_items WHERE id = ?", (int(item_id),)).fetchone()
        if not it:
            return False, "That item isn't on the shelves.", None, 0
        item = {"id": it[0], "category": it[1], "name": it[2], "price": int(it[3])}
        used = c.execute("SELECT COALESCE(SUM(price), 0) FROM bb_shop_purchases WHERE task_id = ?",
                         (int(task_id),)).fetchone()[0]
        left = int(t[0]) - int(used)
        if c.execute("SELECT 1 FROM bb_shop_purchases WHERE task_id = ? AND item_id = ?",
                     (int(task_id), int(item_id))).fetchone():
            return False, f"{item['name']} has already been bought.", item, left
        if item["price"] > left:
            return False, f"Not enough left in the pot ({pounds(left)}) for {item['name']}.", item, left
        c.execute("INSERT INTO bb_shop_purchases (task_id, item_id, user_id, price, at) VALUES (?, ?, ?, ?, ?)",
                  (int(task_id), int(item_id), str(user_id), item["price"], bb._now()))
        return True, "", item, left - item["price"]


def nothing_affordable(task: dict) -> bool:
    left = remaining(task)
    bought = bought_item_ids(task["id"])
    return all(it["id"] in bought or it["price"] > left for it in catalogue())


def split_basket(task: dict) -> tuple[list[dict], list[dict], list[str]]:
    """(on_list, extras, missing): the basket split against the secret list, loosely matched
    so 'potatoes' counts for 'Maris Piper potatoes'."""
    basket = purchases(task["id"])
    basket_norm = [_norm(p["name"]) for p in basket]
    missing, matched = [], set()
    for req in task["required"]:
        rn = _norm(req)
        hit = next((i for i, b in enumerate(basket_norm) if i not in matched and (b == rn or rn in b or b in rn)), None)
        if hit is None:
            missing.append(req)
        else:
            matched.add(hit)
    on_list = [p for i, p in enumerate(basket) if i in matched]
    extras = [p for i, p in enumerate(basket) if i not in matched]
    return on_list, extras, missing


def judge(task: dict) -> tuple[bool, list[str], list[str]]:
    """(passed, missing, extras) from the split basket."""
    _, extras, missing = split_basket(task)
    return (not missing), missing, [p["name"] for p in extras]


def close_task_db(task_id: int, result: str) -> None:
    DatabaseManager.execute("UPDATE bb_shop_tasks SET status = 'closed', closed_at = ?, result = ? WHERE id = ?",
                            (bb._now(), result, int(task_id)))


def export() -> dict:
    ensure_tables()
    rows = DatabaseManager.fetch_all(f"SELECT {_TASK_COLS} FROM bb_shop_tasks ORDER BY id")
    tasks = []
    for r in rows:
        t = _task_row(r)
        t["purchases"] = purchases(t["id"])
        tasks.append(t)
    return {"catalogue": catalogue(), "tasks": tasks}


# ---------------------------------------------------------------------------
# The shop message in the house channel
# ---------------------------------------------------------------------------

def shop_embed(task: dict, guild: Optional[discord.Guild]) -> discord.Embed:
    left = remaining(task)
    bought = purchases(task["id"])
    e = bb.bb_embed("The shop", task["brief"])
    e.add_field(name="Pot", value=f"**{pounds(left)}** left of {pounds(task['budget'])}", inline=True)
    if viewing(task) and task["status"] == "open":
        e.add_field(name="👀 Viewing only", value=f"Buying opens <t:{task['buy_from']}:R>", inline=True)
    if task["closes_at"]:
        e.add_field(name="Closes", value=f"<t:{task['closes_at']}:R>", inline=True)
    def _lines(items: list[dict]) -> str:
        lines = [f"• {p['name']} - {pounds(p['price'])} ({bb._name(guild, p['user_id'])})" for p in items[-15:]]
        if len(items) > 15:
            lines.insert(0, f"-# …and {len(items) - 15} earlier")
        return "\n".join(lines)[:1024]

    if not bought:
        e.add_field(name="Basket", value="Empty. Press Browse to shop.", inline=False)
    elif task["required"]:
        on_list, extras, _ = split_basket(task)
        if on_list:
            e.add_field(name=f"✅ On the list ({len(on_list)})", value=_lines(on_list), inline=False)
        if extras:
            e.add_field(name=f"🍬 Extras ({len(extras)})", value=_lines(extras), inline=False)
    else:
        e.add_field(name=f"Basket ({len(bought)})", value=_lines(bought), inline=False)
    if task["status"] != "open":
        e.add_field(name="Status", value=f"**Closed.** {task.get('result') or ''}"[:1024], inline=False)
    return e


class ShopBrowseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"bb:shop:browse:(?P<tid>\d+)"):
    def __init__(self, task_id: int, closed: bool = False):
        self.task_id = int(task_id)
        super().__init__(discord.ui.Button(
            label="Shop closed" if closed else "Browse the shop", emoji="🛒", disabled=closed,
            style=discord.ButtonStyle.secondary if closed else discord.ButtonStyle.primary,
            custom_id=f"bb:shop:browse:{self.task_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["tid"]), closed=bool(item.disabled))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not bb.enabled():
            await interaction.response.send_message("Big Brother isn't running right now.", ephemeral=True)
            return
        if not can_shop(interaction.user.id):
            await interaction.response.send_message(f"{bb.EYE} Only housemates can shop.", ephemeral=True)
            return
        task = get_task(self.task_id)
        if not task or task["status"] != "open":
            await interaction.response.send_message("The shop is closed.", ephemeral=True)
            return
        await interaction.response.send_message(
            _aisles_prompt(task), view=_AisleView(task["id"], interaction.guild), ephemeral=True)


def _aisles_prompt(task: dict) -> str:
    if viewing(task):
        return (f"👀 **Viewing only.** Have a look round; the tills open <t:{task['buy_from']}:R>. "
                f"**{pounds(remaining(task))}** in the pot. Pick an aisle.")
    return f"🛒 **{pounds(remaining(task))}** left in the pot. Pick an aisle."


def _shop_view(task: dict) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(ShopBrowseButton(task["id"], closed=task["status"] != "open"))
    return v


async def update_shop_message(client: discord.Client, task: dict) -> None:
    if not task["channel_id"] or not task["message_id"]:
        return
    ch = await bb._channel(client, task["channel_id"])
    if not ch:
        return
    try:
        msg = await ch.fetch_message(task["message_id"])
        await msg.edit(embed=shop_embed(task, bb._guild(client)), view=_shop_view(task))
    except discord.HTTPException as e:
        log.info("Big Brother shop: could not update shop message: %s", e)


# ---------------------------------------------------------------------------
# Browsing (ephemeral): aisle buttons -> item buttons -> buy on press
# ---------------------------------------------------------------------------

def _shelf(task_id: int, category: str) -> str:
    """The aisle written out in full above its buttons: a long name gets cut on a phone-width
    button, so this is where it can always be read."""
    bought = bought_item_ids(task_id)
    lines = []
    for it in catalogue():
        if it["category"] != category:
            continue
        is_bought = it["id"] in bought
        is_rogue = it["name"].strip().lower() in _ROGUE_NAMES
        marker = "⚡ " if (is_rogue and not is_bought) else ""
        strike = "~~" if is_bought else ""
        tick = "~~ ✓" if is_bought else ""
        lines.append(f"{marker}{strike}{pounds(it['price'])} · {it['name']}{tick}")
    return "\n".join(lines[:24])[:1500]


class _AisleView(discord.ui.View):
    def __init__(self, task_id: int, guild):
        super().__init__(timeout=300)
        self.task_id, self.guild = task_id, guild
        bought = bought_item_ids(task_id)
        rogue_cats = {it["category"] for it in catalogue()
                      if it["name"].strip().lower() in _ROGUE_NAMES and it["id"] not in bought}
        for cat in categories()[:25]:
            has_rogue = cat in rogue_cats
            label = f"⚡ {cat}"[:80] if has_rogue else cat[:80]
            style = discord.ButtonStyle.primary if has_rogue else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=label, style=style)

            async def _open(interaction: discord.Interaction, _cat=cat):
                task = get_task(self.task_id)
                if not task or task["status"] != "open":
                    await interaction.response.edit_message(content="The shop is closed.", view=None)
                    return
                await interaction.response.edit_message(
                    content=(f"👀 **{_cat}** · {pounds(remaining(task))} in the pot. Buying opens "
                             f"<t:{task['buy_from']}:R>, then come back and press an item." if viewing(task) else
                             f"🛒 **{_cat}** · {pounds(remaining(task))} left. Press an item to buy it for the house.")
                            + "\n" + _shelf(self.task_id, _cat),
                    view=_ItemView(self.task_id, _cat, self.guild))
            btn.callback = _open
            self.add_item(btn)


class _ItemView(discord.ui.View):
    def __init__(self, task_id: int, category: str, guild):
        super().__init__(timeout=300)
        self.task_id, self.category, self.guild = task_id, category, guild
        task = get_task(task_id)
        left = remaining(task) if task else 0
        bought = bought_item_ids(task_id)
        looking = viewing(task)
        items = [it for it in catalogue() if it["category"] == category][:24]
        for it in items:
            gone = it["id"] in bought
            pricey = it["price"] > left
            is_rogue = it["name"].strip().lower() in _ROGUE_NAMES
            if gone:
                style = discord.ButtonStyle.secondary
            elif is_rogue:
                style = discord.ButtonStyle.danger if pricey else discord.ButtonStyle.primary
            elif looking:
                style = discord.ButtonStyle.secondary
            elif pricey:
                style = discord.ButtonStyle.danger
            else:
                style = discord.ButtonStyle.success

            prefix = "✓ " if gone else ("⚡ " if is_rogue else "")
            btn = discord.ui.Button(
                # Price first: when a phone cuts a long label short, the price is what survives.
                label=(prefix + f"{pounds(it['price'])} · {it['name']}")[:80],
                style=style,
                disabled=gone or pricey or looking)

            async def _buy(interaction: discord.Interaction, _item=it):
                await interaction.response.defer()
                ok, reason, item, left_now = buy(self.task_id, _item["id"], interaction.user.id)
                if not ok:
                    await interaction.edit_original_response(
                        content=f"❌ {reason}\n" + _shelf(self.task_id, self.category),
                        view=_ItemView(self.task_id, self.category, self.guild))
                    return
                task = get_task(self.task_id)
                bb.log_event("shop_purchase", actor=interaction.user.id, task_id=self.task_id,
                             item=item["name"], price=item["price"], remaining=left_now)
                # The channel itself, not the shop thread: the house watches the pot drain.
                ch = await shop_channel(interaction.client)
                if ch:
                    await bb.bb_send(ch, f"🛒 **{bb._name(interaction.guild, interaction.user.id)}** bought "
                                         f"**{item['name']}** for {pounds(item['price'])}. "
                                         f"**{pounds(left_now)}** left in the pot.")
                await update_shop_message(interaction.client, task)
                await interaction.edit_original_response(
                    content=f"✅ Bought **{item['name']}** for {pounds(item['price'])}. {pounds(left_now)} left. "
                            f"Keep shopping or dismiss this.\n" + _shelf(self.task_id, self.category),
                    view=_ItemView(self.task_id, self.category, self.guild))
                if nothing_affordable(task):
                    asyncio.create_task(close_shop(interaction.client, self.task_id,
                                                   "Nothing left that the house can afford."))
            btn.callback = _buy
            self.add_item(btn)
        back = discord.ui.Button(label="Back to aisles", emoji="↩️", style=discord.ButtonStyle.primary, row=4)

        async def _back(interaction: discord.Interaction):
            task = get_task(self.task_id)
            left_now = remaining(task) if task else 0
            await interaction.response.edit_message(
                content=_aisles_prompt(task) if task else f"🛒 **{pounds(left_now)}** left in the pot. Pick an aisle.",
                view=_AisleView(self.task_id, self.guild))
        back.callback = _back
        self.add_item(back)


# ---------------------------------------------------------------------------
# Open / close
# ---------------------------------------------------------------------------

async def open_shop(client: discord.Client, brief: str, budget: int, required: list[str],
                    minutes: Optional[int], view_seconds: int = 0) -> tuple[Optional[dict], str]:
    if current_task():
        return None, "A shop is already open. Close it first."
    if not catalogue():
        return None, "The catalogue is empty. Add items first."
    ch = await shop_channel(client)
    if not ch:
        return None, "Shop channel not found."
    # The timer counts buying time, so a viewing period pushes the close back by as much.
    buy_from = bb._now() + view_seconds if view_seconds > 0 else None
    closes_at = (buy_from or bb._now()) + minutes * 60 if minutes else None
    tid = create_task(brief, budget, required, closes_at, buy_from)
    task = get_task(tid)
    thread = await bb.open_house_thread(ch, f"🛒 the shop - {bb.today_label()}",
                                        "Big Brother: the shop") if shop_in_thread() else None
    where = thread or ch
    opening = (f"**The shop is open for viewing.** Look round now; the tills open <t:{buy_from}:R>."
               if buy_from else "**The shop is open.**")
    msg = await bb.bb_send(where, content=f"{'' if thread else bb._role_mention()}{bb.EYE} {opening}",
                           embed=shop_embed(task, bb._guild(client)), view=_shop_view(task))
    set_task_message(tid, where.id, msg.id)
    task = get_task(tid)
    if thread:
        await bb.fill_thread(thread)
        await bb.bb_send(ch, f"{bb._role_mention()}{bb.EYE} **The shop is open** in <#{thread.id}>. "
                             f"{pounds(budget)} in the pot. "
                             + (f"Viewing only until <t:{buy_from}:t>, then buying opens. " if buy_from else "")
                             + "Everything you buy still shows up here.")
    bb.log_event("shop_opened", task_id=tid, brief=brief, budget=budget, required=required, closes_at=closes_at,
                 buy_from=buy_from, thread_id=thread.id if thread else None)
    schedule_close(client, task)
    schedule_buying(client, task)
    asyncio.create_task(bb.refresh_panel(client))
    return task, ""


async def close_shop(client: discord.Client, task_id: int, reason: str) -> Optional[dict]:
    task = get_task(task_id)
    if not task or task["status"] != "open":
        return None
    passed, missing, extras = judge(task)
    left = remaining(task)
    verdict = ("PASSED" if passed else "FAILED") if task["required"] else ""
    close_task_db(task_id, f"{reason} {verdict}".strip())
    b = _buying_tasks.pop(task_id, None)
    if b and b is not asyncio.current_task() and not b.done():
        b.cancel()
    t = _close_tasks.pop(task_id, None)
    # When the timer itself is what called us, cancelling it would cancel this very coroutine
    # at the next await and silently drop the announcement and DM.
    if t and t is not asyncio.current_task() and not t.done():
        t.cancel()
    task = get_task(task_id)
    guild = bb._guild(client)
    await update_shop_message(client, task)
    ch = await shop_channel(client)
    if ch:
        if task["required"]:
            text = (f"🛒 **The shop is closed.** {reason}\n\n"
                    + (f"✅ **Task complete.** The house got everything on the list with {pounds(left)} to spare."
                       if passed else
                       f"❌ **Task failed.** Missing: {', '.join(missing)}."))
        else:
            text = f"🛒 **The shop is closed.** {reason} {pounds(left)} was left unspent."
        await bb.bb_send(ch, f"{bb._role_mention()}{text}")
    by_person: dict[int, list[str]] = {}
    for p in purchases(task_id):
        by_person.setdefault(p["user_id"], []).append(f"{p['name']} ({pounds(p['price'])})")
    detail = "\n".join(f"**{bb._name(guild, u)}**: " + ", ".join(items) for u, items in by_person.items()) or "Nobody bought anything."
    if task["required"]:
        detail += f"\n\n**Required:** {', '.join(task['required'])}\n**Missing:** {', '.join(missing) or 'none'}"
        detail += f"\n**Temptations bought:** {', '.join(extras) or 'none'}"
    await bb.notify_host(client, embed=bb.bb_embed(f"Shop task #{task_id} {verdict or 'closed'}", detail[:3900]))
    if task["channel_id"] and task["channel_id"] != shop_channel_id():
        shelf = await bb._channel(client, task["channel_id"])
        if isinstance(shelf, discord.Thread):
            try:
                await shelf.edit(archived=True, locked=True, reason="Big Brother: the shop is shut")
            except discord.HTTPException:
                pass
    bb.log_event("shop_closed", task_id=task_id, reason=reason, passed=passed if task["required"] else None,
                 missing=missing, extras=extras, left=left)
    asyncio.create_task(bb.refresh_panel(client))
    return task


def schedule_close(client: discord.Client, task: dict) -> None:
    if not task.get("closes_at") or task["status"] != "open":
        return
    old = _close_tasks.pop(task["id"], None)
    if old and not old.done():
        old.cancel()

    async def _wait():
        try:
            await asyncio.sleep(max(0, task["closes_at"] - bb._now()))
            await close_shop(client, task["id"], "Time's up.")
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Big Brother shop: timed close failed")
    _close_tasks[task["id"]] = asyncio.create_task(_wait())


async def start_buying(client: discord.Client, task_id: int, *, early: bool = False) -> bool:
    """End the viewing period: the tills open, the house is told, the board redraws."""
    task = get_task(task_id)
    if not task or task["status"] != "open" or not task.get("buy_from"):
        return False
    if early:
        set_buy_from(task_id, bb._now())
        # Pull the close in by however much viewing was skipped, so buying time stays as set.
        if task["closes_at"]:
            DatabaseManager.execute("UPDATE bb_shop_tasks SET closes_at = ? WHERE id = ?",
                                    (task["closes_at"] - max(0, task["buy_from"] - bb._now()), task_id))
        b = _buying_tasks.pop(task_id, None)
        if b and b is not asyncio.current_task() and not b.done():
            b.cancel()
        task = get_task(task_id)
        schedule_close(client, task)
    await update_shop_message(client, task)
    ch = await shop_channel(client)
    where = f" in <#{task['channel_id']}>" if task["channel_id"] and task["channel_id"] != shop_channel_id() else ""
    if ch:
        await bb.bb_send(ch, f"{bb._role_mention()}🛒 **The tills are open!** Buying has started{where}. "
                             f"{pounds(remaining(task))} in the pot.")
    bb.log_event("shop_buying_opened", task_id=task_id, early=early)
    asyncio.create_task(bb.refresh_panel(client))
    return True


def schedule_buying(client: discord.Client, task: dict) -> None:
    if not viewing(task) or task["status"] != "open":
        return
    old = _buying_tasks.pop(task["id"], None)
    if old and not old.done():
        old.cancel()

    async def _wait():
        try:
            await asyncio.sleep(max(0, task["buy_from"] - bb._now()))
            await start_buying(client, task["id"])
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Big Brother shop: opening the tills failed")
    _buying_tasks[task["id"]] = asyncio.create_task(_wait())


def restore_timers(client: discord.Client) -> None:
    """After a restart, pick the open task's timers back up."""
    task = current_task()
    if task:
        schedule_close(client, task)
        schedule_buying(client, task)


# ---------------------------------------------------------------------------
# Jev sorts and prices bare item names: one call per item, two picks (aisle, price band),
# calibrated on a few priced examples from each existing aisle.
# ---------------------------------------------------------------------------

PRICE_BANDS = [50, 80, 100, 120, 150, 180, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700,
               800, 900, 1000, 1200, 1500, 1800, 2000, 2500]
DEFAULT_AISLES = ["Meat & Fish", "Fruit & Veg", "Misc", "Breakfast & Tea", "Sweets", "Crisps & Snacks",
                  "Drinks & Temptations", "Spices"]
JEV_CONCURRENCY = 6


def bare_item_lines(text: str) -> list[tuple[Optional[str], str]]:
    """For a message with no priced lines at all: every line is an item. A line ending with a
    colon is an aisle hint for the lines under it. Returns (aisle_hint, name) pairs."""
    if parse_catalogue(text):
        return []
    out, hint = [], None
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•* ").strip()
        if not line:
            continue
        if line.endswith(":"):
            hint = line.rstrip(":").strip()[:60] or None
            continue
        out.append((hint, line[:80]))
    return out


def _price_examples() -> dict[str, list[str]]:
    by: dict[str, list[str]] = {}
    for it in catalogue():
        lst = by.setdefault(it["category"], [])
        if len(lst) < 5:
            lst.append(f"{it['name']} {pounds(it['price'])}")
    return by


async def jev_sort_and_price(items: list[tuple[Optional[str], str]]) -> tuple[list[tuple[str, str, int]], int]:
    """(entries, failures). Each entry is (aisle, name, pence). Items Jev couldn't judge are
    counted in failures and left out rather than guessed."""
    from lib.features.mention_signals import MENTION_SIGNALS_MODEL, _post
    key = os.getenv("TYPESAFE_API_KEY")
    if not key:
        return [], len(items)
    aisles = categories() or DEFAULT_AISLES
    examples = _price_examples()
    price_criteria = {pounds(p): None for p in PRICE_BANDS}
    sem = asyncio.Semaphore(JEV_CONCURRENCY)

    async def one(hint: Optional[str], name: str):
        questions = {
            "price": {
                "type": "choice",
                "instructions": "A fair UK supermarket shelf price for one `item` (one normal pack, bottle or unit), "
                                "on the same scale as `examples`. A single veg, tin or sachet is cheap; alcohol, "
                                "joints of meat and multipacks are dear.",
                "criteria": price_criteria,
            },
        }
        if not hint or hint not in aisles:
            questions["aisle"] = {
                "type": "choice",
                "instructions": "Which aisle of the shop does `item` belong in? `examples` shows what each aisle holds.",
                "criteria": {a: None for a in aisles},
            }
        payload = {"model": MENTION_SIGNALS_MODEL,
                   "state": {"item": name, "aisles": aisles, "examples": examples},
                   "questions": questions}
        async with sem:
            body = await _post(payload, key, session=None, timeout=8.0)
        answers = (body or {}).get("answers") or {}
        price_label = (answers.get("price") or {}).get("choice")
        pence = parse_money(price_label or "")
        aisle = hint if hint else (answers.get("aisle") or {}).get("choice")
        if pence is None or not aisle:
            return None
        return (aisle, name, pence)

    results = await asyncio.gather(*(one(h, n) for h, n in items), return_exceptions=True)
    entries = [r for r in results if isinstance(r, tuple)]
    return entries, len(items) - len(entries)


NOT_IN_SHOP = "None of these"
MATCH_CANDIDATES = 150  # the whole catalogue in practice: spelling alone never finds "spuds" -> potatoes


def _candidates(req: str) -> list[str]:
    """The catalogue names most like req: shared words first, then spelling closeness, so
    'carots' still finds Carrots and 'spuds' at least gets the whole veg aisle's best guesses."""
    rn = _norm(req)
    words = set(rn.split())

    def score(name: str) -> float:
        nn = _norm(name)
        overlap = len(words & set(nn.split())) / max(1, len(words))
        return overlap + difflib.SequenceMatcher(None, rn, nn).ratio()
    names = list(dict.fromkeys(it["name"] for it in catalogue()))
    return sorted(names, key=score, reverse=True)[:MATCH_CANDIDATES]


async def resolve_required(required: list[str]) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Map the host's shopping list onto real catalogue names, so a typo or a different
    wording still counts. Exact matches pass straight through; the rest go to Jev with the
    closest candidates. Returns (resolved list, [(typed, matched)] corrections, not found).
    Without Jev, an item is kept as typed and judged by the loose word match."""
    from lib.features.mention_signals import MENTION_SIGNALS_MODEL, _post
    by_norm = {_norm(it["name"]): it["name"] for it in catalogue()}
    key = os.getenv("TYPESAFE_API_KEY")
    sem = asyncio.Semaphore(JEV_CONCURRENCY)

    def loosely_found(req: str) -> bool:
        rn = _norm(req)
        return any(rn in n or n in rn for n in by_norm)

    async def one(req: str) -> tuple[str, Optional[str]]:
        exact = by_norm.get(_norm(req))
        if exact:
            return req, exact
        if not key:
            return req, req if loosely_found(req) else None
        cands = _candidates(req)
        payload = {"model": MENTION_SIGNALS_MODEL,
                   "state": {"wanted": req, "shop_items": cands},
                   "questions": {"match": {
                       "type": "choice",
                       "instructions": "Big Brother typed `wanted` as an item the house must buy. Which of the "
                                       "shop's items is it? Allow for typos, plurals, brand or wording "
                                       f"differences. Pick '{NOT_IN_SHOP}' if none of them is that item.",
                       "criteria": {**{c: None for c in cands}, NOT_IN_SHOP: None}}}}
        try:
            async with sem:
                body = await _post(payload, key, session=None, timeout=8.0)
        except Exception:
            body = None
        choice = (((body or {}).get("answers") or {}).get("match") or {}).get("choice")
        if choice in cands:
            return req, choice
        if choice == NOT_IN_SHOP:
            return req, None
        return req, req if loosely_found(req) else None   # Jev didn't answer: fall back

    results = await asyncio.gather(*(one(r) for r in required))
    resolved = [m for _, m in results if m]
    fixed = [(r, m) for r, m in results if m and m != r]
    missing = [r for r, m in results if not m]
    return list(dict.fromkeys(resolved)), fixed, missing


# ---------------------------------------------------------------------------
# Catalogue capture: the host sends the list as a normal message (or a .txt) in the
# control channel after pressing Add items, and the bot picks it up.
# ---------------------------------------------------------------------------

CAPTURE_KEY = "shop_catalogue_capture"
CAPTURE_WINDOW = 15 * 60


def start_capture(user_id: int, replace: bool) -> None:
    bb.set_state(CAPTURE_KEY, {"user": int(user_id), "replace": bool(replace), "until": bb._now() + CAPTURE_WINDOW})


def pending_capture(user_id: int) -> Optional[dict]:
    cap = bb.get_state(CAPTURE_KEY)
    if not cap or int(cap.get("user", 0)) != int(user_id) or int(cap.get("until", 0)) < bb._now():
        return None
    return cap


async def maybe_capture(client: discord.Client, message: discord.Message) -> bool:
    """Called for every message in the control channel. Returns True if it was a catalogue."""
    cap = pending_capture(message.author.id)
    if not cap:
        return False
    text = message.content or ""
    for att in message.attachments:
        if (att.filename or "").lower().endswith((".txt", ".md", ".csv")) and att.size < 200_000:
            try:
                text += "\n" + (await att.read()).decode("utf-8", errors="replace")
            except discord.HTTPException:
                pass
    entries = parse_catalogue(text)
    judged = ""
    if not entries:
        bare = bare_item_lines(text)
        if not bare:
            return False  # ordinary chat while a capture is pending; keep waiting
        # No prices given: let Jev sort and price them.
        try:
            await message.add_reaction("🤔")
        except discord.HTTPException:
            pass
        entries, failures = await jev_sort_and_price(bare)
        if not entries:
            try:
                await message.reply("🛒 I couldn't price those (is Jev reachable?). Send them with prices, "
                                    "e.g. `Sausages — £3.00`.", mention_author=False)
            except discord.HTTPException:
                pass
            return True
        judged = "\n".join(f"• {n} — {pounds(p)} ({a})" for a, n, p in entries)[:1500]
        if failures:
            judged += f"\n-# {failures} item(s) skipped: Jev wasn't sure."
        judged += "\n-# Don't like a price or aisle? Send that line again with a price and it'll update."
    bb.set_state(CAPTURE_KEY, None)
    set_catalogue(entries, replace=cap["replace"])
    bb.log_event("shop_catalogue_updated", actor=message.author.id, added=len(entries), replaced=cap["replace"],
                 priced_by_jev=bool(judged))
    try:
        await message.reply(
            f"🛒 {'Replaced the catalogue with' if cap['replace'] else 'Added'} **{len(entries)}** item(s). "
            f"Now {len(catalogue())} items in {len(categories())} aisles."
            + (f"\n{judged}" if judged else " Press Catalogue on the panel to check."),
            mention_author=False)
    except discord.HTTPException:
        pass
    return True


# ---------------------------------------------------------------------------
# Control panel actions
# ---------------------------------------------------------------------------

class _OpenShopModal(discord.ui.Modal, title="Open the shop"):
    def __init__(self, on_submit):
        super().__init__()
        self._on_submit = on_submit
        # Discord caps modal labels at 45 characters.
        self.brief = discord.ui.TextInput(label="The task, as the house will see it", style=discord.TextStyle.long,
                                          required=True, max_length=1000)
        total = sum(it["price"] for it in catalogue())
        self.budget = discord.ui.TextInput(label=f"Shared budget (whole shop costs {pounds(total)})"[:45],
                                           required=True, max_length=12, placeholder="e.g. 100 or £75.50")
        self.minutes = discord.ui.TextInput(label="Minutes open (blank = until you close it)",
                                            required=False, max_length=5)
        self.viewing = discord.ui.TextInput(label="Viewing seconds before buying (0 = off)",
                                            required=False, max_length=4, default=str(viewing_seconds()))
        self.required = discord.ui.TextInput(
            label="Task goal: items they MUST buy (1 per line)", style=discord.TextStyle.long, required=False,
            max_length=1000, placeholder="Kept secret. Pass if they buy all of these, e.g.\nWhole chicken\nCarrots")
        for item in (self.brief, self.budget, self.minutes, self.viewing, self.required):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await self._on_submit(interaction, self)


def _catalogue_summary() -> str:
    items = catalogue()
    if not items:
        return "🛒 **Catalogue is empty.** Press Load saved list, or Add items and paste a list."
    counts: dict[str, int] = {}
    for it in items:
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    lines = [f"🛒 **Catalogue:** {len(items)} items in {len(counts)} aisles. Press an aisle to see its prices."]
    lines += [f"• **{cat}** · {n}" for cat, n in counts.items()]
    return "\n".join(lines)


def _aisle_text(category: str) -> str:
    items = [it for it in catalogue() if it["category"] == category]
    return f"🛒 **{category}** ({len(items)})\n" + "\n".join(f"{it['name']} — {pounds(it['price'])}" for it in items)


class _CatalogueView(discord.ui.View):
    """Summary with one button per aisle (rows 0-3), and Add / Load on the last row."""

    def __init__(self, aisle: Optional[str] = None):
        super().__init__(timeout=300)
        cats = categories()[:20]
        for cat in cats:
            btn = discord.ui.Button(label=cat[:80], style=discord.ButtonStyle.primary if cat == aisle
                                    else discord.ButtonStyle.secondary)

            async def _show(interaction: discord.Interaction, _cat=cat):
                await interaction.response.edit_message(content=_aisle_text(_cat)[:1900], view=_CatalogueView(_cat))
            btn.callback = _show
            self.add_item(btn)

        if aisle:
            back = discord.ui.Button(label="All aisles", emoji="↩️", row=4)

            async def _back(interaction: discord.Interaction):
                await interaction.response.edit_message(content=_catalogue_summary(), view=_CatalogueView())
            back.callback = _back
            self.add_item(back)

        full = discord.ui.Button(label="Full list", emoji="📜", row=4)

        async def _full(interaction: discord.Interaction):
            # Everything, one aisle per block, spread over as many ephemeral follow-ups as it takes.
            await interaction.response.defer(ephemeral=True, thinking=True)
            chunks, current = [], ""
            for cat in categories():
                block = _aisle_text(cat) + "\n\n"
                if len(current) + len(block) > 1900:
                    chunks.append(current)
                    current = ""
                current += block
            if current:
                chunks.append(current)
            await interaction.edit_original_response(content=chunks[0].rstrip() if chunks else "Empty.")
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk.rstrip(), ephemeral=True)
        full.callback = _full
        self.add_item(full)

        add = discord.ui.Button(label="Add items", emoji="➕", row=4)
        replace = discord.ui.Button(label="Replace all", emoji="♻️", style=discord.ButtonStyle.danger, row=4)

        async def _capture(interaction: discord.Interaction, replace_all: bool):
            start_capture(interaction.user.id, replace_all)
            await interaction.response.edit_message(
                content=("♻️ **Replacing the catalogue.**" if replace_all else "➕ **Adding to the catalogue.**")
                        + " Now send the list as a normal message in this channel, or attach a .txt file.\n\n"
                        "**With prices**, a line with no price starts an aisle:\n"
                        "```\nMeat & Fish\nWhole chicken — £7.00\nSausages — £3.00\n```"
                        "**Or just names**, and Big Brother's assistant will sort them into aisles and price them "
                        "(a line ending in a colon fixes the aisle for the lines under it):\n"
                        "```\nSweets:\nHaribo\nJelly Babies\nBottle of rum\n```"
                        "I'll pick it up within the next 15 minutes and reply with what I added.",
                view=None)

        async def _add(interaction: discord.Interaction):
            await _capture(interaction, False)

        async def _replace(interaction: discord.Interaction):
            await _capture(interaction, True)
        add.callback, replace.callback = _add, _replace
        self.add_item(add)
        self.add_item(replace)

        if aisle:
            # Row 4 holds five buttons; an aisle only shows once there's a catalogue, so the
            # Load button (for an empty one) gives up its slot to this.
            here = discord.ui.Button(label="Add to this aisle", emoji="🎁", style=discord.ButtonStyle.success, row=4)

            async def _here(interaction: discord.Interaction):
                await interaction.response.send_modal(_AddItemModal(aisle))
            here.callback = _here
            self.add_item(here)
        elif os.path.exists(SEED_FILE) and not catalogue():
            load = discord.ui.Button(label="Load saved list", emoji="📥", row=4)

            async def _load(interaction: discord.Interaction):
                with open(SEED_FILE, encoding="utf-8") as f:
                    await _catalogue_submitted(interaction, f.read(), True)
            load.callback = _load
            self.add_item(load)


class _AddItemModal(discord.ui.Modal, title="Add an item to this aisle"):
    """Hand-placed: no Jev, the host's own name and price, hidden somewhere in the aisle."""

    def __init__(self, aisle: str):
        super().__init__()
        self.aisle = aisle
        # The buy button shows "name £price" and Discord cuts labels at 80 characters.
        self.name = discord.ui.TextInput(label="Item name", max_length=70, required=True,
                                         placeholder="Diary Room Leak: read one anonymous diary entry")
        self.price = discord.ui.TextInput(label="Price (e.g. 30 or £4.50)", max_length=12, required=True)
        self.add_item(self.name)
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction):
        pence = parse_money(self.price.value)
        name = " ".join(self.name.value.split())
        if pence is None or pence <= 0 or not name:
            await interaction.response.send_message("Price needs to be a number like 30 or £4.50.", ephemeral=True)
            return
        add_hidden_item(self.aisle, name, pence)
        bb.log_event("shop_item_added", actor=interaction.user.id, aisle=self.aisle, name=name, price=pence)
        await interaction.response.edit_message(
            content=f"🎁 Added **{name}** ({pounds(pence)}) somewhere in **{self.aisle}**.\n\n"
                    + _aisle_text(self.aisle)[:1700], view=_CatalogueView(self.aisle))


async def _catalogue_submitted(inter: discord.Interaction, text: str, replace: bool):
    await inter.response.defer(ephemeral=True, thinking=True)
    entries = parse_catalogue(text)
    if not entries:
        await inter.edit_original_response(content="No priced lines found. Use `Name — £1.50`, one per line.")
        return
    set_catalogue(entries, replace=replace)
    bb.log_event("shop_catalogue_updated", actor=inter.user.id, added=len(entries), replaced=replace)
    await inter.edit_original_response(
        content=f"{'Replaced with' if replace else 'Added'} {len(entries)} item(s).\n\n" + _catalogue_summary(),
        view=_CatalogueView())


async def act_catalogue(interaction: discord.Interaction):
    """Ephemeral: aisle summary, one button per aisle to see prices, Add / Load buttons."""
    await interaction.response.send_message(_catalogue_summary(), view=_CatalogueView(), ephemeral=True)


async def act_shop(interaction: discord.Interaction):
    """Open a shop task, or close the open one."""
    task = current_task()
    if task and viewing(task):
        view = discord.ui.View(timeout=120)
        go = discord.ui.Button(label="Start buying now", emoji="🛒", style=discord.ButtonStyle.success)
        shut = discord.ui.Button(label="Close shop", style=discord.ButtonStyle.danger)

        async def _go(inter: discord.Interaction):
            await inter.response.defer()
            ok = await start_buying(inter.client, task["id"], early=True)
            await inter.edit_original_response(content="The tills are open." if ok else "The shop has already moved on.",
                                               view=None)

        async def _shut(inter: discord.Interaction):
            await inter.response.defer()
            await close_shop(inter.client, task["id"], "Big Brother has closed the shop.")
            await inter.edit_original_response(content="Shop closed.", view=None)
        go.callback, shut.callback = _go, _shut
        view.add_item(go)
        view.add_item(shut)
        await interaction.response.send_message(
            f"👀 The shop is in its viewing period; buying opens <t:{task['buy_from']}:R>.", view=view, ephemeral=True)
        return
    if task:
        async def yes(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True)
            await close_shop(inter.client, task["id"], "Big Brother has closed the shop.")
            await inter.edit_original_response(content="Shop closed. The result is posted in the house and in your DMs.",
                                               view=None)
        await interaction.response.send_message(
            f"Close the shop now? {pounds(remaining(task))} is still in the pot. The basket will be judged "
            f"against the secret list and the result announced.",
            view=bb._Confirm(yes, "Close shop"), ephemeral=True)
        return
    if not catalogue():
        await interaction.response.send_message("The catalogue is empty. Press Catalogue and paste the items first.",
                                                ephemeral=True)
        return

    async def submitted(inter: discord.Interaction, modal: _OpenShopModal):
        await inter.response.defer(ephemeral=True, thinking=True)
        budget = parse_money(modal.budget.value)
        if budget is None or budget <= 0:
            await inter.edit_original_response(content="Budget needs to be a number like 100 or £75.50.")
            return
        minutes = None
        if (modal.minutes.value or "").strip():
            if not modal.minutes.value.strip().isdigit():
                await inter.edit_original_response(content="Minutes needs to be a whole number, or blank.")
                return
            minutes = int(modal.minutes.value.strip())
        view_raw = (modal.viewing.value or "").strip() or "0"
        if not view_raw.isdigit():
            await inter.edit_original_response(content="Viewing seconds needs to be a whole number (0 for none).")
            return
        view_seconds = int(view_raw)
        bb.set_state(STATE_VIEWING_SECONDS, view_seconds)
        required = [ln.strip() for ln in re.split(r"[\n,]", modal.required.value or "") if ln.strip()]
        # Keep the items that aren't in the shop too: the task can then never pass, which the
        # host is warned about below and may well want (a trick task).
        resolved, fixed, unknown = await resolve_required(required)
        required = resolved + unknown
        task, err = await open_shop(inter.client, modal.brief.value.strip(), budget, required, minutes, view_seconds)
        if err:
            await inter.edit_original_response(content=err)
            return
        note = f"Shop #{task['id']} is open in the house with {pounds(budget)}."
        if view_seconds:
            note += f" Viewing only for {view_seconds}s; the panel's shop button can start buying early."
        if required:
            note += f" Items they must buy: {', '.join(required)}."
        if fixed:
            note += "\n🔎 Matched to the catalogue: " + ", ".join(f"{a} → {b}" for a, b in fixed) + "."
        if unknown:
            note += f"\n⚠️ Not in the catalogue (can never be bought): {', '.join(unknown)}."
        await inter.edit_original_response(content=note)

    await interaction.response.send_modal(_OpenShopModal(submitted))
