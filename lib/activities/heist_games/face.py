"""The Face's games beyond the first (which is in heist.py): the guard's small talk, a forged pass to check, a hard
stare to sit through, the staff board, the guard's jumbled password and the shift rota. See __init__.py for the rules
a game follows."""

import math

from lib.activities.heist_games import Game, Refuse


def _whole(x) -> bool:
    """A whole number (True and False are ints in Python, so they're turned away by hand)."""
    return isinstance(x, int) and not isinstance(x, bool)


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# the same lists heist.py draws the cover story from, for making a wrong one (heist.py imports this, so it can't be
# imported from here)
FIRMS = ["Thames Water", "British Gas", "Openreach", "Rentokil", "the council", "Otis Lifts", "Dyno-Rod", "Securicor"]
WHYS = ["a leak", "a gas smell", "the broadband", "a mouse problem", "a fire door inspection", "the lift", "the drains",
        "the CCTV"]
FIRST = ["Gary", "Brenda", "Keith", "Sharon", "Derek", "Linda", "Trevor", "Mandy", "Nigel", "Tracey", "Clive", "Denise",
         "Barry", "Julie", "Colin", "Pauline"]
HER = ["Brenda", "Sharon", "Linda", "Mandy", "Tracey", "Denise", "Julie", "Pauline", "Karen", "Sandra", "Debbie",
       "Lorraine"]                                             # the Face is a woman, so her cover's name is too
SURNAMES = ["Pratt", "Higgins", "Bloggs", "Parker", "Dobbs", "Wright", "Shaw", "Fletcher", "Clegg", "Platt"]
FLOORS = ["the basement", "the ground floor", "the first floor", "the second floor", "the roof"]
STORY = {"name": "Denise Wright", "firm": "Thames Water", "why": "a leak", "floor": "the second floor", "ref": "7354",
         "boss": "Julie in head office"}                       # in case a game's made without one


class _Quiz(Game):
    """A few questions, three answers each and one of them right: p["qs"] holds them ({"options", "answer"} and
    whatever the game shows), p["picks"] what was picked, None for one the guard got tired of waiting on (only in a
    game with patience: the page times it and says so). Which answer's right is the secret."""
    pace = (2.0, 4.0)
    patience = None

    def _ready(self, p):
        """Whether there's nothing left to learn first (a game with a board to study says otherwise)."""
        return p.get("ready", True)

    def move(self, p, body):
        if not self._ready(p):
            if body.get("ready") is True:
                p["ready"] = True
                return
            raise Refuse("Have a good look first.")
        k = len(p["picks"])
        at = body.get("round", k)
        if not _whole(at) or at != k:
            raise Refuse("That question's been and gone.")
        if self.patience and body.get("slow") is True:
            p["picks"].append(None)
            return
        pick = body.get("pick")
        if not _whole(pick) or not 0 <= pick < len(p["qs"][k]["options"]):
            raise Refuse("Pick an answer.")
        p["picks"].append(pick)

    def done(self, p):
        return len(p["picks"]) >= len(p["qs"])

    def _right(self, p):
        return [a == q["answer"] for q, a in zip(p["qs"], p["picks"])]

    def score(self, p):
        return sum(self._right(p)) / len(p["qs"])

    def _shown(self, p):
        """The question being asked now, as the page sees it, and how the ones before went."""
        k = len(p["picks"])
        out = {"count": len(p["qs"]), "at": k, "right": self._right(p)}
        if k < len(p["qs"]):
            out["options"] = p["qs"][k]["options"]
        return out

    def solve(self, p):
        first = [] if self._ready(p) else [{"ready": True}]
        return first + [{"pick": q["answer"], "round": i} for i, q in enumerate(p["qs"]) if i >= len(p["picks"])]

    def bot(self, p, rng, skill):
        if not self._ready(p):
            return {"ready": True}
        k = len(p["picks"])
        q = p["qs"][k]
        if rng.random() < skill:
            return {"pick": q["answer"], "round": k}
        return {"pick": rng.choice([i for i in range(len(q["options"])) if i != q["answer"]]), "round": k}


