import os, json
from lib.prediction_system import Prediction

BRITBUCKS_FILE="britbucks.json"

SHOP={"shutcoin":1000}

def _load():
    return json.load(open(BRITBUCKS_FILE)) if os.path.exists(BRITBUCKS_FILE) else {}

def _save(d):
    json.dump(d,open(BRITBUCKS_FILE,"w"),indent=4)

def get_bb(uid): return _load().get(str(uid),0)

def set_bb(uid,amt):
    d=_load(); d[str(uid)]=amt; _save(d)

def add_bb(uid,amt): set_bb(uid,get_bb(uid)+amt)

def remove_bb(uid,amt):
    bal=get_bb(uid)
    if amt>bal: return False
    set_bb(uid,bal-amt); return True

def prediction_embed(pred:Prediction):
    t1,t2=pred.totals()
    total=t1+t2
    pct1=f"{int(t1/total*100) if total else 50}%"
    pct2=f"{int(t2/total*100) if total else 50}%"
    e=discord.Embed(title=pred.title)
    e.add_field(name=pred.opt1,value=f"{pct1} – {t1:,}",inline=True)
    e.add_field(name=pred.opt2,value=f"{pct2} – {t2:,}",inline=True)
    bar=int((t1/total if total else 0.5)*20)
    e.description="█"*bar+"░"*(20-bar)
    return e

class BetModal(discord.ui.Modal,title="Place your bet"):
    amount=discord.ui.TextInput(label="Amount",placeholder="Number ≤100000")
    def __init__(self,pred,side):
        super().__init__()
        self.pred=pred; self.side=side
    async def on_submit(self,interaction):
        if self.pred.stake(interaction.user.id,self.side,int(self.amount.value)):
            await interaction.response.send_message(f"Bet placed!",ephemeral=True)
            await interaction.message.edit(embed=prediction_embed(self.pred))
            _save({k:v.to_dict() for k,v in interaction.client.predictions.items()})
        else:
            await interaction.response.send_message("Bet failed.",ephemeral=True)

class BetButtons(discord.ui.View):
    def __init__(self,pred):
        super().__init__(timeout=None); self.pred=pred
    @discord.ui.button(label="Bet on Option 1",style=discord.ButtonStyle.success)
    async def bet1(self,interaction,button):
        if self.pred.locked: return await interaction.response.send_message("Locked.",ephemeral=True)
        await interaction.response.send_modal(BetModal(self.pred,1))
    @discord.ui.button(label="Bet on Option 2",style=discord.ButtonStyle.primary)
    async def bet2(self,interaction,button):
        if self.pred.locked: return await interaction.response.send_message("Locked.",ephemeral=True)
        await interaction.response.send_modal(BetModal(self.pred,2))
