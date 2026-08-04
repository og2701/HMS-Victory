"""HMS Crossword - a daily mini that pays UKPence.

One shared puzzle per UK day (deterministic from the date, so everyone gets the same
grid), entered clue by clue. Discord has no way to click a cell and type into it, so the
board is a rendered image and answers go in through a picker plus a popup box: choose a
clue, type the word. That sidesteps 2D input entirely and reuses the shape /wordle
already established.

Solving pays from the house bank on a sliding scale, once per person per day. Both
revealed letters and wrong answers cost a tier, so the money tracks how cleanly you
solved it rather than merely whether you finished - and there's deliberately no clock,
because a hard clue should reward thinking rather than punish it. The payout is
DISCRETIONARY, so it scales down with the bank's reserves like every other reward (see
lib/economy/reserve_policy.py).

Puzzle sets are date-gated (see _load_sets): grid size, payouts and rules all travel with
the set, so tightening any of them never changes a puzzle somebody is midway through.

State lives in CROSSWORD_STATE_FILE keyed by the current date, so it survives restarts
and the ephemeral being dismissed - just run /crossword again to resume today's grid.
"""

import datetime
import logging

import discord
import pytz

import config
from lib.core.file_operations import load_json_file, save_json_file
from lib.economy.economy_manager import add_bb

log = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_EPOCH = datetime.date(2024, 1, 1)


# --- puzzles (loaded once) ------------------------------------------------------
# Sets are date-gated. Changing the grid size, the payout or the rules must never alter a
# puzzle somebody is halfway through, so a new set takes effect from its own start date
# and everything before it keeps playing by the rules it was published under.
def _load_sets():
    try:
        data = load_json_file(config.CROSSWORD_PUZZLES_FILE) or {}
        if isinstance(data, list):        # v1: a bare list of 5x5 puzzles
            data = {"sets": [{"from": _EPOCH.isoformat(), "size": 5, "puzzles": data}]}
        sets = []
        for s in data.get("sets", []):
            good = [p for p in s.get("puzzles", []) if p.get("entries") and p.get("black") is not None]
            if good:
                sets.append({**s, "puzzles": good,
                             "_from": datetime.date.fromisoformat(s.get("from", _EPOCH.isoformat()))})
        sets.sort(key=lambda s: s["_from"])
        if not sets:
            log.error("HMS Crossword: no usable puzzles in %s", config.CROSSWORD_PUZZLES_FILE)
        return sets
    except Exception:
        log.error("HMS Crossword: puzzle file failed to load", exc_info=True)
        return []


_SETS = _load_sets()
_READY = bool(_SETS)


def _today():
    return datetime.datetime.now(_UK).date()


def _pretty(d):
    return d.strftime("%-d %b")


def active_set(d):
    """The rules in force on a given day: the last set whose start date has passed."""
    live = [s for s in _SETS if s["_from"] <= d] or _SETS[:1]
    return live[-1]


def rules(d) -> dict:
    s = active_set(d)
    return {
        "size": int(s.get("size", 5)),
        "rewards": s.get("rewards") or getattr(config, "CROSSWORD_REWARDS",
                                               [250, 200, 150, 100, 50]),
        # wrong answers cost a tier every N of them; 0 disables the penalty entirely
        "wrong_per_tier": int(s.get("wrong_per_tier", 0)),
        # most letters you may ever reveal; 0 means uncapped
        "max_hints": int(s.get("max_hints", 0)),
    }


def grid_size(d) -> int:
    return rules(d)["size"]


def _todays_puzzle(d):
    s = active_set(d)
    ps = s["puzzles"]
    return ps[max(0, (d - s["_from"]).days) % len(ps)]


def _key(entry) -> str:
    """Stable id for one clue, e.g. '3-down'."""
    return f"{entry['num']}-{entry['dir']}"


# --- state ----------------------------------------------------------------------
def _blank():
    return {"solved": [], "revealed": [], "wrong": 0, "done": False, "rewarded": False}


def _load_state():
    return load_json_file(config.CROSSWORD_STATE_FILE) or {}


def _day_players(state, date_str):
    if state.get("date") != date_str:
        state["date"] = date_str
        state["players"] = {}
    return state.setdefault("players", {})


def _player(date_str, uid):
    return _day_players(_load_state(), date_str).get(str(uid)) or _blank()


