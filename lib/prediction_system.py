import json, os, io, discord
from PIL import Image, ImageDraw
from lib.ukpence import add_bb, remove_bb

PRED_FILE = "predictions.json"

def _load() -> dict:
    return json.load(open(PRED_FILE)) if os.path.exists(PRED_FILE) else {}

def _save(d: dict) -> None:
    json.dump(d, open(PRED_FILE, "w"), indent=4)

class Prediction:
    def __init__(self, msg_id: int, title: str, opt1: str, opt2: str, end_ts: float):
        self.msg_id = msg_id
        self.title = title
        self.opt1 = opt1
        self.opt2 = opt2
        self.bets = {1: {}, 2: {}}
        self.locked = False
        self.end_ts = end_ts

    def stake(self, uid: int, side: int, amount: int) -> bool:
        if self.locked or side not in (1, 2) or uid in self.bets[3 - side]:
            return False
        if amount > 100_000 or not remove_bb(uid, amount):
            return False
        self.bets[side][uid] = self.bets[side].get(uid, 0) + amount
        return True

    def totals(self) -> tuple[int, int]:
        return sum(self.bets[1].values()), sum(self.bets[2].values())

    def resolve(self, win_side: int) -> dict[int, int]:
        lose_side = 2 if win_side == 1 else 1
        lose_pool = sum(self.bets[lose_side].values())
        win_total = sum(self.bets[win_side].values())
        payouts = {}
        if win_total == 0:
            return payouts
        for uid, stake in self.bets[win_side].items():
            share = stake / win_total
            winnings = stake + int(share * lose_pool)
            payouts[uid] = winnings
            add_bb(uid, winnings)
        return payouts

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "title": self.title,
            "opt1": self.opt1,
            "opt2": self.opt2,
            "bets": self.bets,
            "locked": self.locked,
            "end": self.end_ts,
        }

    @staticmethod
    def from_dict(d: dict):
        p = Prediction(d["msg_id"], d["title"], d["opt1"], d["opt2"], d["end"])
        p.bets, p.locked = d["bets"], d["locked"]
        return p

def _progress_png(pct: float) -> io.BytesIO:
    W, H = 400, 18
    green, blurple = (46, 204, 113), (88, 101, 242)
    img = Image.new("RGB", (W, H), blurple)
    ImageDraw.Draw(img).rectangle([0, 0, int(W * pct), H], fill=green)
    buff = io.BytesIO()
    img.save(buff, format="PNG")
    buff.seek(0)
    return buff

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
    time_line = f"⏰ closes <t:{int(pred.end_ts)}:R>" if not pred.locked else "🔒 **locked**"
    e = discord.Embed(title=pred.title, description=time_line)

    e.add_field(
        name=pred.opt1,
        value=(
            f"{CASH} **{_fmt_money(t1)}** `{int(pct1*100)}%`\n"
            f"{TROPHY} **{_odds(t1,t2,1):.2f}x**\n"
            f"{USER} {len(pred.bets[1])}\n"
            f"{MEDAL} {_top_bettor(pred.bets[1], client)}"
        ),
        inline=True,
    )
    e.add_field(
        name=pred.opt2,
        value=(
            f"{CASH} **{_fmt_money(t2)}** `{int(pct2*100)}%`\n"
            f"{TROPHY} **{_odds(t1,t2,2):.2f}x**\n"
            f"{USER} {len(pred.bets[2])}\n"
            f"{MEDAL} {_top_bettor(pred.bets[2], client)}"
        ),
        inline=True,
    )
    bar_file = discord.File(_progress_png(pct1), filename="bar.png")
    e.set_image(url="attachment://bar.png")
    return e, bar_file

class BetModal(discord.ui.Modal, title="Place your bet"):
    amount = discord.ui.TextInput(label="Amount (≤ 100 000)", placeholder="whole number")

    def __init__(self, pred: Prediction, side: int):
        super().__init__()
        self.pred = pred
        self.side = side

    async def on_submit(self, interaction: discord.Interaction):
        try:
            stake = int(self.amount.value.replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message("Enter a valid integer.", ephemeral=True)
        if self.pred.stake(interaction.user.id, self.side, stake):
            embed, bar = prediction_embed(self.pred, interaction.client)
            await interaction.message.edit(embed=embed, attachments=[bar])
            await interaction.response.send_message("Bet placed!", ephemeral=True)
            _save({k: v.to_dict() for k, v in interaction.client.predictions.items()})
        else:
            await interaction.response.send_message("Bet failed.", ephemeral=True)

class BetButtons(discord.ui.View):
    def __init__(self, pred: Prediction):
        super().__init__(timeout=None)
        self.pred = pred
        self.bet1.label = f"Bet on {pred.opt1}"
        self.bet2.label = f"Bet on {pred.opt2}"

    @discord.ui.button(label="opt1", style=discord.ButtonStyle.success)
    async def bet1(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message("Betting locked.", ephemeral=True)
        await interaction.response.send_modal(BetModal(self.pred, 1))

    @discord.ui.button(label="opt2", style=discord.ButtonStyle.primary)
    async def bet2(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message("Betting locked.", ephemeral=True)
        await interaction.response.send_modal(BetModal(self.pred, 2))
