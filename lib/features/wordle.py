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
import time
from pathlib import Path

import discord
import pytz

import config
from lib.core.file_operations import load_json_file, save_json_file
from lib.economy.economy_manager import add_bb

log = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")
_EPOCH = datetime.date(2024, 1, 1)
UKP_ART = Path(__file__).resolve().parents[2] / "data" / "ukpence.png"
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


def _note_opened(uid, date_str) -> None:
    """Stamp when the board was first shown, so a solve can be measured against it.

    Only the FIRST open counts - reopening the board to look again would otherwise reset the
    clock and hand an easy way around the timing checks."""
    state = _load_state()
    players = _day_players(state, date_str)
    p = players.setdefault(str(uid), {"guesses": [], "solved": False, "done": False,
                                      "rewarded": False})
    if not p.get("opened_at"):
        p["opened_at"] = int(time.time())
        save_json_file(config.WORDLE_STATE_FILE, state)


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
    # Stamped per guess, not only at the end: the gap BETWEEN guesses is what separates
    # someone thinking from someone typing an answer they already had.
    p.setdefault("guess_times", []).append(int(time.time()))
    if guess == word:
        p["solved"] = True
        p["done"] = True
    elif len(p["guesses"]) >= 6:
        p["done"] = True
    save_json_file(config.WORDLE_STATE_FILE, state)
    return "ok", None, p


def _mark_rewarded(uid, date_str, paid=None):
    state = _load_state()
    players = _day_players(state, date_str)
    if str(uid) in players:
        players[str(uid)]["rewarded"] = True
        if paid is not None:
            players[str(uid)]["paid"] = int(paid)
        save_json_file(config.WORDLE_STATE_FILE, state)


def _get_consecutive_first_try(uid: int) -> int:
    """Returns how many consecutive times this user has solved Wordle on attempt 1."""
    state = _load_state()
    history = state.setdefault("first_try_streaks", {})
    return int(history.get(str(uid), 0))


def _record_solve_guesses(uid: int, guess_count: int):
    """Updates consecutive 1-guess solve counters."""
    state = _load_state()
    history = state.setdefault("first_try_streaks", {})
    if guess_count == 1:
        history[str(uid)] = int(history.get(str(uid), 0)) + 1
    else:
        history[str(uid)] = 0
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
# Chunky ivory tiles on an HMS-navy board, the same set as /crossword: green for the right
# spot, gold for the wrong spot, slate for not in the word. The coin says what the next
# guess is worth while you're playing, and what you won once you're done.
_FACE = {"correct": ("#2F8F5B", "#1E6A41", "#F4EBD9"),
         "present": ("#E2B33C", "#A9801F", "#0F1B33"),
         "absent": ("#34405C", "#232D45", "#9DAAC4")}