def _save_player(date_str, uid, p):
    state = _load_state()
    _day_players(state, date_str)[str(uid)] = p
    save_json_file(config.CROSSWORD_STATE_FILE, state)


def _cascade(puzzle, p) -> list:
    """Mark any entry the grid has already spelled out. Returns the keys newly filled.

    A crossword fills itself sideways: get every Down that crosses an Across and the
    Across is there on the board whether you typed it or not. Without this you could be
    staring at a completely finished grid while the game insisted you hadn't finished -
    and worse, the last clue would be unanswerable because its letters were all showing.

    Loops because one freebie can complete another.
    """
    gained = []
    while True:
        seen = _letters(puzzle, p)
        progress = False
        for e in puzzle["entries"]:
            k = _key(e)
            if k in p["solved"]:
                continue
            cells = [tuple(c) for c in e["cells"]]
            if any(c not in seen for c in cells):
                continue
            if "".join(seen[c] for c in cells) == e["answer"]:
                p["solved"] = sorted(set(p["solved"] + [k]))
                gained.append(k)
                progress = True
        if not progress:
            return gained


def _is_complete(puzzle, p) -> bool:
    return len(set(p["solved"])) >= len(puzzle["entries"])


def penalties(p, d) -> tuple:
    """(tiers_lost_to_hints, tiers_lost_to_wrong_answers) under the day's rules."""
    r = rules(d)
    hints = len(p.get("revealed", []))
    per = r["wrong_per_tier"]
    wrong = int(p.get("wrong", 0)) // per if per else 0
    return hints, wrong


def reward_for(p, d=None) -> int:
    """Payout after penalties, under the rules in force on that day.

    Hints and wrong answers both cost a tier, so the money tracks how cleanly you solved
    it rather than merely whether you finished. There's deliberately no clock: taking your
    time over a hard clue costs nothing, guessing wildly at it does.
    """
    d = d or _today()
    tiers = rules(d)["rewards"]
    hints, wrong = penalties(p, d)
    return tiers[min(hints + wrong, len(tiers) - 1)]


# --- play -----------------------------------------------------------------------
def submit(uid, date_str, puzzle, entry_key: str, guess: str):
    """(status, message, player). status: ok | wrong | invalid | already | done."""
    entry = next((e for e in puzzle["entries"] if _key(e) == entry_key), None)
    if entry is None:
        return "invalid", "That clue isn't in today's grid.", None
    p = _player(date_str, uid)
    if p["done"]:
        return "done", None, p
    if entry_key in p["solved"]:
        return "already", "You've already filled that one in.", p

    clean = "".join(ch for ch in (guess or "").upper() if ch.isalpha())
    if len(clean) != len(entry["answer"]):
        return "invalid", (f"**{entry['num']} {entry['dir'].title()}** needs "
                           f"{len(entry['answer'])} letters."), p
    if clean != entry["answer"]:
        p["wrong"] = int(p.get("wrong", 0)) + 1
        _save_player(date_str, uid, p)
        return "wrong", f"**{clean}** isn't it. Try again.", p

    p["solved"] = sorted(set(p["solved"] + [entry_key]))
    free = _cascade(puzzle, p)          # crossings may have filled others in for you
    if _is_complete(puzzle, p):
        p["done"] = True
    _save_player(date_str, uid, p)
    msg = None
    if free:
        names = ", ".join(
            f"{e['num']} {e['dir'].title()}"
            for e in puzzle["entries"] if _key(e) in free)
        msg = f"✅ The crossings filled in **{names}** too."
    return "ok", msg, p


def reveal_letter(uid, date_str, puzzle, d=None):
    """Give away one letter of the shortest unsolved entry, at the cost of a reward tier.
    Returns (message, player)."""
    p = _player(date_str, uid)
    if p["done"]:
        return None, p
    d = d or datetime.date.fromisoformat(date_str)
    cap = rules(d)["max_hints"]
    if cap and len(p.get("revealed", [])) >= cap:
        return None, p                       # hints are finite now - work the rest out

    def given(e):
        return len([r for r in p["revealed"] if r.startswith(_key(e) + ":")])

    # Shortest unsolved entry that still has a letter left to give. Skipping the
    # exhausted ones matters: otherwise a player who reveals a whole 3-letter word gets
    # stuck on it forever and can never take another hint, however much of the grid is
    # still blank.
    unsolved = [e for e in puzzle["entries"]
                if _key(e) not in p["solved"] and given(e) < len(e["answer"])]
    if not unsolved:
        return None, p
    entry = min(unsolved, key=lambda e: (len(e["answer"]), e["num"]))
    idx = given(entry)
    p["revealed"] = p["revealed"] + [f"{_key(entry)}:{idx}"]
    _save_player(date_str, uid, p)
    return (f"💡 **{entry['num']} {entry['dir'].title()}** starts "
            f"**{entry['answer'][:idx + 1]}**"), p


