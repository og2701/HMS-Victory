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
import json
import logging
import os
import re
from typing import Optional

import discord

import config
from database import DatabaseManager
from lib.features import big_brother as bb

log = logging.getLogger(__name__)

_tables_ready = False
_close_tasks: dict[int, asyncio.Task] = {}
SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "data", "big_brother_catalogue.txt")
_PRICE_LINE = re.compile(r"^(?P<name>.+?)\s*[-–—:]+\s*£?\s*(?P<price>\d+(?:[.,]\d{1,2})?)\s*$")


async def shop_channel(client: discord.Client):
    """Where the shop and its announcements post: the house, unless config points elsewhere
    (used to test the shop in the control channel without touching the live house)."""
    override = getattr(config, "BIG_BROTHER_SHOP_CHANNEL", None)
    if override:
        return await bb._channel(client, int(override))
    return await bb.house_channel(client)


def can_shop(user_id: int) -> bool:
    return bb.is_housemate(user_id) or bb.is_operator(user_id)


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
            "result": r[10]}


_TASK_COLS = "id, brief, budget, required, status, opened_at, closes_at, closed_at, channel_id, message_id, result"


def current_task() -> Optional[dict]:
    ensure_tables()
    return _task_row(DatabaseManager.fetch_one(
        f"SELECT {_TASK_COLS} FROM bb_shop_tasks WHERE status = 'open' ORDER BY id DESC LIMIT 1"))


def get_task(task_id: int) -> Optional[dict]:
    ensure_tables()
    return _task_row(DatabaseManager.fetch_one(f"SELECT {_TASK_COLS} FROM bb_shop_tasks WHERE id = ?", (int(task_id),)))


def create_task(brief: str, budget: int, required: list[str], closes_at: Optional[int]) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_shop_tasks (brief, budget, required, status, opened_at, closes_at) VALUES (?, ?, ?, 'open', ?, ?)",
        (brief, int(budget), json.dumps(required), bb._now(), closes_at))


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
        t = c.execute("SELECT budget, status FROM bb_shop_tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not t or t[1] != "open":
            return False, "The shop is closed.", None, 0
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


def judge(task: dict) -> tuple[bool, list[str], list[str]]:
    """(passed, missing, extras): required names against the basket, loosely matched."""
    basket = [p["name"] for p in purchases(task["id"])]
    basket_norm = [_norm(n) for n in basket]
    missing, matched = [], set()
    for req in task["required"]:
        rn = _norm(req)
        hit = next((i for i, b in enumerate(basket_norm) if i not in matched and (b == rn or rn in b or b in rn)), None)
        if hit is None:
            missing.append(req)
        else:
            matched.add(hit)
    extras = [basket[i] for i in range(len(basket)) if i not in matched]
    return (not missing), missing, extras


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
    if task["closes_at"]:
        e.add_field(name="Closes", value=f"<t:{task['closes_at']}:R>", inline=True)
    if bought:
        lines = [f"• {p['name']} - {pounds(p['price'])} ({bb._name(guild, p['user_id'])})" for p in bought[-15:]]
        if len(bought) > 15:
            lines.insert(0, f"-# …and {len(bought) - 15} earlier")
        e.add_field(name=f"Basket ({len(bought)})", value="\n".join(lines)[:1024], inline=False)
    else:
        e.add_field(name="Basket", value="Empty. Press Browse to shop.", inline=False)
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
            f"🛒 **{pounds(remaining(task))}** left in the pot. Pick an aisle.",
            view=_AisleView(task["id"], interaction.guild), ephemeral=True)


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

