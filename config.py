import os
from collections import defaultdict

# --- Core Bot Settings ---
GUILD_ID = 959493056242008184
CHROME_PATH = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")

# --- Feature Toggles & Limits ---
SHUTCOIN_ENABLED = True
SUMMARISE_DAILY_LIMIT = 10

# --- File Paths & Directories ---
XP_FILE = "chat_leaderboard.json"
ECONOMY_METRICS_FILE = "economy_metrics.json"
BALANCE_SNAPSHOT_DIR = "balance_snapshots"
VC_LOCKDOWN_FILE = "vc_lockdown_status.txt"
OVERNIGHT_MUTE_FILE = "overnight_mute.txt"

# --- Whitelists & Trackers ---
command_usage_tracker = defaultdict(lambda: {"count": 0, "last_used": None})
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
    
    # For secret server
    MEMBER_UPDATES = 1279873633602244668
    DATA_BACKUP = 1281734214756335757
    IMAGE_CACHE = 1271188365244497971

class CATEGORIES:
    TICKETS = 1139976595336069161

# --- User IDs ---
class USERS:
    OGGERS = 404634271861571584
    COUNTRYBALL_BOT = 999736048596816014

# --- XP & Rank System ---
CHAT_LEVEL_ROLE_THRESHOLDS = [
    (1000, ROLES.SERF), (2500, ROLES.PEASANT), (5000, ROLES.FREEMAN),
    (10000, ROLES.COMMONER), (15000, ROLES.YEOMAN), (20000, ROLES.GENTLEMAN),
    (25000, ROLES.ESQUIRE), (35000, ROLES.LORD), (50000, ROLES.KNIGHT),
    (70000, ROLES.BARON), (100000, ROLES.VISCOUNT), (150000, ROLES.EARL),
    (200000, ROLES.MARQUESS), (250000, ROLES.DUKE),
]

CUSTOM_RANK_BACKGROUNDS = {
    "404634271861571584": "oggers.png",
    "347842997641281536": "blank.png",
    "797207976548499518": "johnny.png",
    "725155180680577066": "cherry.png",
    "1204435534416580679": "gunner.png"
}

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

# --- Flag to Language Mappings for Translation ---
FLAG_LANGUAGE_MAPPINGS = {
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿": "British English", "🏴󠁧󠁢󠁷󠁬󠁳󠁿": "Welsh", "🇺🇸": "Over the top american yank speak",
    "🇦🇩": "Catalan", "🇦🇪": "Arabic", "🇦🇫": "Pashto", "🇦🇱": "Albanian",
    "🇦🇲": "Armenian", "🇦🇴": "Portuguese", "🇦🇷": "Spanish", "🇦🇹": "German",
    "🇦🇺": "British English", "🇦🇿": "Azerbaijani", "🇧🇦": "Bosnian", "🇧🇩": "Bengali",
    "🇧🇪": "Dutch", "🇧🇫": "French", "🇧🇬": "Bulgarian", "🇧🇭": "Arabic",
    "🇧🇷": "Portuguese", "🇧🇾": "Belarusian", "🇨🇦": "British English", "🇨🇭": "German",
    "🇨🇱": "Spanish", "🇨🇳": "Mandarin Chinese", "🇨🇴": "Spanish", "🇨🇷": "Spanish",
    "🇨🇺": "Spanish", "🇨🇿": "Czech", "🇩🇪": "German", "🇩🇰": "Danish",
    "🇩🇴": "Spanish", "🇩🇿": "Arabic", "🇪🇨": "Spanish", "🇪🇪": "Estonian",
    "🇪🇬": "Arabic", "🇪🇸": "Spanish", "🇫🇮": "Finnish", "🇫🇷": "French",
    "🇬🇧": "British English", "🇬🇷": "Greek", "🇭🇷": "Croatian", "🇭🇺": "Hungarian",
    "🇮🇩": "Indonesian", "🇮🇪": "British English", "🇮🇱": "Hebrew", "🇮🇳": "Hindi",
    "🇮🇶": "Arabic", "🇮🇷": "Persian", "🇮🇸": "Icelandic", "🇮🇹": "Italian",
    "🇯🇲": "Jamaican Patois", "🇯🇴": "Arabic", "🇯🇵": "Japanese", "🇰🇪": "Swahili",
    "🇰🇬": "Kyrgyz", "🇰🇭": "Khmer", "🇰🇷": "Korean", "🇰🇼": "Arabic",
    "🇱🇧": "Arabic", "🇱🇰": "Sinhala", "🇱🇹": "Lithuanian", "🇱🇻": "Latvian",
    "🇲🇦": "Arabic", "🇲🇩": "Romanian", "🇲🇰": "Macedonian", "🇲🇽": "Spanish",
    "🇲🇾": "Malay", "🇳🇱": "Dutch", "🇳🇴": "Norwegian", "🇳🇿": "British English",
    "🇵🇭": "Filipino", "🇵🇰": "Urdu", "🇵🇱": "Polish", "🇵🇸": "Arabic",
    "🇵🇹": "Portuguese", "🇷🇴": "Romanian", "🇷🇸": "Serbian", "🇷🇺": "Russian",
    "🇸🇦": "Arabic", "🇸🇪": "Swedish", "🇸🇮": "Slovene", "🇸🇰": "Slovak",
    "🇸🇾": "Arabic", "🇹🇭": "Thai", "🇹🇷": "Turkish", "🇺🇦": "Ukrainian",
    "🇻🇳": "Vietnamese", "🇿🇦": "Zulu",
    # Stylistic translations
    "🏴‍☠️": "Pirate Speak", "🤓": "Nerd Speak", "🥷": "Over the top 'roadman' speak",
    "🎩": "British 'rp'/posh talk - 'the queens english'",
    "🏰": "Medieval/Olde English",
}