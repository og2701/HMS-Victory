# Bank Job

A heist on the Bank of HMS Victory, the server's own bank, for a crew of up to six, under **Together** in the
activity next to UKP Kart, Broadside and Countdown.
Bot side: `lib/activities/heist.py` (the game and the money), `heist_posts.py` and `heist_card.py` (the post and its
picture), routes in `lib/activities/server.py`, `/heist` and a Join/Play button in `launcher.py`. Page side: `src/heist/` in
`ukplace-activities`. Tests: `tests/test_heist.py`.

## How a job goes

1. **The crew.** The host picks a target and a buy-in. Anyone can join from the lobby or the post's Join button and
   take one of six jobs. Any job nobody takes is done by Two-Pint Terry, who takes 8-16s over each thing and does it
   anywhere from 0% to 60% well.

   | Target | Vault holds | Crew | Buy-in up to |
   |---|---|---|---|
   | The Village Branch (Nether Wallop) | 3x | 1+ (free alone, 2+ staked) | 1,000 |
   | The High Street Branch | 6x | 2+ | 1,000 |
   | The Vault | 12x | 3+ | 500 |

   The fence's **tip of the day** is the same target for everyone (seeded by the UK date) and holds 25% more. The
   **Old Bill** warm up with every staked job and halve every three hours. When they're hot, every vault holds up to
   40% less, so a run of big wins makes the next jobs smaller.
