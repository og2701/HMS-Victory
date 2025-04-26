import json, os, asyncio, discord, io
from lib.britbucks import get_bb, add_bb, remove_bb
from PIL import Image, ImageDraw


PRED_FILE="predictions.json"

def _load(): return json.load(open(PRED_FILE)) if os.path.exists(PRED_FILE) else {}

def _save(d): json.dump(d,open(PRED_FILE,"w"),indent=4)

class Prediction:
    def __init__(self,msg_id,title,opt1,opt2,end_ts):
        self.msg_id=msg_id; self.title=title; self.opt1=opt1; self.opt2=opt2
        self.bets={1:{},2:{}}; self.locked=False; self.end_ts=end_ts
    def stake(self,user_id,side,amount):
        if self.locked or side not in (1,2) or user_id in self.bets[3-side]: return False
        if amount>100000 or not remove_bb(user_id,amount): return False
        self.bets[side][user_id]=self.bets[side].get(user_id,0)+amount
        return True
    def totals(self): return sum(self.bets[1].values()),sum(self.bets[2].values())
    def resolve(self,win_side):
        lose_side=2 if win_side==1 else 1
        lose_pool=sum(self.bets[lose_side].values())
        win_total=sum(self.bets[win_side].values())
        if win_total==0: return
        for uid,bet in self.bets[win_side].items():
            share=bet/ win_total
            add_bb(uid,bet+int(share*lose_pool))
    def to_dict(self): return {"msg_id":self.msg_id,"title":self.title,"opt1":self.opt1,"opt2":self.opt2,"bets":self.bets,"locked":self.locked,"end":self.end_ts}
    @staticmethod
    def from_dict(d):
        p=Prediction(d["msg_id"],d["title"],d["opt1"],d["opt2"],d["end"])
        p.bets=d["bets"]; p.locked=d["locked"]; return p

def _progress_png(pct: float) -> io.BytesIO:
    W, H = 400, 18
    left  = (88, 101, 242)
    right = (54, 57, 63)
    img = Image.new("RGB", (W, H), right)
    ImageDraw.Draw(img).rectangle([0, 0, int(W * pct), H], fill=left)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def prediction_embed(pred):
    t1, t2 = pred.totals()
    total  = t1 + t2 or 1
    pct    = t1 / total
    e = discord.Embed(title=pred.title)
    e.add_field(name=pred.opt1, value=f"{int(pct*100)} % – {t1:,}", inline=True)
    e.add_field(name=pred.opt2, value=f"{int((1-pct)*100)} % – {t2:,}", inline=True)
    e.set_image(url="attachment://bar.png")
    bar_file = discord.File(_progress_png(pct), filename="bar.png")
    return e, bar_file
