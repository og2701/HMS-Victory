"""Bank Job's run-downs: what each puzzle is and how to play it, shown before its clock starts.

The first time someone meets a game (or hasn't for a month), they get the full run-down (HOW, a few short lines);
after that a quick "ready" with the one-line reminder (LINE). heist.py decides which, holds the puzzle's clock
until it's over (or they tap GOT IT), and grows the room's clock to cover it. Keyed "role:game" (a role's first
game by its heist_games.FIRST key)."""

# key: (the full run-down, a line or two each; the one-line reminder)
BRIEFS: dict[str, tuple[list[str], str]] = {
    # the Hacker
    "hacker:keypad": (["Guess the code. Each digit is one of the keys shown.",
                       "Green pin: right digit, right spot. Yellow pin: right digit, wrong spot.",
                       "Fewer tries scores more."], "Guess the code. Green: right spot. Yellow: wrong spot."),
    "hacker:lights": (["Tapping a light flips it and the ones next to it.",
                       "Put every light out before the panel locks you out."], "Tap to flip a light and its neighbours. All out."),
    "hacker:wire": (["Follow the wires through the tangle.",
                     "Cut the one that runs to the lamp asked for, before its fuse burns down."], "Cut the wire that reaches the lamp asked for."),
    "hacker:password": (["Hangman: guess the password a letter at a time.",
                         "Every wrong letter costs a try. Naval, bank and British words."], "Guess the password a letter at a time."),
    "hacker:grid": (["Tap a tile to turn it a quarter.",
                     "Join the power from the left to the alarm box on the right."], "Turn the tiles to join the power across."),
    "hacker:logic": (["Each clue code says how many digits are right, and whether in the right place.",
                      "Only one code fits every clue. Work it out and key it in."], "One code fits every clue. Key it in."),
    "hacker:tiles": (["A CCTV still cut into a sliding puzzle.",
                      "Slide the tiles back into the picture, in as few slides as you can."], "Slide the picture back together."),
    # the Safecracker
    "safecracker:dial": (["Turn the dial and LISTEN. It says cold, warm, hot or click, and which way to go.",
                          "A click sets a tumbler. Set them all."], "Listen, follow the arrow, find the click."),
    "safecracker:sweet": (["Tap the door to listen there. Hot is one square off, warm two, cool three or four.",
                           "Find the sweet spot in as few taps as you can."], "Tap to listen. Hot, warm, cool: find the spot."),
    "safecracker:pins": (["Set each pin from 1 to 9 and TEST.",
                          "The lock only says how many pins are set and how many are too high, never which."], "Set the pins, TEST, work it out from the counts."),
    "safecracker:levers": (["Set each lever up, middle or down and PULL.",
                            "It says how many are right, not which. Move one at a time and watch the count."], "PULL and watch the count. One lever at a time."),
    "safecracker:order": (["Press the buttons in the hidden order. A right one stays lit.",
                           "A wrong one clunks and the pad scrambles, so remember them by their look."], "Find the order. Remember the buttons by their look."),
    "safecracker:needle": (["A needle sweeps round the dial.",
                            "Tap while it's in the green notch to set a tumbler. It speeds up."], "Tap when the needle's in the green."),
    "safecracker:rings": (["Tap a ring to turn it a notch. Some rings drag their neighbour round too.",
                           "Line every gap up at the top."], "Turn the rings till every gap's at the top."),
    # the Lookout
    "lookout:patrol": (["Watch the guard's torch go from window to window. It plays once.",
                        "Then tap the windows in the same order."], "Watch the torch, then tap the windows in order."),
    "lookout:spot": (["A row of lit windows flashes up, each with a shadow on the blind.",
                      "Tap where the guard was: the peaked cap and the torch."], "Spot the guard's shadow, then tap where he was."),
    "lookout:count": (["Folk hurry past the bank. Count only the guards.",
                       "Watch out for look-alikes in peaked caps and hi-vis."], "Count only the guards going past."),
    "lookout:moved": (["Look at the hall. It goes dark for a blink.",
                       "When it's back, one person has moved. Tap them."], "Who moved in the blink? Tap them."),
    "lookout:sweep": (["A camera watches the corridor and now and then looks away.",
                       "Tap DASH to run to the next pillar while it's looking away."], "DASH only while the camera's turned away."),
    "lookout:duck": (["A guard's torch shines out of a window your way: tap HIDE, quick.",
                      "Don't hide for headlights, cats or a beam pointing along the street."], "HIDE from torches pointed at you. Nothing else."),
    "lookout:sensors": (["Get from the bottom row to the top, one tile at a time.",
                         "Each tile you stand on says how many sensors are round it."], "Step to the top. The numbers count sensors nearby."),
    # the Wheelman
    "wheelman:route": (["Plot the cheapest route from the bank (bottom left) to the lock-up (top right).",
                        "Each road costs its number in minutes. Straight up and across is usually a trap."], "Cheapest route across. Watch for the trap."),
    "wheelman:gears": (["The rev needle climbs. Shift while it's in the green band.",
                        "Too early bogs the van down, too late hits the limiter."], "Shift in the green."),
    "wheelman:dodge": (["Steer left and right round the cones, police cars and roadblocks."], "Dodge everything in the road."),
    "wheelman:jammed": (["Cars only slide along their length.",
                         "Shuffle them so the van can get out of the gap on the right."], "Slide the cars and get the van out."),
    "wheelman:memory": (["The van drives the way out once, a turn at a time.",
                         "Then drive it from memory: left, straight on or right at each junction."], "Watch the way out, then drive it from memory."),
    "wheelman:hotwire": (["Join each pair of coloured ends with a wire.",
                          "Wires can't cross, and between them they fill every square."], "Join the pairs and fill every square."),
    "wheelman:plate": (["Your car's plate flashes up. Learn it.",
                        "Then find your car in the car park full of near misses, before the fuse goes."], "Learn your plate, then find your car."),
    # the Face
    "face:cover": (["Learn your cover story. Tap a line to read it: one at a time.",
                    "Then the guard asks about it with it hidden, and he won't wait long."], "Learn the story, then answer the guard. Quick."),
    "face:talk": (["The guard fancies a natter. Pick the reply anyone would say.",
                   "The other two give you away. He only waits so long."], "Pick the innocent reply, quick."),
    "face:id": (["Your forged pass next to the job sheet: one line doesn't match.",
                 "Tap it before the guard looks up. Wrong taps cost you."], "Tap the line on the pass that doesn't match."),
    "face:calm": (["The guard stares and your heart rate climbs.",
                   "Hold BREATHE to bring it down, but it carries on a moment. Keep it in the green."], "Hold BREATHE. Stay in the green."),
    "face:names": (["The staff board: tap a card to look at it, one at a time.",
                    "Then the guard asks who has what. Pick their name before he loses patience."], "Learn who has what, then name them. Quick."),
    "face:anagram": (["The guard's password, jumbled on a sticky note, with a clue under it.",
                      "Tap the letters in order to spell it. Quicker scores more."], "Unscramble the password."),
    "face:rota": (["The shift rota's up by the door.",
                   "The guard tests you on it, quick. Read it fast."], "Read the rota and answer, quick."),
    # the Muscle
    "muscle:van": (["Bags of loot, a van that can't take them all.",
                    "Load the most money that fits the van's weight."], "The most £ that fits the van."),
    "muscle:weight": (["The fence wants an exact weight.",
                       "Put bags on the pan until the needle sits on it. Add it up first: taps are few."], "Hit the exact weight."),
    "muscle:pack": (["Fit the crates into the van, Tetris style. Pick, turn, tap where it goes.",
                     "There's one crate too many: leave the right one out."], "Fill the van. One crate's spare."),
    "muscle:cash": (["Bundles of notes land one after another.",
                     "Keep a running total, then pick it when the cloth goes over."], "Keep a running total."),
    "muscle:dye": (["A dye pack goes into one bag, then they're shuffled.",
                    "Follow it and tap the bag with the dye pack, to leave it behind."], "Follow the dye pack."),
    "muscle:mash": (["Carry the sacks up the stairs: mash HEAVE as fast as you can."], "Mash HEAVE."),
    "muscle:stack": (["A gold bar slides back and forth. Tap to drop it on the stack.",
                      "Whatever hangs over is chopped off. They speed up."], "Drop each bar dead on the one below."),
}


def brief(key: str) -> tuple[list[str], str]:
    return BRIEFS.get(key) or (["Do your bit, quick."], "Do your bit.")