def _board_html(uid, date):
    word = _todays_word(date)
    p = _player(date.isoformat(), uid)
    guesses = p["guesses"]
    playing = not p["done"]
    rows = []
    for r in range(6):
        if r < len(guesses):
            g = guesses[r]
            rows.append("<div class='row'>" + "".join(
                f"<div class='tile' style='background:{_FACE[s][0]};box-shadow:inset 0 -8px 0 {_FACE[s][1]};"
                f"color:{_FACE[s][2]}'>{g[i].upper()}</div>" for i, s in enumerate(_score(g, word))) + "</div>")
        else:
            nxt = " next" if playing and r == len(guesses) else ""
            rows.append(f"<div class='row{nxt}'>" + "<div class='slot'></div>" * 5 + "</div>")
    status = {}
    for g in guesses:
        for s, ch in zip(_score(g, word), g.upper()):
            if ch not in status or _RANK[s] > _RANK[status[ch]]:
                status[ch] = s
    kb = "".join("<div class='krow'>" + "".join(
        f"<div class='key {status.get(ch, '')}'>{ch}</div>" for ch in row) + "</div>" for row in _KB_ROWS)

    if p["solved"]:
        n = len(guesses)
        coin = (f"<div class='coin'><i></i><div><b>+{config.WORDLE_REWARDS[n - 1]:,} UKP</b>"
                f"<span>solved in {n}/6</span></div></div>")
    elif p["done"]:
        coin = f"<div class='coin plain'><div><b>{word.upper()}</b><span>was the word</span></div></div>"
    else:
        nxt = config.WORDLE_REWARDS[len(guesses)]
        when = "first try" if not guesses else ("on your last go" if len(guesses) == 5 else "next")
        coin = f"<div class='coin'><i></i><div><b>{nxt:,} UKP</b><span>if you get it {when}</span></div></div>"

    title = ("".join(f"<div class='mini r'>{ch}</div>" for ch in "HMS") + "<div style='width:10px'></div>"
             + "".join(f"<div class='mini'>{ch}</div>" for ch in "WORDLE"))
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}} html,body{{background:#0F1B33}} body{{width:820px}}
.card{{width:820px;padding:30px 30px 32px;background:#0F1B33;font-family:Archivo,sans-serif;color:#F4EBD9;display:flex;flex-direction:column;gap:20px}}
.head{{display:flex;align-items:center;justify-content:space-between}}
.word{{display:flex;gap:6px}}
.mini{{width:46px;height:50px;border-radius:6px;background:#F4EBD9;color:#0F1B33;box-shadow:inset 0 -5px 0 #CDBF9F;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:28px;padding-bottom:4px}}
.mini.r{{background:#CF142B;color:#F4EBD9;box-shadow:inset 0 -5px 0 #8E0D1D}}
.sub{{font-size:19px;font-weight:600;color:#9DAAC4;margin-top:8px}}
.coin{{display:flex;align-items:center;gap:10px;padding:10px 20px 10px 10px;border-radius:40px;background:#1A2948}}
.coin.plain{{padding-left:20px}}
.coin i{{width:38px;height:38px;background:url('file://{UKP_ART}') center/contain no-repeat;display:block}}
.coin b{{font-size:26px;font-weight:900;display:block}} .coin span{{font-size:16px;color:#9DAAC4;font-weight:600;display:block}}
.panel{{background:#0A1426;border-radius:16px;padding:22px;box-shadow:inset 0 3px 0 rgba(0,0,0,.35)}}
.board{{display:flex;flex-direction:column;gap:12px;align-items:center}}
.row{{display:flex;gap:12px}}
.tile{{width:108px;height:112px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:58px;padding-bottom:8px}}
.slot{{width:108px;height:112px;border-radius:12px;background:#132241;box-shadow:inset 0 4px 0 rgba(0,0,0,.4)}}
.row.next .slot{{background:#1A2948;box-shadow:inset 0 4px 0 rgba(0,0,0,.4),0 0 0 3px #E2B33C}}
.kb{{display:flex;flex-direction:column;gap:9px;align-items:center}} .krow{{display:flex;gap:7px}}
.key{{width:64px;height:76px;border-radius:9px;background:#F4EBD9;color:#0F1B33;box-shadow:inset 0 -6px 0 #CDBF9F;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:28px;padding-bottom:5px}}
.key.correct{{background:#2F8F5B;color:#F4EBD9;box-shadow:inset 0 -6px 0 #1E6A41}} .key.present{{background:#E2B33C;box-shadow:inset 0 -6px 0 #A9801F}}
.key.absent{{background:#132241;color:#3E4C6B;box-shadow:inset 0 3px 0 rgba(0,0,0,.4)}}
</style></head><body><div class='card'>
<div class='head'><div><div class='word'>{title}</div><div class='sub'>{date:%A %-d %B} · No. {(date - _EPOCH).days + 1:,}</div></div>{coin}</div>
<div class='panel'><div class='board'>{''.join(rows)}</div></div>
<div class='kb'>{kb}</div>
</div></body></html>"""


async def render_board(uid, date):
    """Render the board to a PNG BytesIO + done flag. Returns (None, done) if rendering fails.

    Off by default (WORDLE_IMAGE_ENABLED). The board is an EPHEMERAL message and Discord
    is slow to load image attachments on those - a grey placeholder for seconds however
    good your connection - so the native text grid, which arrives with the message
    itself, is the faster board even though the picture is prettier.
    """
    p = _player(date.isoformat(), uid)
    if not getattr(config, "WORDLE_IMAGE_ENABLED", False):
        return None, p["done"]
    try:
        from lib.core.image_processing import screenshot_html
        img = await screenshot_html(_board_html(uid, date), size=(820, 1400), element_selector=".card")
        return img, p["done"]
    except Exception:
        log.error("HMS Wordle board render failed", exc_info=True)
        return None, p["done"]


# --- UI ------------------------------------------------------------------------
def _note_daily(interaction, name: str) -> None:
    """Feed the lockstep-alt detector. Never allowed to interrupt opening a puzzle."""
    try:
        from lib.core import detection as D
        D.note_daily_command(interaction.user.id, name, client=interaction.client)
    except Exception:
        log.debug("daily co-occurrence note failed", exc_info=True)


def _run_solve_checks(client, uid: int, player: dict, guess_count: int, reward: int) -> None:
    """Hand the finished game to the detectors.

    Wrapped whole in a try: this runs on the path that pays the player out, and a detector
    raising must never cost someone a solve they earned."""
    try:
        from lib.core import detection as D, detection_rules as R

        date_str = _today().isoformat()
        if guess_count == 1:
            # Recorded on every first-try solve, not just suspicious ones - the rolling rule
            # counts them over a fortnight and cannot look backwards for what wasn't kept.
            D.record_event(uid, D.WORDLE_ONE_GUESS_STREAK, {"date": date_str})

        for kind, triggers in R.wordle_solve_findings(
                player.get("guess_times", []), player.get("opened_at"), player.get("solved")):
            D.flag(client, kind, uid, triggers,
                   context=f"HMS Wordle {date_str} · solved in {guess_count} · payout {reward:,} UKP",
                   amount=reward)

        rate = R.wordle_one_guess_rate(uid)
        if rate:
            D.flag(client, D.WORDLE_ONE_GUESS_STREAK, uid, rate,
                   context=f"HMS Wordle {date_str}", amount=reward)
    except Exception:
        log.exception("wordle detection checks failed for %s", uid)


def _pay_solve(client, uid: int, date, p) -> int | None:
    """Pay a just-finished solve, once. Returns what was paid (0 under the anti-cheat tax),
    or None when nothing was due or the bank couldn't cover it.

    Shared by /wordle and the ukplace activity, so both pay through exactly the same path.
    Deliberately synchronous: there is no await between checking `rewarded` and setting
    it, so two windows submitting the winning guess at once can't both collect."""
    if not (p and p.get("solved") and not p.get("rewarded")):
        return None
    guess_count = len(p["guesses"])
    _record_solve_guesses(uid, guess_count)
    consecutive_1g = _get_consecutive_first_try(uid)
    if guess_count == 1 and consecutive_1g >= 2:
        # 2+ consecutive 1-guess solves: 100% anti-cheat tax
        reward = 0
        log.warning("HMS Wordle anti-cheat tax (100%%): User %s solved in 1 guess %d times in a row",
                    uid, consecutive_1g)
    else:
        reward = config.WORDLE_REWARDS[guess_count - 1]

    _run_solve_checks(client, uid, p, guess_count, reward)

    # discretionary: a puzzle prize is a reward the server chooses to give, so it scales
    # with bank reserves like every other one (see reserve_policy.py). Scaled here too only
    # so the board can show what actually landed; add_bb does the real scaling.
    from lib.economy.reserve_policy import scale_reward
    shown = scale_reward(reward) if reward else 0
    if add_bb(uid, reward, reason="HMS Wordle solve", taxable=False, discretionary=True):
        _mark_rewarded(uid, date.isoformat(), paid=shown)
        return shown
    return None


async def settle_solve(client, uid: int, date, p) -> int | None:
    """_pay_solve plus the bookkeeping that has to await (the income badge)."""
    paid = _pay_solve(client, uid, date, p)
    if paid is not None:
        try:
            from lib.features.income_badges import record_income_source
            await record_income_source(client, uid, "wordle")
        except Exception:
            pass
    return paid


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
        if status == "ok":
            await settle_solve(interaction.client, int(self.user_id), self.date, p)
        await interaction.response.defer()
        content, embed, files, done = await _board_payload(
            interaction.client, self.user_id, self.date)
        view = _view_for(self.user_id, self.date, done)
        await interaction.edit_original_response(
            content=content, embed=embed, attachments=files, view=view)


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



async def _board_payload(client, uid, date):
    """(content, embed, files) for the board.

    Prefers an embed pointing at a hosted CDN URL: Discord loads image ATTACHMENTS on
    ephemeral messages slowly, but an ordinary image link is fine. Falls back to a plain
    attachment if hosting is unavailable, and to the text board if the render fails.
    """
    img, done = await render_board(uid, date)
    if img is not None:
        from lib.core.image_host import as_embed_or_file
        embed, files = await as_embed_or_file(client, img, "wordle.png", colour=0x6AAA64)
        return None, embed, files, done
    content, _ = _render(uid, date)
    return content, None, [], done


def _view_for(user_id, date, done):
    return WordleShareView(user_id, date) if done else WordleView(user_id, date)


async def handle_wordle_command(interaction: discord.Interaction):
    if not _READY:
        await interaction.response.send_message(
            "HMS Wordle's word list isn't loaded right now, try again later.", ephemeral=True)
        return
    date = _today()
    _note_opened(interaction.user.id, date.isoformat())
    _note_daily(interaction, "wordle")
    await interaction.response.defer(ephemeral=True, thinking=True)
    content, embed, files, done = await _board_payload(
        interaction.client, interaction.user.id, date)
    view = _view_for(interaction.user.id, date, done)
    await interaction.followup.send(ephemeral=True, content=content, embed=embed,
                                    files=files, view=view)