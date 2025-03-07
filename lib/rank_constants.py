from lib.constants import *

CHAT_LEVEL_ROLE_THRESHOLDS = [
    (1000, ROLES.SERF),
    (2500, ROLES.PEASANT),
    (5000, ROLES.FREEMAN),
    (10000, ROLES.COMMONER),
    (15000, ROLES.YEOMAN),
    (20000, ROLES.GENTLEMAN),
    (25000, ROLES.ESQUIRE),
    (35000, ROLES.LORD),
    (50000, ROLES.KNIGHT),
    (70000, ROLES.BARON),
    (100000, ROLES.VISCOUNT),
    (150000, ROLES.EARL),
    (200000, ROLES.MARQUESS),
    (250000, ROLES.DUKE),
]

XP_FILE = "chat_leaderboard.json" 

CUSTOM_RANK_BACKGROUNDS = {
    # "USER_ID_STRING": "custom_background_filename.png",
    "404634271861571584": "oggers.png",
    "347842997641281536": "blank.png",
    "797207976548499518": "johnny.png"
}

