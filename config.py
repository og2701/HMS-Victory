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

# --- File Paths & Directories ---
DATA_DIR = os.path.join(BASE_DIR, "data")
JSON_DATA_DIR = os.path.join(DATA_DIR, "json")

# Ensure directories exist
os.makedirs(JSON_DATA_DIR, exist_ok=True)
os.makedirs("daily_summaries", exist_ok=True)
os.makedirs("balance_snapshots", exist_ok=True)

XP_FILE = os.path.join(JSON_DATA_DIR, "chat_leaderboard.json")
ECONOMY_METRICS_FILE = os.path.join(JSON_DATA_DIR, "economy_metrics.json")
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
ICEBERG_DATA_FILE = os.path.join(DATA_DIR, "iceberg_texts.json")

BALANCE_SNAPSHOT_DIR = "balance_snapshots"
VC_LOCKDOWN_FILE = os.path.join(JSON_DATA_DIR, "vc_lockdown_status.txt")
OVERNIGHT_MUTE_FILE = os.path.join(JSON_DATA_DIR, "overnight_mute.txt")

# --- Whitelists ---
POLITICS_WHITELISTED_USER_IDS = []

# --- Role IDs ---
class ROLES:
    # Staff & Permissions
    DEPUTY_PM = 960538130761527386
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

class CATEGORIES:
    TICKETS = 1139976595336069161

# --- User IDs ---
class USERS:
    OGGERS = 404634271861571584
    COUNTRYBALL_BOT = 999736048596816014
    CHIN = 795003706717372462
    CHERRY_BLOSSOM = 725155180680577066

# --- Voice Channel Lockdown ---
VC_LOCKDOWN_WHITELIST = [
    ROLES.DUKE, ROLES.MARQUESS, ROLES.EARL, ROLES.VISCOUNT, ROLES.BARON,
    ROLES.KNIGHT, ROLES.LORD, ROLES.ESQUIRE, ROLES.GENTLEMAN, ROLES.YEOMAN,
    ROLES.COMMONER, ROLES.FREEMAN, ROLES.PEASANT, ROLES.SERF,
]

# --- Role Button Mappings ---
ROLE_BUTTONS = {
    "1132280073379123311": {"name": "Soldier 💂 ", "description": "Receive pings for important events and battles."},
    "1132951426386116629": {"name": "Night Watch 🦇", "description": "Signify that you will maintain defence of the flag while others sleep."},
    "1133022537962491964": {"name": "Voting ✅", "description": "Receive pings for new votes in <#959848236384919692>."},
    "1132285964094558288": {"name": "Visitor ✈️", "description": "You're a visitor from another community."},
    "1156757081924313161": {"name": "Gardener 🌳", "description": "Receive pings when <#1142970908059910204> needs watering."}
}
