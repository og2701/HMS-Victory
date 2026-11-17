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
2. **The way in (25s, or until everyone's voted).** The crew votes on a route into the branch, drawn on its cutaway:
   the Village Branch has the front door or the back yard (3 rooms), the High Street the roof or front door (5) or
   the drains (2, loud), the Vault the roof (7), front door (6) or drains (3, loud). A tie goes to the host. A random
   **twist** for the night (rain, a shift change, Brian on the door, a 1962 Chubb) doubles one job's weight, or
   makes the vault fill faster or slower. `lib/activities/heist_rooms.py` says which rooms each route goes through
   and what's in them.
3. **The rooms.** The crew walks the route a room at a time (6s between rooms, with how the last room went: every job in it, who did it and how). Each room has two to four
   things in the way, each one job's: a keypad, a lock, a patrol, a route, someone to blag past, loot to load. Any
   player whose job a room hasn't got gets a quick spare thing there instead (a padlock, the way out, a cash box), so
   nobody stands about. Terry never gets these, they don't make the room's clock any longer, and they don't soften
   a short crew: the alarm is weighed over the building's own things, so Terry's jobs cost what they always did, and
   a spare done badly only adds to it. Its
   holder picks **quiet** (play the puzzle, sized to the thing: a 3-digit code for a small keypad, the full puzzle
   at the vault door) or **loud** (done at once, scored 35%). A thing whose job nobody holds goes to Terry, unless
   someone free taps **Step in** first and does it themselves at 80% of their score. The room ends when everything
   in it is done or its clock runs out (20s plus enough for its biggest thing and 6s for each other); anything
   unfinished is scored as it stands, and a thing nobody started scores 0.

   **The run-down.** Going quiet doesn't throw you straight onto the clock. First comes a run-down of the puzzle:
   what it is and how to play it (`lib/activities/heist_briefs.py`, a few lines for each of the 42 games). The
   first time someone meets a game it's the full one, 12 seconds; after that it's a 4-second "ready" with a
   one-line reminder; a game they haven't met in a month gets the full one again. The bot remembers who's met
   what (`met` in the saved file), because a job never deals anyone the same game twice, so "the first time in
   this job" would mean the full run-down in every room. The clock is the bot's, not the page's: the puzzle takes
   no moves and its board isn't even sent until the run-down's over, and **GOT IT** starts it early. Each room's
   clock grows by the longest run-down any player in it will get, so reading it never costs anyone time.

   **Screenshots.** A memory puzzle can't be a lookup from a screenshot of what you were shown:
   - the Face's cover story is shown a line at a time (tap a line to read it, and the last one hides), and the
     staff board (STAFF NAMES) a card at a time (tap a card to turn it, the rest stay face down), so one
     screenshot holds one line or one card;
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
     are **loot**: each one makes the vault hold up to 12% more, 36% at most.

   One **alarm** covers the whole job: every thing adds its weight (by size, doubled by the twist) times what it
   missed by. If the alarm passes 85% the crew runs straight for the vault from wherever they are, and every thing
   left scores 0.
4. **The alarm (8s).** The job score J is 1 minus the alarm. The screen shows the police's odds of arriving by 1.5x,
   2x and 3x, and says so if they're already at the door.
5. **The vault.** The take climbs from 1x to the cap (with the loot added), slowly at first and faster at the end
   (`cap ** (t / secs) ** 2`), so the low takes, where the police most often come, get a few seconds each. That only
   paces it: when the police come is drawn as a take, so it changes nobody's odds. Each of the crew presses **Leg it**
   whenever they like and banks their buy-in times the take at that moment. Anyone still inside when the police
   arrive is nicked and loses their buy-in. If the police never come before the cap, everyone left inside clears the
   vault at the cap.

A Village Branch job takes about a minute and a half in the rooms, the High Street about five and the Vault about
six and a half, so a crew that took a while to get together gets a proper game out of it.

## The money

### What the bank keeps

The police arrive at take `M = (1 - e) / U`, where U is uniform on (0, 1]. The chance the take reaches m before
they do is `(1 - e) / m`, so leaving at m returns `m * (1 - e) / m = 1 - e` of the buy-in. That is the same at
every m, so **no strategy beats the house edge e**: not legging it early, not holding to the cap, not anything in
between. The edge depends only on how well the crew did its job:

`e = 0.08 - 0.07 * J`, so 8% for a botched job and 1% for a perfect one.

Simulated return to players (400k jobs per row; `test_the_bank_keeps_its_edge_however_long_anyone_waits`
checks the same thing on every test run):

| Job score J | Edge | Return if everyone legs it at 1.2x | Return if everyone holds to the cap |
|---|---|---|---|
| 0.0 (all Terry) | 8% | 0.920 | 0.91-0.93 |
| 0.5 | 4.5% | 0.955 | 0.95-0.96 |
| 0.75 | 2.75% | 0.973 | 0.97-0.98 |
| 1.0 (perfect) | 1% | 0.990 | 0.98-1.00 |

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

So a single job can never take more than 15% of the spare reserves. The chance of that worst case is about
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
  skill. It legs it from the vault at a moment it picked (some bottle it early, some hold to the cap), and one
  still inside when the police come is nicked like anyone else. Bots have negative ids in the room and are
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
