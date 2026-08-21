import os
import sys

# --- Base Directory for Absolute Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Core Bot Settings ---
GUILD_ID = 959493056242008184
CHROME_PATH = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")

# --- Feature Toggles & Limits ---
SHUTCOIN_ENABLED = True
ROAST_DAILY_LIMIT = 4  # per-user /roast uses per UTC day (oggers exempt)
# Prediction view style (checked in priority order):
#   PREDICTION_CV2_ENABLED   - native Components V2 layout: one block per outcome
#                              with stats, a Bet button and a coloured proportion
#                              bar image. All native components (crisp on mobile).
#   PREDICTION_IMAGE_ENABLED - custom HTML→PNG card (templates/prediction_card.html).
#                              NOTE: it's an image Discord downscales, so it renders
#                              small/hard-to-read on mobile - kept for reference only.
#   (neither)                - the standard Discord embed (default).
# Where generated images are parked so ephemeral messages can link them instead of
# attaching them (see lib/core/image_host.py). The 'Rendered images' thread - somewhere
# nobody reads, since a puzzle posts a board here on every answer. 0 falls back to the
# bot-usage log.
IMAGE_HOST_CHANNEL_ID = 1534163732656033932

PREDICTION_CV2_ENABLED = True
PREDICTION_IMAGE_ENABLED = False

# --- Blackjack (vs-the-house) ---
#   BLACKJACK_ENABLED        - master toggle for the /blackjack command.
#   BLACKJACK_IMAGE_ENABLED  - render the premium HTML→PNG felt table each action
#                              (templates/blackjack_table.html). When off - or if a
#                              render raises - the game falls back to a native
#                              Components V2 text layout (mobile-crisp, zero render
#                              cost). Mirrors the PREDICTION_* dual-path design.
BLACKJACK_ENABLED = True
BLACKJACK_IMAGE_ENABLED = True
BLACKJACK_MIN_BET = 5
BLACKJACK_MAX_BET = 10_000

# --- Higher or Lower (vs-the-house card ladder) ---
# Each correct guess pays a fraction (HIGHERLOWER_PAYOUT_FACTOR) of the fair odds,
# so every step carries a small house edge; the more you ride, the more the house
# edges in - cash out to lock winnings. Same dual image/native render path as above.
HIGHERLOWER_ENABLED = True
HIGHERLOWER_IMAGE_ENABLED = True
HIGHERLOWER_MIN_BET = 5
HIGHERLOWER_MAX_BET = 10_000
HIGHERLOWER_PAYOUT_FACTOR = 0.98   # house edge per correct guess = 1 - this (2%/step, was 5%)
# A direction is only offered if winning it pays at least this much (a guess must
# always increase your banked value). Near-certain bets - e.g. 'lower' on an Ace -
# would otherwise round to <=1.0x ("win but lose money"), so they're disabled instead.
HIGHERLOWER_MIN_MULTIPLIER = 1.05

# --- Fruit Machine / Slots (vs-the-house) ---
SLOTS_ENABLED = True
SLOTS_IMAGE_ENABLED = True
SLOTS_MIN_BET = 1
SLOTS_MAX_BET = 10_000

# --- Video Poker / Jacks or Better (vs-the-house) ---
VIDEOPOKER_ENABLED = True
VIDEOPOKER_IMAGE_ENABLED = True
VIDEOPOKER_MIN_BET = 5
VIDEOPOKER_MAX_BET = 10_000

# --- Red Dog / In-Between (vs-the-house) ---
REDDOG_ENABLED = True
REDDOG_IMAGE_ENABLED = True
REDDOG_MIN_BET = 5
REDDOG_MAX_BET = 10_000

# --- Three Card Poker (vs-the-house) ---
TCP_ENABLED = True
TCP_IMAGE_ENABLED = True
TCP_MIN_BET = 5
TCP_MAX_BET = 10_000

# --- European Roulette (single-zero, vs-the-house) ---
ROULETTE_ENABLED = True
ROULETTE_IMAGE_ENABLED = True
ROULETTE_MIN_BET = 5       # informational (min chip is the smallest CHIP_SIZE)
ROULETTE_MAX_BET = 10_000  # max total stake across all bets on one spin

# Mines: reveal gems on a 5x5 grid, cash out before hitting a mine.
MINES_ENABLED = True
MINES_MIN_BET = 5
MINES_MAX_BET = 5_000
MINES_DEFAULT_MINES = 3       # default bomb count when /mines is called without one
MINES_HOUSE_EDGE = 0.02       # fraction of the stake the house keeps (EV-constant)
MINES_MAX_WIN = 0             # payout ceiling; 0 = no cap (a lucky board pays the full
                              # multiplier - if the bank can't cover it, credit_from_bank
                              # mints the win and logs CRITICAL; amend the supply after)

# Chest Upgrade - a linear "press your luck" ladder. Open the free Wood chest (1x), then
# choose, tier by tier, whether to risk it to upgrade; a failed upgrade shatters the chest
# and the whole stake is lost. Each upgrade carries a flat CHEST_HOUSE_EDGE, so the success
# odds are DERIVED from the multipliers below - every push is equally house-favoured, so there
# is no exploitable stopping point (the edge is the same wherever the player cashes out).
CHEST_ENABLED = True
CHEST_MIN_BET = 5
CHEST_MAX_BET = 1_000
CHEST_HOUSE_EDGE = 0.05       # flat edge per upgrade; odds are computed from this + multipliers
CHEST_MAX_WIN = 0             # payout ceiling; 0 = no cap (max bet already bounds the 8x payout)
# (name, emoji, cash-out multiplier). The first tier is the free starting chest at 1.0x;
# success odds to reach each later tier are derived from CHEST_HOUSE_EDGE, not stored.
CHEST_TIERS = [
    ("Wood", "🪵", 1.0),
    ("Silver", "🥈", 1.8),
    ("Gold", "🥇", 3.5),
    ("Diamond", "💎", 8.0),
]

