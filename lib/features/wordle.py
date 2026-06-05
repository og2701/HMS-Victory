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
        left = 6 - len(p["guesses"])
        lines.append(f"\n-# {left} guess{'es' if left != 1 else ''} left · "
                     f"\U0001f7e9 right spot · \U0001f7e8 wrong spot · ⬛ not in word")
    return "\n".join(lines), p["done"]


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
        content, done = _render(self.user_id, self.date)
        view = None if done else WordleView(self.user_id, self.date)
        await interaction.response.edit_message(content=content, view=view)


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


async def handle_wordle_command(interaction: discord.Interaction):
    if not _READY:
        await interaction.response.send_message(
            "HMS Wordle's word list isn't loaded right now, try again later.", ephemeral=True)
        return
    date = _today()
    content, done = _render(interaction.user.id, date)
    view = None if done else WordleView(interaction.user.id, date)
    await interaction.response.send_message(content=content, view=view, ephemeral=True)