class Talk(_Quiz):
    """The guard fancies a natter: he says something, and the three things you could say back all start the same
    way. One's what anyone would say; the other two give you away on a careful read (fishing for when he's off,
    where the cameras are, how much is kept here, or letting slip there's a van waiting). He only waits so long for
    an answer (the page times it from the round's wait and says when he's had enough, which counts as wrong)."""
    key, role, title = "talk", "face", "SMALL TALK"
    patience = True
    ROUNDS = {"s": 3, "m": 4, "big": 5}
    WAIT = {"s": 9, "m": 8, "big": 7}                            # seconds he waits for an answer
    # what he says, the reply that keeps him sweet, and two that give you away
    BANK = [
        ("Chucking it down out there, isn't it?", "Biblical. I'm soaked right through.",
         "Biblical. Your cameras won't see a thing.", "Biblical. Nobody out walking past, then."),
        ("Did you see the match last night?", "Don't. Two-nil up and we still lost.",
         "Don't. Were you watching it here, on shift?", "Don't. Is that why it's just you tonight?"),
        ("Kettle's just boiled. Fancy a cuppa?", "Go on then. Milk and two sugars, ta.",
         "Go on then. Take your time making it.", "Go on then. Is the kitchen far from here?"),
        ("My knee's been playing up something rotten.", "It's the damp. My mum's goes just the same.",
         "It's the damp. Slows you down on your rounds?", "It's the damp. Go and sit down, I'll be fine."),
        ("Traffic on the A3 was murder tonight.", "Tell me about it. Roadworks at Guildford again.",
         "Tell me about it. Clear for later, though?", "Tell me about it. Police were stuck in it too."),
        ("Bit nippy tonight, isn't it?", "Freezing. Our heating's not on till November.",
         "Freezing. Good job I brought my gloves.", "Freezing. Is the heating off in the vault too?"),
        ("Watch Bake Off this week?", "Shocking. That poor lad's bread never rose.",
         "Shocking. I was too busy planning, to be fair.", "Shocking. Do you get to watch it here at night?"),
        ("Custard cream? It's all we've got, I'm afraid.", "Never say no. Ta, you're a star.",
         "Never say no. I'll take a few for the lads outside.", "Never say no. Are you the only one here to share?"),
        ("They've moved the bins to Tuesdays now.", "Have they? Nobody tells you anything.",
         "Have they? Is that when the cash van comes too?", "Have they? So nobody's round the back tonight?"),
        ("Thirty quid to park round here. Daylight robbery.", "Criminal. I'm on a meter round the back.",
         "Criminal. I left the engine running, so it's fine.", "Criminal. Robbery's better done at night, mind."),
        ("Price of a pint these days, eh?", "Seven quid in town. I nearly fainted.",
         "Seven quid in town. Won't matter after tonight.", "Seven quid in town. I'll be buying, soon enough."),
        ("Clocks go back this weekend.", "Extra hour in bed. Best night of the year.",
         "Extra hour in bed. Does your alarm reset, then?", "Extra hour in bed. Is the safe on a timer too?"),
        ("Trains were all cancelled again tonight.", "Leaves on the line. Every autumn, without fail.",
         "Leaves on the line. Fewer people about, then.", "Leaves on the line. Good job we've got the van."),
        ("The wife's sister's staying with us. All week.", "All week? You poor man. Hide in the shed.",
         "All week? So you're here every night this week?", "All week? Is she the one who works in the bank?"),
        ("Carol on the telly says frost tomorrow.", "I'll have to dig the scraper out, then.",
         "I'll have to watch my footprints, then.", "I'll want to be well away by morning, then."),
        ("Cricket's been rained off again.", "Typical. Five days and not a ball bowled.",
         "Typical. Did you have the radio on all shift?", "Typical. Still, a quiet night suits me."),
        ("I'm on nights all week. Don't know what day it is.", "Nightmare. You'll want a fry-up after.",
         "Nightmare. And on your own every night, is it?", "Nightmare. What time do you knock off, then?"),
        ("Got anything nice planned for the weekend?", "Car boot sale, if it stays dry.",
         "Car boot sale, if tonight goes to plan.", "Car boot sale. Plenty to sell after tonight."),
        ("My allotment's overrun with slugs.", "Beer traps. Work a treat on my lettuces.",
         "Beer traps. They only come out at night, like me.", "Beer traps. You must be out there a lot on shift."),
        ("A pigeon got in the lobby again this morning.", "Cheeky things. One nicked my sandwich once.",
         "Cheeky things. Did it set the alarm off?", "Cheeky things. How'd it get past the doors?"),
        ("Got yourself a meal deal from the garage?", "Ham sandwich, crisps and a Ribena. Lovely.",
         "Ham sandwich. Quick one, I won't be here long.", "Ham sandwich. It's caviar from tomorrow, mind."),
        ("They're putting the Christmas lights up already.", "In October! Earlier every year.",
         "In October! Is the high street lit all night?", "In October! I'll be spending big this year."),
        ("Those roadworks outside have been there months.", "Three cones and nobody working. Classic.",
         "Three cones. Do they block the back way out?", "Three cones. I moved one for the van, sorry."),
        ("Lottery's a rollover tonight. Fourteen million.", "Got my numbers on. Same ones for twenty years.",
         "Got my numbers on. Won't need them after tonight.", "Got my numbers on. How much is kept here, roughly?"),
        ("I'm in the darts at the Red Lion on Friday.", "Good luck. Mind you don't hit the barman.",
         "Good luck. So who's covering here Friday?", "Good luck. What time do you head off for it?"),
        ("My grandson's just started at big school.", "Bless him. Do they still wear blazers?",
         "Bless him. Tell him crime doesn't pay. Mostly.", "Bless him. Is that why you're on nights now?"),
        ("Vending machine's eaten my pound again.", "Give it a thump on the side. Works on ours.",
         "Give it a thump. Or I could open it up for you.", "Give it a thump. Machines are easy to get into."),
        ("The chippy on the corner's shut down, you know.", "No! Best battered sausage for miles.",
         "No! I always parked the van outside it.", "No! Handy spot for keeping an eye on this place."),
        ("Bank holiday Monday and I'm still working.", "That's rotten. Double time, at least?",
         "That's rotten. Is the whole bank shut, then?", "That's rotten. Who else is in on Monday?"),
        ("Bit late for a call-out, isn't it?", "Tell me about it. Overtime's the only perk.",
         "Tell me about it. Quieter, though. Fewer people.", "Tell me about it. Best time for it, mind."),
        ("My Sandra's got us a dog. Little sausage dog.", "Ah, lovely. What have you called him?",
         "Ah, lovely. Does he bark at strangers at night?", "Ah, lovely. Do you bring him on shift with you?"),
        ("Boiler's packed in at home. Freezing, we are.", "Nightmare. Get a man in before the weekend.",
         "Nightmare. I've a crowbar if it needs a hand.", "Nightmare. You'll be keen to get off home, then."),
        ("They reckon it might snow this Christmas.", "They say that every year. Never does.",
         "They say that. Snow's handy for covering tracks.", "They say that. Hope it holds off till I'm gone."),
        ("Strictly's on Saturday. My Sandra loves it.", "Ooh. I'm rooting for the weather girl.",
         "Ooh. So you'll be glued to it, not the cameras?", "Ooh. Is it quiet here Saturday nights, then?"),
        ("You're not from round here, are you?", "No, Croydon. The van's my second home.",
         "No, and I won't be round here long, either.", "No, but I had a good look round earlier."),
        ("Doctor says I've got to cut down on biscuits.", "Doctors. What do they know, eh?",
         "Doctors. Does that mean fewer tea breaks, then?", "Doctors. Do you keep them locked up in here?"),
    ]

    def make(self, rng, size, story):
        size = size if size in self.ROUNDS else "s"
        qs = []
        for say, good, *odd in rng.sample(self.BANK, self.ROUNDS[size]):
            options = [good, *odd]
            rng.shuffle(options)
            qs.append({"say": say, "options": options, "answer": options.index(good)})
        return {"qs": qs, "picks": [], "wait": self.WAIT[size]}

    def public(self, p):
        out = {**self._shown(p), "patience": p.get("wait", self.WAIT["s"])}
        if out["at"] < len(p["qs"]):
            out["say"] = p["qs"][out["at"]]["say"]
        return out

    def bot(self, p, rng, skill):
        """A so-so listener: usually hears the slip, now and then reads too slowly."""
        k = len(p["picks"])
        if rng.random() < 0.12 * (1 - skill):
            return {"slow": True, "round": k}
        q = p["qs"][k]
        if rng.random() < min(0.97, skill + 0.1):
            return {"pick": q["answer"], "round": k}
        return {"pick": rng.choice([i for i in range(3) if i != q["answer"]]), "round": k}


