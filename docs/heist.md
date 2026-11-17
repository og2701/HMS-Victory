# Bank Job

A heist for a crew of up to six, under **Together** in the activity next to UKP Kart, Broadside and Countdown.
Bot side: `lib/activities/heist.py` (the game and the money), `heist_posts.py` and `heist_card.py` (the post and its
picture), routes in `lib/activities/server.py`, a Join/Play button in `launcher.py`. Page side: `src/heist/` in
`ukplace-activities`. Tests: `tests/test_heist.py`.

## How a job goes

1. **The crew.** The host picks a target and a buy-in. Anyone can join from the lobby or the post's Join button and
   take one of six jobs. Any job nobody takes is done by Dodgy Dave, who is never more than 30% good at anything.

   | Target | Vault holds | Crew | Buy-in up to |
   |---|---|---|---|
   | Nether Wallop Post Office | 3x | 1+ (free alone, 2+ staked) | 1,000 |
   | Little Puddleton Building Society | 6x | 2+ | 1,000 |
   | The Bank of HMS Victory | 12x | 3+ | 500 |

   The fence's **tip of the day** is the same target for everyone (seeded by the UK date) and holds 25% more. The
   **Old Bill** warm up with every staked job and halve every three hours. When they're hot, every vault holds up to
   40% less, so a run of big wins makes the next jobs smaller.
2. **The plan (15s).** The crew votes **quiet** or **loud**; a tie goes to the host. Quiet fills the vault over 30s
   and weights the Hacker, Safecracker, Lookout and Face. Loud fills it over 20s and weights the Wheelman and Muscle.
   A random **twist** for the night (rain, a shift change, Brian on the door, a 1962 Chubb) doubles one job's
   weight, or makes the vault fill faster or slower.
3. **The job (50s).** Everyone plays their puzzle at once. The bot makes each puzzle, keeps its answer and judges
   the result, so the page never sees an answer:
   - **Hacker:** crack a 4-digit keypad code (digits 1-6) in 6 guesses, Mastermind-style.
   - **Safecracker:** three tumblers. Each listen says cold, warm, hot or click, and which way to turn. 15 listens
     in all.
   - **Lookout:** watch a guard's torch go from window to window seven times, then tap the windows in that order.
   - **Wheelman:** plot the cheapest route across a 6x6 town of traffic and roadblocks. Scored against the true
     cheapest route (Dijkstra).
   - **Face:** learn a generated cover story, then answer the guard's four questions once it's hidden.
   - **Muscle:** load the van with the most valuable bags that fit. Scored against the true best load (a knapsack).
4. **The alarm (8s).** Each job's score, weighted, gives the crew's **job score** J (0-1), shown as an alarm level.
5. **The vault.** The take climbs from 1x to the cap. Each of the crew presses **Leg it** whenever they like and banks
   their buy-in times the take at that moment. Anyone still inside when the police arrive is nicked and loses their
   buy-in. If the police never come before the cap, everyone left inside clears the vault at the cap.

## The money

### What the bank keeps

The police arrive at take `M = (1 - e) / U`, where U is uniform on (0, 1]. The chance the take reaches m before
they do is `(1 - e) / m`, so leaving at m returns `m * (1 - e) / m = 1 - e` of the buy-in. That is the same at
every m, so **no strategy beats the house edge e**: not legging it early, not holding to the cap, not anything in
between. The edge depends only on how well the crew did its job:

`e = 0.12 - 0.10 * J`, so 12% for a botched job and 2% for a perfect one.

Simulated return to players (400k jobs per row; `test_the_bank_keeps_its_edge_however_long_anyone_waits`
checks the same thing on every test run):

| Job score J | Edge | Return if everyone legs it at 1.2x | Return if everyone holds to the cap |
|---|---|---|---|
| 0.0 (all Dave) | 12% | 0.880 | 0.867-0.888 |
| 0.5 | 7% | 0.930 | 0.923-0.928 |
| 0.75 | 4.5% | 0.954 | 0.951-0.953 |
| 1.0 (perfect) | 2% | 0.980 | 0.975-0.986 |

The other house games sit at 2-7% (Mines and Penalty 2%, Plinko and Blockade 3%, Glass Bridge 4%, Chest 5%,
Darts 7%). A typical crew of real people, with a mix of good and bad rounds and a role or two left to Dave, should
land around J 0.5-0.7, so an edge of 5-7%. That is a casino game where playing well is worth something.

Even a page modified to cheat at every puzzle only reaches J = 1, which still leaves the bank 2%. Flooring payouts
to whole UKPence adds a little more. Because a smaller crew has more of its jobs done by Dave (each role a player
fills can lift J), a bigger crew does better.

### How much one job can cost the bank

The whole crew shares one police arrival, so a vault cleared at the cap pays everyone at once. The worst case is
`6 x buy-in x cap x 1.25` (the tip). `max_stake()` caps every buy-in so that this worst case is at most
`HEIST_EXPOSURE_PCT` (15%) of what the bank holds **above its reserve floor** (`RESERVE_FLOOR`, 80k):

| Bank reserves | Spare above floor | Post Office max | Building Society max | Vault max |
|---|---|---|---|---|
| 1,000,000 | 920k | 1,000 | 1,000 | 500 |
| 400,000 | 320k | 1,000 | 1,000 | 500 |
| 250,000 | 170k | 1,000 | 500 | 250 |
| 160,000 | 80k | 500 | 250 | 100 |
| 80,000 (the floor) | 0 | free only | free only | free only |

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
chance of a crew pulling it off is at most `(1 - e) / 1.5`, about 62% for an average crew.

What the bonus does to the first job of someone's day (everyone leaving together at 1.5x):

| Buy-in | Edge cost (average crew, e = 6%) | Bonus value (x 0.63) | Net | Same for a perfect crew (e = 2%) |
|---|---|---|---|---|
| 100 | -6 | +6.3 | about 0 | +4.5 |
| 500 | -30 | +31 | about +1 | +23 |
| 750 | -45 | +47 | about +2 | +34 |
| 1,000 | -60 | +47 | -13 | +27 |

So the first job of the day is roughly break-even for an ordinary crew and pays a little for a very good one. Every
job after that keeps the full edge. **At most about 35 UKP per player per day comes out of the bank** this way,
and only from crews who play properly. Twenty regular heisters a day would cost at most around 5k a week. That's
the same order as benefits (40-100 a day each for anyone under 400 UKP), and the throttle shrinks it further
whenever reserves are low. Every other job is a sink at 5-7%, like the rest of the casino, so across a week Bank Job
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

## Rolling out

`HEIST_LIVE = False` means only `HEIST_TESTERS` see the tile or can get in. There's no `/heist`, and the activity
posts nothing anywhere else. Set it to `True` to add `/heist` and the Together tile for everyone.

Every number above is in `config.py` (`HEIST_*`) or at the top of `heist.py`: the edge range, the targets, the
clocks, the heat and the bonus.
