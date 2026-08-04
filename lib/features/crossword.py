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

    seen = _letters(puzzle, p)

    def next_idx(e):
        """First letter of this entry the player can't already see, or None.

        Skipping letters a crossing has already filled in matters: reveal them and the
        hint costs a whole reward tier while putting nothing new on the board.
        """
        k = _key(e)
        for i, cell in enumerate(e["cells"]):
            if tuple(cell) in seen or f"{k}:{i}" in p["revealed"]:
                continue
            return i
        return None

    # Shortest unsolved entry that still has something to give. Skipping the exhausted
    # ones matters too: otherwise a player who reveals a whole 3-letter word gets stuck
    # on it forever and can never take another hint, however much of the grid is blank.
    unsolved = [(e, next_idx(e)) for e in puzzle["entries"] if _key(e) not in p["solved"]]
    unsolved = [(e, i) for e, i in unsolved if i is not None]
    if not unsolved:
        return None, p
    entry, idx = min(unsolved, key=lambda t: (len(t[0]["answer"]), t[0]["num"]))
    p["revealed"] = p["revealed"] + [f"{_key(entry)}:{idx}"]
    _cascade(puzzle, p)                  # a given letter can complete an entry outright
    if _is_complete(puzzle, p):
        p["done"] = True
    _save_player(date_str, uid, p)
    return (f"💡 **{entry['num']} {entry['dir'].title()}** has "
            f"**{entry['answer'][idx]}** at position {idx + 1}"), p


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


# --- drawing the board ----------------------------------------------------------------
# Drawn with PIL rather than the usual HTML->Chrome path, because a crossword redraws on
# EVERY answer - a dozen-plus times a puzzle - while a rank card is rendered once. Chrome
# renders share one global asyncio.Semaphore(1) with slots, blackjack, higher/lower, rank
# cards and the summaries, so each redraw queued behind whatever the casino was doing and
# the board sat on a loading spinner for seconds at a time. Straight PIL takes no lock,
# starts no browser and fetches no webfont, so it lands in milliseconds.
# First hit wins. Liberation is what the server has; the rest are so the board still
# draws (and can be eyeballed) on a dev machine.
_FONT_CANDIDATES = {
    "bold": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    "regular": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
}
_font_cache = {}