class FakeId(Game):
    """Your forged paperwork next to the job sheet (the crew's cover story): it carries most of the sheet's lines,
    and one of them doesn't match, by a letter, a digit, the floor next door, a firm or a job that's nearly right.
    Tap it before the guard looks up. A wrong tap costs half the round, and two and he's seen you fiddling. The page
    times the guard (when he looks, it says so, with the round, so a late or repeated one does nothing); which
    line's wrong is the secret, until the round's over."""
    key, role, title = "id", "face", "FAKE ID"
    pace = (2.0, 4.5)
    ROUNDS = {"s": 2, "m": 3, "big": 4}
    LOOK = {"s": 9, "m": 8, "big": 9}                            # seconds before the guard looks up
    LINES = {"s": 4, "m": 5, "big": 6}                           # of the sheet's six, on each pass
    SHEET = [("name", "NAME"), ("firm", "FIRM"), ("why", "JOB"), ("floor", "FLOOR"), ("ref", "REF"),
             ("boss", "SIGNED OFF")]
    KINDS = {
        "badge": [("name", "NAME"), ("firm", "COMPANY"), ("why", "VISITING"), ("floor", "ACCESS"), ("ref", "JOB NO"),
                  ("boss", "ISSUED BY")],
        "order": [("name", "ENGINEER"), ("firm", "FIRM"), ("why", "JOB"), ("floor", "FLOOR"), ("ref", "REF"),
                  ("boss", "SIGNED")],
        "permit": [("name", "DRIVER"), ("firm", "FIRM"), ("ref", "JOB REF"), ("why", "FOR"), ("floor", "DELIVER TO"),
                   ("boss", "OK'D BY")],
    }
    # the forger's near misses
    NEAR = {"Thames Water": "Thames Waters", "British Gas": "British Gass", "Openreach": "Openreech",
            "Rentokil": "Rentakil", "the council": "the counsel", "Otis Lifts": "Otis Lift", "Dyno-Rod": "Dyna-Rod",
            "Securicor": "Securicorp"}
    NEAR_WHY = {"a leak": "a leek", "a gas smell": "a gas meter", "the broadband": "the phone line",
                "a mouse problem": "a rat problem", "a fire door inspection": "a fire alarm inspection",
                "the lift": "the lifts", "the drains": "the drain", "the CCTV": "the TV"}

    def make(self, rng, size, story):
        story = {**STORY, **{k: str(v) for k, v in (story or {}).items() if k in STORY}}
        size = size if size in self.LINES else "s"
        rounds, used = [], set()
        kinds = (rng.sample(list(self.KINDS), len(self.KINDS)) * 2)[:self.ROUNDS[size]]
        for kind in kinds:
            keep = sorted(rng.sample(range(len(self.KINDS[kind])), self.LINES[size]))
            lines = [self.KINDS[kind][i] for i in keep]
            wrong = rng.choice([i for i, (k, _) in enumerate(lines) if k not in used] or range(len(lines)))
            key = lines[wrong][0]
            used.add(key)                                         # a different line goes wrong each round
            shown = [[label, story[k].upper()] for k, label in lines]
            shown[wrong][1] = self._fake(rng, size, key, story[key]).upper()
            rounds.append({"kind": kind, "lines": shown, "wrong": wrong, "taps": [], "end": None})
        return {"sheet": [[label, story[k].upper()] for k, label in self.SHEET], "rounds": rounds,
                "look": self.LOOK[size]}

    def _fake(self, rng, size, key, real):
        for _ in range(60):
            fake = self._fake_once(rng, size, key, real)
            if fake.upper() != real.upper():
                return fake
        return real + "s"

    def _fake_once(self, rng, size, key, real):
        """One slip on the pass. Even the quick ones only read wrong next to the job sheet: a name that starts the
        same, the floor next door, one digit out. The bigger the job, the smaller the slip."""
        others = lambda pool: [x for x in pool if x.upper() != real.upper()] or pool  # noqa: E731
        alike = lambda pool, word: [x for x in pool if x[0] == word[:1] and x != word]  # noqa: E731
        if key == "name":
            first, _, last = real.partition(" ")
            if size != "big" and rng.random() < (0.5 if size == "s" else 0.3):
                firsts, lasts = alike(HER, first), alike(SURNAMES, last)   # a woman's name, next to her photo
                if firsts and (rng.random() < 0.5 or not lasts):
                    return f"{rng.choice(firsts)} {last}"
                if lasts:
                    return f"{first} {rng.choice(lasts)}"
            return f"{first} {self._misspell(rng, last)}" if rng.random() < 0.65 or not last else f"{self._misspell(rng, first)} {last}"
        if key == "firm":
            if size == "big" or rng.random() < (0.5 if size == "s" else 0.8):
                return (rng.random() < 0.5 and self.NEAR.get(real)) or self._misspell(rng, real)
            return rng.choice(others(FIRMS))
        if key == "why":
            if size != "s" and real in self.NEAR_WHY:
                return self.NEAR_WHY[real]
            return rng.choice(others(WHYS))
        if key == "floor":
            at = FLOORS.index(real) if real in FLOORS else -1
            return rng.choice([FLOORS[i] for i in (at - 1, at + 1) if 0 <= i < len(FLOORS)] if at >= 0 else others(FLOORS))
        if key == "ref":
            digits = list(real)
            if len(digits) < 2:
                return str(rng.randint(1000, 9999))
            swaps = [i for i in range(len(digits) - 1) if digits[i] != digits[i + 1]]
            if swaps and (size == "big" or (size == "m" and rng.random() < 0.5)):
                i = rng.choice(swaps)
                digits[i], digits[i + 1] = digits[i + 1], digits[i]
            else:
                i = rng.randrange(len(digits))
                digits[i] = rng.choice([d for d in "0123456789" if d != digits[i]])
            return "".join(digits)
        who, _, rest = real.partition(" ")                        # boss: "Julie in head office"
        if size == "big" or (size == "m" and rng.random() < 0.5):
            name = self._misspell(rng, who)
        else:
            name = rng.choice(alike(FIRST, who) or others(FIRST))
        return f"{name} {rest}".strip()

    @staticmethod
    def _misspell(rng, text):
        """One slip of the forger's pen in one longish word: two letters swapped, one dropped or doubled, or a
        vowel changed. The first letter stays, so it reads right at a glance."""
        words = text.split(" ")
        at = rng.choice([i for i, w in enumerate(words) if len(w) >= 4] or [len(words) - 1])
        w = list(words[at])
        if len(w) < 3:
            w.append(w[-1])
        else:
            how = rng.choice(["swap", "drop", "double", "vowel"])
            i = rng.randrange(1, len(w) - 1)
            if how == "swap" and w[i] != w[i + 1]:
                w[i], w[i + 1] = w[i + 1], w[i]
            elif how == "drop":
                del w[i]
            elif how == "vowel" and any(ch in "aeiou" for ch in w[1:]):
                j = rng.choice([j for j in range(1, len(w)) if w[j] in "aeiou"])
                w[j] = rng.choice([v for v in "aeiou" if v != w[j]])
            else:
                w.insert(i, w[i])
        words[at] = "".join(w)
        return " ".join(words)

    def _at(self, p):
        return next((i for i, r in enumerate(p["rounds"]) if r["end"] is None), len(p["rounds"]))

    @staticmethod
    def _credit(r):
        return max(0.0, 1 - 0.5 * len(r["taps"])) if r["end"] == "found" else 0.0

    def public(self, p):
        k = self._at(p)
        out = {"sheet": p["sheet"], "look": p["look"], "rounds": len(p["rounds"]), "at": k,
               "results": [self._credit(r) for r in p["rounds"][:k]]}
        if k < len(p["rounds"]):
            r = p["rounds"][k]
            out.update(kind=r["kind"], lines=r["lines"], taps=r["taps"])
        if k:
            r = p["rounds"][k - 1]                               # the one just gone, shown with its slip ringed
            out["prev"] = {"kind": r["kind"], "lines": r["lines"], "taps": r["taps"], "end": r["end"], "wrong": r["wrong"]}
        return out

    def move(self, p, body):
        k = self._at(p)
        at = body.get("round")
        if not _whole(at) or not 0 <= at <= k:
            raise Refuse("Wait for the next one.")
        if body.get("look") is True:                               # the guard looked up
            if at == k:
                p["rounds"][k]["end"] = "late"
            return
        if at != k:
            raise Refuse("Too late. He's seen that one.")
        r = p["rounds"][k]
        i = body.get("at")
        if not _whole(i) or not 0 <= i < len(r["lines"]):
            raise Refuse("Tap a line on your pass.")
        if i in r["taps"]:
            raise Refuse("You've checked that one.")
        if i == r["wrong"]:
            r["end"] = "found"
            return
        r["taps"].append(i)
        if len(r["taps"]) >= 2:
            r["end"] = "caught"

    def done(self, p):
        return self._at(p) >= len(p["rounds"])

    def score(self, p):
        return sum(self._credit(r) for r in p["rounds"]) / len(p["rounds"])

    def solve(self, p):
        k = self._at(p)
        return [{"at": r["wrong"], "round": i} for i, r in enumerate(p["rounds"]) if i >= k]

    def bot(self, p, rng, skill):
        k = self._at(p)
        r = p["rounds"][k]
        if rng.random() < 0.06:
            return {"look": True, "round": k}
        if rng.random() < skill:
            return {"at": r["wrong"], "round": k}
        return {"at": rng.choice([i for i in range(len(r["lines"])) if i != r["wrong"] and i not in r["taps"]]),
                "round": k}