# --- Darts (vs-the-house "darts blackjack") --------------------------------------
# Throw up to 3 darts; each lands on an AREA-WEIGHTED board region (singles common, trebles &
# doubles rare). Your score accumulates - Stand to bank a multiplier by how high you got, but go
# over DARTS_BUST and the throw busts (lose the stake). Optimal play (stand at 51+) leaves the
# house a ~7% edge; the maths live in scratch (optimal-stopping DP + sim).
DARTS_ENABLED = True
DARTS_MIN_BET = 5
DARTS_MAX_BET = 1_000
DARTS_DARTS = 3              # max darts per round
DARTS_BUST = 60             # a running total over this busts (lose the stake)
DARTS_MISS_PROB = 0.05      # chance a throw misses the board entirely (scores 0)
DARTS_MAX_WIN = 0           # payout ceiling; 0 = none (8x of the max bet already bounds it)
# (min_total, max_total, multiplier): standing with a total in a band pays that ×; below the
# first band's floor is a loss, over DARTS_BUST busts. Tuned to ~7% house edge under optimal play.
DARTS_PAYOUTS = [(34, 43, 1.0), (44, 51, 2.0), (52, 58, 4.0), (59, 60, 8.0)]

# --- Blockade Run (vs-the-house "crash") -----------------------------------------
# Your ship runs the enemy blockade. Each "Sail On" click pushes deeper and lifts the multiplier;
# Drop Anchor to bank stake×multiplier before a hidden, pre-rolled bust point sinks you (lose the
# stake). Player-paced (no timer), so edit latency never costs a click. The bust distribution
# bakes in a flat CRASH_HOUSE_EDGE, so every cash-out target carries the same edge - no exploitable
# strategy, only nerve. CRASH_MAX_MULT bounds the climb and the bank's tail exposure.
CRASH_ENABLED = True
CRASH_MIN_BET = 5
CRASH_MAX_BET = 1_000
CRASH_HOUSE_EDGE = 0.03      # baked into the bust distribution (edge is identical at any cash-out)
CRASH_MAX_MULT = 25.0        # auto-bank + bust ceiling (caps the climb and bank exposure)
CRASH_GROWTH = 1.15          # multiplier ×= this each Sail On (~2x in 5 pushes, 25x ceiling in ~23)

# Penalty Shootout (commands/economy/penalty.py)
PENALTY_ENABLED = True
PENALTY_MIN_BET = 5
PENALTY_MAX_BET = 1_000
PENALTY_HOUSE_EDGE = 0.02     # fraction of the stake the house keeps (EV-constant)
PENALTY_SCORE_PROB = 0.80     # P(score) per shot = an honest keeper saving a fair 1/5 of corners.
                              # Sets the ladder: 1.25x a goal, up to ~2.99x for a clean sheet.
PENALTY_MAX_WIN = 0           # payout ceiling; 0 = no cap (the ~2.99x top multiplier is already
                              # self-limiting, so a max bet can't dwarf the bank).

# --- Skyrim (standalone persistent RPG - NOT a casino game, no UKPence anywhere) ---
# Every member gets a persistent Dovahkiin: skills that level by use, gear tiers, dragon
# souls and shout words. /skyrim opens an ephemeral hub; delves (dungeon runs) post as
# public boards. Progress always banks - only the septims in the delve satchel are at
# stake. Septims are an internal currency with no UKPence bridge, so this sits entirely
# outside the economy (no bank flows, no casino channel gate, no /casino listing).
SKYRIM_ENABLED = True
# Stamina regenerates on a timer rather than resetting at midnight, so there's no
# use-it-or-lose-it rush before the reset and a day away comes back as a stockpile
# instead of nothing. 4h regen is 6 a day at a steady pace; the cap is what you can bank
# by not playing. The Legacy 'Long Stride' boon adds +1 to the CAP (it used to be
# +1/day, which no longer means anything under a regen model).
SKYRIM_DELVE_REGEN_HOURS = 4       # one delve returns this often
SKYRIM_DELVE_MAX_STORED = 5        # most you can have banked at once
SKYRIM_DELVES_PER_DAY = 3          # legacy: only used to convert old midnight-reset profiles
SKYRIM_DRAGON_MIN_LEVEL = 8        # dragon lairs appear as destinations from this level
SKYRIM_ALDUIN_MIN_LEVEL = 20       # Skuldafn (the Alduin fight) needs this level...
SKYRIM_ALDUIN_MIN_DRAGONS = 5      # ...plus all 3 shout words and this many dragons slain.
SKYRIM_ALDUIN_DRAGONS_PER_ECHO = 3  # each Alduin kill demands this many MORE dragons before a rematch
# The game log: every meaningful action (delves, kills of note, purchases, pit bouts,
# duels, marches, builds, retirements...) is posted as compact one-liners to this
# thread - a full audit trail of who is doing what. Reactive only: lines are queued
# by the engine and flushed when the triggering interaction is handled; nothing is
# ever posted on a schedule. Set to 0 to disable.
SKYRIM_LOG_THREAD_ID = 1529065067553882133
                                   # One attempt per UK day, offered as a picker button only
                                   # (nothing in Skyrim ever posts on its own).

# One-time UKPence reward paid (from the bank) when a badge is earned, by rarity. A tier
# set to 0 pays nothing. Used by the live grant hook and scripts/backfill_badge_rewards.py.
BADGE_REWARDS = {"Bronze": 25, "Silver": 100, "Gold": 500, "Secret": 1000}

# --- UKP earning rewards (all paid from the house bank; supply stays at 800k) ---
TREE_CHANNEL_ID = 1142970908059910204
GROW_A_TREE_BOT_ID = 972637072991068220
TREE_WATER_REWARD = 20            # UKP for the first few waters of the day, then it decays
TREE_WATER_FULL_COUNT = 3         # this many waters/day at full reward; after that -1 each
                                  # water (20,..,19,18,...) down to a floor of 1 UKP, reset
                                  # at midnight UK. The decay self-limits, so no hard cap.

