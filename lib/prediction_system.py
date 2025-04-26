import json, os, io, discord
from PIL import Image, ImageDraw
from lib.britbucks import get_bb, add_bb, remove_bb

PRED_FILE = "predictions.json"


def _load():
    return json.load(open(PRED_FILE)) if os.path.exists(PRED_FILE) else {}


def _save(d):
    json.dump(d, open(PRED_FILE, "w"), indent=4)


class Prediction:
    def __init__(self, msg_id, title, opt1, opt2, end_ts):
        self.msg_id = msg_id
        self.title = title
        self.opt1 = opt1
        self.opt2 = opt2
        self.bets = {1: {}, 2: {}}
        self.locked = False
        self.end_ts = end_ts

    def stake(self, user_id, side, amount):
        if (
            self.locked
            or side not in (1, 2)
            or user_id in self.bets[3 - side]
            or amount > 100_000
            or not remove_bb(user_id, amount)
        ):
            return False
        self.bets[side][user_id] = self.bets[side].get(user_id, 0) + amount
        return True

    def totals(self):
        return sum(self.bets[1].values()), sum(self.bets[2].values())

    def resolve(self, win_side):
        lose_side = 2 if win_side == 1 else 1
        lose_pool = sum(self.bets[lose_side].values())
        win_total = sum(self.bets[win_side].values())
        if win_total == 0:
            return
        for uid, bet in self.bets[win_side].items():
            share = bet / win_total
            add_bb(uid, bet + int(share * lose_pool))

    def to_dict(self):
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
    def from_dict(d):
        p = Prediction(d["msg_id"], d["title"], d["opt1"], d["opt2"], d["end"])
        p.bets, p.locked = d["bets"], d["locked"]
        return p


def _progress_png(pct: float) -> io.BytesIO:
    w, h = 400, 18
    img = Image.new("RGB", (w, h), (54, 57, 63))
    ImageDraw.Draw(img).rectangle([0, 0, int(w * pct), h], fill=(88, 101, 242))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _side_block(total, pct, voters, ratio, top_uid, top_amt, guild):
    top_line = f"<@{top_uid}>" if (guild and top_uid) else "-"
    return (
        f"💰 {_fmt(total)} **{pct}%**\n"
        f"🏆 **{ratio:.2f}x**\n"
        f"👥 {voters}\n"
        f"🥇 {top_line}\n"
        f"🪙 {_fmt(top_amt)}"
    )


def prediction_embed(pred: Prediction, guild: discord.Guild | None = None):
    t1, t2 = pred.totals()
    total = (t1 + t2) or 1
    pct1 = round(t1 / total * 100)
    pct2 = 100 - pct1
    r1 = (total / t1) if t1 else 0
    r2 = (total / t2) if t2 else 0
    voters1 = len(pred.bets[1])
    voters2 = len(pred.bets[2])
    if pred.bets[1]:
        top1_uid, top1_amt = max(pred.bets[1].items(), key=lambda kv: kv[1])
    else:
        top1_uid, top1_amt = (0, 0)
    if pred.bets[2]:
        top2_uid, top2_amt = max(pred.bets[2].items(), key=lambda kv: kv[1])
    else:
        top2_uid, top2_amt = (0, 0)

    e = discord.Embed(title=pred.title, colour=0x2B2D31)
    e.add_field(
        name=pred.opt1,
        value=_side_block(t1, pct1, voters1, r1, top1_uid, top1_amt, guild),
        inline=True,
    )
    e.add_field(
        name=pred.opt2,
        value=_side_block(t2, pct2, voters2, r2, top2_uid, top2_amt, guild),
        inline=True,
    )

    pct_bar = t1 / total
    e.set_image(url="attachment://bar.png")
    bar_file = discord.File(_progress_png(pct_bar), filename="bar.png")

    if not pred.locked:
        secs_left = int(pred.end_ts - discord.utils.utcnow().timestamp())
        if secs_left > 0:
            m, s = divmod(secs_left, 60)
            e.description = f"🕒 closes in **{m} m {s} s**"
        else:
            e.description = "🔒 locked"

    return e, bar_file


class BetModal(discord.ui.Modal, title="Place your bet"):
    amount = discord.ui.TextInput(
        label="Amount (≤ 100 000)", placeholder="Whole number", required=True
    )

    def __init__(self, pred: Prediction, side: int):
        super().__init__()
        self.pred = pred
        self.side = side

    async def on_submit(self, interaction: discord.Interaction):
        try:
            stake = int(self.amount.value.replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message(
                "Enter a valid integer.", ephemeral=True
            )

        if self.pred.stake(interaction.user.id, self.side, stake):
            embed, bar = prediction_embed(self.pred, interaction.guild)
            await interaction.message.edit(embed=embed, attachments=[bar])
            await interaction.response.send_message("Bet placed!", ephemeral=True)
            _save({k: v.to_dict() for k, v in interaction.client.predictions.items()})
        else:
            await interaction.response.send_message(
                "Bet failed (locked, wrong side, >100 000, or low balance).",
                ephemeral=True,
            )


class BetButtons(discord.ui.View):
    def __init__(self, pred: Prediction):
        super().__init__(timeout=None)
        self.pred = pred

    @discord.ui.button(label="Bet on Option 1", style=discord.ButtonStyle.success)
    async def bet1(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message(
                "Betting is locked.", ephemeral=True
            )
        await interaction.response.send_modal(BetModal(self.pred, 1))

    @discord.ui.button(label="Bet on Option 2", style=discord.ButtonStyle.primary)
    async def bet2(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message(
                "Betting is locked.", ephemeral=True
            )
        await interaction.response.send_modal(BetModal(self.pred, 2))
