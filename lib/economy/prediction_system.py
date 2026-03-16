import json, os, io, discord
from typing import Optional, Union
from PIL import Image, ImageDraw
import uuid
from functools import lru_cache
from lib.economy.economy_manager import add_bb, remove_bb, get_bb
from config import ROLES, PREDICTIONS_FILE, PREDICTION_STREAKS_FILE

def _load() -> dict:
    return json.load(open(PREDICTIONS_FILE)) if os.path.exists(PREDICTIONS_FILE) else {}

def _save(d: dict) -> None:
    json.dump(d, open(PREDICTIONS_FILE, "w"), indent=4)

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
        
    json.dump(data, open(PREDICTION_STREAKS_FILE, "w"), indent=4)
    return data[uid]["win_streak"], data[uid]["lose_streak"]

class Prediction:
    def __init__(self, msg_id: int, title: str, opt1: str, opt2: str, end_ts: float, channel_id: Optional[int] = None):
        self.msg_id = msg_id
        self.channel_id = channel_id
        self.title = title
        self.opt1 = opt1
        self.opt2 = opt2
        self.bets = {1: {}, 2: {}}
        self.locked = False
        self.end_ts = end_ts
        self.last_bet_times = {} # uid -> timestamp
        self.initial_balances = {} # uid -> balance before first bet on this pred

    def stake(self, uid: int, side: int, amount: int) -> bool:
        if self.locked or side not in (1, 2) or uid in self.bets[3 - side]:
            return False
        side_name = self.opt1 if side == 1 else self.opt2
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

    def totals(self) -> tuple[int, int]:
        return sum(self.bets.get(1, {}).values()), sum(self.bets.get(2, {}).values())

    def resolve(self, win_side: int) -> dict[int, int]:
        # This is used by PredAdminView._resolve
        # Calculate winning payouts based on ratio
        t1, t2 = self.totals()
        win_total = t1 if win_side == 1 else t2
        lose_total = t2 if win_side == 1 else t1
        pool_total = win_total + lose_total
        
        payouts = {}
        if win_total == 0:
            # Nobody won, so everything goes back to the bank
            if pool_total > 0:
                from lib.economy.bank_manager import BankManager
                BankManager.deposit(float(pool_total), description=f"Prediction unclaimed pool (No winners): {self.title[:50]}")
            return payouts
            
        ratio = pool_total / win_total
        distributed_total = 0
        
        for uid, stake in self.bets.get(win_side, {}).items():
            payout = int(stake * ratio)
            payouts[uid] = payout
            distributed_total += payout
            add_bb(uid, payout, reason=f"Prediction win: {self.title[:50]}")
            
            # Double or Nothing Check: Bet > 50% of balance
            initial_bal = self.initial_balances.get(uid, 0)
            if initial_bal > 0:
                total_bet = self.bets[win_side].get(uid, 0)
                if total_bet > (initial_bal / 2):
                    # We can't award with notify easily here without a client,
                    # but we'll award silently and it'll show in /rank.
                    from database import award_badge
                    award_badge(uid, 'double_or_nothing')
        
        # Collect rounding dust
        dust = pool_total - distributed_total
        if dust > 0:
            from lib.economy.bank_manager import BankManager
            BankManager.deposit(float(dust), description=f"Prediction rounding dust: {self.title[:50]}")
            
        return payouts

    def to_dict(self) -> dict:
        bets_dump = {str(side): {str(uid): amt for uid, amt in pool.items()} for side, pool in self.bets.items()}
        return {
            "msg_id": self.msg_id,
            "channel_id": self.channel_id,
            "title": self.title,
            "opt1": self.opt1,
            "opt2": self.opt2,
            "bets": bets_dump,
            "locked": self.locked,
            "end": self.end_ts,
            "initial_balances": {str(uid): bal for uid, bal in self.initial_balances.items()}
        }


    @staticmethod
    def from_dict(d: dict):
        p = Prediction(d["msg_id"], d["title"], d["opt1"], d["opt2"], d["end"], d.get("channel_id"))
        p.bets = {int(side): {int(uid): amt for uid, amt in pool.items()} for side, pool in d["bets"].items()}
        p.locked = d["locked"]
        p.initial_balances = {int(uid): bal for uid, bal in d.get("initial_balances", {}).items()}
        return p


@lru_cache(maxsize=101)
def _progress_png_bytes(pct_int: int) -> bytes:
    pct = pct_int / 100.0
    W, H = 400, 18
    green, blurple = (46, 204, 113), (88, 101, 242)
    img = Image.new("RGB", (W, H), blurple)
    ImageDraw.Draw(img).rectangle([0, 0, int(W * pct), H], fill=green)
    buff = io.BytesIO()
    img.save(buff, format="PNG")
    return buff.getvalue()