BENEFITS_THRESHOLD = 400          # only claimable while balance is under this
BENEFITS_MIN = 40                 # random payout range (always pays when eligible)
BENEFITS_MAX = 100                # one claim per UK calendar day (resets at midnight)
BENEFITS_LOOKBACK_DAYS = 1        # /pay sent in this window counts toward "effective wealth"
BENEFITS_BAN_RAMP = [1, 3, 7, 14]   # benefits-fraud cooldown (days), ramps per offence

# Wealth demurrage: a weekly "use it or lose it" charge on the portion of a balance ABOVE the
# threshold, paid to the bank (supply conserved). Unlike the progressive income wealth-tax
# (which only hits taxable bank-funded earnings), this taxes the STOCK - so it can't be dodged
# by earning through untaxed channels (gambling/predictions). A soft cap on hoarding: a balance
# settles where weekly demurrage == weekly income, i.e. ~ THRESHOLD + income/RATE. Only the
# excess above the threshold is ever charged, so normal players never feel it.
WEALTH_DEMURRAGE_ENABLED = True
# 10,000 deliberately matches the point where chat and Stage rewards taper to zero, so the
# rule reads as one line: above 10k you stop earning passively and start funding everyone
# else. At 20k it only ever caught two accounts and reclaimed ~1.7k/week, which wasn't
# enough to fund the dividend pot below.
WEALTH_DEMURRAGE_THRESHOLD = 10000   # only balance above this is charged
WEALTH_DEMURRAGE_RATE = 0.05         # fraction of the excess taken per weekly run (5%)
INACTIVITY_TAX_RATE = 0.20           # fraction of total balance taken per weekly run (20%)
# Taxes apply, and itemised statements show net transaction amounts directly.
STATEMENT_HIDE_TAX = False

# Economy-dormant tax: the chat-activity inactivity tax above only catches people who stopped
# TALKING. This catches the other half - people who still chat (so they dodge that one) but
# never gamble, shop, /pay, bond or enter the lottery, and so accumulate from the faucets
# without ever returning anything. Charged ONLY on the excess above the floor, like demurrage,
# because the population is overwhelmingly small holders: a flat % would take a fifth of a
# casual chatter's 200 UKP to reach the handful of accounts that actually matter.
ECONOMY_DORMANT_TAX_ENABLED = True
ECONOMY_DORMANT_DAYS = 60            # no spending/risking activity for this long
ECONOMY_DORMANT_FLOOR = 100          # balance under this is exempt entirely
ECONOMY_DORMANT_RATE = 0.10          # fraction of the excess above the floor, per weekly run

# Anti-shuffle: all three taxes are charged on "effective wealth" = balance + UKP you've sent
# out − UKP you've been sent, over this window. Moving money onto an alt/friend (or splitting a
# hoard) therefore doesn't lower your tax base, and the recipient isn't double-charged for funds
# just passing through. Demurrage additionally bases the charge on your PEAK balance over the
# window, so dipping below the threshold right before the weekly snapshot doesn't help either.
TRANSFER_LOOKBACK_DAYS = 7
# Daily anti-shuffle cap: the most UKP one person can /pay to OTHER MEMBERS per UK day
# (resets at midnight). Pays to the bank are exempt - that's money leaving circulation, not
# shuffling. Caps how fast a hoard can be relocated across accounts to dodge demurrage.
DAILY_PAY_CAP = 10000
# House rake on player-vs-player pots (Connect 4, Battleship, wagers): the winner is paid the
# pot minus this %, which stays in the bank. Applied silently, only to genuine wins (never
# draws/refunds/vs-AI). Stops "stake and lose on purpose" being a free, uncapped, untaxed way
# to move UKP around the /pay cap and the wealth taxes - a thrown game now costs more than the
# demurrage it dodges.
PVP_RAKE_RATE = 0.05

# --- Bank reserve policy -------------------------------------------------------------
# The bank is a float, not an infinite faucet: every passive reward is paid out of it and
# the taxes above are what refill it. When reserves fall, DISCRETIONARY payouts (rewards
# the server chooses to give) scale down so the tax flow can catch up. Obligations - casino
# wins, refunds, bond maturities, anything already promised - are never scaled; you cannot
# shrink a bet someone has already won.
RESERVE_POLICY_ENABLED = True
# A FLAT floor, deliberately not a % of live supply: if the mint ever fires, supply rises and
# a percentage floor would rise with it, tightening the throttle exactly when the bank has
# just been bailed out. 80,000 is 10% of the 800k baseline.
RESERVE_FLOOR = 80_000
# (reserves_at_or_below, multiplier applied to discretionary rewards). First match wins.
RESERVE_THROTTLE_TIERS = ((80_000, 0.25), (120_000, 0.50), (160_000, 0.75))
# Hard ceiling on total UKP in existence. The emergency mint (casino insolvency) may only
# ever raise supply to this; past it a win cannot be honoured and is logged CRITICAL rather
# than silently inflating the currency. 800k baseline -> at most 25% expansion, ever.
MAX_TOTAL_SUPPLY = 1_000_000

# Dynamic Max Bet scaling: cap single-win max exposure to a % of live Bank reserves
DYNAMIC_MAX_BET_ENABLED = True
MAX_EXPOSURE_PCT = 0.80        # max single-win payout capped at 80% of Bank liquid balance
DYNAMIC_MIN_MAX_BET = 500      # floor for dynamic max bet so table is always playable

# --- Demurrage dividend ---------------------------------------------------------------
# Reclaimed hoard money is earmarked to pay for chat activity rewards, turning a wealth tax
# into a visible community payout. The pot is an EARMARK on the bank's existing balance, not
# a second wallet - no UKP is created, the supply invariant is untouched, and the counter just
# records how much of the bank is spoken for. Empty pot = no chat rewards until it refills.
DEMURRAGE_DIVIDEND_ENABLED = True
DEMURRAGE_DIVIDEND_PCT = 0.50     # share of each demurrage run diverted into the pot
# Pot level considered "healthy" - at or above this, chat rewards pay at their full rate; the
# frequency scales down linearly below it. Roughly one week of chat-reward spend.
DEMURRAGE_DIVIDEND_FULL_POT = 2_500
DEMURRAGE_DIVIDEND_MIN_RATE = 0.25   # floor on the scaling while the pot still has anything


