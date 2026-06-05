"""HMS Wordle - a daily 5-letter word puzzle that pays UKPence.

One shared word per UK day (deterministic from the date, so everyone gets the same one),
6 guesses, classic green/yellow/grey feedback. Guesses are validated against a bundled
dictionary. Solving pays from the house bank on a sliding scale (fewer guesses = more),
once per person per day. The board is an ephemeral message; a popup box takes each guess.

State lives in WORDLE_STATE_FILE keyed by the current date, so it survives restarts and the
ephemeral being dismissed (just run /wordle again to resume today's board).
"""

import datetime
import logging
import random as _random

import discord
import pytz

import config
from lib.core.file_operations import load_json_file, save_json_file
from lib.economy.economy_manager import add_bb

log = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_EPOCH = datetime.date(2024, 1, 1)
_SQUARES = {"correct": "\U0001f7e9", "present": "\U0001f7e8", "absent": "⬛"}

# --- word lists (loaded once) --------------------------------------------------
def _load_words():
    try:
        valid = set(open(config.WORDLE_VALID_FILE).read().split())
        answers = sorted({w for w in open(config.WORDLE_ANSWERS_FILE).read().split()
                          if len(w) == 5 and w.isalpha() and w in valid})
        valid |= set(answers)  # every answer must be an accepted guess
        # Fixed-seed shuffle so the daily sequence isn't alphabetical but is stable across runs.
        _random.Random(1805).shuffle(answers)
        return valid, answers
    except Exception:
        log.error("HMS Wordle: failed to load word lists", exc_info=True)
        return set(), []


_VALID, _ANSWERS = _load_words()
_READY = bool(_ANSWERS)


# --- core helpers --------------------------------------------------------------
def _today():
    return datetime.datetime.now(_UK).date()


def _pretty(d):
    return d.strftime("%-d %b")


def _todays_word(d):
    return _ANSWERS[(d - _EPOCH).days % len(_ANSWERS)]


def _score(guess, answer):
    """Standard Wordle scoring: greens first, then yellows accounting for letter counts."""
    res = ["absent"] * 5
    # Pool of answer letters not consumed by a green (so duplicate letters score correctly).
    pool = [answer[i] if guess[i] != answer[i] else None for i in range(5)]
    for i in range(5):
        if guess[i] == answer[i]:
            res[i] = "correct"
    for i, ch in enumerate(guess):
        if res[i] == "correct":
            continue
        if ch in pool:
            res[i] = "present"
            pool[pool.index(ch)] = None
    return res


def _load_state():
    return load_json_file(config.WORDLE_STATE_FILE) or {}


def _day_players(state, date_str):
    if state.get("date") != date_str:
        state["date"] = date_str
        state["players"] = {}
    return state.setdefault("players", {})


def _player(date_str, uid):
    players = _day_players(_load_state(), date_str)
    return players.get(str(uid), {"guesses": [], "solved": False, "done": False, "rewarded": False})


# --- guess submission ----------------------------------------------------------
def _submit_guess(uid, date_str, word, guess):
    guess = guess.strip().lower()
    if len(guess) != 5 or not guess.isalpha():
        return "invalid", "Enter a five-letter word.", None
    if guess not in _VALID:
        return "invalid", f"**{guess.upper()}** isn't in the word list.", None
    state = _load_state()
    players = _day_players(state, date_str)
    p = players.setdefault(str(uid), {"guesses": [], "solved": False, "done": False, "rewarded": False})
    if p["done"]:
        return "done", None, p
    if guess in p["guesses"]:
        return "invalid", "You've already tried that word.", None
    p["guesses"].append(guess)
    if guess == word:
        p["solved"] = True
        p["done"] = True
    elif len(p["guesses"]) >= 6:
        p["done"] = True
    save_json_file(config.WORDLE_STATE_FILE, state)
    return "ok", None, p


def _mark_rewarded(uid, date_str):
    state = _load_state()
    players = _day_players(state, date_str)
    if str(uid) in players:
        players[str(uid)]["rewarded"] = True
        save_json_file(config.WORDLE_STATE_FILE, state)


# --- rendering -----------------------------------------------------------------
def _rows(guesses, word):
    return ["".join(_SQUARES[s] for s in _score(g, word)) + f"  `{g.upper()}`" for g in guesses]


def _share_block(p, word, date):
    n = len(p["guesses"]) if p["solved"] else "X"
    grid = "\n".join("".join(_SQUARES[s] for s in _score(g, word)) for g in p["guesses"])
    return f"```\nHMS Wordle · {_pretty(date)} · {n}/6\n{grid}\n```"


_KB_ROWS = ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM")
_RANK = {"absent": 0, "present": 1, "correct": 2}


