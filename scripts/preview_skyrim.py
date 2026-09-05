"""Export real Skyrim component payloads into an offline layout review page.

This is an approximation of Discord styling, not a Discord client. No messages
are sent; every game store is redirected into a temporary directory.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import discord
except ImportError:
    sys.path.append(str(Path.home() / ".local/share/uv/python/cpython-3.12.13-macos-aarch64-none/lib/python3.12/site-packages"))
    import discord
import config

OUTPUT = ROOT / ".workflow/ultracode/skyrim-readable-adventures/preview"


class Capture:
    def __init__(self, uid):
        self.user = SimpleNamespace(id=uid, display_name="Dovahkiin")
        self.channel_id = 1
        self.guild_id = 2
        self.client = SimpleNamespace(add_view=lambda *a, **kw: None, get_channel=lambda *a: None)
        self.last = None
        self.message = SimpleNamespace(id=888, edit=self.send)
        self.response = SimpleNamespace(edit_message=self.send, send_message=self.send,
                                        defer=AsyncMock(), is_done=lambda: False)
        self.followup = SimpleNamespace(send=self.send)
        self.channel = SimpleNamespace(id=1, send=self.send)

    async def send(self, *args, **kwargs):
        self.last = kwargs.get("view")
        return self.message


async def samples():
    from lib.features.skyrim import engine as E, data as D, views as V
    out = []
    def add(name, view, art=None):
        out.append({"name": name, "art": art, "components": view.to_components()})
    fresh = E.create_profile(88001, "Dovahkiin", "warrior")
    fresh = E.get_profile(fresh["user_id"])
    old = E.create_profile(88002, "Dovahkiin", "mage")
    old = E.get_profile(old["user_id"])
    old.update(xp=sum(D.xp_needed(l) for l in range(1, 24)), septims=25000,
               weapon_tier=5, armour_tier=5, words=3, souls=6,
               skills={k: 100 for k in E.SKILLS}, stats={**old["stats"], "delves":100,
                       "kills":400,"dragons":12,"clears":65}, allegiance="companions")
    old["homestead"]["built"] = {k:"2026-09-01" for k in D.HOMESTEAD}
    old["ingredients"] = {k: 15 for k in D.INGREDIENTS}
    E.save_profile(old)
    with patch.object(V,"_award_badges",AsyncMock()), patch.object(V,"_flush_game_log",AsyncMock()), \
         patch.object(V,"_flush_wonders",AsyncMock()):
        for name, profile in (("New player hub", fresh),("Experienced hub", old)):
            inter = Capture(profile["user_id"])
            await V._show_hub_root(inter, profile, first_response=True)
            add(name,inter.last,"hub")
        for name, fn, art in (("Character", V._hub_character, None),
                              ("Shop",V._hub_shop,None), ("Holdings",V._hub_holdings,"homestead_3"),
                              ("Notice Board",V._hub_notice,"notice_board"),
                              ("Factions",V._hub_factions,None),("Masteries",V._hub_masteries,None),
                              ("Adventure picker",V._open_location_picker,None),
                              ("Hall",V._hub_hall,"hall_of_legends")):
            inter = Capture(old["user_id"])
            await fn(inter)
            add(name,inter.last,art)
        for name,key,profile,hp in (("Combat: heavy strike","bandit_chief",old,2),
                                    ("Combat: regeneration","troll",old,3),
                                    ("Combat: airborne dragon","dragon",old,3)):
            d=E.Delve(profile["user_id"],profile["name"],1,"embershard",
                      [{"kind":"enemy","key":key,"boss":True,"resolved":False}],
                      hearts=3,enemy_hp=hp,shout_charges=3)
            if key == "bandit_chief":
                d.room["combat"] = {"intent":"charge"}
                d.hearts = 2
            view,_=V.build_delve_layout(d,profile)
            add(name,view,V._scene_art(d))
        p=copy.deepcopy(fresh)
        d=E.start_delve(p,1,"embershard",kind="tutorial")
        view,_=V.build_delve_layout(d,p)
        add("First adventure",view,V._scene_art(d))
        d=E.Delve(old["user_id"],old["name"],1,"embershard",
                  [{"kind":"event","key":"fork","story":"captive","boss":False,"resolved":False},
                   {"kind":"enemy","key":"bandit_chief","boss":True,"resolved":False}],
                  hearts=3,shout_charges=3)
        view,_=V.build_delve_layout(d,old)
        add("Connected choice",view,"fork")
        q=copy.deepcopy(old)
        d.summary={"start":{"level":23},"banked_gold":280,"lost_gold":0,
                   "banked_ingredients":{"troll_fat":2},"skill_gains":{"blade":2},
                   "task_gains":{"clear":1}}
        d.state="cleared";d.xp_gained=120;d.kills=5
        view,_=V.build_delve_layout(d,q)
        add("Run debrief",view,"victory")
        d.state="dead";d.summary.update(banked_gold=0,lost_gold=280,
                                       banked_ingredients={},lost_ingredients={"troll_fat":2})
        view,_=V.build_delve_layout(d,q)
        add("Death debrief",view,"death")
    return out


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skyrim layout review</title><style>
*{box-sizing:border-box}body{margin:0;background:#191a1e;color:#e1e2e6;font:15px/1.45 Arial, sans-serif}header{position:sticky;top:0;background:#17181ceF;padding:14px 20px;z-index:4;border-bottom:1px solid #42434a}header p{margin:4px 0 10px;font-size:12px;color:#afb2bb}select,header button{background:#343640;color:#eee;border:1px solid #545761;border-radius:5px;padding:7px 10px;margin:2px}main{padding:25px 10px}#surface{width:360px;max-width:100%;margin:auto;background:#313338;padding:12px;transition:width .2s}.author{font-size:14px;font-weight:700;color:#c4a980;margin-bottom:10px}.bot{font:10px Arial;background:#5865f2;color:white;padding:2px 4px;margin-left:5px;border-radius:3px}.gallery img{width:100%;max-height:210px;object-fit:cover;border-radius:7px;margin-bottom:7px}.box{border-left:3px solid #5a7d9a;background:#2b2d31;padding:12px;border-radius:7px;margin-bottom:8px;overflow-wrap:anywhere}.text{white-space:pre-wrap}h3{font-size:18px;line-height:1.25;margin:0 0 7px}small{font-size:12px;color:#b4b8c0}.row{display:flex;gap:7px;margin:7px 0}.row button{font:13px/1.2 Arial;min-height:34px;border:0;border-radius:4px;background:#4e5058;color:white;padding:7px 10px;white-space:nowrap;min-width:0;flex:0 1 auto}.row button.primary{background:#5865f2}.row button.success{background:#248046}.row button.danger{background:#b7393d}.row button:disabled{opacity:.45}.row select{width:100%;margin:0;background:#232428;font:13px Arial;padding:10px;color:#ddd;border:1px solid #50525d;border-radius:4px}#metrics{max-width:900px;margin:15px auto;color:#c4c7d0;font:12px/1.5 monospace}a{color:#80baff}#surface.desktop{width:780px}.rule{height:1px;background:#464851;margin:8px 0}
</style></head><body><header><strong>Skyrim · Layout review</strong><p>Actual game component payloads, approximate Discord styling. Local fixtures; no live messages or player data.</p><select id="sample"></select><button id="mobile">Mobile · 360px</button><button id="desktop">Desktop · 780px</button></header><main><div id="surface"><div class="author">HMS Victory <span class="bot">APP</span></div><div id="board"></div></div><div id="metrics"></div></main><script>
const samples=__DATA__;const sel=document.querySelector('#sample');samples.forEach((s,i)=>sel.add(new Option(s.name,i)));
const escape=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function md(s){return escape(s).replace(/&lt;@\d+&gt;/g,'@Dovahkiin').replace(/&lt;t:\d+:[A-Za-z]&gt;/g,'in 3 hours').replace(/^## (.*)$/gm,'<h3>$1</h3>').replace(/^-# (.*)$/gm,'<small>$1</small>').replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\[([^\]]+)\]\([^\)]+\)/g,'<a>$1</a>')}
function render(c,s){if(c.type===17)return `<div class="box">${(c.components||[]).map(x=>render(x,s)).join('')}</div>`;if(c.type===10)return `<div class="text">${md(c.content)}</div>`;if(c.type===12)return s.art?`<div class="gallery"><img src="/data/skyrim/${s.art}.webp" onerror="this.style.display='none'"></div>`:'';if(c.type===14)return '<div class="rule"></div>';if(c.type===1)return `<div class="row">${c.components.map(x=>render(x,s)).join('')}</div>`;if(c.type===2)return `<button class="${{1:'primary',3:'success',4:'danger'}[c.style]||''}" ${c.disabled?'disabled':''}>${escape(c.emoji?.name||'')} ${escape(c.label||'')}</button>`;if(c.type===3)return `<select><option>${escape(c.placeholder||'Choose')}</option>${(c.options||[]).map(o=>`<option>${escape(o.label)}</option>`).join('')}</select>`;return ''}
function show(){const s=samples[+sel.value];document.querySelector('#board').innerHTML=s.components.map(c=>render(c,s)).join('');requestAnimationFrame(()=>{const surface=document.querySelector('#surface'),bad=[...surface.querySelectorAll('.row')].filter(r=>r.scrollWidth>r.clientWidth+1);document.querySelector('#metrics').textContent=`${s.name} | screen ${surface.offsetWidth}px | card height ${surface.offsetHeight}px | overflowing control rows ${bad.length}`;window.previewMetrics={name:s.name,width:surface.offsetWidth,height:surface.offsetHeight,overflowRows:bad.length};});}
sel.onchange=show;document.querySelector('#mobile').onclick=()=>{document.querySelector('#surface').classList.remove('desktop');show()};document.querySelector('#desktop').onclick=()=>{document.querySelector('#surface').classList.add('desktop');show()};show();
</script></body></html>'''


def main():
    with tempfile.TemporaryDirectory(prefix="skyrim_preview_") as folder:
        for key in dir(config):
            if key.startswith("SKYRIM_") and key.endswith("_FILE") or key == "PERSISTENT_VIEWS_FILE":
                setattr(config,key,str(Path(folder)/(key.lower()+".json")))
        payload=asyncio.run(samples())
    OUTPUT.mkdir(parents=True,exist_ok=True)
    data=json.dumps(payload,ensure_ascii=False).replace('</','<\\/')
    (OUTPUT/'samples.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    (OUTPUT/'index.html').write_text(HTML.replace('__DATA__',data))
    print(f"Exported {len(payload)} actual component samples to {OUTPUT/'index.html'}")


if __name__=='__main__':
    main()