HOF_REWARD = 100                  # UKP DM'd to a message's author on Hall of Fame entry
TICKET_REWARD = 100               # UKP a staff member can grant a ticket's opener
DISBOARD_BOT_ID = 302050872383242240   # DISBOARD: the /bump bot
BUMP_REWARD = 50                  # UKP for the member who bumps the server on DISBOARD (max once per ~2h, DISBOARD's own cooldown)
# Welcoming pays for a reply, not for the word "welcome". Greeting a newcomer books a
# claim; the claim only pays once that newcomer answers you. Measured over three months
# of #general, 87.9% of welcomes were never followed by another word to that person, and
# the biggest welcomers had the worst follow-up rates - the old rule paid the greeting,
# so the cheapest way to earn was to type one word and move on.
WELCOME_REWARD = 10               # UKP for welcoming a new member - paid on the greeting
WELCOME_WINDOW_MINUTES = 15       # how long after a join a welcome still counts
WELCOME_MAX_WELCOMERS = 5         # only the first N welcomers per newcomer are paid
WELCOME_FOLLOWUP_REWARD = 15      # UKP for going back to that newcomer later
WELCOME_FOLLOWUP_MIN_HOURS = 1    # a follow-up has to be a separate occasion, not the same conversation
WELCOME_FOLLOWUP_WINDOW_HOURS = 48  # ...and still close enough to their join to matter

# Greeting pays straight away for almost everyone. Measured over three months, though,
# 87.9% of welcomes were never followed by another word to that person, and the heaviest
# welcomers had the worst follow-up rates - one greeted 155 newcomers and spoke again to
# 3% of them. So the payout is earned back rather than removed: go this many welcomes in
# a row without the newcomer ever replying or you ever going back to them, and your
# greeting stops paying up front and starts paying when they answer instead. Get a couple
# of real conversations and it goes back to paying immediately.
WELCOME_DRY_STREAK_LIMIT = 3      # consecutive welcomes that were answered and then dropped
                                  # before it tightens. Lower than it was, because a dry mark
                                  # now needs the greeter to have ignored a real reply.
WELCOME_REDEMPTION_ENGAGEMENTS = 2  # real interactions needed to get instant pay back
WELCOME_REPLY_WINDOW_MINUTES = 60 # while tightened, how long the greeter has to answer them back
# Off. Counting any message in the same channel as a response was tried and it defeats the
# point: in a busy channel a regular posts something within minutes whatever they are doing,
# so nobody would ever be marked. Answering means replying to them or naming them. Set a
# number of minutes here if that turns out to mark people who did reply, just not by
# pressing reply.
WELCOME_CONTINUE_WINDOW_MINUTES = 0
WELCOME_HISTORY_KEPT = 12         # rolling welcome outcomes remembered per member
# Channels whose messages can't enter the Hall of Fame (announcements etc. always get a
# lot of reactions but aren't organic HoF-worthy posts). Bot/webhook and Discord
# announcement-type channels are also excluded automatically in the HoF check.
HOF_EXCLUDED_CHANNELS = {959503403199905862, 1133386861033832448, 1279873633602244668}

# --- Bonds (fixed-term Treasury savings; interest paid from the bank) ---
BOND_ENABLED = True
BOND_TERMS = {3: 2, 7: 6, 30: 30}   # term in days -> interest percent
BOND_MAX = 5000                      # max principal per bond (one active bond per user)
BOND_EARLY_PENALTY_PCT = 10          # early exit: forfeit interest + lose this % of principal
BOND_FUNNEL_LOOKBACK_DAYS = 3        # UKP /pay-received in this window can't be bonded
                                     # (stops a whale funnelling 5k to alts to invest past the cap)

# --- National Lottery (shared pooled draw) ---
# Each round picks a RANDOM ticket price and ticket cap from the ranges below (a little
# mystery each week). A round draws when it sells out OR at the weekly time, whichever
# comes first - but never sooner t  n LOTTERY_MIN_RUNTIME_MIN after opening (so a cheap
# small round can't sell out and vanish in minutes), and a sold-out round won't reopen
# until the next weekly tick. Winner takes the whole pot (LOTTERY_RAKE_PCT bank cut, 0 by default).
LOTTERY_ENABLED = True
LOTTERY_IMAGE_ENABLED = True
LOTTERY_TICKET_PRICE_MIN = 2      # random ticket price per round, inclusive
LOTTERY_TICKET_PRICE_MAX = 20
LOTTERY_TICKET_CAP_MIN = 300      # random ticket cap (sellout threshold) per round
LOTTERY_TICKET_CAP_MAX = 1000
LOTTERY_MIN_RUNTIME_MIN = 30      # a round can't draw on sellout sooner than this (minutes)
LOTTERY_RAKE_PCT = 0              # house bank keeps this %; 0 = winner takes the whole pot
LOTTERY_DRAW_DOW = "sun"          # weekly draw day (APScheduler day_of_week)
LOTTERY_DRAW_HOUR = 20            # 8pm UK
LOTTERY_DRAW_MINUTE = 0
# Random "feeling lucky?" reminders in the casino channel, linking to the live board.
LOTTERY_REMINDER_START_HOUR = 10  # only remind during active UK hours
LOTTERY_REMINDER_END_HOUR = 23
LOTTERY_REMINDER_MIN_GAP_MIN = 45   # min minutes between reminders
LOTTERY_REMINDER_MAX_GAP_MIN = 120  # max (2h); also suppressed if a reminder is already in recent msgs
LOTTERY_REMINDER_RECENT_LOOKBACK = 10  # skip if a reminder is within this many of the channel's last msgs
# Fallback defaults (only used if a range is missing); live rounds use the ranges above.
LOTTERY_TICKET_PRICE = 10
LOTTERY_TICKET_CAP = 500

