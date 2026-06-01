import json, os, io, discord
from typing import Optional, Union
from PIL import Image, ImageDraw
import uuid
from lib.economy.economy_manager import add_bb, remove_bb, get_bb
from config import ROLES, CHANNELS, PREDICTIONS_FILE, PREDICTION_STREAKS_FILE

def _load() -> dict:
    return json.load(open(PREDICTIONS_FILE)) if os.path.exists(PREDICTIONS_FILE) else {}

def _save(d: dict) -> None:
    from lib.core.file_operations import atomic_write_json
    atomic_write_json(PREDICTIONS_FILE, d, indent=4)

def track_prediction_streak(user_id: int, is_win: bool) -> tuple[int, int]:
    """Tracks a user's prediction streak. Returns (win_streak, lose_streak)."""
    data = json.load(open(PREDICTION_STREAKS_FILE)) if os.path.exists(PREDICTION_STREAKS_FILE) else {}
    uid = str(user_id)
    
    if uid not in data:
        data[uid] = {"win_streak": 0, "lose_streak": 0}
        
    if is_win:
        data[uid]["win_streak"] += 1
        data[uid]["lose_streak"] = 0
    else:
        data[uid]["lose_streak"] += 1
        data[uid]["win_streak"] = 0
        
    from lib.core.file_operations import atomic_write_json
    atomic_write_json(PREDICTION_STREAKS_FILE, data, indent=4)
    return data[uid]["win_streak"], data[uid]["lose_streak"]

async def award_indecisive_badges(client, pred, window: int = 10) -> None:
    """Award the 'indecisive' badge to anyone who bet within `window` seconds of
    the prediction being locked — used by both the manual Lock button and the
    auto-lock sweep, so the badge is awarded consistently however a pred closes."""
    import time
    from lib.bot.event_handlers import award_badge_with_notify
    now = time.time()
    for uid, bet_time in pred.last_bet_times.items():
        if now - bet_time <= window:
            await award_badge_with_notify(client, uid, 'indecisive')

# A prediction can have between 2 and 5 outcomes. 5 is the practical ceiling:
# the bet buttons must fit in a single Discord action row (max 5 buttons).
MAX_PRED_OPTIONS = 5
MIN_PRED_OPTIONS = 2


class Prediction:
    def __init__(self, msg_id: int, title: str, options: list, end_ts: float, channel_id: Optional[int] = None):
        self.msg_id = msg_id
        self.channel_id = channel_id
        self.title = title
        self.options = [str(o) for o in options]          # 2..5 outcome labels
        # side numbers are 1-based and map to options[side - 1]
        self.bets = {i: {} for i in range(1, len(self.options) + 1)}  # side -> {uid: amount}
        self.locked = False
        self.end_ts = end_ts
        self.last_bet_times = {} # uid -> timestamp
        self.initial_balances = {} # uid -> balance before first bet on this pred

    def stake(self, uid: int, side: int, amount: int) -> bool:
        if self.locked or not (1 <= side <= len(self.options)):
            return False
        # A user may only back ONE outcome — reject if they've already bet on another.
        if any(uid in pool for s, pool in self.bets.items() if s != side):
            return False
        side_name = self.options[side - 1]
        if amount > 100_000 or not remove_bb(uid, amount, reason=f"Prediction bet: {self.title[:50]} ({side_name})"):
            return False

        if uid not in self.initial_balances:
            # We add back the amount because remove_bb already took it,
            # and we want the balance BEFORE the bet.
            self.initial_balances[uid] = get_bb(uid) + amount

        self.bets[side][uid] = self.bets[side].get(uid, 0) + amount
        import time
        self.last_bet_times[uid] = time.time()
        return True

    def totals(self) -> list:
        """Per-option staked totals, indexed by side-1 (i.e. totals()[0] is option 1)."""
        return [sum(self.bets.get(s, {}).values()) for s in range(1, len(self.options) + 1)]

    def resolve(self, win_side: int) -> dict[int, int]:
        # This is used by PredAdminView._resolve.
        #
        # Conservation model: every stake was already moved INTO the bank when the
        # bet was placed (stake() -> remove_bb with to_bank=True). The bank
        # therefore already holds the whole pool. Winners are paid back OUT of the
        # bank; any forfeited stakes or integer-rounding remainder simply stay in
        # the bank. We must NOT deposit the pool (or the dust) again here — doing so
        # would mint UKP and break the fixed 800k supply.
        totals = self.totals()
        win_total = totals[win_side - 1]
        pool_total = sum(totals)

        payouts = {}
        if win_total == 0:
            # Nobody backed the winning side. The banked pool is forfeited to the
            # bank, where it already sits — nothing to pay out, nothing to deposit.
            return payouts

        ratio = pool_total / win_total

        for uid, stake in self.bets.get(win_side, {}).items():
            payout = int(stake * ratio)
            payouts[uid] = payout
            add_bb(uid, payout, reason=f"Prediction win: {self.title[:50]}", taxable=False)

            # Double or Nothing Check: Bet > 50% of balance
            initial_bal = self.initial_balances.get(uid, 0)
            if initial_bal > 0:
                total_bet = self.bets[win_side].get(uid, 0)
                if total_bet > (initial_bal / 2):
                    from lib.bot.event_handlers import award_badge_notify
                    award_badge_notify(uid, 'double_or_nothing')

        # Rounding dust (pool_total - sum of integer payouts) is left in the bank
        # automatically, since we withdrew less than the pool that was banked.
        return payouts

    def to_dict(self) -> dict:
        bets_dump = {str(side): {str(uid): amt for uid, amt in pool.items()} for side, pool in self.bets.items()}
        return {
            "msg_id": self.msg_id,
            "channel_id": self.channel_id,
            "title": self.title,
            "options": list(self.options),
            "bets": bets_dump,
            "locked": self.locked,
            "end": self.end_ts,
            "initial_balances": {str(uid): bal for uid, bal in self.initial_balances.items()},
            "last_bet_times": {str(uid): ts for uid, ts in self.last_bet_times.items()},
        }


    @staticmethod
    def from_dict(d: dict):
        options = d.get("options")
        if not options:
            # Backward-compat: predictions created before multi-option support
            # stored opt1/opt2 instead of an options list.
            options = [d.get("opt1", "Option 1"), d.get("opt2", "Option 2")]
        p = Prediction(d["msg_id"], d["title"], options, d["end"], d.get("channel_id"))
        p.bets = {int(side): {int(uid): amt for uid, amt in pool.items()} for side, pool in d["bets"].items()}
        # Make sure every side has a (possibly empty) pool.
        for s in range(1, len(p.options) + 1):
            p.bets.setdefault(s, {})
        p.locked = d["locked"]
        p.initial_balances = {int(uid): bal for uid, bal in d.get("initial_balances", {}).items()}
        # Persisted so the "indecisive" badge can still be awarded for bets placed
        # shortly before a restart, and by the auto-lock sweep.
        p.last_bet_times = {int(uid): ts for uid, ts in d.get("last_bet_times", {}).items()}
        return p