2. **The plan (25s, or until everyone's voted on both).** The crew votes on a route into the branch, drawn on its
   cutaway (the Village Branch has the front door or the back yard, 3 rooms; the High Street the roof or front door,
   5, or the drains, 2; the Vault the roof, 7, front door, 6, or drains, 3), and on going in **quiet** or **loud**.
   A tie on either goes to the host. The drains are just the short way in now, for either: the old "loud" tag on
   them meant nothing once the whole job is quiet or loud. A random **twist** for the night (rain, a shift change,
   Brian on the door, a 1962 Chubb) doubles one job's weight, or makes the vault fill faster or slower.
   `lib/activities/heist_rooms.py` says which rooms each route goes through and what's in them.
3. **A quiet job: the rooms.** The crew walks the route a room at a time (6s between rooms, with how the last room
   went: every job in it, who did it and how). Each room has two to four things in the way, each one job's: a keypad,
   a lock, a patrol, a route, someone to blag past, loot to load. Any player whose job a room hasn't got gets a quick
   spare thing there instead (a padlock, the way out, a cash box), so nobody stands about. Terry never gets these,
   they don't make the room's clock any longer, and they don't soften a short crew: the alarm is weighed over the
   building's own things, so Terry's jobs cost what they always did, and a spare done badly only adds to it. Each
   player gets straight on with their thing as they walk in: its puzzle, sized to the thing (a 3-digit code for a
   small keypad, the full puzzle at the vault door). There's no skipping it any more (the old "loud" on a thing, done
   at once at 35%, is gone: the choice is the whole job's now). A thing whose job nobody holds goes to Terry, unless
   someone free taps **Step in** first and does it themselves at 80% of their score. The room ends when everything
   in it is done or its clock runs out (20s plus enough for its biggest thing and 6s for each other, plus the
   run-down below), so whoever finishes first waits for the rest; anything unfinished is scored as it stands.

   One **alarm** covers the whole job: every thing adds its weight (by size, doubled by the twist) times what it
   missed by. If the alarm passes 85% the crew runs straight for the vault from wherever they are, and every thing
   left scores 0. The job score J is 1 minus the alarm.
4. **A loud job: the breach.** Real time, one timer for the whole crew. Each player gets up to three tasks, their own
   things on the way in (the building's first, the most important first, then spares) and nothing for Terry, and
   does them one after another at their own pace, moving on the moment they're done. Every task is a quick bit of
   action (`heist_games/loud.py`), easier and quicker than a puzzle: the Hacker fries a panel, the Safecracker drills
   a lock and the Wheelman rams a gate (tap while the swinging marker's in the zone), the Lookout smashes the cameras
   and leaves the pigeons and the Face floors whoever reaches for the alarm (tap the right ones as they pop up), the
   Muscle smashes and grabs (mash GRAB). The timer starts at 60s plus 15s a room on the way in (plus a run-down's
   15s): 120s for the Village Branch, 150s for the High Street's front door, 180s for the Vault's roof. **Every task
   done, by anyone, takes 5s off it and adds 1s of safe time in the vault**, each times how well it went; the safe
   time starts at 3s and stops at 15s. When the timer runs out (or nobody has anything left to do) the whole crew's
   in the vault, done or not, and a task never finished scores 0. A typical six-handed loud job is over in about a
   minute; a quiet one takes one and a half to six and a half.

   **Loose cash.** Anyone whose tasks are done picks up bonus tasks (an ATM, a till, a cash box) until the timer runs
   out. Each pays a few UKPence straight to that player there and then (Bank Job loose cash), theirs whatever happens
   in the vault: 3 UKPence at the smallest buy-in up to 15 at 1,000, the luck of the draw over the 4 below that (never
   under 3) times how well it went, at most four of them and never past **3% of the buy-in a job**. None on a free
   job; play money in practice. The main tasks help the whole crew (the timer and the safe time); loose cash is the
   player's own.

   A loud job's score J is the average of its tasks' scores, and its edge runs from 8% down to 4% (`loud_edge`).
5. **The run-down.** No puzzle or task throws anyone straight onto the clock. First comes a run-down: what it is and
   how to play it (`lib/activities/heist_briefs.py`: for each of the 42 games, the six loud tasks and the three kinds
   of loose cash, the goal in plain words, then the one thing you do, naming what's on screen, then what scores best
   or what to avoid), with a little looping demo of the game's control playing itself beside it (the page's
   `src/heist/demo.ts`, one demo for each kind of control). The first time someone meets a game it's the full one, 15 seconds; after that it's a
   5-second "ready" with a one-line reminder; a game they haven't met in a month gets the full one again. The bot
   remembers who's met what (`met` in the saved file): a job never deals anyone the same game twice, so "the first
   time in this job" would mean the full run-down in every room. The clock is the bot's, not the page's: the puzzle
   takes no moves and its board isn't even sent until the run-down's over, and **GOT IT** starts it early. Each quiet
   room's clock grows by the longest run-down any player in it will get, so reading it never costs anyone time.

   **Screenshots.** A memory puzzle can't be a lookup from a screenshot of what you were shown:
   - the Face's cover story is shown a line at a time (tap a line to read it, and the last one hides), and the
     staff board (STAFF NAMES) a card at a time (tap a card to turn it, the rest stay face down; the board's up 2s
     longer to make up), so one screenshot holds one line or one card;
   - then the guard only waits so long on each answer (9 seconds on the story, 7 on a name, timed on the page
     like the small talk's, and too slow counts as wrong), so flicking through a phone's photos runs him out;
   - the Wheelman's ROUTE MEMORY shows the way out as the van drives it, with only the turn it's at lit, never
     the whole route at once;
   - the Lookout's patrol was already one window at a time.
   The other quick-look games (SPOT THE GUARD, WHO MOVED, the number plate) flash for a moment and stay as they
   are, and the easy ones stay easy.

   The bot makes each puzzle, keeps its answer and judges the result, so the page never sees an answer:
   - **Hacker:** crack a 3- or 4-digit keypad code, Mastermind-style.
   - **Safecracker:** one to three tumblers. Each listen says cold, warm, hot or click, and which way to turn.
   - **Lookout:** watch a guard's torch go from window to window, then tap the windows in that order.
   - **Wheelman:** plot the cheapest route across a town of traffic and roadblocks (5x5 up to 7x7 at the vault door). Every
     town is made so the obvious way (straight up and across) is a trap. Scored against the true cheapest route
     (Dijkstra), squared, so a route a fifth too dear scores about 70%.
   - **Face:** learn the crew's cover story, then answer the guard's questions once it's hidden.
   - **Muscle:** load the most valuable bags that fit. Scored against the true best load (a knapsack). Muscle things
     are **loot**: each one makes the vault hold up to 12% more (18% done loud), 36% at most.
6. **The report (8s).** How it went: the alarm (quiet) or the tasks done (loud), the safe time, the loot, any loose
   cash, and the police's odds of arriving by 1.5x, 2x and 3x.
7. **The vault: grab the cash.** Everyone **grabs**: each tap of GRAB keeps their bag up with the piles for half a
   second (two taps a second will do; mashing doesn't add more), so a bag is what the piles gained while they kept
   grabbing. First comes the **safe time**: a quiet job's is 4s for a botched job up to 12s for a clean one (4 + 8J);
   a loud job's is what the crew earned in the breach (3-15s). The police can't come in it, but the piles only fill
   from nothing to **break-even less the edge** (1 - e of the buy-in), so the safe time is comfort, not profit. Then
   the piles climb on from there to the cap (with the loot added), slowly at first and faster at the end, and the
   police can come at any moment. The **police scanner** is the tell: it shows the real chance the police turn up in
   the next second, given they haven't yet (and the siren in the distance gets louder with it). It's the honest
   hazard, the same for any job with this edge, cap and pace, so it shows how dicey holding on is, never when these
   police will come. Each of the crew presses **Leg it** whenever they like and banks their buy-in times their bag.
   Anyone still inside when the police arrive is nicked and loses their buy-in (loose cash already paid stays paid).
   **Gold bars.** Two taps a second is all GRAB can use (that cap is what stops an auto-clicker being worth
   anything), so for anyone mashing faster than that (four or more grabs in a second), every 5-9 seconds GRAB greys
   out and locks and a gold bar turns up somewhere on the vault's floor for a second and a half. Tap the bar and
   it's caught: the bag is topped up by half of whatever it's fallen short of the piles, and kept up hands free for
   4 seconds. Tap the locked GRAB instead (after the first 0.3s) and the bar's fumbled, so mindless hammering drops
   every one. While a bar's showing the bag is kept up anyway, so going for it costs nothing, and someone tapping at
   two a second never sees one and loses nothing: a bar only ever closes a gap, it never puts a bag past the piles.
   The bot decides when bars come, whose they are and whether one was caught (the page is only told about the one
   showing now), and bot crewmates catch some, by their skill. The report screen before the vault says all this
   in three lines, with the demo. On the page every grab shows at once (the button punches, a note flies into the
   bag, a count ticks up) and a GRAB SPEED meter reads MAX while you're keeping up.
   If the police never come before the cap, everyone left inside walks out with their bag. Someone who walked off has
   the crew bag a bit for them (half of what the piles hold).

A Village Branch job takes about a minute and a half in the rooms quiet, the High Street about five and the Vault
about six and a half; a loud one, about a minute.

**Quick chat.** On every screen of a crew (the lobby, the plan, the rooms, the vault) a chat button opens a dozen
set lines ("Hurry up!", "Nice one!", "Well done!", "Help!", "Leg it!", "Oops", "Where's Terry?", "Put the kettle on",
"It's Brian!", "Shh!", "Hold your nerve!", "Grab it all!"); the line pops up for the whole crew for a few seconds as a
bubble with the sender's face. No free text: the bot only takes a line from its list (`heist.CHAT`), and one every
2.5 seconds per player. Bot crewmates in practice pipe up now and then (a "Nice one!" or an "Oops" as they finish
something, a "Leg it!" as they get out).

## The money

### What the player sees: the bank's cut up front

The sums below are all in multiples of the buy-in (m), and that's how the bot works them out and pays them. The page
shows them another way, so a good job doesn't read as a loss: **the bank's cut is taken up front and the vault is
shown in multiples of the stake left in play.**

- The report (as the vault door opens) shows the cut, tied to the job: "Buy-in 100 · Bank's cut 1 · In play 99",
  "a clean job: the bank only takes 1%". The stake in play is `in_play(S, b) = int(S * floor2(b))`, what getting out
  at exactly 1.00x pays, and the cut is `S - in_play`.
- Every multiplier in the vault is `m' = m / b` (`shown()`, floored to 0.01). The safe time fills the piles from 0
  to **1.00x: your stake back** ("GETTING YOUR STAKE BACK", then "STAKE BACK · ANYTHING MORE IS PROFIT"). After it the
  bag shows profit over the stake in play ("STAY IN TO PROFIT", "+0.12x · +11") and the climb runs on to the cap,
  shown as `cap / b`. The police come at `M' = M / b = 1 / U`, so the chance of reaching m' is `1 / m'`, the same
  for every job: the report's "police by 1.5x / 2x / 3x" are 33% / 50% / 67% whatever the edge.
- Leaving at m' pays `int(S * floor2(m' * b)) = int(S * floor2(m))`, exactly what it always did
  (`test_the_vault_shown_of_the_stake_in_play_pays_the_same_at_every_moment`).
- The end screen and the #casino card say the result against the buy-in, the cut included: "Out at 1.00x: stake
  back, broke even less the bank's cut (-4)", "Out at 1.40x: +34", or, for leaving in the safe time, "before the
  stake was back". The police are shown at m' too. The target picker keeps the target's cap in buy-in terms ("x6, up
  to 600"): in the vault that same 600 is `6 / b` of the stake in play.
- What's stored is unchanged: a result's `take` and the clean job bonus's CLEAN_TAKE (1.5) and Greedy Git's 8x stay
  multiples of the buy-in; `shown` sits beside `take` in the results for the page.

### What the bank keeps

The vault has two parts. In the **safe time** (S seconds) the piles fill from 0 to `b = 1 - e`, and the police can't
come. After it they climb from b to the cap, and the police arrive at take `M = (1 - e) / U`, where U is uniform on
(0, 1], so M is never below b and they never come in the safe time. The chance the piles reach m (at least b) before
the police do is `(1 - e) / m`.

- **Leaving after the safe time, bag kept up.** A bag kept up with the piles is worth m, so leaving at m returns
  `m * (1 - e) / m = 1 - e`. That is the same at every m, so no moment beats any other: not as the safe time ends,
  not at the cap, not anywhere in between.
- **Leaving in the safe time.** Sure money, but the piles are below b: leaving at t returns `b * t / S < 1 - e`.
  That's what pays for the safe time: however long it is (4s or 15s), nothing in it is worth more than 1 - e.
- **Grabbing.** A grab keeps the bag up for half a second, and the bag only gains what the piles gained over the time
  it was kept up, so it's never worth more than the piles. A bag kept up a fraction q of the way returns q of the
  above (at most). Perfect grabbing (or a page doctored to grab perfectly) reaches 1 - e and no further. Grabs sent
  from the future, out of order or more than 2s late are dropped.
- **Gold bars.** Catching one adds half of (piles - bag) to the bag and keeps it up for a few seconds, so the bag is
  still never worth more than the piles: for a perfect grabber a bar is worth nothing but a rest, and for anyone
  else it wins back part of what slow grabbing lost. Always catching them, never catching them or hammering through
  them all leave the bag at or under the piles (`test_gold_bars_top_a_bag_up_but_never_past_the_piles`).
- **Any strategy at all.** The only thing anyone can see during the vault is that the police haven't come yet, and
  the scanner, which is a fixed function of time (the same for every job with this edge, cap and pace). So any way of
  deciding when to go, off the scanner, a hunch or the crew, comes down to a moment chosen without knowing M, and
  every moment returns at most 1 - e. **No strategy beats the house edge e.**

The edge depends on the job:

- **Quiet:** `e = 0.08 - 0.07 * J`, so 8% for a botched job and 1% for a perfect one.
- **Loud:** `e = 0.08 - 0.04 * J`, so 8% for a botched breach and 4% for a perfect one. On top of it a player can pick
  up loose cash, at most 3% of the buy-in a job. The best a loud job can ever return, all its loose cash included, is
  `1 - 0.04 + 0.03 = 0.99`: the same as a perfect quiet job, never better. A loud job is quicker and easier; a quiet
  one is where the best odds are.

Simulated return to players (400k vaults per row, cap 6x, a 28s climb; `test_the_vault_with_its_grab_safe_time_and_
scanner_never_beats_the_edge` and `test_a_loud_job_with_all_its_loose_cash_never_beats_a_perfect_quiet_one` check the
same on every test run, over the grab, both lengths of safe time, random moments and the scanner):

| Job | Edge | Safe time | Out just before safe ends | Out as it ends | Out at 1.5x | Hold to the cap | 1.5x, slow grabber (80%) | Out when the scanner says 5%/s |
|---|---|---|---|---|---|---|---|---|
| Quiet, J = 0.0 | 8.0% | 4s | 0.805 | 0.920 | 0.922 | 0.922 | 0.737 | 0.922 |
| Quiet, J = 0.5 | 4.5% | 8s | 0.895 | 0.955 | 0.956 | 0.960 | 0.765 | 0.955 |
| Quiet, J = 0.75 | 2.7% | 10s | 0.924 | 0.973 | 0.972 | 0.968 | 0.777 | 0.972 |
| Quiet, J = 1.0 | 1.0% | 12s | 0.949 | 0.990 | 0.991 | 0.992 | 0.793 | 0.990 |
| Loud, J = 0.0, max loose cash | 8.0% | 9s | 0.899 | 0.950 | 0.950 | 0.954 | 0.766 | 0.950 |
| Loud, J = 0.5, max loose cash | 6.0% | 9s | 0.918 | 0.970 | 0.970 | 0.975 | 0.782 | 0.971 |
| Loud, J = 1.0, max loose cash | 4.0% | 9s | 0.937 | 0.990 | 0.990 | 0.990 | 0.798 | 0.990 |

"Out when the scanner says 5%/s" is going the first moment the scanner shows a 5% chance of the police in the next
second; any other level gives the same, as it's just another moment. The loud rows count the most loose cash on top.
"Hold to the cap" wobbles by about half a point either way between runs: it rests on the few vaults the police never
reach, each paying the whole cap.

The other house games sit at 2-7% (Mines and Penalty 2%, Plinko and Blockade 3%, Glass Bridge 4%, Chest 5%,
Darts 7%). A typical crew of real people, with a mix of good and bad rounds and a role or two left to Terry, should
land around J 0.4-0.6, so an edge of about 4-5%, in line with the rest of the casino. That is a casino game where
playing well is worth something.

Even a page modified to cheat at every puzzle only reaches J = 1, which still leaves the bank 1%. Flooring payouts
to whole UKPence adds a little more. Because a smaller crew has more of its jobs done by Terry (each role a player
fills can lift J), a bigger crew does better.

### How much one job can cost the bank

The whole crew shares one police arrival, so a vault cleared at the cap pays everyone at once. The worst case is
`6 x buy-in x cap x 1.25 (the tip) x 1.36 (the most loot)`. `max_stake()` caps every buy-in so that this worst case is at most
`HEIST_EXPOSURE_PCT` (15%) of what the bank holds **above its reserve floor** (`RESERVE_FLOOR`, 5k). The reserves
are `reserve_policy.bank_reserves()`: free reserves, the bank's balance less the principal locked in bonds.

| Free reserves | Spare above floor | Village Branch max | High Street max | Vault max |
|---|---|---|---|---|
| 925,000 | 920k | 1,000 | 1,000 | 500 |
| 325,000 | 320k | 1,000 | 500 | 250 |
| 175,000 | 170k | 500 | 250 | 100 |
| 85,000 | 80k | 250 | 100 | 50 |
| 5,000 (the floor) | 0 | free only | free only | free only |

A bag is never worth more than the piles, so grabbing can't add to this; a loud job's loose cash adds at most 3% of
the buy-in each, under 0.2% of the worst case. So a single job can never take more than about 15% of the spare
reserves. The chance of that worst case is about
`(1 - e) / cap`: under 8% at the Vault, and only if all six hold their nerve to the very end. As the bank nears its
floor, the game shrinks to free jobs on its own. The heat then cuts caps after any run of luck.

### The clean job bonus (the healthy reward)

A **clean job** is a staked crew of three or more, every one of them out with at least 1.5x. Each member gets a
bonus of 10% of the buy-in, up to 75 UKP:

- **once per person per UK day** (`HEIST_CLEAN_BONUS_PCT`, `HEIST_CLEAN_BONUS_MAX`)
- **only for members who joined the server at least 14 days ago** (`HEIST_BONUS_MIN_DAYS`), so alts can't farm it
- **paid as a discretionary reward** (`add_bb(..., discretionary=True)`): it scales down with the reserve throttle,
  and the usual earnings wealth tax applies above 10k

The 1.5x floor stops a crew all legging it at 1.01x to collect it. Everyone has to hold their nerve a bit, and the
chance of a crew pulling it off is at most `(1 - e) / 1.5`, about 64% for an average crew.

What the bonus does to the first job of someone's day (everyone leaving together at 1.5x):

| Buy-in | Edge cost (average crew, e = 4.5%) | Bonus value (x 0.64) | Net | Same for a perfect crew (e = 1%) |
|---|---|---|---|---|
| 100 | -4.5 | +6.4 | about +2 | +5.6 |
| 500 | -22.5 | +32 | about +9 | +28 |
| 750 | -34 | +48 | about +14 | +42 |
| 1,000 | -45 | +48 | about +3 | +40 |

So the first job of the day pays a little for an ordinary crew and a bit more for a very good one. Every
job after that keeps the full edge. **At most about 45 UKP per player per day comes out of the bank** this way,
and only from crews who play properly. Twenty regular heisters a day would cost at most around 6k a week. That's
the same order as benefits (40-100 a day each for anyone under 400 UKP), and the throttle shrinks it further
whenever reserves are low. Every other job is a sink at about 4-5%, like the rest of the casino, so across a week Bank Job
should take in more than it pays out. The bank's 🏦 Bank Job line on `/bank` will show whether it does.

### Badges (one-off rewards)

These only count on staked jobs, so a free job can't farm a badge's reward:

| Badge | Tier | For |
|---|---|---|
| First Job | Bronze | getting out of a staked job with something |
| Clean Getaway | Silver | a clean job at the Bank of HMS Victory |
| Criminal Mastermind | Silver | a crew of 4+ with a job score of 90% or more |
| Greedy Git | Gold | getting out at 8x or more |
| Banged Up | Bronze | getting nicked in 5 staked jobs |

### Where it shows up

| Where | What |
|---|---|
| Bank stats | `total_heist_in` / `total_heist_out` (reasons containing "Bank Job"), on `/bank` as 🏦 Bank Job and in the casino total |
| Statements | under Casino |
| `casino_results` | one row per player per staked job, game `heist`. That also keeps them clear of the economy-dormant tax. |
| `game_records` / `game_record_players` | the whole job, with each player's result (`escaped`, `cleared` or `nicked`) and payout |
| Lobby | Most Wanted: the week's top earners |
| Records analyst and data queries | know it as "Bank Job" |

The `heist` command name is in the `economy` tier's casino set (`restrictions.ACTIVITY_GAMES`), and the activity
API's `_gate` enforces it.

## Safety

- **Buy-ins** go into the bank when the host sends the crew in, and are refunded if anyone can't cover theirs.
- **Settling:** a job is saved as paid before any money moves, so a crash can only ever leave it unpaid, never paid
  twice.
- **Restarts:** while a job is under way it is saved every second. On the next boot every live job's clocks move on
  by the time the bot was away, so the vault carries on from the moment it stopped. Nobody gets nicked while they
  couldn't press anything, and nobody gets a free look at a later take. Graceful shutdown also waits for live jobs
  (`heist.active()`).
- **Leaving** before the job starts just walks you off. After it starts your buy-in stays in: you're still in the
  vault until the police come or it's cleared.

## Practice (testers only)

A tester can tick **Practice** on the target picker to open a practice job. It's the real game (the same rooms, puzzles,
alarm, vault and police) with play money:

- **Bot crewmates.** When the host sends it in, every role nobody took gets a bot crewmate, so a practice crew is
  always six and Two-Pint Terry never turns up (unless someone walks off). The bots back whatever way in the crew
  votes for, so the plan waits only on the people. Each bot rolls a skill level (35-95%); in every room it gets
  straight on with its things and finishes each 30-85% of the way into the room's time, with a score near its
  skill; in a loud job it does its tasks one after another, each 6-14s, and its scores cut the timer like anyone's
  (bots don't go after loose cash). In the vault it grabs about as well as its skill (75-100% of the piles) and legs
  it at a moment it picked (some bottle it early, some hold to the cap), and one still inside when the police come is
  nicked like anyone else. Your own loose cash in practice is play money. Bots have negative ids in the room and are
  marked `bot` in what the page sees.
- **Play money.** The buy-in can be anything up to the target's own limit whatever the reserves, and needs nothing
  in your wallet. Nothing moves: no buy-in taken, no take paid, no clean job bonus, no heat, no badges, no
  `casino_results` or `game_records` row (so not on Most Wanted), and it doesn't hold up a restart.
- **Private.** It's never posted (no listener sees it), it doesn't show in anyone else's list of crews, and nobody
  can join it.
- **Testers only**, whether Bank Job is live or not (`heist.can_practise`).

## Rolling out

`HEIST_LIVE = False`: `/heist` is registered and opens the activity straight on Bank Job, but only for
`HEIST_TESTERS`; anyone else gets an ephemeral "isn't open yet" (`launcher._not_open_to`), and the activity's API
turns them away too. There's no tile in the `/games` hub. A real (non-practice) crew opened by a tester is still
posted in the channel and listed for anyone the game's open to. Set it to `True` to open `/heist` to everyone and
add the Together tile. Practice stays with the testers either way.

Every number above is in `config.py` (`HEIST_*`) or at the top of `heist.py`: the edge range, the targets, the
clocks, the heat and the bonus.