# --- File Paths & Directories ---
DATA_DIR = os.path.join(BASE_DIR, "data")
JSON_DATA_DIR = os.path.join(DATA_DIR, "json")

# Ensure directories exist
os.makedirs(JSON_DATA_DIR, exist_ok=True)
os.makedirs("daily_summaries", exist_ok=True)
os.makedirs("balance_snapshots", exist_ok=True)

XP_FILE = os.path.join(JSON_DATA_DIR, "chat_leaderboard.json")
ECONOMY_METRICS_FILE = os.path.join(JSON_DATA_DIR, "economy_metrics.json")
TREE_WATER_FILE = os.path.join(JSON_DATA_DIR, "tree_water_rewards.json")
BUMP_REWARD_FILE = os.path.join(JSON_DATA_DIR, "bump_rewards.json")
WELCOME_REPUTATION_FILE = os.path.join(JSON_DATA_DIR, "welcome_reputation.json")  # per-member welcome outcomes
WELCOME_TRACKING_FILE = os.path.join(JSON_DATA_DIR, "welcome_tracking.json")  # pending newcomers + who's already been paid for welcoming them
GAME_PINNACLE_FILE = os.path.join(JSON_DATA_DIR, "game_pinnacles.json")  # per-user top-tier wins in the new games (for a secret cross-game badge)
BENEFITS_FILE = os.path.join(JSON_DATA_DIR, "benefits_claims.json")
HOF_REWARD_CLAIMS_FILE = os.path.join(JSON_DATA_DIR, "hof_reward_claims.json")  # last UK day each user earned HoF UKP
EARNED_SOURCES_FILE = os.path.join(JSON_DATA_DIR, "earned_sources.json")
WORDLE_STATE_FILE = os.path.join(JSON_DATA_DIR, "wordle_state.json")
CROSSWORD_STATE_FILE = os.path.join(JSON_DATA_DIR, "crossword_state.json")
SKYRIM_PROFILES_FILE = os.path.join(JSON_DATA_DIR, "skyrim_profiles.json")  # persistent Dovahkiin characters
SKYRIM_DAILY_FILE = os.path.join(JSON_DATA_DIR, "skyrim_daily.json")  # today's shared-dungeon results board
SKYRIM_GRAVEYARD_FILE = os.path.join(JSON_DATA_DIR, "skyrim_graveyard.json")  # fallen adventurers (corpses + obituary)
SKYRIM_WORLDBOSS_FILE = os.path.join(JSON_DATA_DIR, "skyrim_worldboss.json")  # the week's shared hunt (pooled HP + strikes)

# --- County Balls (BallsDex-style collectibles: county balls spawn in chat, first
# correct guess catches; duplicates sell to the bank for UKPence) ---
COUNTY_ENABLED = True
COUNTY_SPAWN_CHANNELS = [1141037835445616640]  # bot-workshop while testing
COUNTY_SPAWN_CATEGORY = 0          # go-live: 959544877325115424 (General category); 0 = off
COUNTY_SPAWN_RANGE = (15, 30)      # qualifying messages per spawn; go-live: (180, 320)
                                   # (calibrated from daily summaries: ~3.9k msgs/day in
                                   # #general -> roughly 5 spawns quiet days, 11 typical, 20 busy)
COUNTY_SPAWN_MIN_GAP = 60          # seconds between spawns; go-live: 600
COUNTY_HINT_AFTER = 3              # wrong guesses before the spawn message gains a hint
COUNTY_SPAWN_WEIGHTS = {"common": 32, "uncommon": 14, "rare": 5, "epic": 3, "legendary": 2}
COUNTY_SELL_PRICES = {"common": 20, "uncommon": 60, "rare": 300, "epic": 750, "legendary": 2000}
COUNTY_STATE_FILE = os.path.join(JSON_DATA_DIR, "county_state.json")  # spawn counter + active spawn
COUNTY_ASSET_DIR = os.path.join("data", "counties")
COUNTY_ALLOWED_ROLE_IDS = []                    # roles allowed while gated (e.g. Deputy PM
                                                # 960538130761527386); [] = no role grants access
COUNTY_ALLOWED_USER_IDS = [404634271861571584]  # users allowed while gated (oggers). Gate is
                                                # active while either list is non-empty; empty
                                                # both to open the feature to everyone
COUNTY_DEX_IMAGE_ENABLED = True    # /county-dex renders the full-roster graphic; False = text embed
COUNTY_DEBUG_ANNOUNCE = True       # testing: after a spawn, post the answer (spoilered) in a
                                   # follow-up message - MUST be False for go-live