# Distinct segment colours for the stacked pool bar / option fields (cycled).
_OPTION_COLORS = [
    (46, 204, 113),   # green
    (88, 101, 242),   # blurple
    (230, 126, 34),   # orange  (was a low-contrast yellow — hard to read, esp. as a card accent)
    (231, 76, 60),    # red
    (155, 89, 182),   # purple
]

def _progress_png_multi(pcts: list) -> io.BytesIO:
    """Render a stacked horizontal bar: one coloured segment per option, sized to
    its share of the total pool. Works for 2–5 options."""
    W, H = 400, 18
    img = Image.new("RGB", (W, H), (60, 63, 69))  # dark backing for the empty/no-bets case
    draw = ImageDraw.Draw(img)
    x = 0.0
    for i, pct in enumerate(pcts):
        seg = W * pct
        # The final non-zero segment snaps to the right edge to avoid a rounding gap.
        right = W if i == len(pcts) - 1 else x + seg
        if right > x:
            draw.rectangle([int(round(x)), 0, int(round(right)), H], fill=_OPTION_COLORS[i % len(_OPTION_COLORS)])
        x += seg
    buff = io.BytesIO()
    img.save(buff, format="PNG")
    buff.seek(0)
    return buff

CASH, TROPHY, USER, COIN, MEDAL = "💰", "🏆", "👥", "🪙", "🏅"

def _fmt_money(n: int) -> str:
    return f"{n:,}"

def _odds(totals: list, side: int) -> float:
    """Decimal odds for an outcome: (whole pool) / (amount on this outcome)."""
    win = totals[side - 1]
    pool = sum(totals)
    return 0 if win == 0 else max(pool / win, 1)

def _top_bettor(bets: dict, client: Optional[discord.Client]) -> str:
    if not bets:
        return "-"
    uid = max(bets, key=bets.get)
    if client:
        user = client.get_user(uid)
        name = discord.utils.escape_markdown(user.display_name) if user else f"{uid}"
    else:
        name = f"<@{uid}>"
    return f"{name} {_fmt_money(bets[uid])}"

def prediction_embed(pred: Prediction, client: Optional[discord.Client] = None) -> Union[tuple, any]:
    totals = pred.totals()
    grand = sum(totals) or 1
    pcts = [t / grand for t in totals]
    now = discord.utils.utcnow().timestamp()
    if pred.locked:
        time_line = "🔒 **locked**"
    elif pred.end_ts and pred.end_ts > now:
        time_line = f"⏰ closes <t:{int(pred.end_ts)}:R>"
    else:
        time_line = "🔓 **unlocked**"

    e = discord.Embed(title=pred.title, description=time_line)
    for i, opt in enumerate(pred.options):
        side = i + 1
        e.add_field(
            name=opt,
            value=(
                f"{CASH} **{_fmt_money(totals[i])}** `{int(pcts[i]*100)}%`\n"
                f"{TROPHY} **{_odds(totals, side):.2f}x**\n"
                f"{USER} {len(pred.bets.get(side, {}))}\n"
                f"{MEDAL} {_top_bettor(pred.bets.get(side, {}), client)}"
            ),
            inline=True,
        )
    bar_file = discord.File(_progress_png_multi(pcts), filename="bar.png")
    e.set_image(url="attachment://bar.png")
    return e, bar_file


def _hex(rgb: tuple) -> str:
    return "#%02x%02x%02x" % rgb


def _build_option_rows_html(pred: Prediction, client: Optional[discord.Client]) -> str:
    """Build the per-outcome HTML rows injected into the {{OPTIONS}} slot of
    templates/prediction_card.html. Each row uses fixed class names + a per-option
    --accent colour so the (design-generated) CSS can style them consistently."""
    import html as _html
    totals = pred.totals()
    grand = sum(totals) or 1
    rows = []
    for i, opt in enumerate(pred.options):
        side = i + 1
        t = totals[i]
        pct = int(round(t / grand * 100))
        color = _hex(_OPTION_COLORS[i % len(_OPTION_COLORS)])
        pool = pred.bets.get(side, {})
        if pool:
            top_uid = max(pool, key=pool.get)
            user = client.get_user(top_uid) if client else None
            top_name = user.display_name if user else str(top_uid)
            top = f"{_html.escape(top_name)} · {_fmt_money(pool[top_uid])}"
        else:
            top = "—"
        rows.append(
            f'<div class="pred-option" style="--accent: {color};">'
            f'<div class="pred-option-top">'
            f'<span class="pred-option-rank">{side}</span>'
            f'<span class="pred-option-name">{_html.escape(opt)}</span>'
            f'<span class="pred-option-odds">{_odds(totals, side):.2f}x</span>'
            f'</div>'
            f'<div class="pred-bar"><div class="pred-bar-fill" style="width:{pct}%;"></div></div>'
            f'<div class="pred-option-bottom">'
            f'<span class="pred-option-total">{_fmt_money(t)} UKP</span>'
            f'<span class="pred-option-pct">{pct}%</span>'
            f'<span class="pred-option-bettors">{len(pool)} bettors</span>'
            f'<span class="pred-option-top-bettor">{top}</span>'
            f'</div>'
            f'</div>'
        )
    return "".join(rows)


async def render_prediction_image(pred: Prediction, client: Optional[discord.Client]) -> io.BytesIO:
    """Render a prediction as a custom HTML→PNG card (templates/prediction_card.html)."""
    import html as _html
    from datetime import datetime
    import pytz
    from lib.core.image_processing import screenshot_html
    from lib.core.file_operations import read_html_template

    totals = pred.totals()
    pool_total = sum(totals)
    unique_bettors = len({uid for pool in pred.bets.values() for uid in pool})

    status = "Locked" if pred.locked else "Open"
    if pred.end_ts:
        closes = datetime.fromtimestamp(
            pred.end_ts, tz=pytz.timezone("Europe/London")
        ).strftime("Closes %H:%M · %d %b")
    else:
        closes = "No deadline"

    template = read_html_template("templates/prediction_card.html")
    html_out = (
        template
        .replace("{{TITLE}}", _html.escape(pred.title))
        .replace("{{STATUS}}", status)
        .replace("{{CLOSES}}", _html.escape(closes))
        .replace("{{POOL_TOTAL}}", _fmt_money(pool_total))
        .replace("{{BETTOR_COUNT}}", str(unique_bettors))
        .replace("{{OPTION_COUNT}}", str(len(pred.options)))
        .replace("{{OPTIONS}}", _build_option_rows_html(pred, client))
    )
    # Landscape canvas: Discord width-fits a wide image inline (no tap-to-open),
    # whereas a tall portrait card gets height-capped to a small thumbnail.
    return await screenshot_html(html_out, size=(1000, 1200))