def _letters(puzzle, p) -> dict:
    """{(r,c): letter} for everything the player can currently see."""
    out = {}
    for e in puzzle["entries"]:
        k = _key(e)
        if k in p["solved"]:
            for i, (r, c) in enumerate(e["cells"]):
                out[(r, c)] = e["answer"][i]
        else:
            for r in p["revealed"]:
                if r.startswith(k + ":"):
                    i = int(r.split(":")[1])
                    rr, cc = e["cells"][i]
                    out[(rr, cc)] = e["answer"][i]
    return out


def _numbers(puzzle) -> dict:
    return {tuple(e["cells"][0]): e["num"] for e in puzzle["entries"]}


# --- rendering ------------------------------------------------------------------
def _board_html(uid, date):
    puzzle = _todays_puzzle(date)
    p = _player(date.isoformat(), uid)
    black = {tuple(b) for b in puzzle["black"]}
    seen = _letters(puzzle, p)
    nums = _numbers(puzzle)

    size = grid_size(date)
    cells = []
    for r in range(size):
        for c in range(size):
            if (r, c) in black:
                cells.append("<div class='c b'></div>")
                continue
            n = nums.get((r, c))
            tag = f"<span class='n'>{n}</span>" if n else ""
            ch = seen.get((r, c), "")
            solved = any(_key(e) in p["solved"] and (r, c) in [tuple(x) for x in e["cells"]]
                         for e in puzzle["entries"])
            cls = "c" + (" got" if solved and ch else (" hint" if ch else ""))
            cells.append(f"<div class='{cls}'>{tag}{ch}</div>")

    def clue_list(direction):
        rows = []
        for e in puzzle["entries"]:
            if e["dir"] != direction:
                continue
            done = _key(e) in p["solved"]
            rows.append(f"<li class='{'d' if done else ''}'>"
                        f"<b>{e['num']}</b> {e['clue']} "
                        f"<i>({len(e['answer'])})</i></li>")
        return "".join(rows)

    total = len(puzzle["entries"])
    got = len(set(p["solved"]))
    r = rules(date)
    hints, wrong_tiers = penalties(p, date)
    if p["done"]:
        sub = f"Complete · +{reward_for(p, date):,} UKPence"
    else:
        sub = f"{got}/{total} filled · worth {reward_for(p, date):,}"
        if r["max_hints"]:
            sub += f" · {r['max_hints'] - hints} hint(s) left"
        elif hints:
            sub += f" · {hints} revealed"
        # shown from the first wrong answer, not just once it has cost a tier - people
        # can't ration something they can't see coming
        if r["wrong_per_tier"] and int(p.get("wrong", 0)):
            nxt = r["wrong_per_tier"] - (int(p["wrong"]) % r["wrong_per_tier"])
            sub += f" · {p['wrong']} wrong ({nxt} to the next drop)"
    px = 64 if size <= 5 else (54 if size == 6 else 46)
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@500;600;800&family=Outfit:wght@800&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}html,body{{overflow:hidden}}::-webkit-scrollbar{{width:0;height:0}}
body{{background:#0a0e1a;display:flex;justify-content:center;padding:18px;font-family:'Inter',sans-serif}}
.card{{background:#121624;border:4px solid #CF142B;border-radius:18px;padding:22px 26px 24px;
 box-shadow:0 14px 44px rgba(0,0,0,.55);width:620px}}
.title{{font-family:'Outfit',sans-serif;font-weight:800;color:#fff;font-size:26px;text-align:center;letter-spacing:.5px}}
.date{{color:rgba(255,255,255,.45);font-size:14px;text-align:center;margin:2px 0 16px}}
.grid{{display:grid;grid-template-columns:repeat({size},{px}px);grid-gap:4px;justify-content:center}}
.c{{width:{px}px;height:{px}px;background:#f5f6f8;border-radius:4px;position:relative;
 display:flex;align-items:center;justify-content:center;font-weight:800;font-size:32px;color:#11141c}}
.c.b{{background:#1c2030}}
.c.got{{background:#6aaa64;color:#fff}}
.c.hint{{background:#c9b458;color:#11141c}}
.n{{position:absolute;top:3px;left:5px;font-size:13px;font-weight:600;color:#5a5f6b}}
.c.got .n{{color:rgba(255,255,255,.75)}}
.cols{{display:flex;gap:26px;margin-top:20px}}
.col{{flex:1}}
.h{{color:#CF142B;font-weight:800;font-size:15px;letter-spacing:1px;margin-bottom:7px}}
ul{{list-style:none}}
li{{color:rgba(255,255,255,.82);font-size:14px;line-height:1.5;margin-bottom:3px}}
li.d{{color:rgba(255,255,255,.3);text-decoration:line-through}}
li b{{color:#fff}} li i{{color:rgba(255,255,255,.4);font-style:normal}}
.sub{{color:rgba(255,255,255,.6);font-size:15px;text-align:center;margin-top:18px}}
</style></head><body><div class='card'>
<div class='title'>HMS Crossword</div><div class='date'>{_pretty(date)}</div>
<div class='grid'>{''.join(cells)}</div>
<div class='cols'>
 <div class='col'><div class='h'>ACROSS</div><ul>{clue_list('across')}</ul></div>
 <div class='col'><div class='h'>DOWN</div><ul>{clue_list('down')}</ul></div>
</div>
<div class='sub'>{sub}</div>
</div></body></html>"""


async def render_board(uid, date):
    """(PNG BytesIO or None, done flag). Rendering is best-effort - a failure falls back
    to the text board rather than losing the player's game."""
    p = _player(date.isoformat(), uid)
    try:
        from lib.core.image_processing import screenshot_html
        img = await screenshot_html(_board_html(uid, date), size=(700, 1100), apply_trim=True)
        return img, p["done"]
    except Exception:
        log.error("HMS Crossword board render failed", exc_info=True)
        return None, p["done"]


def text_board(uid, date) -> str:
    """Plain-text fallback, also used for the share block."""
    puzzle = _todays_puzzle(date)
    p = _player(date.isoformat(), uid)
    black = {tuple(b) for b in puzzle["black"]}
    seen = _letters(puzzle, p)
    size = grid_size(date)
    rows = []
    for r in range(size):
        row = []
        for c in range(size):
            row.append("⬛" if (r, c) in black else (seen.get((r, c), "·")))
        rows.append(" ".join(row))
    lines = [f"## 🧩 HMS Crossword - {_pretty(date)}", "```", *rows, "```"]
    for d in ("across", "down"):
        lines.append(f"**{d.title()}**")
        for e in puzzle["entries"]:
            if e["dir"] != d:
                continue
            mark = "~~" if _key(e) in p["solved"] else ""
            lines.append(f"-# {mark}**{e['num']}** {e['clue']} ({len(e['answer'])}){mark}")
    return "\n".join(lines)


def share_block(uid, date) -> str:
    """Spoiler-free: shape of the grid and the score, never the answers."""
    puzzle = _todays_puzzle(date)
    p = _player(date.isoformat(), uid)
    total = len(puzzle["entries"])
    got = len(set(p["solved"]))
    hint = f" · 💡{len(p['revealed'])}" if p["revealed"] else " · no hints"
    if int(p.get("wrong", 0)):
        hint += f" · ❌{p['wrong']}"
    head = "🧩 **HMS Crossword** " + _pretty(date)
    if p["done"]:
        return f"{head}\nSolved {got}/{total}{hint} · +{reward_for(p, date):,} UKPence"
    return f"{head}\n{got}/{total} filled{hint}"


# --- UI -------------------------------------------------------------------------
class AnswerModal(discord.ui.Modal, title="HMS Crossword"):
    answer = discord.ui.TextInput(label="Answer", placeholder="type the word")

    def __init__(self, user_id, date, entry):
        super().__init__()
        self.user_id = int(user_id)
        self.date = date
        self.entry = entry
        self.answer.label = f"{entry['num']} {entry['dir'].title()} ({len(entry['answer'])})"[:45]
        self.answer.placeholder = entry["clue"][:100]
        self.answer.max_length = len(entry["answer"])

    async def on_submit(self, interaction: discord.Interaction):
        puzzle = _todays_puzzle(self.date)
        status, msg, p = submit(self.user_id, self.date.isoformat(), puzzle,
                                _key(self.entry), str(self.answer.value))
        if status in ("invalid", "wrong", "already") and msg:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        if status == "ok" and p["done"] and not p["rewarded"]:
            reward = reward_for(p, self.date)
            # discretionary: this is a reward the server chooses to give, so it scales
            # down when bank reserves are low
            if add_bb(self.user_id, reward, reason="HMS Crossword solve", discretionary=True):
                p["rewarded"] = True
                _save_player(self.date.isoformat(), self.user_id, p)
                try:
                    from lib.features.income_badges import record_income_source
                    await record_income_source(interaction.client, self.user_id, "crossword")
                except Exception:
                    pass
        await interaction.response.defer()
        await _refresh(interaction, self.user_id, self.date, edit=True)


class ClueSelect(discord.ui.Select):
    def __init__(self, user_id, date, entries):
        self.user_id = int(user_id)
        self.date = date
        self._entries = {_key(e): e for e in entries}
        opts = [discord.SelectOption(
            label=f"{e['num']} {e['dir'].title()} ({len(e['answer'])})"[:100],
            value=_key(e), description=e["clue"][:100],
            emoji="➡️" if e["dir"] == "across" else "⬇️") for e in entries[:25]]
        super().__init__(placeholder="✏️ Pick a clue to answer...", options=opts)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your grid.", ephemeral=True)
            return
        entry = self._entries.get(self.values[0])
        if entry is None:
            await interaction.response.defer()
            return
        await interaction.response.send_modal(AnswerModal(self.user_id, self.date, entry))


class CrosswordView(discord.ui.View):
    def __init__(self, user_id, date):
        super().__init__(timeout=900)
        self.user_id = int(user_id)
        self.date = date
        puzzle = _todays_puzzle(date)
        p = _player(date.isoformat(), user_id)
        unsolved = [e for e in puzzle["entries"] if _key(e) not in p["solved"]]
        if not p["done"] and unsolved:
            self.add_item(ClueSelect(user_id, date, unsolved))
        else:
            self.add_item(_ShareButton(user_id, date))

    @discord.ui.button(label="Reveal a letter", emoji="💡", style=discord.ButtonStyle.secondary,
                       row=1)
    async def hint(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your grid.", ephemeral=True)
            return
        p = _player(self.date.isoformat(), self.user_id)
        if p["done"]:
            await interaction.response.defer()
            return
        msg, _p = reveal_letter(self.user_id, self.date.isoformat(),
                                _todays_puzzle(self.date), self.date)
        await interaction.response.defer()
        await _refresh(interaction, self.user_id, self.date, edit=True)
        if msg:
            await interaction.followup.send(msg, ephemeral=True)


class _ShareButton(discord.ui.Button):
    def __init__(self, user_id, date):
        super().__init__(label="Share result", emoji="📣", style=discord.ButtonStyle.primary)
        self.user_id = int(user_id)
        self.date = date

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your grid.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"<@{self.user_id}>\n{share_block(self.user_id, self.date)}",
            allowed_mentions=discord.AllowedMentions(users=True))


async def _refresh(interaction: discord.Interaction, uid, date, *, edit: bool):
    img, _done = await render_board(uid, date)
    view = CrosswordView(uid, date)
    if img is not None:
        payload = dict(content=None, attachments=[discord.File(img, "crossword.png")], view=view)
    else:
        payload = dict(content=text_board(uid, date), attachments=[], view=view)
    if edit:
        await interaction.edit_original_response(**payload)
    else:
        payload.pop("attachments", None)
        files = [discord.File(img, "crossword.png")] if img is not None else []
        await interaction.followup.send(ephemeral=True, files=files, view=view,
                                        content=payload.get("content"))


async def handle_crossword_command(interaction: discord.Interaction):
    if not _READY:
        await interaction.response.send_message(
            "The crossword isn't set up yet - no puzzles are loaded.", ephemeral=True)
        return
    date = _today()
    await interaction.response.defer(ephemeral=True, thinking=True)
    img, _done = await render_board(interaction.user.id, date)
    view = CrosswordView(interaction.user.id, date)
    if img is not None:
        await interaction.followup.send(files=[discord.File(img, "crossword.png")],
                                        view=view, ephemeral=True)
    else:
        await interaction.followup.send(content=text_board(interaction.user.id, date),
                                        view=view, ephemeral=True)