COUNTY_STAT_BONUS_RANGE = 20       # each caught instance rolls +/- this % on Clout and Grit
# Fletcher bot posts the same message-link summary HMS does; auto-delete its duplicate.
FLETCHER_DEDUPE_ENABLED = True
FLETCHER_BOT_ID = None            # set to Fletcher's user id for an exact match (optional)
FLETCHER_BOT_NAMES = ["fletcher"]  # otherwise matched by bot name
# HMS Wordle: one shared 5-letter word per UK day, dictionary-validated guesses.
# Same picture-via-CDN path as CROSSWORD_IMAGE_ENABLED.
WORDLE_IMAGE_ENABLED = True
WORDLE_ANSWERS_FILE = os.path.join("data", "words", "answers.txt")
WORDLE_VALID_FILE = os.path.join("data", "words", "valid.txt")
WORDLE_REWARDS = [200, 140, 100, 70, 45, 25]  # payout by number of guesses to solve (1..6)
# HMS Crossword: one shared mini per UK day, answered clue by clue. Grid size, payout
# tiers and difficulty rules all live per puzzle-SET inside the file below and are gated
# by date, so tightening them never changes a puzzle someone is midway through. The tiers
# here are only the fallback for a set that doesn't state its own.
# Board style. Discord is slow to load image ATTACHMENTS on ephemeral messages, so the
# board is posted once to IMAGE_HOST_CHANNEL_ID and shown as an embed pointing at the
# resulting CDN URL - same picture, but it arrives with the message instead of sitting on
# a placeholder. If hosting fails it falls back to a plain attachment, and if the render
# itself fails, to the native text board.
CROSSWORD_IMAGE_ENABLED = True
CROSSWORD_PUZZLES_FILE = os.path.join("data", "words", "crosswords.json")
CROSSWORD_REWARDS = [250, 200, 150, 100, 50]  # fallback tiers, by penalties incurred
# Texas Hold'em (player-vs-player; bank is escrow, no rake)
POKER_SMALL_BLIND = 5
POKER_BIG_BLIND = 10
POKER_MIN_BUYIN = 200
POKER_MAX_BUYIN = 2000
POKER_MAX_SEATS = 6
POKER_TURN_SECONDS = 45            # auto check/fold if a player stalls
POKER_IDLE_CLOSE_SECONDS = 180     # close a table after this long with no human action
POKER_ESCROW_FILE = os.path.join(JSON_DATA_DIR, "poker_escrow.json")
# Connect 4 (1v1 PvP wager; both stake the same, winner takes the whole pot, no rake)
# --- Rock Paper Scissors (1v1 wager) ---
RPS_ENABLED = True
RPS_MIN_BET = 5
# Lower than Connect 4's 5,000 on purpose: a match is over in seconds, so the same ceiling
# would move money far faster than a board game can.
RPS_MAX_BET = 2000
RPS_ACCEPT_SECONDS = 300           # opponent has this long to accept the challenge
RPS_ROUND_SECONDS = 120            # 2 min to pick each round, or you forfeit the pot
RPS_WINS_NEEDED = 2                # best of three

CONNECT4_ENABLED = True
CONNECT4_MIN_BET = 5
CONNECT4_MAX_BET = 5000
CONNECT4_ACCEPT_SECONDS = 300      # opponent has this long to accept the challenge
CONNECT4_FORFEIT_SECONDS = 120     # PvP: 2 min to make each move, or you forfeit the pot
CONNECT4_AI_FORFEIT_SECONDS = 120  # vs-AI: 2 min to make each move, or you forfeit the pot
CONNECT4_AI_MOVE_TIMEOUT = 60      # vs-AI: if the AI doesn't move within this (a hang), the player WINS the pot by forfeit
CONNECT4_AI_STARTS_CHANCE = 0.70   # vs-AI: probability the AI opens (it's perfect -> unbeatable when it starts; the rest let the human open so the bounty stays winnable)
CONNECT4_AI_DAILY_WIN_CAP = 4000   # vs-AI: max NET profit per user per UK day (anti-farm); over it a win just returns the stake, losses count against it
CONNECT4_AI_DEPTH = 22             # vs-AI (bank-funded): max negamax depth (deep-search cap)
CONNECT4_AI_TIME = 5.0             # vs-AI: per-move think budget in seconds (iterative deepening)

BATTLESHIP_ENABLED = True
BATTLESHIP_MIN_BET = 5
BATTLESHIP_MAX_BET = 5000
BATTLESHIP_ACCEPT_SECONDS = 300    # opponent has this long to accept the challenge
BATTLESHIP_FORFEIT_SECONDS = 120   # 2 min to set up / make each move, or you forfeit the pot
# "Analyse User" moderation tool (Gemini). Reads GEMINI_API_KEY from the environment.
GEMINI_MODEL = "gemini-2.5-flash"
# Join-watch raid screening models (image-capable). OpenAI is primary (reads
# OPENAI_TOKEN); the Gemini model is the automatic fallback if that call fails.
JOIN_WATCH_OPENAI_MODEL = "gpt-5.4-mini"
JOIN_WATCH_GEMINI_MODEL = "gemini-3.5-flash"
RULES_CHANNEL_ID = None            # set to your rules channel id for accurate analysis (else data/rules.txt / generic)
USER_ANALYSIS_MSG_LIMIT = 1000     # hard cap on messages gathered (only heavy chatters reach it)
USER_ANALYSIS_MIN_MSGS = 150       # collect at least this many before stopping a non-heavy chatter early
USER_ANALYSIS_DAYS = 14            # only look at messages from the last this-many days
USER_ANALYSIS_READ_BUDGET = 18000  # hard cap on total messages read (bounds scan time for low-posters)
# Cache the scraped messages per member so follow-up questions skip re-scraping Discord.
USER_ANALYSIS_CONTEXT_FILE = os.path.join(JSON_DATA_DIR, "user_analysis_context.json")
# USER_ANALYSIS_CHANNEL_ID is set below, once CHANNELS is defined.
HALL_OF_FAME_FILE = os.path.join(JSON_DATA_DIR, "hall_of_fame.json")
PREDICTIONS_FILE = os.path.join(JSON_DATA_DIR, "predictions.json")
PREDICTION_STREAKS_FILE = os.path.join(JSON_DATA_DIR, "prediction_streaks.json")
ROAST_TARGETS_FILE = os.path.join(JSON_DATA_DIR, "roast_targets.json")
WARDEN_TARGETS_FILE = os.path.join(JSON_DATA_DIR, "warden_targets.json")
SHUT_COUNTS_FILE = os.path.join(JSON_DATA_DIR, "shut_counts.json")
MORNING_PERSON_COUNTS_FILE = os.path.join(JSON_DATA_DIR, "morning_person_counts.json")
NIGHT_OWL_COUNTS_FILE = os.path.join(JSON_DATA_DIR, "night_owl_counts.json")
PARTY_ANIMAL_TARGETS_FILE = os.path.join(JSON_DATA_DIR, "party_animal_targets.json")
PERSISTENT_VIEWS_FILE = os.path.join(JSON_DATA_DIR, "persistent_views.json")
WEBHOOK_DELETIONS_FILE = os.path.join(JSON_DATA_DIR, "webhook_deletions.json")
WHITELIST_FILE = os.path.join(JSON_DATA_DIR, "whitelist.json")
PAY_LOG_FILE = os.path.join(JSON_DATA_DIR, "pay_log.json")
PERMISSIONS_BACKUP_FILE = os.path.join(JSON_DATA_DIR, "role_permissions_backup.json")
THREAD_MESSAGES_FILE = os.path.join(JSON_DATA_DIR, "thread_messages.json")
ADDED_USERS_FILE = os.path.join(JSON_DATA_DIR, "added_users.json")
ROAST_TARGET_FILE = os.path.join(JSON_DATA_DIR, "roast_targets.json")
RECIPE_BOOK_FILE = os.path.join(JSON_DATA_DIR, "recipe_book.json")
WEEKEND_WARRIOR_COUNTS_FILE = os.path.join(JSON_DATA_DIR, "weekend_warrior_counts.json")
# Toggle (oggers' "piggyreact"): when on, every message from PIGGY gets H-O-G reactions.
PIGGY_REACT_FILE = os.path.join(JSON_DATA_DIR, "piggy_react.json")
JOIN_WATCH_FILE = os.path.join(JSON_DATA_DIR, "join_watch.json")  # oggers' AI raid screening toggle + incident context
# Who is mid-scan, so a deploy does not quietly stop screening everyone who joined
# before it. Kept separate from the toggle file because it churns on every screened
# message while the toggle changes about twice a week.
JOIN_WATCH_BUFFERS_FILE = os.path.join(JSON_DATA_DIR, "join_watch_buffers.json")
JOIN_WATCH_MAX_WATCH_HOURS = 168  # stop watching a joiner after 7d even if they never hit the message cap
TOWN_CRIER_TRACKING_FILE = os.path.join(JSON_DATA_DIR, "town_crier_tracking.json")
BALANCE_SNAPSHOT_DIR = "balance_snapshots"
VC_LOCKDOWN_FILE = os.path.join(JSON_DATA_DIR, "vc_lockdown_status.txt")
OVERNIGHT_MUTE_FILE = os.path.join(JSON_DATA_DIR, "overnight_mute.txt")