class _AisleView(discord.ui.View):
    def __init__(self, task_id: int, guild):
        super().__init__(timeout=300)
        self.task_id, self.guild = task_id, guild
        for cat in categories()[:25]:
            btn = discord.ui.Button(label=cat[:80], style=discord.ButtonStyle.secondary)

            async def _open(interaction: discord.Interaction, _cat=cat):
                task = get_task(self.task_id)
                if not task or task["status"] != "open":
                    await interaction.response.edit_message(content="The shop is closed.", view=None)
                    return
                await interaction.response.edit_message(
                    content=f"🛒 **{_cat}** · {pounds(remaining(task))} left. Press an item to buy it for the house.",
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
        items = [it for it in catalogue() if it["category"] == category][:24]
        for it in items:
            gone = it["id"] in bought
            pricey = it["price"] > left
            btn = discord.ui.Button(
                label=(("✓ " if gone else "") + f"{it['name']} {pounds(it['price'])}")[:80],
                style=discord.ButtonStyle.secondary if gone else
                (discord.ButtonStyle.danger if pricey else discord.ButtonStyle.success),
                disabled=gone or pricey)

            async def _buy(interaction: discord.Interaction, _item=it):
                await interaction.response.defer()
                ok, reason, item, left_now = buy(self.task_id, _item["id"], interaction.user.id)
                if not ok:
                    await interaction.edit_original_response(
                        content=f"❌ {reason}", view=_ItemView(self.task_id, self.category, self.guild))
                    return
                task = get_task(self.task_id)
                bb.log_event("shop_purchase", actor=interaction.user.id, task_id=self.task_id,
                             item=item["name"], price=item["price"], remaining=left_now)
                ch = await shop_channel(interaction.client)
                if ch:
                    await bb.bb_send(ch, f"🛒 **{bb._name(interaction.guild, interaction.user.id)}** bought "
                                         f"**{item['name']}** for {pounds(item['price'])}. "
                                         f"**{pounds(left_now)}** left in the pot.")
                await update_shop_message(interaction.client, task)
                await interaction.edit_original_response(
                    content=f"✅ Bought **{item['name']}** for {pounds(item['price'])}. {pounds(left_now)} left. "
                            f"Keep shopping or dismiss this.",
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
                content=f"🛒 **{pounds(left_now)}** left in the pot. Pick an aisle.",
                view=_AisleView(self.task_id, self.guild))
        back.callback = _back
        self.add_item(back)


# ---------------------------------------------------------------------------
# Open / close
# ---------------------------------------------------------------------------

async def open_shop(client: discord.Client, brief: str, budget: int, required: list[str],
                    minutes: Optional[int]) -> tuple[Optional[dict], str]:
    if current_task():
        return None, "A shop is already open. Close it first."
    if not catalogue():
        return None, "The catalogue is empty. Add items first."
    ch = await shop_channel(client)
    if not ch:
        return None, "Shop channel not found."
    closes_at = bb._now() + minutes * 60 if minutes else None
    tid = create_task(brief, budget, required, closes_at)
    task = get_task(tid)
    msg = await bb.bb_send(ch, content=f"{bb._role_mention()}{bb.EYE} **The shop is open.**",
                           embed=shop_embed(task, bb._guild(client)), view=_shop_view(task))
    set_task_message(tid, ch.id, msg.id)
    task = get_task(tid)
    bb.log_event("shop_opened", task_id=tid, brief=brief, budget=budget, required=required, closes_at=closes_at)
    schedule_close(client, task)
    asyncio.create_task(bb.refresh_panel(client))
    return task, ""


async def close_shop(client: discord.Client, task_id: int, reason: str) -> Optional[dict]:
    task = get_task(task_id)
    if not task or task["status"] != "open":
        return None
    passed, missing, extras = judge(task)
    left = remaining(task)
    verdict = ("PASSED" if passed else "FAILED") if task["required"] else "closed"
    close_task_db(task_id, f"{reason} {verdict}.".strip())
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
    await bb.notify_host(client, embed=bb.bb_embed(f"Shop task #{task_id} {verdict}", detail[:3900]))
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


def restore_timers(client: discord.Client) -> None:
    """After a restart, pick the open task's timer back up."""
    task = current_task()
    if task:
        schedule_close(client, task)


# ---------------------------------------------------------------------------
# Control panel actions
# ---------------------------------------------------------------------------

class _CatalogueModal(discord.ui.Modal, title="Shop catalogue"):
    def __init__(self, on_submit):
        super().__init__()
        self._on_submit = on_submit
        # No label on the TextInput itself: the Label wrapper carries it (Discord rejects both).
        self.text = discord.ui.TextInput(
            style=discord.TextStyle.long, required=True, max_length=4000,
            placeholder="Meat & Fish\nWhole chicken — £7.00\nSausages — £3.00\n\nFruit & Veg\nCarrots — £1.00")
        self.replace = discord.ui.Checkbox(default=False)
        self.add_item(discord.ui.Label(text="Items (a line with no price starts an aisle)", component=self.text))
        self.add_item(discord.ui.Label(text="Replace the whole catalogue",
                                       description="Unticked: these are added to what's already there.",
                                       component=self.replace))

    async def on_submit(self, interaction: discord.Interaction):
        await self._on_submit(interaction, self.text.value or "", bool(self.replace.value))


class _OpenShopModal(discord.ui.Modal, title="Open the shop"):
    def __init__(self, on_submit):
        super().__init__()
        self._on_submit = on_submit
        # Discord caps modal labels at 45 characters.
        self.brief = discord.ui.TextInput(label="The task, as the house will see it", style=discord.TextStyle.long,
                                          required=True, max_length=1000)
        self.budget = discord.ui.TextInput(label="Shared budget (e.g. 100 or £75.50)", required=True, max_length=12)
        self.minutes = discord.ui.TextInput(label="Minutes open (blank = until you close it)",
                                            required=False, max_length=5)
        self.required = discord.ui.TextInput(
            label="Secret shopping list (one per line)", style=discord.TextStyle.long, required=False,
            max_length=1000, placeholder="Whole chicken\nMaris Piper potatoes\nCarrots")
        for item in (self.brief, self.budget, self.minutes, self.required):
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

        async def _add(interaction: discord.Interaction):
            await interaction.response.send_modal(_CatalogueModal(_catalogue_submitted))
        add.callback = _add
        self.add_item(add)

        if os.path.exists(SEED_FILE):
            load = discord.ui.Button(label="Load saved list", emoji="📥", row=4)

            async def _load(interaction: discord.Interaction):
                with open(SEED_FILE, encoding="utf-8") as f:
                    await _catalogue_submitted(interaction, f.read(), True)
            load.callback = _load
            self.add_item(load)


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
        required = [ln.strip() for ln in re.split(r"[\n,]", modal.required.value or "") if ln.strip()]
        unknown = [r for r in required if not any(_norm(r) in _norm(it["name"]) or _norm(it["name"]) in _norm(r)
                                                  for it in catalogue())]
        task, err = await open_shop(inter.client, modal.brief.value.strip(), budget, required, minutes)
        if err:
            await inter.edit_original_response(content=err)
            return
        note = f"Shop #{task['id']} is open in the house with {pounds(budget)}."
        if required:
            note += f" Secret list: {', '.join(required)}."
        if unknown:
            note += f"\n⚠️ Not in the catalogue (can never be bought): {', '.join(unknown)}."
        await inter.edit_original_response(content=note)

    await interaction.response.send_modal(_OpenShopModal(submitted))