def _keyboard(p, word):
    """Letter tracker: each letter's best-known status across all guesses. Ruled-out letters
    are blanked to ⬛; confirmed letters are listed so you can see what's still in play."""
    status = {}
    for g in p["guesses"]:
        for st, ch in zip(_score(g, word), g.upper()):
            if ch not in status or _RANK[st] > _RANK[status[ch]]:
                status[ch] = st
    rows = []
    for row in _KB_ROWS:
        rows.append(" ".join("⬛" if status.get(ch) == "absent" else ch for ch in row))
    greens = [ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if status.get(ch) == "correct"]
    yellows = [ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if status.get(ch) == "present"]
    out = "\n".join(rows)
    hint = []
    if greens:
        hint.append("\U0001f7e9 " + " ".join(greens))
    if yellows:
        hint.append("\U0001f7e8 " + " ".join(yellows))
    if hint:
        out += "\n" + "    ".join(hint)
    return out


def _render(uid, date):
    word = _todays_word(date)
    p = _player(date.isoformat(), uid)
    lines = [f"## \U0001f7e9 HMS Wordle · {_pretty(date)}"]
    if not p["guesses"]:
        lines.append("Guess the **five-letter word** - you've got 6 tries. Tap **Guess** to start.")
    lines += _rows(p["guesses"], word)
    if p["solved"]:
        n = len(p["guesses"])
        reward = config.WORDLE_REWARDS[n - 1]
        lines.append(f"\n**Solved in {n}/6!** **+{reward:,} UKPence** \U0001f389")
        lines.append(_share_block(p, word, date))
    elif p["done"]:
        lines.append(f"\nOut of guesses - the word was **{word.upper()}**. Back tomorrow for a new one.")
        lines.append(_share_block(p, word, date))
    else:
        if p["guesses"]:
            lines.append(_keyboard(p, word))
        left = 6 - len(p["guesses"])
        lines.append(f"-# {left} guess{'es' if left != 1 else ''} left · "
                     f"\U0001f7e9 right spot · \U0001f7e8 wrong spot · ⬛ ruled out")
    return "\n".join(lines), p["done"]


# --- image board ---------------------------------------------------------------
_TILE = {"correct": "#6aaa64", "present": "#c9b458", "absent": "#3a3a3c"}