def _progress_png(pct: float) -> io.BytesIO:
    pct_int = int(pct * 100)
    img_bytes = _progress_png_bytes(pct_int)
    return io.BytesIO(img_bytes)

CASH, TROPHY, USER, COIN, MEDAL = "💰", "🏆", "👥", "🪙", "🏅"

def _fmt_money(n: int) -> str:
    return f"{n:,}"

def _odds(t1: int, t2: int, side: int) -> float:
    win, lose = (t1, t2) if side == 1 else (t2, t1)
    return 0 if win == 0 else max((win + lose) / win, 1)

def _top_bettor(bets: dict[int, int], client: discord.Client | None) -> str:
    if not bets:
        return "-"
    uid = max(bets, key=bets.get)
    if client:
        user = client.get_user(uid)
        name = user.display_name if user else f"{uid}"
    else:
        name = f"<@{uid}>"
    return f"{name} {_fmt_money(bets[uid])}"

def prediction_embed(pred: Prediction, client: discord.Client | None = None) -> tuple[discord.Embed, discord.File]:
    t1, t2 = pred.totals()
    total = t1 + t2 or 1
    pct1 = t1 / total
    pct2 = 1 - pct1
    now = discord.utils.utcnow().timestamp()
    if pred.locked:
        time_line = "🔒 **locked**"
    elif pred.end_ts and pred.end_ts > now:
        time_line = f"⏰ closes <t:{int(pred.end_ts)}:R>"
    else:
        time_line = "🔓 **unlocked**"



    e = discord.Embed(title=pred.title, description=time_line)
    e.add_field(
        name=pred.opt1,
        value=(
            f"{CASH} **{_fmt_money(t1)}** `{int(pct1*100)}%`\n"
            f"{TROPHY} **{_odds(t1,t2,1):.2f}x**\n"
            f"{USER} {len(pred.bets.get(1, {}))}\n"
            f"{MEDAL} {_top_bettor(pred.bets.get(1, {}), client)}"
        ),
        inline=True,
    )
    e.add_field(
        name=pred.opt2,
        value=(
            f"{CASH} **{_fmt_money(t2)}** `{int(pct2*100)}%`\n"
            f"{TROPHY} **{_odds(t1,t2,2):.2f}x**\n"
            f"{USER} {len(pred.bets.get(2, {}))}\n"
            f"{MEDAL} {_top_bettor(pred.bets.get(2, {}), client)}"
        ),
        inline=True,
    )
    bar_file = discord.File(_progress_png(pct1), filename="bar.png")
    e.set_image(url="attachment://bar.png")
    return e, bar_file

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
            embed, bar = prediction_embed(self.pred, interaction.client)
            
            try:
                await interaction.message.edit(embed=embed, attachments=[bar])
            except discord.NotFound:
                pass # Message was deleted by a mod while they were typing
                
            await interaction.response.send_message(
                f"✅ Bet placed! You now have **{_fmt_money(user_total)}** on **{self.pred.opt1 if self.side==1 else self.pred.opt2}**.\n"
                f"💰 Remaining Balance: **{_fmt_money(new_balance)}**",
                ephemeral=True
            )
            _save({k: v.to_dict() for k, v in interaction.client.predictions.items()})
            
            # High Stakes Badge
            if stake >= 5000:
                from lib.bot.event_handlers import award_badge_with_notify
                await award_badge_with_notify(interaction.client, interaction.user.id, 'high_stakes')
                
        else:
            curr = get_bb(interaction.user.id)
            await interaction.response.send_message(
                f"❌ Bet failed. You have **{_fmt_money(curr)}** UKPence.\n"
                "Ensure you have enough balance and aren't exceeding the **100,000** limit.", 
                ephemeral=True
            )

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

        btn1 = discord.ui.Button(
            label=f"Bet on {pred.opt1}",
            style=discord.ButtonStyle.success,
            custom_id = f"prediction:{pred.msg_id}:bet1"
        )
        btn2 = discord.ui.Button(
            label=f"Bet on {pred.opt2}",
            style=discord.ButtonStyle.primary,
            custom_id = f"prediction:{pred.msg_id}:bet2"
        )

        async def btn1_cb(interaction: discord.Interaction):
            await _handler(interaction, 1)

        async def btn2_cb(interaction: discord.Interaction):
            await _handler(interaction, 2)

        btn1.callback = btn1_cb
        btn2.callback = btn2_cb

        self.add_item(btn1)
        self.add_item(btn2)

        # Notification toggle button
        notif_btn = discord.ui.Button(
            label="🔔",
            style=discord.ButtonStyle.secondary,
            custom_id=f"prediction:{pred.msg_id}:notif_toggle"
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
                description=f"ID: {p.msg_id} | {p.opt1} vs {p.opt2}",
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
        # Dynamically set button labels to actual option text
        self.win1.label = f"Winner: {pred.opt1[:70]}"
        self.win2.label = f"Winner: {pred.opt2[:70]}"

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.danger)
    async def lock(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message("Already locked.", ephemeral=True)
        self.pred.locked = True
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, bar = prediction_embed(self.pred, self.client)
            await msg.edit(embed=embed, attachments=[bar], view=None)
        except discord.NotFound:
            pass
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message("🔒 Locked.", ephemeral=True)
        
        # Indecisive Badge Logic
        import time
        now = time.time()
        from lib.bot.event_handlers import award_badge_with_notify
        for uid, bet_time in self.pred.last_bet_times.items():
            if now - bet_time <= 10:
                await award_badge_with_notify(self.client, uid, 'indecisive')

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.success)
    async def unlock(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not self.pred.locked:
            return await interaction.response.send_message("Already unlocked.", ephemeral=True)

        self.pred.locked = False
        self.pred.end_ts = None

        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, bar = prediction_embed(self.pred, self.client)
            view = BetButtons(self.pred)
            await msg.edit(embed=embed, attachments=[bar], view=view)
            self.client.add_view(view, message_id=self.pred.msg_id)
        except discord.NotFound:
            pass

        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message("🔓 Unlocked.", ephemeral=True)

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.secondary)
    async def draw(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        for side in (1, 2):
            for uid, amt in self.pred.bets.get(side, {}).items():
                add_bb(uid, amt, reason=f"Prediction refund (Draw): {self.pred.title[:50]}")
        self.pred.locked = True
        self.pred.bets = {1: {}, 2: {}}
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, bar = prediction_embed(self.pred, self.client)
            await msg.edit(embed=embed, attachments=[bar], view=None)
        except discord.NotFound:
            pass
        self.client.predictions.pop(self.pred.msg_id, None)
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message("🟡 Draw called – all bets refunded.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Winner: Option 1", style=discord.ButtonStyle.primary)
    async def win1(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await self._resolve(interaction, 1)

    @discord.ui.button(label="Winner: Option 2", style=discord.ButtonStyle.primary)
    async def win2(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await self._resolve(interaction, 2)

    async def _resolve(self, interaction: discord.Interaction, winner: int):
        payouts = self.pred.resolve(winner)
        win_side = self.pred.opt1 if winner == 1 else self.pred.opt2
        self.pred.locked = True
        msg = None
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, bar = prediction_embed(self.pred, self.client)
            await msg.edit(embed=embed, attachments=[bar], view=None)
        except discord.NotFound:
            pass  # Message was deleted, proceed with payout and summary

        lines = []
        for uid, amt in sorted(payouts.items(), key=lambda x: x[1], reverse=True):
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"<@{uid}>"
            lines.append(f"**{name}** won **{amt:,}**")

        descr = "\n".join(lines) or "*Nobody backed the winner*"
        summary = discord.Embed(title=f"🏁 Prediction settled: **{win_side}** wins!", description=descr, color=0x2ECC71)

        mentions = " ".join([f"<@{uid}>" for uid in payouts.keys()])
        if msg:
            await msg.reply(content=mentions, embed=summary, mention_author=False)
        else:
            await interaction.channel.send(content=f"{mentions}\nPrediction resolved (original message deleted).", embed=summary)

        self.client.predictions.pop(self.pred.msg_id, None)
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        
        # Award streak badges
        from lib.economy.prediction_system import track_prediction_streak
        from lib.bot.event_handlers import award_badge_with_notify
        import asyncio

        lose_side = 2 if winner == 1 else 1

        async def award_streaks():
            # Process winners
            for uid in self.pred.bets.get(winner, {}).keys():
                win_streak, lose_streak = track_prediction_streak(uid, is_win=True)
                if win_streak >= 5:
                    await award_badge_with_notify(self.client, uid, 'oracle')
            
            # Process losers
            for uid in self.pred.bets.get(lose_side, {}).keys():
                win_streak, lose_streak = track_prediction_streak(uid, is_win=False)
                if lose_streak >= 5:
                    await award_badge_with_notify(self.client, uid, 'unlucky')

        asyncio.create_task(award_streaks())
        
        # If the interaction message (admin panel) is still valid, respond there too
        try:
             if not interaction.response.is_done():
                 await interaction.response.send_message("✅ Resolved & paid out.", ephemeral=True)
             else:
                 await interaction.followup.send("✅ Resolved & paid out.", ephemeral=True)
        except discord.NotFound:
             pass # Admin interaction might be old/invalid too
        self.stop()