# --- Whitelists ---
POLITICS_WHITELISTED_USER_IDS = []

# --- Role IDs ---
class ROLES:
    # Staff & Permissions
    DEPUTY_PM = 960538130761527386
    # NOTE: MINISTER and CABINET intentionally share the same role ID today, so any
    # command gated on one is effectively gated on both. If CABINET is ever given
    # its own ID, audit every `checks=[...]` / role-id list that references either,
    # because access on those commands will silently change.
    MINISTER = 1250190944502943755
    CABINET = 1250190944502943755
    BORDER_FORCE = 959500686746345542
    DEPUTY_MINISTER_OF_COMMUNITY = 1142859374088437952
    PCSO = 1132949441389797397
    VOICE_CHAT_WARDEN = 1334593677686870166
    EMBED_PERMS = 1339021325032751215
    
    # Functional Roles
    SERVER_BOOSTER = 959650957325635707
    VC_BAN = 1394034697738260500
    BALL_INSPECTOR = 1197712388493934692
    DONT_DM_WHEN_MESSAGE_BLOCKED = 1345805710000128000
    MEMBER = 1142491622563643442
    VIDEO_BAN = 1405300412352954388
    VIP = 1417558416637034658
    PRED_NOTIFICATIONS = 1478709859422572595

    # Chat Rank Roles
    ROYAL_DUKE = 1483496936232190094
    ARCHDUKE = 1483496922714083479
    GRAND_DUKE = 1483496909287985345
    LORD_HIGH_STEWARD = 1483496897774747790
    LORD_HIGH_CHANCELLOR = 1483496882587046091
    VICEROY = 1483496784448716841
    DUKE = 1226304695086219345
    MARQUESS = 1226304808257065155
    EARL = 1226304907750150264
    VISCOUNT = 1226309228315017226
    BARON = 1226312266430021674
    KNIGHT = 1226312237766021216
    LORD = 1226312094807232522
    ESQUIRE = 1226312063941607525
    GENTLEMAN = 1195060260956807280
    YEOMAN = 1226311471269675018
    COMMONER = 1195060173346177065
    FREEMAN = 1226311235537080340
    PEASANT = 1228860092200386571
    SERF = 1226311204281122926

    # Onboarding Roles
    BRITISH = 1220038224051830834
    ENGLISH = 1220038335226052697
    SCOTTISH = 1220038357204074607
    WELSH = 1220038385058582568
    NORTHERN_IRISH = 1220038413101568171
    COMMONWEALTH = 1295105020928462949
    VISITOR = 1132285964094558288

# --- Channel & Category IDs ---
class CHANNELS:
    GENERAL = 959493057076666380
    COMMONS = 959501347571531776
    BOT_SPAM = 968502541107228734
    GAMING = 1139977009389387959
    CASINO = 1512095841517699213          # casino games + lottery board live here
    VIP_LOUNGE = 1333482774157590609      # vip lounge channel; casino commands allowed here
    BOT_WORKSHOP = 1141037835445616640    # casino commands allowed here too
    POLITICS = 1141097424849481799
    LOGS = 959723562892144690
    POLICE_STATION = 1132970233502650388
    BOT_USAGE_LOG = 1197572903294730270
    CABINET = 1155637791917613147
    COMMUNITY_MANAGEMENT = 1144634236352069672
    
    # For secret server
    MEMBER_UPDATES = 1279873633602244668
    DATA_BACKUP = 1281734214756335757
    IMAGE_CACHE = 1271188365244497971
    ANNOUNCEMENTS = 959503403199905862
    MINOR_ANNOUNCEMENTS = 1133386861033832448
    HALL_OF_FAME_THREAD = 1479149572591845376
    ECONOMY_LOG_THREAD = 1527262792095367258  # 'economy log' thread under bot-usage-log: 💰 Economy Activity digests
    PASSIVE_CHAT_REWARDS_THREAD = 1488926630767366416  # 'passive chat rewards logs' thread: 📈 Chat Activity Rewards digests
    DAILY_SUMMARY_THREAD = 1511784346451710113  # daily server summaries post here (weekly/monthly stay in COMMONS)
    VOTING = 959848236384919692
    VOICE_LOG_THREAD = 1493403784074760362
    VOICE_ACTIVITY_THREAD = 1539312670761812019  # 'voice chat activity' thread under logs: 🎧 join/leave/move digests