def _board_html(uid, date):
    word = _todays_word(date)
    p = _player(date.isoformat(), uid)
    guesses = p["guesses"]
    rows = []
    for r in range(6):
        if r < len(guesses):
            g = guesses[r]
            sc = _score(g, word)
            tiles = "".join(f'<div class="t {s}">{g[i].upper()}</div>' for i, s in enumerate(sc))
        else:
            tiles = '<div class="t e"></div>' * 5
        rows.append(f'<div class="row">{tiles}</div>')
    status = {}
    for g in guesses:
        for s, ch in zip(_score(g, word), g.upper()):
            if ch not in status or _RANK[s] > _RANK[status[ch]]:
                status[ch] = s
    kb = []
    for row in _KB_ROWS:
        keys = "".join(f'<div class="k {status.get(ch, "u")}">{ch}</div>' for ch in row)
        kb.append(f'<div class="krow">{keys}</div>')
    if p["solved"]:
        n = len(guesses)
        sub = f"Solved in {n}/6 · +{config.WORDLE_REWARDS[n - 1]:,} UKPence"
    elif p["done"]:
        sub = f"Out of guesses · the word was {word.upper()}"
    else:
        left = 6 - len(guesses)
        sub = f"{left} guess{'es' if left != 1 else ''} left"
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@600;800&family=Outfit:wght@800&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}html,body{{overflow:hidden}}::-webkit-scrollbar{{width:0;height:0}}
body{{background:#0a0e1a;display:flex;justify-content:center;padding:18px;font-family:'Inter',sans-serif}}
.card{{background:#121624;border:4px solid #CF142B;border-radius:18px;padding:22px 26px 24px;
 box-shadow:0 14px 44px rgba(0,0,0,.55)}}
.title{{font-family:'Outfit',sans-serif;font-weight:800;color:#fff;font-size:26px;text-align:center;letter-spacing:.5px}}
.date{{color:rgba(255,255,255,.45);font-size:14px;text-align:center;margin:2px 0 16px}}
.grid{{display:flex;flex-direction:column;gap:7px;align-items:center}}
.row{{display:flex;gap:7px}}
.t{{width:58px;height:58px;border-radius:6px;display:flex;align-items:center;justify-content:center;
 font-weight:800;font-size:30px;color:#fff;text-transform:uppercase}}
.t.correct{{background:#6aaa64}}.t.present{{background:#c9b458}}.t.absent{{background:#3a3a3c}}
.t.e{{background:transparent;border:2px solid #2b2f3a}}
.kb{{display:flex;flex-direction:column;gap:6px;align-items:center;margin-top:18px}}
.krow{{display:flex;gap:5px}}
.k{{min-width:30px;height:42px;padding:0 7px;border-radius:5px;display:flex;align-items:center;justify-content:center;
 font-weight:700;font-size:16px;color:#fff;background:#818384}}
.k.correct{{background:#6aaa64}}.k.present{{background:#c9b458}}.k.absent{{background:#2b2f3a;color:#6b6f78}}
.sub{{color:rgba(255,255,255,.6);font-size:15px;text-align:center;margin-top:16px}}
</style></head><body><div class='card'>
<div class='title'>\U0001f1ec\U0001f1e7 HMS Wordle</div><div class='date'>{_pretty(date)}</div>
<div class='grid'>{''.join(rows)}</div>
<div class='kb'>{''.join(kb)}</div>
<div class='sub'>{sub}</div>
</div></body></html>"""


async def render_board(uid, date):
    """Render the board to a PNG BytesIO + done flag. Returns (None, done) if rendering fails."""
    p = _player(date.isoformat(), uid)
    try:
        from lib.core.image_processing import screenshot_html
        img = await screenshot_html(_board_html(uid, date), size=(520, 760), apply_trim=True)
        return img, p["done"]
    except Exception:
        log.error("HMS Wordle board render failed", exc_info=True)
        return None, p["done"]


# --- UI ------------------------------------------------------------------------
class WordleModal(discord.ui.Modal, title="HMS Wordle"):
    guess = discord.ui.TextInput(label="Your guess", placeholder="a five-letter word",
                                 min_length=5, max_length=5)

    def __init__(self, user_id, date):
        super().__init__()
        self.user_id = user_id
        self.date = date

    async def on_submit(self, interaction: discord.Interaction):
        word = _todays_word(self.date)
        status, err, p = _submit_guess(self.user_id, self.date.isoformat(), word, str(self.guess.value))
        if status == "invalid":
            await interaction.response.send_message(err, ephemeral=True)
            return
        if status == "ok" and p["solved"] and not p["rewarded"]:
            reward = config.WORDLE_REWARDS[len(p["guesses"]) - 1]
            if add_bb(int(self.user_id), reward, reason="HMS Wordle solve"):
                _mark_rewarded(self.user_id, self.date.isoformat())
                try:
                    from lib.features.income_badges import record_income_source
                    await record_income_source(interaction.client, self.user_id, "wordle")
                except Exception:
                    pass
        await interaction.response.defer()
        img, done = await render_board(self.user_id, self.date)
        view = _view_for(self.user_id, self.date, done)
        if img is not None:
            await interaction.edit_original_response(
                content=None, attachments=[discord.File(img, "wordle.png")], view=view)
        else:
            content, _ = _render(self.user_id, self.date)
            await interaction.edit_original_response(content=content, attachments=[], view=view)


class WordleView(discord.ui.View):
    def __init__(self, user_id, date):
        super().__init__(timeout=600)
        self.user_id = int(user_id)
        self.date = date

    @discord.ui.button(label="Guess", emoji="✏️", style=discord.ButtonStyle.primary)
    async def guess(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your game.", ephemeral=True)
            return
        await interaction.response.send_modal(WordleModal(self.user_id, self.date))


class WordleShareView(discord.ui.View):
    """Shown once the day's game is over: a button to post your spoiler-free grid to the
    channel, tagging you."""

    def __init__(self, user_id, date):
        super().__init__(timeout=600)
        self.user_id = int(user_id)
        self.date = date

    @discord.ui.button(label="Share result", emoji="\U0001f4e3", style=discord.ButtonStyle.success)
    async def share(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your result.", ephemeral=True)
            return
        word = _todays_word(self.date)
        p = _player(self.date.isoformat(), self.user_id)
        n = len(p["guesses"]) if p["solved"] else "X"
        verb = (f"solved today's **HMS Wordle** in **{n}/6**" if p["solved"]
                else f"played today's **HMS Wordle** (**{n}/6**)")
        grid = "\n".join("".join(_SQUARES[s] for s in _score(g, word)) for g in p["guesses"])
        try:
            await interaction.channel.send(
                f"\U0001f7e9 <@{self.user_id}> {verb}!\n{grid}",
                allowed_mentions=discord.AllowedMentions(users=True))
        except Exception:
            await interaction.response.send_message("Couldn't post to the channel here.", ephemeral=True)
            return
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Shared to the channel!", ephemeral=True)


def _view_for(user_id, date, done):
    return WordleShareView(user_id, date) if done else WordleView(user_id, date)


async def handle_wordle_command(interaction: discord.Interaction):
    if not _READY:
        await interaction.response.send_message(
            "HMS Wordle's word list isn't loaded right now, try again later.", ephemeral=True)
        return
    date = _today()
    await interaction.response.defer(ephemeral=True, thinking=True)
    img, done = await render_board(interaction.user.id, date)
    view = _view_for(interaction.user.id, date, done)
    if img is not None:
        await interaction.followup.send(
            file=discord.File(img, "wordle.png"), view=view, ephemeral=True)
    else:
        content, _ = _render(interaction.user.id, date)
        await interaction.followup.send(content=content, view=view, ephemeral=True)