def _font(weight: str, size: int):
    """A cached truetype face, falling back to PIL's bitmap default if none is installed
    (the board still draws, it just looks plainer)."""
    key = (weight, size)
    if key in _font_cache:
        return _font_cache[key]
    from PIL import ImageFont
    face = None
    for path in _FONT_CANDIDATES[weight]:
        try:
            face = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if face is None:
        # No truetype anywhere: the bitmap default ignores `size` and kerns badly, but a
        # readable-ish board beats no board at all.
        face = ImageFont.load_default()
        log.warning("HMS Crossword: no truetype font found, falling back to the default")
    _font_cache[key] = face
    return face


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_board(uid, date):
    """The board as a PNG, drawn directly. Returns a BytesIO."""
    import io
    from PIL import Image, ImageDraw

    puzzle = _todays_puzzle(date)
    p = _player(date.isoformat(), uid)
    size = grid_size(date)
    seen = _letters(puzzle, p)
    black = {tuple(b) for b in puzzle["black"]}
    nums = _numbers(puzzle)
    revealed_cells = set()
    for e in puzzle["entries"]:
        if _key(e) in p["solved"]:
            continue
        for r in p["revealed"]:
            if r.startswith(_key(e) + ":"):
                revealed_cells.add(tuple(e["cells"][int(r.split(":")[1])]))
    solved_cells = {tuple(c) for e in puzzle["entries"] if _key(e) in p["solved"]
                    for c in e["cells"]}

    CELL, GAP, PAD, W = 54, 4, 26, 620
    grid_w = size * CELL + (size - 1) * GAP
    gx, gy = (W - grid_w) // 2, 84

    f_title, f_date = _font("bold", 27), _font("regular", 15)
    f_cell, f_num = _font("bold", 30), _font("regular", 13)
    f_head, f_clue, f_sub = _font("bold", 15), _font("regular", 14), _font("regular", 15)

    # lay the clue columns out first so the card can be sized to fit them exactly
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    col_w = (W - 2 * PAD - 26) // 2
    cols = []
    for d in ("across", "down"):
        lines = []
        for e in puzzle["entries"]:
            if e["dir"] != d:
                continue
            txt = f"{e['num']}. {e['clue']} ({len(e['answer'])})"
            for i, ln in enumerate(_wrap(scratch, txt, f_clue, col_w - 4)):
                lines.append((ln, _key(e) in p["solved"], i))
        cols.append(lines)
    clue_y = gy + size * CELL + (size - 1) * GAP + 22
    body_h = max(len(c) for c in cols) * 21 + 26
    H = clue_y + body_h + 46

    img = Image.new("RGB", (W + 36, H + 36), "#0a0e1a")
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle([18, 18, W + 18, H + 18], 18, fill="#121624", outline="#CF142B", width=4)

    def at(x, y):
        return x + 18, y + 18

    dr.text(at(W // 2, 30), "HMS Crossword", font=f_title, fill="#ffffff", anchor="mt")
    dr.text(at(W // 2, 62), _pretty(date), font=f_date, fill="#6f7686", anchor="mt")

    for r in range(size):
        for c in range(size):
            x, y = gx + c * (CELL + GAP), gy + r * (CELL + GAP)
            box = [*at(x, y), *at(x + CELL, y + CELL)]
            if (r, c) in black:
                dr.rounded_rectangle(box, 4, fill="#1c2030")
                continue
            if (r, c) in solved_cells:
                bg, fg, nfg = "#6aaa64", "#ffffff", "#d8ecd5"
            elif (r, c) in revealed_cells:
                bg, fg, nfg = "#c9b458", "#11141c", "#6b6033"
            else:
                bg, fg, nfg = "#f5f6f8", "#11141c", "#5a5f6b"
            dr.rounded_rectangle(box, 4, fill=bg)
            if (r, c) in nums:
                dr.text(at(x + 5, y + 3), str(nums[(r, c)]), font=f_num, fill=nfg)
            ch = seen.get((r, c))
            if ch:
                dr.text(at(x + CELL // 2, y + CELL // 2 + 2), ch, font=f_cell,
                        fill=fg, anchor="mm")

    for i, (head, lines) in enumerate(zip(("ACROSS", "DOWN"), cols)):
        cx = PAD + i * (col_w + 26)
        dr.text(at(cx, clue_y), head, font=f_head, fill="#CF142B")
        for j, (ln, done, wrapped) in enumerate(lines):
            dr.text(at(cx + (12 if wrapped else 0), clue_y + 24 + j * 21), ln,
                    font=f_clue, fill="#4c515e" if done else "#d2d6de")

    r = rules(date)
    hints, wrong_tiers = penalties(p, date)
    total, got = len(puzzle["entries"]), len(set(p["solved"]))
    if p["done"]:
        sub = f"Complete · +{reward_for(p, date):,} UKPence"
    else:
        sub = f"{got}/{total} filled · worth {reward_for(p, date):,}"
        if r["max_hints"]:
            sub += f" · {r['max_hints'] - hints} hint(s) left"
        if r["wrong_per_tier"] and int(p.get("wrong", 0)):
            sub += f" · {p['wrong']} wrong"
    dr.text(at(W // 2, H - 34), sub, font=f_sub, fill="#8f96a5", anchor="mt")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


async def render_board(uid, date):
    """(PNG BytesIO or None, done flag). Best-effort - a failure falls back to the text
    board rather than losing the player's game.

    Returns None while CROSSWORD_IMAGE_ENABLED is off, so the flag holds wherever this is
    called rather than only on the path that happens to check it.
    """
    p = _player(date.isoformat(), uid)
    if not getattr(config, "CROSSWORD_IMAGE_ENABLED", False):
        return None, p["done"]
    try:
        return draw_board(uid, date), p["done"]
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
    """Redraw the board in place.

    Text by default, not a picture. The board is an EPHEMERAL message, and Discord
    handles image attachments on those badly - they sit on a grey placeholder for
    seconds regardless of connection, and re-uploading a fresh PNG on every answer makes
    it worse. Drawing the PNG locally took that from seconds to 82ms and changed nothing
    the player could see, because the wait was never the render: it was the attachment.
    The native text layout arrives with the message itself, so there's nothing to wait
    for. Same call the prediction market and blackjack already made - see
    CROSSWORD_IMAGE_ENABLED, off by default.
    """
    view = CrosswordView(uid, date)
    img, _done = await render_board(uid, date)      # None unless the image flag is on

    content, files, embed = text_board(uid, date), [], None
    if img is not None:
        from lib.core.image_host import as_embed_or_file
        embed, files = await as_embed_or_file(
            interaction.client, img, "crossword.png", colour=0xCF142B)
        content = None

    if edit:
        await interaction.edit_original_response(
            content=content, embed=embed, attachments=files, view=view)
    else:
        await interaction.followup.send(
            ephemeral=True, content=content, embed=embed, files=files, view=view)


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