# Casino games + the lottery may only be used in these channels.
CASINO_CHANNELS = [CHANNELS.CASINO, CHANNELS.VIP_LOUNGE, CHANNELS.BOT_SPAM, CHANNELS.BOT_WORKSHOP]
# Where /Analyse User reports post.
USER_ANALYSIS_CHANNEL_ID = CHANNELS.POLICE_STATION
# Only these channels are scanned for the member's messages (the main chats), in parallel.
USER_ANALYSIS_CHANNELS = [CHANNELS.VIP_LOUNGE, CHANNELS.GENERAL, CHANNELS.POLITICS]
# The lottery board + winner announcements post here.
LOTTERY_CHANNEL = CHANNELS.CASINO

class CATEGORIES:
    TICKETS = 1139976595336069161

# --- User IDs ---
class USERS:
    OGGERS = 404634271861571584
    COUNTRYBALL_BOT = 999736048596816014
    CHIN = 795003706717372462
    CHERRY_BLOSSOM = 725155180680577066
    HADIDAS = 198144909583056898
    WICK_BOT = 536991182035746816
    LANCA = 1398652914737741956
    HMS_VICTORY = 1171842947440967770
    PIGGY = 447010711936303115

BOT_ID = USERS.HMS_VICTORY

# ---------------------------------------------------------------------------
# Anti-alt / anti-farm / anti-laundering detection (lib/core/detection.py)
#
# Every threshold is here rather than in the detectors, because these are the numbers that
# actually need tuning once real traffic hits them - and the cost of getting one wrong is
# asymmetric. Too tight floods the review channel until nobody reads it, which is the same
# as having no detection; too loose is quietly useless. Start conservative and tighten
# against observed false positives.
# ---------------------------------------------------------------------------
DETECTION_ENABLED = True
DETECTION_ALERT_CHANNEL = CHANNELS.BOT_WORKSHOP
DETECTION_ALERT_PING = USERS.OGGERS
DETECTION_ALERT_COOLDOWN = 6 * 3600                # per user per kind, in seconds

# Module A - Wordle. The floor is reading time, not typing time: the grid has to be read
# before a guess can be meaningful, and a correct first guess inside a few seconds means the
# answer arrived from outside the game.
WORDLE_MIN_SOLVE_SECONDS = 3.5
WORDLE_ONE_GUESS_WINDOW_DAYS = 14
WORDLE_ONE_GUESS_MAX = 2                           # first-try solves allowed in that window
WORDLE_DUMMY_GUESS_SECONDS = 4.0                   # gap between a throwaway and the answer

# Module B - Crossword. Twelve clues cannot be read, understood and typed inside a minute,
# so a clean sub-minute grid is the signal; hints or wrong answers exempt it, since a real
# solve that fast would have neither.
CROSSWORD_MIN_SOLVE_SECONDS = 60
CROSSWORD_SEQUENCE_MATCH_WINDOW = 30 * 60          # two solvers this close, same order
CROSSWORD_SEQUENCE_MIN_ENTRIES = 8                 # shorter orders coincide by chance

# Module C - lockstep daily activity. One pairing is coincidence; the rule is repetition
# across separate days, so a shared household or a habit at the same hour does not qualify.
ALT_CO_OCCURRENCE_SECONDS = 120
ALT_CO_OCCURRENCE_MIN_DAYS = 3
ALT_CO_OCCURRENCE_WINDOW_DAYS = 14

# Module D - wager washing. Losing is normal; losing almost everything to the same person
# repeatedly is a transfer wearing a game as a disguise.
WASH_MIN_GAMES = 5
WASH_LOSS_RATIO = 0.8
WASH_WINDOW_DAYS = 14
RECYCLE_SECONDS = 300                              # received, then moved straight back out
RECYCLE_MIN_FRACTION = 0.7

# Module E - funnelling. Each transfer can sit under the audit cap and the pattern still be
# obvious once you count senders rather than amounts.
FUNNEL_WINDOW_HOURS = 72
FUNNEL_MIN_SENDERS = 3
FUNNEL_NEW_ACCOUNT_DAYS = 30                       # what counts as low-tenure

# Module F - onboarding. Each question has one honest answer, so a selfbot that ticks every
# box contradicts itself. Two home nations is a real answer for anyone with mixed parents,
# which is why it only counts when it was also instant; three is nobody's honest answer.
ONBOARDING_NATIONALITY_FLAG_AT = 3
ONBOARDING_STATUS_FLAG_AT = 2                      # British / Commonwealth / Visitor clash
ONBOARDING_INSTANT_SECONDS = 5                     # onboarding is several screens to read
ONBOARDING_FLAG_TTL_HOURS = 6                      # one alert per member per window


# --- Mute Notifications ---
# Users DM'd whenever a member is muted (shut, bedtime, native timeout, Wick, etc.)
MUTE_NOTIFY_USER_IDS = [USERS.OGGERS, USERS.HADIDAS]

# --- Automated Moderation ---
HATE_SPEECH_TIMEOUT_MINUTES = 24 * 60

# --- Voice Channel Lockdown ---
VC_LOCKDOWN_WHITELIST = [
    ROLES.ROYAL_DUKE, ROLES.ARCHDUKE, ROLES.GRAND_DUKE, ROLES.LORD_HIGH_STEWARD,
    ROLES.LORD_HIGH_CHANCELLOR, ROLES.VICEROY,
    ROLES.DUKE, ROLES.MARQUESS, ROLES.EARL, ROLES.VISCOUNT, ROLES.BARON,
    ROLES.KNIGHT, ROLES.LORD, ROLES.ESQUIRE, ROLES.GENTLEMAN, ROLES.YEOMAN,
    ROLES.COMMONER, ROLES.FREEMAN, ROLES.PEASANT, ROLES.SERF,
]
