import os
import sys

# --- Base Directory for Absolute Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Core Bot Settings ---
GUILD_ID = 959493056242008184
CHROME_PATH = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")

# --- Feature Toggles & Limits ---
SHUTCOIN_ENABLED = True
SUMMARISE_DAILY_LIMIT = 10
# Prediction view style (checked in priority order):
#   PREDICTION_CV2_ENABLED   - native Components V2 layout: one block per outcome
#                              with stats, a Bet button and a coloured proportion
#                              bar image. All native components (crisp on mobile).
#   PREDICTION_IMAGE_ENABLED - custom HTML→PNG card (templates/prediction_card.html).
#                              NOTE: it's an image Discord downscales, so it renders
#                              small/hard-to-read on mobile - kept for reference only.
#   (neither)                - the standard Discord embed (default).
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
MINES_MAX_WIN = 100_000       # hard payout ceiling so a lucky board can't drain the bank

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

HOF_REWARD = 100                  # UKP DM'd to a message's author on Hall of Fame entry
TICKET_REWARD = 100               # UKP a staff member can grant a ticket's opener
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
BENEFITS_FILE = os.path.join(JSON_DATA_DIR, "benefits_claims.json")
EARNED_SOURCES_FILE = os.path.join(JSON_DATA_DIR, "earned_sources.json")
WORDLE_STATE_FILE = os.path.join(JSON_DATA_DIR, "wordle_state.json")
# Fletcher bot posts the same message-link summary HMS does; auto-delete its duplicate.
FLETCHER_DEDUPE_ENABLED = True
FLETCHER_BOT_ID = None            # set to Fletcher's user id for an exact match (optional)
FLETCHER_BOT_NAMES = ["fletcher"]  # otherwise matched by bot name
# HMS Wordle: one shared 5-letter word per UK day, dictionary-validated guesses.
WORDLE_ANSWERS_FILE = os.path.join("data", "words", "answers.txt")
WORDLE_VALID_FILE = os.path.join("data", "words", "valid.txt")
WORDLE_REWARDS = [200, 140, 100, 70, 45, 25]  # payout by number of guesses to solve (1..6)
# Texas Hold'em (player-vs-player; bank is escrow, no rake)
POKER_SMALL_BLIND = 5
POKER_BIG_BLIND = 10
POKER_MIN_BUYIN = 200
POKER_MAX_BUYIN = 2000
POKER_MAX_SEATS = 6
POKER_TURN_SECONDS = 45            # auto check/fold if a player stalls
POKER_ESCROW_FILE = os.path.join(JSON_DATA_DIR, "poker_escrow.json")
# "Analyse User" moderation tool (Gemini). Reads GEMINI_API_KEY from the environment.
GEMINI_MODEL = "gemini-2.5-flash"
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
    PCSO = 1132949441389797397
    VOICE_CHAT_WARDEN = 1334593677686870166
    EMBED_PERMS = 1339021325032751215
    
    # Functional Roles
    SERVER_BOOSTER = 959650957325635707
    POLITICS_BAN = 1265295557115510868
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
    ECONOMY_LOG_THREAD = 1488926630767366416
    DAILY_SUMMARY_THREAD = 1511784346451710113  # daily server summaries post here (weekly/monthly stay in COMMONS)
    VOTING = 959848236384919692
    VOICE_LOG_THREAD = 1493403784074760362

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