async def build_prediction_render(pred: Prediction, client: Optional[discord.Client]):
    """Return ``(embed_or_None, [file])`` for sending/editing a prediction message.

    Honours the PREDICTION_IMAGE_ENABLED feature flag: when on, returns the custom
    HTML→PNG card (no embed); when off — or if rendering raises — returns the
    standard Discord embed + progress-bar file. Callers pass the returned file list
    to ``send(files=...)`` or ``edit(attachments=...)`` and the embed to ``embed=``.
    """
    import config
    if getattr(config, "PREDICTION_IMAGE_ENABLED", False):
        try:
            img = await render_prediction_image(pred, client)
            return None, [discord.File(img, filename="prediction.png")]
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Prediction image render failed; falling back to embed.", exc_info=True
            )
    embed, bar = prediction_embed(pred, client)
    return embed, [bar]

class BetModal(discord.ui.Modal, title="Place your bet"):
    amount = discord.ui.TextInput(label="Amount", placeholder="whole number")

    def __init__(self, pred: Prediction, side: int, user_balance: int):
        super().__init__()
        self.pred = pred
        self.side = side
        self.user_balance = user_balance
        max_bet = min(user_balance, 100_000)
        self.amount.label = f"Amount (Your Max: {_fmt_money(max_bet)})"
        self.amount.placeholder = f"Current Balance: {_fmt_money(user_balance)}"

    async def on_submit(self, interaction: discord.Interaction):
        from lib.economy.economy_manager import get_bb

        raw_val = self.amount.value.strip().lower()
        stake = 0

        if raw_val in ("all", "max"):
            stake = min(get_bb(interaction.user.id), 100_000)
        elif raw_val == "half":
            stake = min(get_bb(interaction.user.id) // 2, 100_000)
        elif raw_val.endswith("%") and raw_val[:-1].isdigit():
            pct = min(max(int(raw_val[:-1]), 1), 100)
            stake = min(int(get_bb(interaction.user.id) * (pct / 100.0)), 100_000)
        else:
            try:
                stake = int(raw_val.replace(",", ""))
            except ValueError:
                return await interaction.response.send_message("Enter a valid amount (e.g., 500, 'all', 'half', '50%').", ephemeral=True)

        if stake <= 0:
            return await interaction.response.send_message("You must bet at least 1 coin.", ephemeral=True)

        if self.pred.stake(interaction.user.id, self.side, stake):
            user_total = self.pred.bets[self.side][interaction.user.id]
            new_balance = get_bb(interaction.user.id)

            # Respond to the bettor FIRST so a slow image re-render (when the HTML
            # card flag is on) can't make this interaction time out.
            await interaction.response.send_message(
                f"✅ Bet placed! You now have **{_fmt_money(user_total)}** on **{self.pred.options[self.side - 1]}**.\n"
                f"💰 Remaining Balance: **{_fmt_money(new_balance)}**",
                ephemeral=True
            )
            _save({k: v.to_dict() for k, v in interaction.client.predictions.items()})

            embed, files = await build_prediction_render(self.pred, interaction.client)
            try:
                await interaction.message.edit(embed=embed, attachments=files)
            except discord.NotFound:
                pass # Message was deleted by a mod while they were typing

            # High Stakes Badge
            if stake >= 5000:
                from lib.bot.event_handlers import award_badge_with_notify
                await award_badge_with_notify(interaction.client, interaction.user.id, 'high_stakes')
                
        else:
            # Work out the REAL reason the stake was rejected so the message is
            # accurate (the old message always blamed balance/limit).
            if self.pred.locked:
                msg = "❌ Betting is locked on this prediction."
            else:
                backed = next(
                    (self.pred.options[s - 1] for s, pool in self.pred.bets.items()
                     if s != self.side and interaction.user.id in pool),
                    None,
                )
                if backed is not None:
                    msg = (
                        f"❌ You've already backed **{backed}** on this prediction — you can only "
                        f"bet on one outcome. Add more to **{backed}**, or wait for the next prediction."
                    )
                elif stake > 100_000:
                    msg = "❌ The maximum single bet is **100,000** UKPence."
                else:
                    curr = get_bb(interaction.user.id)
                    msg = f"❌ Bet failed — you only have **{_fmt_money(curr)}** UKPence."
            await interaction.response.send_message(msg, ephemeral=True)

class BetButtons(discord.ui.View):
    def __init__(self, pred: Prediction):
        super().__init__(timeout=None)
        self.pred = pred

        async def _handler(interaction: discord.Interaction, side: int):
            if self.pred.locked:
                await interaction.response.send_message("Betting locked.", ephemeral=True)
                return
            
            from lib.economy.economy_manager import get_bb
            bal = get_bb(interaction.user.id)
            if bal <= 0:
                await interaction.response.send_message("❌ You don't have any UKPence to bet!", ephemeral=True)
                return

            await interaction.response.send_modal(BetModal(self.pred, side, bal))

        def _make_bet_cb(side: int):
            async def _cb(interaction: discord.Interaction):
                await _handler(interaction, side)
            return _cb

        # One bet button per outcome (2–5). All fit on action row 0 (max 5 buttons).
        bet_styles = [
            discord.ButtonStyle.success,
            discord.ButtonStyle.primary,
            discord.ButtonStyle.secondary,
            discord.ButtonStyle.danger,
            discord.ButtonStyle.primary,
        ]
        for i, opt in enumerate(pred.options):
            side = i + 1
            btn = discord.ui.Button(
                label=f"Bet on {opt}"[:80],
                style=bet_styles[i % len(bet_styles)],
                custom_id=f"prediction:{pred.msg_id}:bet{side}",
                row=0,
            )
            btn.callback = _make_bet_cb(side)
            self.add_item(btn)

        # Notification toggle button (own row so it never crowds the bet buttons)
        notif_btn = discord.ui.Button(
            label="🔔",
            style=discord.ButtonStyle.secondary,
            custom_id=f"prediction:{pred.msg_id}:notif_toggle",
            row=1,
        )

        async def notif_cb(interaction: discord.Interaction):
            role = interaction.guild.get_role(ROLES.PRED_NOTIFICATIONS)
            if not role:
                await interaction.response.send_message("Notification role not found.", ephemeral=True)
                return
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("🔕 You will no longer be notified for new predictions.", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("🔔 You will be notified when new predictions are created!", ephemeral=True)

        notif_btn.callback = notif_cb
        self.add_item(notif_btn)




class PredSelectView(discord.ui.View):
    def __init__(self, predictions: list[Prediction], client: discord.Client):
        super().__init__(timeout=600)
        self.predictions = sorted(predictions, key=lambda x: x.msg_id, reverse=True)[:25]
        self.client = client

        options = []
        for p in self.predictions:
            status = "🔒" if p.locked else "🔓"
            options.append(discord.SelectOption(
                label=f"{status} {p.title[:80]}",
                # Discord rejects SelectOption descriptions over 100 chars. Keep the
                # ID intact and truncate the options text so the whole pred-admin
                # menu can't fail to render on long option names.
                description=f"ID: {p.msg_id} | {' / '.join(p.options)}"[:100],
                value=str(p.msg_id)
            ))

        self.select = discord.ui.Select(
            placeholder="Choose a prediction to manage...",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        mid = int(self.select.values[0])
        p = self.client.predictions.get(mid)
        if not p:
            return await interaction.response.edit_message(content="❌ Prediction no longer found.", view=None)
        
        view = PredAdminView(p, self.client)
        await interaction.response.edit_message(content=f"Managing: **{p.title}**", view=view)


class PredAdminView(discord.ui.View):
    def __init__(self, pred: Prediction, client: discord.Client):
        super().__init__(timeout=600)
        self.pred = pred
        self.client = client
        # One "Winner: <option>" button per outcome (2–5), built dynamically so the
        # panel scales with the prediction's option count. Lock/Unlock/Draw live on
        # row 0; winner buttons on row 1.
        for i, opt in enumerate(pred.options):
            side = i + 1
            btn = discord.ui.Button(
                label=f"Winner: {opt[:60]}",
                style=discord.ButtonStyle.primary,
                row=1,
            )
            btn.callback = self._make_resolve_cb(side)
            self.add_item(btn)

    def _make_resolve_cb(self, side: int):
        async def _cb(interaction: discord.Interaction):
            await self._resolve(interaction, side)
        return _cb

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.danger, row=0)
    async def lock(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message("Already locked.", ephemeral=True)
        self.pred.locked = True
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, files = await build_prediction_render(self.pred, self.client)
            await msg.edit(embed=embed, attachments=files, view=None)
        except discord.NotFound:
            pass
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.followup.send("🔒 Locked.", ephemeral=True)

        await award_indecisive_badges(self.client, self.pred)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.success, row=0)
    async def unlock(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not self.pred.locked:
            return await interaction.response.send_message("Already unlocked.", ephemeral=True)

        self.pred.locked = False
        self.pred.end_ts = None
        await interaction.response.defer(ephemeral=True)

        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, files = await build_prediction_render(self.pred, self.client)
            view = BetButtons(self.pred)
            await msg.edit(embed=embed, attachments=files, view=view)
            self.client.add_view(view, message_id=self.pred.msg_id)
        except discord.NotFound:
            pass

        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.followup.send("🔓 Unlocked.", ephemeral=True)

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.secondary, row=0)
    async def draw(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        # Same idempotency claim as _resolve: pop synchronously before any await so
        # a double-click can't refund every bettor twice (which would mint UKP).
        if self.pred.msg_id not in self.client.predictions:
            return await interaction.response.send_message(
                "This prediction has already been resolved.", ephemeral=True
            )
        self.client.predictions.pop(self.pred.msg_id, None)
        self.pred.locked = True
        await interaction.response.defer(ephemeral=True)
        for side, pool in self.pred.bets.items():
            for uid, amt in pool.items():
                # Bets were banked when staked (remove_bb to_bank=True), so refund from bank is correct
                add_bb(uid, amt, reason=f"Prediction refund (Draw): {self.pred.title[:50]}", taxable=False)
        self.pred.bets = {s: {} for s in range(1, len(self.pred.options) + 1)}
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, files = await build_prediction_render(self.pred, self.client)
            await msg.edit(embed=embed, attachments=files, view=None)
        except discord.NotFound:
            pass
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.followup.send("🟡 Draw called – all bets refunded.", ephemeral=True)
        self.stop()

    async def _resolve(self, interaction: discord.Interaction, winner: int):
        # Idempotency guard. Claiming the prediction (the pop below) happens
        # synchronously before any await, so a double-click — or two open admin
        # panels sharing this Prediction — cannot both pay out: the second
        # invocation finds it already gone from the live registry and bails.
        if self.pred.msg_id not in self.client.predictions:
            return await interaction.response.send_message(
                "This prediction has already been resolved.", ephemeral=True
            )
        self.client.predictions.pop(self.pred.msg_id, None)
        self.pred.locked = True
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})

        # Defer now (big pools can take >3s to pay out, which would otherwise
        # surface as "interaction failed").
        await interaction.response.defer(ephemeral=True)

        payouts = self.pred.resolve(winner)
        win_side = self.pred.options[winner - 1]
        msg = None
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, files = await build_prediction_render(self.pred, self.client)
            await msg.edit(embed=embed, attachments=files, view=None)
        except discord.NotFound:
            pass  # Message was deleted, proceed with payout and summary

        lines = []
        for uid, amt in sorted(payouts.items(), key=lambda x: x[1], reverse=True):
            member = interaction.guild.get_member(uid)
            name = discord.utils.escape_markdown(member.display_name) if member else f"<@{uid}>"
            lines.append(f"**{name}** won **{amt:,}**")

        descr = "\n".join(lines) or "*Nobody backed the winner*"
        summary = discord.Embed(title=f"🏁 Prediction settled: **{win_side}** wins!", description=descr, color=0x2ECC71)

        mentions = " ".join([f"<@{uid}>" for uid in payouts.keys()])
        if msg:
            await msg.reply(content=mentions, embed=summary, mention_author=False)
        else:
            await interaction.channel.send(content=f"{mentions}\nPrediction resolved (original message deleted).", embed=summary)

        # (Already popped from client.predictions and persisted at the top of this
        # method as the idempotency claim.)

        # Award streak badges
        from lib.economy.prediction_system import track_prediction_streak
        from lib.bot.event_handlers import award_badge_with_notify
        import asyncio

        # Everyone who backed a non-winning outcome is a loser (generalised from the
        # old 2-way model where there was a single losing side).
        loser_uids = [
            uid
            for side, pool in self.pred.bets.items()
            if side != winner
            for uid in pool.keys()
        ]

        async def award_streaks():
            # Process winners
            for uid in self.pred.bets.get(winner, {}).keys():
                win_streak, lose_streak = track_prediction_streak(uid, is_win=True)
                if win_streak >= 7:
                    await award_badge_with_notify(self.client, uid, 'oracle')

            # Process losers
            for uid in loser_uids:
                win_streak, lose_streak = track_prediction_streak(uid, is_win=False)
                if lose_streak >= 5:
                    await award_badge_with_notify(self.client, uid, 'unlucky')

        asyncio.create_task(award_streaks())

        # We deferred at the top, so the admin panel response is a followup.
        try:
            await interaction.followup.send("✅ Resolved & paid out.", ephemeral=True)
        except discord.NotFound:
            pass  # Admin interaction might be old/invalid too
        self.stop()


def _parse_post_time(value: str) -> int:
    """Parse the 'when to post' field. Accepts:
      - integer minutes from now
      - "YYYY-MM-DD HH:MM" in Europe/London
      - a Discord timestamp like "<t:1715116200:F>"
    Returns a unix timestamp. Raises ValueError on bad input or past time."""
    import re, pytz
    from datetime import datetime, timedelta
    value = value.strip()
    now_uk = datetime.now(pytz.timezone("Europe/London"))

    ts_match = re.fullmatch(r"<t:(\d+)(?::[A-Za-z])?>", value)
    if ts_match:
        scheduled_ts = int(ts_match.group(1))
    else:
        try:
            mins = int(value)
            if mins <= 0:
                raise ValueError("Minutes from now must be a positive integer.")
            scheduled_ts = int((now_uk + timedelta(minutes=mins)).timestamp())
        except ValueError as int_err:
            if "positive integer" in str(int_err):
                raise
            try:
                naive = datetime.strptime(value, "%Y-%m-%d %H:%M")
            except ValueError:
                raise ValueError(
                    "Post time must be one of: minutes from now (e.g. `30`), "
                    "`YYYY-MM-DD HH:MM` in UK time, or a Discord timestamp like `<t:1715116200:F>`."
                )
            aware = pytz.timezone("Europe/London").localize(naive)
            scheduled_ts = int(aware.timestamp())

    if scheduled_ts <= int(now_uk.timestamp()):
        raise ValueError("Post time must be in the future.")
    return scheduled_ts


def _resolve_end_ts(value: str, reference_ts: float) -> float:
    """Parse duration field as either minutes (int) or absolute UK end time
    (`YYYY-MM-DD HH:MM`). Returns absolute end timestamp. Raises ValueError on
    bad input or non-positive duration."""
    import pytz
    from datetime import datetime
    value = value.strip()
    try:
        mins = int(value)
        if mins <= 0:
            raise ValueError("duration must be positive")
        return reference_ts + mins * 60
    except ValueError as int_err:
        if "positive" in str(int_err):
            raise
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(
            "End time must be a positive integer (minutes) or `YYYY-MM-DD HH:MM` in UK time."
        )
    aware = pytz.timezone("Europe/London").localize(naive)
    end_ts = aware.timestamp()
    if end_ts <= reference_ts:
        raise ValueError("End time must be in the future, after the prediction posts.")
    return end_ts


def _scheduled_pred_embed(sched_id, channel_id, title: str, options: list,
                          scheduled_ts: int, duration: int, creator_mention: str) -> discord.Embed:
    """Build the COMMUNITY_MANAGEMENT announcement embed for a *pending* scheduled
    prediction. Shared by the create and edit flows so the layout stays in sync."""
    embed = discord.Embed(
        title="📅 Prediction Scheduled",
        description=f"**{title}**\n" + " / ".join(f"*{o}*" for o in options),
        color=0x5865F2,
    )
    embed.add_field(name="Posts in", value=f"<#{channel_id}>", inline=True)
    embed.add_field(name="Posts at", value=f"<t:{scheduled_ts}:F> (<t:{scheduled_ts}:R>)", inline=True)
    embed.add_field(name="Betting closes", value=f"{duration} min after post", inline=True)
    embed.add_field(name="Scheduled by", value=creator_mention, inline=True)
    embed.set_footer(text=f"ID #{sched_id}")
    return embed


def _options_from_row(opt1, opt2, options_json) -> list:
    """Decode the stored option list for a scheduled prediction, falling back to
    the legacy opt1/opt2 columns for rows created before multi-option support."""
    if options_json:
        try:
            opts = json.loads(options_json)
            if isinstance(opts, list) and len(opts) >= 2:
                return [str(o) for o in opts]
        except (ValueError, TypeError):
            pass
    return [opt1, opt2]


def _clean_options(raw_options: list) -> tuple:
    """Validate a list of raw option strings (which may include None/blank entries
    from optional slash params or blank modal lines). Returns (options, error)."""
    opts = [o.strip() for o in raw_options if o and str(o).strip()]
    if len(opts) < MIN_PRED_OPTIONS:
        return [], f"A prediction needs at least {MIN_PRED_OPTIONS} outcomes."
    if len(opts) > MAX_PRED_OPTIONS:
        return [], f"A prediction can have at most {MAX_PRED_OPTIONS} outcomes."
    if len({o.lower() for o in opts}) != len(opts):
        return [], "Outcomes must be distinct."
    if any(len(o) > 80 for o in opts):
        return [], "Each outcome must be 80 characters or fewer."
    return opts, None


async def post_prediction(client: discord.Client, channel, title: str, options: list, end_ts: float) -> Prediction:
    """Create a live Prediction, post it (with the bet buttons) to ``channel``,
    register it on the client, and persist. Shared by /pred-create and the
    scheduled-post path."""
    p = Prediction(0, title, options, end_ts)
    embed, files = await build_prediction_render(p, client)
    msg = await channel.send(
        content=f"<@&{ROLES.PRED_NOTIFICATIONS}>",
        embed=embed,
        files=files,
        view=BetButtons(p),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    p.msg_id = msg.id
    p.channel_id = msg.channel.id
    client.predictions[msg.id] = p
    _save({k: v.to_dict() for k, v in client.predictions.items()})
    return p


async def handle_pred_create_command(interaction: discord.Interaction, title: str,
                                     raw_options: list, duration: str):
    """Slash-command backed prediction creation (2–5 outcomes via slash options)."""
    options, err = _clean_options(raw_options)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    title = (title or "").strip()
    if not title:
        return await interaction.response.send_message("The title can't be empty.", ephemeral=True)

    now_ts = discord.utils.utcnow().timestamp()
    try:
        end_ts = _resolve_end_ts(duration, now_ts)
    except ValueError as e:
        return await interaction.response.send_message(str(e), ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    await post_prediction(interaction.client, interaction.channel, title, options, end_ts)
    await interaction.followup.send(f"✅ Prediction opened with {len(options)} outcomes.", ephemeral=True)


async def handle_pred_schedule_command(interaction: discord.Interaction, channel_id: int,
                                       title: str, raw_options: list, when: str, duration: str):
    """Slash-command backed scheduling of a prediction (2–5 outcomes)."""
    options, err = _clean_options(raw_options)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    title = (title or "").strip()
    if not title:
        return await interaction.response.send_message("The title can't be empty.", ephemeral=True)

    try:
        scheduled_ts = _parse_post_time(when)
    except ValueError as e:
        return await interaction.response.send_message(str(e), ephemeral=True)
    try:
        end_ts = _resolve_end_ts(duration, float(scheduled_ts))
    except ValueError as e:
        return await interaction.response.send_message(str(e), ephemeral=True)
    duration_minutes = max(1, int(round((end_ts - scheduled_ts) / 60)))

    from database import DatabaseManager
    import time
    now_ts = int(time.time())
    sched_id = DatabaseManager.execute_insert(
        "INSERT INTO scheduled_predictions "
        "(channel_id, creator_id, title, opt1, opt2, options_json, duration_minutes, scheduled_ts, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (str(channel_id), str(interaction.user.id), title, options[0], options[1],
         json.dumps(options), duration_minutes, scheduled_ts, now_ts),
    )

    from apscheduler.triggers.date import DateTrigger
    from datetime import datetime, timezone
    run_at = datetime.fromtimestamp(scheduled_ts, tz=timezone.utc)
    scheduler = getattr(interaction.client, "scheduler", None)
    if scheduler is not None and sched_id is not None:
        scheduler.add_job(
            post_scheduled_prediction,
            DateTrigger(run_date=run_at),
            args=[interaction.client, sched_id],
            id=f"scheduled_pred_{sched_id}",
            name=f"Scheduled Prediction #{sched_id}",
            misfire_grace_time=3600,
        )

    await interaction.response.send_message(
        f"✅ Prediction #{sched_id} ({len(options)} outcomes) scheduled for "
        f"<t:{scheduled_ts}:F> (<t:{scheduled_ts}:R>) in <#{channel_id}>.",
        ephemeral=True,
    )

    cm_channel = interaction.client.get_channel(CHANNELS.COMMUNITY_MANAGEMENT)
    if cm_channel is not None and sched_id is not None:
        embed = _scheduled_pred_embed(
            sched_id, channel_id, title, options,
            scheduled_ts, duration_minutes, interaction.user.mention,
        )
        try:
            cm_msg = await cm_channel.send(
                embed=embed,
                view=CancelScheduledPredView(sched_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            DatabaseManager.execute(
                "UPDATE scheduled_predictions SET cm_message_id = ? WHERE id = ?",
                (str(cm_msg.id), sched_id),
            )
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Could not send scheduled-pred announcement to COMMUNITY_MANAGEMENT.",
                exc_info=True,
            )


def _build_option_inputs(num_options: int):
    """Build the modal's outcome input(s): two separate boxes for a 2-way
    prediction, or a single slash-separated box for 3–5 outcomes (Discord caps
    modals at 5 fields, so we can't show 5 individual boxes alongside the other
    fields). Returns (separate_inputs_or_None, slash_box_or_None)."""
    if num_options == 2:
        return [
            discord.ui.TextInput(label="Outcome 1", default="Yes", required=True, max_length=80),
            discord.ui.TextInput(label="Outcome 2", default="No", required=True, max_length=80),
        ], None
    box = discord.ui.TextInput(
        label=f"{num_options} outcomes, separated by /",
        style=discord.TextStyle.long,
        placeholder="Labour / Tory / Reform",
        required=True,
        max_length=430,
    )
    return None, box


def _read_option_inputs(separate_inputs, slash_box, expected: int):
    """Pull the raw outcome strings out of the modal inputs and check the count
    matches what was requested. Returns (cleaned_options, error)."""
    if separate_inputs is not None:
        raw = [it.value for it in separate_inputs]
    else:
        raw = slash_box.value.split("/")
    cleaned = [o.strip() for o in raw if o and o.strip()]
    if len(cleaned) != expected:
        return None, (
            f"You selected **{expected}** outcomes but provided **{len(cleaned)}**. "
            f"Separate them with `/` (e.g. `A / B / C`)."
        )
    return cleaned, None


class PredictionCreateModal(discord.ui.Modal):
    """Opened by /pred-create. The number of outcomes is chosen on the slash
    command; this modal then collects the title, outcome(s) and duration."""
    def __init__(self, num_options: int):
        super().__init__(title="Create Prediction")
        self.num_options = max(MIN_PRED_OPTIONS, min(MAX_PRED_OPTIONS, int(num_options)))

        self.title_input = discord.ui.TextInput(
            label="Title", style=discord.TextStyle.long,
            placeholder="The question/topic for the prediction",
            required=True, max_length=200,
        )
        self.add_item(self.title_input)

        self._separate_inputs, self._slash_box = _build_option_inputs(self.num_options)
        for item in (self._separate_inputs or [self._slash_box]):
            self.add_item(item)

        self.duration_input = discord.ui.TextInput(
            label="Minutes or end time YYYY-MM-DD HH:MM",
            style=discord.TextStyle.short, default="5", required=True, max_length=20,
        )
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        options, err = _read_option_inputs(self._separate_inputs, self._slash_box, self.num_options)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        await handle_pred_create_command(
            interaction, self.title_input.value, options, self.duration_input.value
        )


class PredictionScheduleModal(discord.ui.Modal):
    """Opened by /pred-schedule. Like PredictionCreateModal but with a 'post when'
    field; the target channel and outcome count come from the slash command."""
    def __init__(self, channel_id: int, num_options: int):
        super().__init__(title="Schedule Prediction")
        self._channel_id = channel_id
        self.num_options = max(MIN_PRED_OPTIONS, min(MAX_PRED_OPTIONS, int(num_options)))

        self.title_input = discord.ui.TextInput(
            label="Title", style=discord.TextStyle.long,
            placeholder="The question/topic for the prediction",
            required=True, max_length=200,
        )
        self.add_item(self.title_input)

        self._separate_inputs, self._slash_box = _build_option_inputs(self.num_options)
        for item in (self._separate_inputs or [self._slash_box]):
            self.add_item(item)

        self.when_input = discord.ui.TextInput(
            label="Post when? (mins / YYYY-MM-DD HH:MM UK)",
            style=discord.TextStyle.short,
            placeholder="30  or  2026-05-08 19:30  or  <t:1715116200:F>",
            required=True, max_length=40,
        )
        self.add_item(self.when_input)

        self.duration_input = discord.ui.TextInput(
            label="Minutes or end time YYYY-MM-DD HH:MM",
            style=discord.TextStyle.short, default="5", required=True, max_length=20,
        )
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        options, err = _read_option_inputs(self._separate_inputs, self._slash_box, self.num_options)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        await handle_pred_schedule_command(
            interaction, self._channel_id, self.title_input.value, options,
            self.when_input.value, self.duration_input.value,
        )


class PredictionEditModal(discord.ui.Modal):
    """Modal to edit an existing *pending* scheduled prediction. Pre-fills the
    current values; on submit it updates the DB row, reschedules the post job,
    and refreshes the COMMUNITY_MANAGEMENT announcement."""
    def __init__(self, sched_id: int, title: str, options: list,
                 scheduled_ts: int, duration_minutes: int):
        super().__init__(title=f"Edit Scheduled Prediction #{sched_id}")
        self.sched_id = sched_id

        import pytz
        from datetime import datetime
        when_default = datetime.fromtimestamp(
            scheduled_ts, tz=pytz.timezone("Europe/London")
        ).strftime("%Y-%m-%d %H:%M")
        # Keep the originals so an unchanged "when" can skip the future-time
        # check — staff editing only the title of an imminent prediction
        # shouldn't be blocked by "Post time must be in the future."
        self._orig_when_default = when_default
        self._orig_scheduled_ts = scheduled_ts

        self.title_input = discord.ui.TextInput(
            label="Title",
            style=discord.TextStyle.long,
            default=title,
            required=True,
            max_length=200,
        )
        # A single multi-line box (one outcome per line) — a per-outcome field can't
        # be used here because the modal already needs title + when + duration, and
        # Discord caps modals at 5 fields, leaving no room for up to 5 option fields.
        self.options_input = discord.ui.TextInput(
            label="Outcomes (one per line, 2-5)",
            style=discord.TextStyle.long,
            default="\n".join(options),
            required=True,
            max_length=430,
        )
        self.when_input = discord.ui.TextInput(
            label="Post when? (mins / YYYY-MM-DD HH:MM UK)",
            style=discord.TextStyle.short,
            default=when_default,
            required=True,
            max_length=40,
        )
        self.duration_input = discord.ui.TextInput(
            label="Minutes or end time YYYY-MM-DD HH:MM",
            style=discord.TextStyle.short,
            default=str(duration_minutes),
            required=True,
            max_length=20,
        )
        for item in (self.title_input, self.options_input,
                     self.when_input, self.duration_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not any(role.id in [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO] for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ Only staff can edit scheduled predictions.", ephemeral=True
            )

        from database import DatabaseManager
        row = DatabaseManager.fetch_one(
            "SELECT status, cm_message_id, channel_id, creator_id FROM scheduled_predictions WHERE id = ?",
            (self.sched_id,),
        )
        if not row:
            return await interaction.response.send_message(
                f"❌ Scheduled prediction #{self.sched_id} not found.", ephemeral=True
            )
        status, cm_message_id, channel_id, creator_id = row
        if status != 'pending':
            return await interaction.response.send_message(
                f"❌ Scheduled prediction #{self.sched_id} is no longer pending (status={status}); cannot edit.",
                ephemeral=True,
            )

        if self.when_input.value.strip() == self._orig_when_default:
            # Time left untouched: keep the stored timestamp as-is (even if it's
            # now imminent/past) so other fields can still be edited.
            scheduled_ts = self._orig_scheduled_ts
        else:
            try:
                scheduled_ts = _parse_post_time(self.when_input.value)
            except ValueError as e:
                return await interaction.response.send_message(str(e), ephemeral=True)
        try:
            end_ts = _resolve_end_ts(self.duration_input.value, float(scheduled_ts))
        except ValueError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        duration = max(1, int(round((end_ts - scheduled_ts) / 60)))

        title = self.title_input.value.strip()
        options, opt_err = _clean_options(self.options_input.value.splitlines())
        if opt_err:
            return await interaction.response.send_message(opt_err, ephemeral=True)

        DatabaseManager.execute(
            "UPDATE scheduled_predictions SET title = ?, opt1 = ?, opt2 = ?, options_json = ?, duration_minutes = ?, scheduled_ts = ? WHERE id = ?",
            (title, options[0], options[1], json.dumps(options), duration, scheduled_ts, self.sched_id),
        )

        # Reschedule the APScheduler job to fire at the (possibly new) time.
        from apscheduler.triggers.date import DateTrigger
        from datetime import datetime, timezone
        run_at = datetime.fromtimestamp(scheduled_ts, tz=timezone.utc)
        scheduler = getattr(interaction.client, "scheduler", None)
        if scheduler is not None:
            scheduler.add_job(
                post_scheduled_prediction,
                DateTrigger(run_date=run_at),
                args=[interaction.client, self.sched_id],
                id=f"scheduled_pred_{self.sched_id}",
                name=f"Scheduled Prediction #{self.sched_id}",
                misfire_grace_time=3600,
                replace_existing=True,
            )

        await interaction.response.send_message(
            f"✅ Prediction #{self.sched_id} updated — now posts <t:{scheduled_ts}:F> (<t:{scheduled_ts}:R>) in <#{channel_id}>.",
            ephemeral=True,
        )

        # Refresh the CM announcement embed to reflect the edits.
        if cm_message_id:
            try:
                cm_channel = interaction.client.get_channel(CHANNELS.COMMUNITY_MANAGEMENT)
                if cm_channel is not None:
                    cm_msg = await cm_channel.fetch_message(int(cm_message_id))
                    embed = _scheduled_pred_embed(
                        self.sched_id, channel_id, title, options,
                        scheduled_ts, duration, f"<@{creator_id}>",
                    )
                    await cm_msg.edit(embed=embed, view=CancelScheduledPredView(self.sched_id))
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    f"Could not update CM announcement for edited scheduled pred {self.sched_id}.",
                    exc_info=True,
                )


async def cancel_scheduled_prediction(client: discord.Client, sched_id: int, cancelled_by_mention: str) -> tuple[bool, str]:
    """
    Cancels a scheduled prediction by ID.
    Updates the database status to 'cancelled', removes the job from APScheduler,
    and updates the Community Management channel announcement message (if one exists).
    Returns (success, message).
    """
    import logging
    logger = logging.getLogger(__name__)
    from database import DatabaseManager

    row = DatabaseManager.fetch_one(
        "SELECT status, cm_message_id FROM scheduled_predictions WHERE id = ?", (sched_id,)
    )
    if not row:
        return False, f"Scheduled prediction #{sched_id} not found."
    status, cm_message_id = row
    if status != 'pending':
        return False, f"Scheduled prediction #{sched_id} is not pending (status={status})."

    # Update database
    DatabaseManager.execute(
        "UPDATE scheduled_predictions SET status = 'cancelled' WHERE id = ?", (sched_id,)
    )

    # Remove scheduler job
    scheduler = getattr(client, "scheduler", None)
    if scheduler is not None:
        try:
            scheduler.remove_job(f"scheduled_pred_{sched_id}")
        except Exception as e:
            logger.warning(f"Failed to remove scheduler job for pred #{sched_id}: {e}")

    # Update CM Announcement Message
    if cm_message_id:
        try:
            cm_channel = client.get_channel(CHANNELS.COMMUNITY_MANAGEMENT)
            if cm_channel is not None:
                cm_msg = await cm_channel.fetch_message(int(cm_message_id))
                disabled_view = CancelScheduledPredView(sched_id)
                for item in disabled_view.children:
                    item.disabled = True
                cm_embed = cm_msg.embeds[0] if cm_msg.embeds else discord.Embed()
                cm_embed.title = "🗑️ Prediction Scheduled (Cancelled)"
                cm_embed.color = 0x99AAB5
                
                # Check if "Cancelled by" field is already present
                has_cancelled_by = False
                for field in cm_embed.fields:
                    if field.name == "Cancelled by":
                        has_cancelled_by = True
                        break
                if not has_cancelled_by:
                    cm_embed.add_field(name="Cancelled by", value=cancelled_by_mention, inline=True)
                await cm_msg.edit(embed=cm_embed, view=disabled_view)
        except Exception as e:
            logger.warning(f"Could not update CM announcement for scheduled pred {sched_id}: {e}")

    return True, f"Scheduled prediction #{sched_id} cancelled."


class ScheduledPredSelectView(discord.ui.View):
    def __init__(self, rows: list, client: discord.Client):
        super().__init__(timeout=600)
        self.client = client
        self.rows = rows[:25] # max 25 options in discord select menu

        options = []
        from datetime import datetime
        for sched_id, channel_id, title, scheduled_ts, duration, creator_id in self.rows:
            short_title = title[:80]
            options.append(discord.SelectOption(
                label=f"#{sched_id}: {short_title}",
                description=f"Posts: {datetime.fromtimestamp(scheduled_ts).strftime('%Y-%m-%d %H:%M')} UK",
                value=str(sched_id)
            ))

        self.select = discord.ui.Select(
            placeholder="Choose a scheduled prediction to manage...",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        if not any(role.id in [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO] for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ Only staff can manage scheduled predictions.", ephemeral=True
            )

        sched_id = int(self.select.values[0])
        # Find row details
        selected_row = None
        for r in self.rows:
            if r[0] == sched_id:
                selected_row = r
                break
        
        if not selected_row:
            return await interaction.response.edit_message(content="❌ Scheduled prediction no longer found.", view=None)

        sched_id, channel_id, title, scheduled_ts, duration, creator_id = selected_row
        
        embed = discord.Embed(
            title="📅 Scheduled Prediction Details",
            description=f"**Title:** {title}",
            color=0xF5A623
        )
        embed.add_field(name="ID", value=f"#{sched_id}", inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
        embed.add_field(name="Scheduled Time", value=f"<t:{scheduled_ts}:F> (<t:{scheduled_ts}:R>)", inline=True)
        embed.add_field(name="Duration", value=f"{duration} minutes", inline=True)
        embed.add_field(name="Scheduled by", value=f"<@{creator_id}>", inline=True)

        view = ScheduledPredAdminView(sched_id, self.client)
        await interaction.response.edit_message(content="Review details below:", embed=embed, view=view)


class ScheduledPredAdminView(discord.ui.View):
    def __init__(self, sched_id: int, client: discord.Client):
        super().__init__(timeout=600)
        self.sched_id = sched_id
        self.client = client

    @discord.ui.button(label="Cancel Prediction", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(role.id in [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO] for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ Only staff can cancel scheduled predictions.", ephemeral=True
            )

        # Defer interaction to avoid timeout
        await interaction.response.defer(ephemeral=True)

        success, msg = await cancel_scheduled_prediction(
            self.client, self.sched_id, interaction.user.mention
        )
        if not success:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        else:
            for item in self.children:
                item.disabled = True
            await interaction.followup.send(f"✅ {msg}", ephemeral=True)
            try:
                await interaction.message.edit(content=f"🗑️ {msg}", embed=None, view=self)
            except Exception:
                pass

    @discord.ui.button(label="Back to List", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from database import DatabaseManager
        rows = DatabaseManager.fetch_all(
            "SELECT id, channel_id, title, scheduled_ts, duration_minutes, creator_id FROM scheduled_predictions WHERE status = 'pending' ORDER BY scheduled_ts ASC"
        )
        if not rows:
            return await interaction.response.edit_message(content="No pending scheduled predictions.", embed=None, view=None)

        view = ScheduledPredSelectView(rows, self.client)
        await interaction.response.edit_message(content="Choose a scheduled prediction to manage:", embed=None, view=view)


class CancelScheduledPredView(discord.ui.View):
    """Persistent view with Edit and Cancel buttons shown on the COMMUNITY_MANAGEMENT
    announcement message for a scheduled prediction."""
    def __init__(self, sched_id: int):
        super().__init__(timeout=None)
        self.sched_id = sched_id
        self.edit_button.custom_id = f"sched_pred_edit:{sched_id}"
        self.cancel_button.custom_id = f"sched_pred_cancel:{sched_id}"

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(role.id in [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO] for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ Only staff can edit scheduled predictions.", ephemeral=True
            )

        from database import DatabaseManager
        row = DatabaseManager.fetch_one(
            "SELECT title, opt1, opt2, options_json, scheduled_ts, duration_minutes, status FROM scheduled_predictions WHERE id = ?",
            (self.sched_id,),
        )
        if not row:
            return await interaction.response.send_message(
                f"❌ Scheduled prediction #{self.sched_id} not found.", ephemeral=True
            )
        title, opt1, opt2, options_json, scheduled_ts, duration, status = row
        if status != 'pending':
            return await interaction.response.send_message(
                f"❌ Scheduled prediction #{self.sched_id} is no longer pending (status={status}); cannot edit.",
                ephemeral=True,
            )
        options = _options_from_row(opt1, opt2, options_json)
        await interaction.response.send_modal(
            PredictionEditModal(self.sched_id, title, options, scheduled_ts, duration)
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(role.id in [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO] for role in interaction.user.roles):
            return await interaction.response.send_message(
                "❌ Only staff can cancel scheduled predictions.", ephemeral=True
            )

        # Defer first so the interaction is acknowledged immediately
        await interaction.response.defer(ephemeral=True)

        success, msg = await cancel_scheduled_prediction(
            interaction.client, self.sched_id, interaction.user.mention
        )
        if not success:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        else:
            await interaction.followup.send(f"✅ {msg}", ephemeral=True)



async def post_scheduled_prediction(client: discord.Client, sched_id: int) -> bool:
    """Post a prediction that was previously scheduled. Returns True on success."""
    import logging
    logger = logging.getLogger(__name__)
    from database import DatabaseManager

    row = DatabaseManager.fetch_one(
        "SELECT channel_id, title, opt1, opt2, options_json, duration_minutes, status, cm_message_id FROM scheduled_predictions WHERE id = ?",
        (sched_id,),
    )
    if not row:
        logger.warning(f"Scheduled pred {sched_id} not found.")
        return False
    channel_id, title, opt1, opt2, options_json, duration, status, cm_message_id = row
    options = _options_from_row(opt1, opt2, options_json)
    if status != 'pending':
        logger.info(f"Scheduled pred {sched_id} not pending (status={status}); skipping.")
        return False

    channel = client.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except Exception as e:
            logger.error(f"Scheduled pred {sched_id}: cannot fetch channel {channel_id}: {e}")
            DatabaseManager.execute(
                "UPDATE scheduled_predictions SET status = 'failed' WHERE id = ?", (sched_id,)
            )
            return False

    end_ts = discord.utils.utcnow().timestamp() + duration * 60
    try:
        p = await post_prediction(client, channel, title, options, end_ts)
    except Exception as e:
        logger.error(f"Scheduled pred {sched_id}: send failed: {e}")
        DatabaseManager.execute(
            "UPDATE scheduled_predictions SET status = 'failed' WHERE id = ?", (sched_id,)
        )
        return False

    DatabaseManager.execute(
        "UPDATE scheduled_predictions SET status = 'posted' WHERE id = ?", (sched_id,)
    )
    logger.info(f"Posted scheduled prediction {sched_id} as msg {p.msg_id} in channel {p.channel_id}.")

    if cm_message_id:
        try:
            cm_channel = client.get_channel(CHANNELS.COMMUNITY_MANAGEMENT)
            if cm_channel is not None:
                cm_msg = await cm_channel.fetch_message(int(cm_message_id))
                disabled_view = CancelScheduledPredView(sched_id)
                for item in disabled_view.children:
                    item.disabled = True
                cm_embed = cm_msg.embeds[0] if cm_msg.embeds else discord.Embed()
                cm_embed.title = "✅ Prediction Scheduled (Posted)"
                cm_embed.color = 0x57F287
                guild_id = getattr(getattr(channel, "guild", None), "id", None)
                jump = f"https://discord.com/channels/{guild_id}/{p.channel_id}/{p.msg_id}" if guild_id else None
                if jump:
                    cm_embed.add_field(name="Live message", value=f"[jump]({jump})", inline=True)
                await cm_msg.edit(embed=cm_embed, view=disabled_view)
        except Exception as e:
            logger.warning(f"Could not update CM announcement for scheduled pred {sched_id}: {e}")

    return True
