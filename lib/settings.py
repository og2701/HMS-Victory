
from lib.utils import load_whitelist
from collections import defaultdict

class ROLES:
	DEPUTY_PM = 960538130761527386
	MINISTER = 1250190944502943755
	CABINET = 959493505930121226
	BORDER_FORCE = 959500686746345542
	SERVER_BOOSTER = 959650957325635707
	POLITICS_BAN = 1265295557115510868
	BALL_INSPECTOR = 1197712388493934692

class CHANNELS:
	COMMONS = 959501347571531776
	BOT_SPAM = 968502541107228734
	POLITICS = 1141097424849481799
	PORT_OF_DOVER = 1131633452022767698
	POLICE = 1132970233502650388
	MEMBER_UPDATES = 1279873633602244668 #on secret server
	DATA_BACKUP = 1281734214756335757 #on secret server
	IMAGE_CACHE = 1271188365244497971 #on secret server

class USERS:
	OGGERS = 404634271861571584
	COUNTRYBALL_BOT = 999736048596816014

POLITICS_WHITELISTED_USER_IDS = load_whitelist()

command_usage_tracker = defaultdict(lambda: {'count': 0, 'last_used': None})

SUMMARISE_DAILY_LIMIT = 10

SCAM_KEYWORDS = [
    "cashapp", "cash app", "payment", "cash", "sugar", "money", "bitcoin", 
    "btc", "eth", "crypto", "venmo", "paypal", "zelle", "gift card", "western union", "investment", 
    "profit", "quick cash", "easy money", "giveaway", "prize", "dm ", "referral link", "http", "test"
]