class Calm(Game):
    """The guard stares, and your heart rate creeps up, in jolts when he leans in (though not every lean-in is one,
    and not every jolt comes with one). Hold BREATHE to bring it down, but breathing has momentum: it takes a moment
    to get going and carries on a moment after you let go, so a long hold overshoots into looking half asleep. Now
    and then he looks away and it falls on its own: let go. Keep the needle in the green, which narrows as the stare
    goes on. The bot draws all of that (the page animates the same), the page sends the holds (each one's start and
    end, in ms from when it showed, the whole list every time so one that goes missing is carried by the next), and
    the score is the share of the stare the needle spent in the green, replayed here step by step. Every stare is
    one a cool head who knew what was coming could sit through in the green all the way (make() checks)."""
    key, role, title = "calm", "face", "KEEP CALM"
    pace = (1.0, 2.0)
    DT = 20                    # ms a step of the meter
    LEAD = 1500                # ms before the stare starts, to get a thumb on the button
    BREATHE = 0.38             # how fast a full breath brings the needle down, a second
    EASE = 0.08                # how far each step gets towards a full breath (holding) or none: the momentum
    CENTRE = 0.52              # the middle of the green
    # the stare's seconds, the green's half-width at its start and end, a jolt, the seconds between jolts, the creep
    # a second while he's looking, how many times he looks away, and how many lean-ins come to nothing
    SIZES = {"s": (14, (0.13, 0.09), (0.06, 0.11), (1.0, 2.4), (0.02, 0.06), 1, 1),
             "m": (18, (0.12, 0.075), (0.07, 0.13), (0.8, 2.2), (0.03, 0.07), 2, 2),
             "big": (22, (0.11, 0.065), (0.08, 0.14), (0.7, 2.0), (0.03, 0.08), 2, 3)}
    AWAY = ((1200, 2000), (0.05, 0.07))     # how long he looks away (ms), and how fast it falls meanwhile, a second
    DOUBLE = 0.25                           # the chance a jolt has another hard on its heels

    def make(self, rng, size, story):
        size = size if size in self.SIZES else "s"
        for tries in range(80):
            p = self._draw(rng, size, tries // 15)                 # softer and softer if it keeps not working out
            if self._run(p, [], self._end(p), self._cool(p))[0] == p["secs"] * 1000:
                return p
        return self._draw(rng, size, 9)

    def _draw(self, rng, size, soften):
        secs, (hw0, hw1), jolt, gap, creep, looks, feints = self.SIZES[size]
        lead, dt = self.LEAD, self.DT
        end = lead + secs * 1000
        p = {"secs": secs, "lead": lead, "dt": dt, "centre": self.CENTRE, "hw": [hw0, hw1], "v0": self.CENTRE,
             "breathe": self.BREATHE, "ease": self.EASE, "holds": [], "end": False}
        width = lambda t: 2 * self._half(p, t)  # noqa: E731
        away = []
        for _ in range(looks):
            for _ in range(40):
                t0 = rng.randrange(lead + 2500, end - 2500, dt)
                rate = round(rng.uniform(*self.AWAY[1]), 3)
                # never further than most of the green, so a level head can ride it out
                long = min(rng.randrange(*self.AWAY[0], dt), int(0.6 * width(t0) / rate * 1000) // dt * dt)
                if all(t0 + long + 2500 < a or t0 > b + 2500 for a, b, _ in away):
                    away.append([t0, t0 + long, rate])
                    break
        away.sort()
        creeps, t = [], lead
        while t < end:
            creeps.append([t, round(rng.uniform(*creep), 3)])
            t += rng.randrange(2000, 4000, dt)
        cuts = sorted({0, *(c[0] for c in creeps), *(a[0] for a in away), *(a[1] for a in away)})
        p["drift"] = [[c, self._rate(c, creeps, away)] for c in cuts]
        p["away"] = [[a, b] for a, b, _ in away]
        looking = lambda t: not any(a - 300 <= t <= b + 400 for a, b, _ in away)  # noqa: E731
        jolts, t = [], lead + rng.randrange(600, 1300)
        while t < end - 300:
            if looking(t):
                cap = 0.75 * width(t)
                amt = min(cap, rng.uniform(*jolt) * (1 - 0.15 * soften))
                jolts.append([t, round(amt, 3), int(rng.random() < 0.75)])
                if soften < 2 and rng.random() < self.DOUBLE:      # and another, before you've got your breath
                    t += rng.randrange(260, 460, dt)
                    more = min(amt * rng.uniform(0.5, 0.8), 0.8 * width(t) - amt)
                    if more > 0.02 and looking(t):
                        jolts.append([t, round(more, 3), 0])
            t += int(rng.uniform(*gap) * 1000)
        p["jolts"] = jolts
        fakes = []
        for _ in range(feints):
            for _ in range(30):
                t = rng.randrange(lead + 1000, end - 800, dt)
                if looking(t) and all(abs(t - j[0]) > 700 for j in jolts) and all(abs(t - f) > 700 for f in fakes):
                    fakes.append(t)
                    break
        p["feints"] = sorted(fakes)
        return p

    @staticmethod
    def _rate(t, creeps, away):
        for a, b, rate in away:
            if a <= t < b:
                return -rate
        return next((r for at, r in reversed(creeps) if at <= t), 0.0)

    def _end(self, p):
        return p["lead"] + p["secs"] * 1000

    @staticmethod
    def _half(p, t):
        """The green's half-width at t: it narrows as the stare goes on."""
        hw0, hw1 = p["hw"]
        f = min(1.0, max(0.0, (t - p["lead"]) / (p["secs"] * 1000)))
        return hw0 + (hw1 - hw0) * f

    def _run(self, p, holds, upto, policy=None):
        """Replay the needle up to upto (ms), holding through holds; after the last of them, policy(t, v, b, holding)
        (if there is one) decides, and its holds are added. b is the breath under way, which eases towards a full
        one while BREATHE is held and back to none when it isn't. Returns the ms in the green, where the needle
        ended and the holds."""
        holds = [list(h) for h in holds]
        free = holds[-1][1] if holds else 0                       # where the policy takes over
        dt, c = p["dt"], p["centre"]
        drift, jolts = p["drift"], p["jolts"]
        v, b, good, di, ji, hi_ = p["v0"], 0.0, 0, 0, 0, 0
        t, upto = p["lead"], min(upto, self._end(p))
        while t + dt <= upto:
            while di + 1 < len(drift) and drift[di + 1][0] <= t:
                di += 1
            while hi_ < len(holds) and holds[hi_][1] <= t:
                hi_ += 1
            holding = hi_ < len(holds) and holds[hi_][0] <= t
            if policy and t >= free:
                want = policy(t, v, b, holding)
                if want and not holding:
                    holds.append([t, self._end(p)])
                    hi_, holding = len(holds) - 1, True
                elif holding and not want:
                    holds[hi_][1] = t
                    hi_, holding = hi_ + 1, False
            b += ((p["breathe"] if holding else 0.0) - b) * p["ease"]
            v += (drift[di][1] - b) * dt / 1000
            while ji < len(jolts) and jolts[ji][0] <= t + dt:
                v += jolts[ji][1]
                ji += 1
            v = min(1.0, max(0.0, v))
            half = self._half(p, t + dt)
            if c - half <= v <= c + half:
                good += dt
            t += dt
        return good, v, holds

    def _upto(self, p):
        return self._end(p) if p["end"] else (p["holds"][-1][1] if p["holds"] else 0)

    def public(self, p):
        out = {k: p[k] for k in ("secs", "lead", "dt", "centre", "hw", "v0", "breathe", "ease", "drift", "jolts",
                                 "feints", "away", "holds", "end")}
        if p["end"]:
            out["good"] = round(self.score(p), 3)
        return out

    def move(self, p, body):
        holds, end = body.get("holds"), self._end(p)
        if not isinstance(holds, list) or len(holds) > 500 or not all(
                isinstance(h, list) and len(h) == 2 and _number(h[0]) and _number(h[1]) for h in holds):
            raise Refuse("Hold BREATHE to calm down.")
        hs, last = [], 0
        for a, b in holds:
            a, b = int(round(a)), int(round(min(b, end)))
            if not last <= a < b:
                raise Refuse("That's not how breathing works.")
            hs.append([a, b])
            last = b
        old = p["holds"]
        if hs[:len(old)] == old:
            p["holds"] = hs
        elif old[:len(hs)] != hs:                                 # an older list arriving late is fine; others aren't
            raise Refuse("That's not how breathing works.")
        if body.get("end") is True:
            p["end"] = True

    def done(self, p):
        return p["end"]

    def score(self, p):
        return self._run(p, p["holds"], self._upto(p))[0] / (p["secs"] * 1000)

    def _cool(self, p):
        """A cool head who knows what's coming: sits low in the green when a jolt's due and high before he looks
        away, judging each breath by where the needle will come to rest once it's let go."""
        dt, k, c = p["dt"], p["ease"], p["centre"]
        jolts, away = p["jolts"], p["away"]
        coast = dt / 1000 * (1 - k) / k                           # how far a breath carries on, for each unit of it

        def policy(t, v, b, holding):
            half = min(self._half(p, t), self._half(p, t + 600))
            lo, hi = c - half + 0.012, c + half - 0.012
            due = sum(j[1] for j in jolts if t < j[0] <= t + 700)
            rest = v - b * coast
            if any(a - 900 <= t < z for a, z in away):           # he's about to look away, or has: don't breathe
                return rest > hi - due
            top = max(lo + 0.01, hi - due - 0.02)
            return rest > lo + 0.01 if holding else rest > top
        return policy

    def solve(self, p):
        return [{"holds": self._run(p, p["holds"], self._end(p), self._cool(p))[2], "end": True}]

    def bot(self, p, rng, skill):
        """A so-so player: sees the needle a beat late, breathes when it's near the top of the green and stops
        somewhere past the middle, by a different amount each time, and lets go a beat after he looks away."""
        dt, c = p["dt"], p["centre"]
        lag = max(1, int((1.3 - skill) * 320 / dt))
        seen: list[float] = []
        marks = {"top": 0.6, "bottom": 0.0}

        def policy(t, v, b, holding):
            seen.append(v)
            late = seen[-lag] if len(seen) >= lag else v
            half = self._half(p, t)
            if any(a + lag * dt <= t < z + lag * dt for a, z in p["away"]):
                return False
            if not holding and late >= c + half * marks["top"]:
                marks["bottom"] = rng.uniform(-0.3, 0.5) * (1.3 - skill)
                return True
            if holding and late <= c - half * marks["bottom"]:
                marks["top"] = rng.uniform(0.3, 0.95)
                return False
            return holding
        return {"holds": self._run(p, p["holds"], self._end(p), policy)[2], "end": True}


class Names(_Quiz):
    """The staff board: who works here, each with a lanyard and a mug (and at the vault a job), the colours and
    patterns shared about, so nobody's known by one thing alone. It comes down, and the guard asks after one of
    them by what pins them down (usually two things, neither enough on its own): pick their name, from people who
    share some of it. The board shows until you're ready (the page takes it down when the time's up); after that
    the page sees only the question.

    So a screenshot of the board isn't a crib sheet, the page shows it a card at a time (the rest stay face down
    until one's tapped, and only one's ever face up), and the guard only waits so long for each name (WAIT, timed
    on the page like the small talk's): flicking through a phone's photos for the answer runs him out."""
    key, role, title = "names", "face", "STAFF NAMES"
    patience = True
    WAIT = 7.0
    # staff on the board, questions, seconds to study, what each has, how many of each go round, names to pick from
    SIZES = {"s": (4, 2, 12, ("lanyard", "mug"), 3, 3), "m": (5, 4, 14, ("lanyard", "mug"), 3, 3),
             "big": (6, 5, 16, ("lanyard", "mug", "job"), 3, 4)}       # (a card at a time on the page, so a little longer)
    FACES = "WMWMMMWW"                                             # who's in each cell of g-face-staff.webp
    WOMEN = ["SUE", "PAT", "BEV", "TINA", "JAN", "VAL", "DOT", "MAGS", "JUNE", "RITA"]
    MEN = ["KEV", "DAVE", "RAJ", "GARY", "PHIL", "MO", "STEVE", "LEN", "TONY", "DES"]
    THINGS = {"lanyard": ["red", "blue", "green", "yellow", "pink", "orange"],
              "mug": ["spots", "stripes", "hearts", "stars", "cow"],
              "job": ["cashier", "manager", "cleaner", "post room", "IT", "reception"]}

    def make(self, rng, size, story):
        n, rounds, study, kinds, spread, picks = self.SIZES.get(size, self.SIZES["s"])
        pools = {k: rng.sample(self.THINGS[k], spread) for k in kinds}
        while True:                                               # everyone different, nobody by one thing alone
            things = [{k: rng.choice(pools[k]) for k in kinds} for _ in range(n)]
            if len({tuple(t.values()) for t in things}) == n:
                break
        women, men = rng.sample(self.WOMEN, len(self.WOMEN)), rng.sample(self.MEN, len(self.MEN))
        staff = [{"name": (women if self.FACES[face] == "W" else men).pop(), "face": face, "things": t}
                 for face, t in zip(rng.sample(range(len(self.FACES)), n), things)]
        qs = []
        for who in rng.sample(range(n), rounds):
            cue = self._cue(rng, staff, who, size != "s")
            near = sorted((i for i in range(n) if i != who),
                          key=lambda i: (-sum(staff[i]["things"][k] == v for k, v in cue.items()), rng.random()))
            options = [staff[who]["name"], *(staff[i]["name"] for i in near[:picks - 1])]
            rng.shuffle(options)
            qs.append({"who": who, "cue": cue, "options": options, "answer": options.index(staff[who]["name"])})
        return {"staff": staff, "study": study, "ready": False, "qs": qs, "picks": []}

    @staticmethod
    def _matches(staff, cue):
        return [i for i, s in enumerate(staff) if all(s["things"][k] == v for k, v in cue.items())]

    def _cue(self, rng, staff, who, pairs):
        """What the guard asks them by: something only they have. At a bigger job, two things where neither alone
        would do, if there's such a pair; otherwise the fewest things that pin them down."""
        mine = staff[who]["things"]
        sets = [[k] for k in mine]
        twos = [[a, b] for i, a in enumerate(mine) for b in list(mine)[i + 1:]]
        rng.shuffle(sets)
        rng.shuffle(twos)
        if pairs:
            hard = [t for t in twos if all(len(self._matches(staff, {k: mine[k]})) > 1 for k in t)]
            sets = hard + sets + twos
        else:
            sets = sets + twos
        for keys in sets + [list(mine)]:
            cue = {k: mine[k] for k in keys}
            if self._matches(staff, cue) == [who]:
                return cue
        return dict(mine)

    def public(self, p):
        if not p["ready"]:
            return {"ready": False, "study": p["study"], "count": len(p["qs"]), "staff": p["staff"]}
        out = {"ready": True, **self._shown(p), "patience": self.WAIT}
        if out["at"] < len(p["qs"]):
            out["cue"] = p["qs"][out["at"]]["cue"]
        return out

    def bot(self, p, rng, skill):
        """A so-so memory: the more it takes to pin someone down, the likelier a mix-up."""
        if not p["ready"]:
            return {"ready": True}
        k = len(p["picks"])
        q = p["qs"][k]
        if rng.random() < min(0.97, skill + 0.2 - 0.1 * len(q["cue"])):
            return {"pick": q["answer"], "round": k}
        return {"pick": rng.choice([i for i in range(len(q["options"])) if i != q["answer"]]), "round": k}


class Anagram(Game):
    """The guard's password is on a sticky note on his monitor, jumbled up (he's not daft), with a word under it to
    jog his memory that's no great help to anyone else: tap the letters in order to spell it. A wrong go just says
    no; from the second wrong go on, each shows how many letters were right from the start. Quicker and in fewer
    goes scores more; a word never got scores a little for the letters right from the start. Each go carries its
    time in ms from when the puzzle showed, and the clock for a word starts when the one before it ended."""
    key, role, title = "anagram", "face", "UNSCRAMBLE"
    pace = (3.0, 7.0)
    TRIES = 4
    TRY_SCORE = [1.0, 0.8, 0.6, 0.45]
    HINT_AFTER = 2                                                # wrong goes before the letters right show
    SIZES = {"s": ((6,), 8), "m": ((7,), 10), "big": ((7, 8), 12)}  # each word's letters, par seconds a word
    # his passwords, and what he's written under them; none with another word in its letters
    WORDS = {
        6: [("TEAPOT", "TEA TIME"), ("KETTLE", "TEA TIME"), ("TEACUP", "TEA TIME"), ("HOBNOB", "TEA TIME"),
            ("PIGEON", "THE PARK"), ("BOVRIL", "THE CUPBOARD"), ("WICKET", "SUMMER"), ("PICNIC", "SUMMER"),
            ("KIPPER", "BREAKFAST"), ("MUFFIN", "BREAKFAST"), ("JUMPER", "WINTER"), ("TROWEL", "THE GARDEN"),
            ("MARROW", "THE GARDEN"), ("WOMBLE", "THE TELLY"), ("PUDSEY", "THE TELLY"), ("DONKEY", "THE SEASIDE"),
            ("BUCKET", "THE SEASIDE"), ("TOFFEE", "THE SEASIDE"), ("BRANDY", "CHRISTMAS"), ("TURKEY", "CHRISTMAS")],
        7: [("BISCUIT", "TEA TIME"), ("CRUMPET", "TEA TIME"), ("TEABAGS", "THE CUPBOARD"),
            ("CHUTNEY", "THE CUPBOARD"), ("CUSTARD", "AFTERS"), ("PUDDING", "AFTERS"), ("MARMITE", "BREAKFAST"),
            ("WEMBLEY", "SATURDAYS"), ("CRICKET", "SUMMER"), ("CROQUET", "THE GARDEN"), ("SNOOKER", "THE TELLY"),
            ("JUBILEE", "A KNEES-UP"), ("CARAVAN", "HOLIDAYS"), ("SEAGULL", "THE SEASIDE"), ("PANCAKE", "TUESDAY"),
            ("BANGERS", "DINNER"), ("MUSTARD", "DINNER"), ("BOLLARD", "OUTSIDE"), ("DUSTBIN", "OUTSIDE"),
            ("CRACKER", "CHRISTMAS")],
        8: [("ROUNDERS", "SUMMER"), ("SANDWICH", "LUNCH"), ("PORRIDGE", "BREAKFAST"), ("SAUSAGES", "BREAKFAST"),
            ("FOOTBALL", "SATURDAYS"), ("DOMINOES", "GAMES NIGHT"), ("SKITTLES", "GAMES NIGHT"),
            ("BUNGALOW", "HOME"), ("DOORSTEP", "OUTSIDE"), ("CAMPSITE", "HOLIDAYS"), ("SNOWDROP", "THE GARDEN")],
    }

    def make(self, rng, size, story):
        lengths, par = self.SIZES.get(size, self.SIZES["s"])
        words = []
        for n in rng.sample(lengths, len(lengths)):
            word, clue = rng.choice(self.WORDS[n])
            tiles = list(word)
            for _ in range(200):                                  # well jumbled: no more than one letter in place
                rng.shuffle(tiles)
                if tiles[0] != word[0] and sum(a == b for a, b in zip(tiles, word)) <= 1:
                    break
            words.append({"word": word, "clue": clue, "tiles": "".join(tiles), "tries": [], "end": None})
        return {"words": words, "par": par, "t": 0}

    def _at(self, p):
        return next((i for i, w in enumerate(p["words"]) if w["end"] is None), len(p["words"]))

    def _hinted(self, w):
        """Whether the page may see how many letters each go got right from the start: not till the second wrong
        go (or once the word's over)."""
        wrong = sum(t != w["word"] for t in w["tries"])
        return wrong >= self.HINT_AFTER or w["end"] is not None

    @staticmethod
    def _prefix(a, b):
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    def _word_score(self, p, i):
        w = p["words"][i]
        if not w["tries"] or w["tries"][-1] != w["word"]:
            best = max((self._prefix(t, w["word"]) for t in w["tries"]), default=0)
            return 0.4 * best / len(w["word"])
        start = p["words"][i - 1]["end"] if i else 0
        took, par = (w["end"] - start) / 1000, p["par"]
        quick = 1.0 if took <= par else max(0.6, 1 - 0.4 * (took - par) / (1.5 * par))  # 0.6 by 2.5 times par
        return self.TRY_SCORE[len(w["tries"]) - 1] * quick

    def public(self, p):
        k = self._at(p)
        w = p["words"][min(k, len(p["words"]) - 1)]
        return {"count": len(p["words"]), "at": k, "par": p["par"], "limit": self.TRIES, "tiles": w["tiles"],
                "clue": w["clue"], "solved": bool(w["tries"]) and w["tries"][-1] == w["word"],
                "tries": [{"word": t, "right": self._prefix(t, w["word"]) if self._hinted(w) else None}
                          for t in w["tries"]],
                "results": [round(self._word_score(p, i), 3) for i in range(k)],
                "got": [x["tries"][-1] == x["word"] for x in p["words"][:k]]}

    def move(self, p, body):
        k = self._at(p)
        word, t = body.get("word"), body.get("t")
        if k >= len(p["words"]):
            raise Refuse("You're in.")
        w = p["words"][k]
        if not isinstance(word, str) or sorted(word.upper()) != sorted(w["tiles"]):
            raise Refuse("Use every letter on the note.")
        if not _number(t) or not 0 <= t <= 3_600_000:
            raise Refuse("Spell it out.")
        word = word.upper()
        if word in w["tries"]:
            raise Refuse("You've tried that one.")
        p["t"] = max(p["t"], int(t))
        w["tries"].append(word)
        if word == w["word"] or len(w["tries"]) >= self.TRIES:
            w["end"] = p["t"]

    def done(self, p):
        return self._at(p) >= len(p["words"])

    def score(self, p):
        return sum(self._word_score(p, i) for i in range(len(p["words"]))) / len(p["words"])

    def solve(self, p):
        t, out = p["t"], []
        for w in p["words"][self._at(p):]:
            t += 1000
            out.append({"word": w["word"], "t": t})
        return out

    def bot(self, p, rng, skill):
        """A so-so player: a long word takes longer to see and to spell, and each wrong go (and the letters right
        from the start, once they show) makes the next one likelier."""
        w = p["words"][self._at(p)]
        n, goes = len(w["word"]), len(w["tries"])
        t = p["t"] + int(n * rng.uniform(0.9, 1.8) * (1.6 - skill) * 1000)
        if rng.random() < skill * (1.3 - 0.1 * n) + 0.15 * goes:
            return {"word": w["word"], "t": t}
        tiles = list(w["tiles"])
        for _ in range(20):
            rng.shuffle(tiles)
            if "".join(tiles) not in w["tries"]:
                break
        return {"word": "".join(tiles), "t": t}


class Rota(_Quiz):
    """The shift rota's pinned up by the door, everyone on it three or four times, and the guard tests you on it
    quick: plain ones (who's on nights Thursday, how many lates is Kev on) and ones that take two looks (who's on
    lates the day after Sue's night, which day are Val and Jan both on). It stays up the whole time; it's about
    reading it fast, so the guard only waits so long for each answer (the page times it from the question's own
    wait and says when he's had enough, which counts as wrong). Every question has exactly one right answer among
    its three (truth() checks each against the rota), and the wrong two are the easiest to misread for it."""
    key, role, title = "rota", "face", "READ THE ROTA"
    patience = True
    SIZES = {"s": (5, 3, ["plain", "count", "two"], 6), "m": (6, 3, ["plain", "count", "two", "two"], 6),
             "big": (7, 4, ["plain", "plain", "count", "two", "two"], 7)}   # days, shifts, questions, seconds he waits
    EXTRA = {"plain": 0, "count": 1, "two": 2}                      # more seconds for a question that takes more
    WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    SHIFTS = {3: ["EARLIES", "LATES", "NIGHTS"], 4: ["EARLIES", "DAYS", "LATES", "NIGHTS"]}
    ONE = {"EARLIES": "EARLY", "DAYS": "DAY SHIFT", "LATES": "LATE", "NIGHTS": "NIGHT"}
    MANY = {"EARLIES": "EARLIES", "DAYS": "DAY SHIFTS", "LATES": "LATES", "NIGHTS": "NIGHTS"}
    STAFF = ["SUE", "KEV", "PAT", "MO", "BEV", "RAJ", "JAN", "VAL", "DES", "LEN", "DOT", "TED", "ROY", "KIM"]
    KINDS = {"plain": ["who", "what", "when"], "count": ["count"], "two": ["after", "whenon", "both"]}

    def make(self, rng, size, story):
        n_days, n_shifts, plan, wait = self.SIZES.get(size, self.SIZES["s"])
        start = rng.randrange(7)
        days = [self.WEEK[(start + i) % 7] for i in range(n_days)]
        shifts = self.SHIFTS[n_shifts]
        grid = self._fill(rng, n_days, n_shifts)
        plan = rng.sample(plan, len(plan))
        qs, asked = [], set()
        for step in plan:
            for kind in rng.sample(self.KINDS[step], len(self.KINDS[step])) + self.KINDS["plain"]:
                q = self._question(rng, kind, days, shifts, grid, asked)
                if q:
                    q["wait"] = wait + self.EXTRA[step if kind in self.KINDS[step] else "plain"]
                    qs.append(q)
                    break
        return {"days": days, "shifts": shifts, "grid": grid, "qs": qs, "picks": []}

    def _fill(self, rng, n_days, n_shifts):
        """grid[shift][day], everyone three or four times and nobody on two shifts in a day: each day takes the
        people with the most shifts still to work (ties at random), which always works out."""
        cells = n_days * n_shifts
        names = rng.sample(self.STAFF, round(cells / 3.5))
        left = {n: 3 for n in names}
        for n in rng.sample(names, cells - 3 * len(names)):
            left[n] += 1
        grid = [[""] * n_days for _ in range(n_shifts)]
        for d in range(n_days):
            today = sorted(names, key=lambda n: (-left[n], rng.random()))[:n_shifts]
            rng.shuffle(today)
            for s, n in enumerate(today):
                grid[s][d] = n
                left[n] -= 1
        return grid

    @staticmethod
    def _on(grid, name, s):
        return [d for d, n in enumerate(grid[s]) if n == name]

    @staticmethod
    def _works(grid, name):
        return {d for row in grid for d, n in enumerate(row) if n == name}

    def truth(self, q, days, shifts, grid):
        """Every answer that's right for a question, worked out from the rota alone (for checking)."""
        k, a = q["kind"], q["args"]
        if k == "who":
            return {grid[a[0]][a[1]]}
        if k == "what":
            return {shifts[s] for s in range(len(shifts)) if grid[s][a[1]] == a[0]}
        if k == "when":
            return {days[d] for d in self._on(grid, a[0], a[1])}
        if k == "count":
            return {str(len(self._on(grid, a[0], a[1])))}
        if k == "after":
            return {grid[a[2]][d + 1] for d in self._on(grid, a[0], a[1]) if d + 1 < len(days)}
        if k == "whenon":
            return {grid[a[2]][d] for d in self._on(grid, a[0], a[1])}
        return {days[d] for d in self._works(grid, a[0]) & self._works(grid, a[1])}

    def _question(self, rng, kind, days, shifts, grid, asked):
        """One question of a kind with a single right answer, and two wrong ones that are easy to misread for it."""
        names = sorted({n for row in grid for n in row})
        nd, ns = len(days), len(shifts)
        cands = []
        for name in names:
            for s in range(ns):
                on = self._on(grid, name, s)
                if kind == "who" and on:
                    d = on[0]
                    near = [grid[x][d] for x in range(ns) if x != s] + [grid[s][y] for y in (d - 1, d + 1) if 0 <= y < nd]
                    cands.append((f"WHO'S ON {shifts[s]} {days[d]}?", (s, d), grid[s][d], near))
                if kind == "what" and on:
                    d = on[0]
                    cands.append((f"WHAT'S {name} ON {days[d]}?", (name, d), shifts[s], [x for x in shifts if x != shifts[s]]))
                if kind == "when" and len(on) == 1:
                    d = on[0]
                    near = [days[y] for y in sorted(self._works(grid, name)) if y != d] + [days[y] for y in (d - 1, d + 1) if 0 <= y < nd]
                    cands.append((f"WHEN'S {name} ON {shifts[s]}?", (name, s), days[d], near))
                if kind == "count":
                    right = len(on)
                    near = [str(x) for x in (right + 1, right - 1, right + 2) if x >= 0]
                    cands.append((f"HOW MANY {self.MANY[shifts[s]]} IS {name} ON?", (name, s), str(right), near))
                if kind in ("after", "whenon") and len(on) == 1:
                    d = on[0]
                    for s2 in range(ns):
                        if s2 == s:
                            continue
                        if kind == "after" and d + 1 < nd:
                            near = [grid[s2][d], grid[s][d + 1]] + [grid[s2][y] for y in (d + 2, d - 1) if 0 <= y < nd] + \
                                   [grid[x][d + 1] for x in range(ns) if x not in (s, s2)]
                            cands.append((f"WHO'S ON {shifts[s2]} THE DAY AFTER {name}'S {self.ONE[shifts[s]]}?",
                                          (name, s, s2), grid[s2][d + 1], near))
                        if kind == "whenon":
                            near = [grid[s2][y] for y in (d - 1, d + 1) if 0 <= y < nd] + [grid[x][d] for x in range(ns) if x not in (s, s2)]
                            cands.append((f"WHO'S ON {shifts[s2]} WHEN {name}'S ON {shifts[s]}?", (name, s, s2), grid[s2][d], near))
        if kind == "both":
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    both = self._works(grid, a) & self._works(grid, b)
                    if len(both) == 1:
                        d = next(iter(both))
                        one = self._works(grid, a) ^ self._works(grid, b)
                        near = [days[y] for y in sorted(one, key=lambda y: abs(y - d))] + [days[y] for y in range(nd) if y != d]
                        cands.append((f"WHICH DAY ARE {a} AND {b} BOTH ON?", (a, b), days[d], near))
        rng.shuffle(cands)
        for ask, args, right, near in cands:
            if ask in asked:
                continue
            q = {"kind": kind, "args": list(args), "ask": ask}
            true = self.truth(q, days, shifts, grid)
            if true != {right}:
                continue
            wrong = [x for x in dict.fromkeys(near) if x not in true][:4]
            if len(wrong) < 2:
                continue
            options = [right, *rng.sample(wrong, 2)]
            rng.shuffle(options)
            asked.add(ask)
            return {**q, "options": options, "answer": options.index(right)}
        return None

    def public(self, p):
        out = {"days": p["days"], "shifts": p["shifts"], "grid": p["grid"], **self._shown(p)}
        if out["at"] < len(p["qs"]):
            q = p["qs"][out["at"]]
            out.update(ask=q["ask"], patience=q["wait"])
        return out

    def bot(self, p, rng, skill):
        """A so-so reader: the two-step ones and the counting trip them up more, and now and then he's too slow."""
        k = len(p["picks"])
        q = p["qs"][k]
        hard = 0.15 if q["kind"] in self.KINDS["two"] else 0.08 if q["kind"] == "count" else 0.0
        if rng.random() < 0.15 * (1 - skill) + hard / 3:
            return {"slow": True, "round": k}
        if rng.random() < min(0.97, skill + 0.2 - hard):
            return {"pick": q["answer"], "round": k}
        return {"pick": rng.choice([i for i in range(3) if i != q["answer"]]), "round": k}


GAMES: dict[str, Game] = {g.key: g for g in (Talk(), FakeId(), Calm(), Names(), Anagram(), Rota())}
