from lib.utils import load_whitelist
from collections import defaultdict

GUILD_ID = 959493056242008184

class ROLES:
    DEPUTY_PM = 960538130761527386
    MINISTER = 1250190944502943755
    CABINET = 959493505930121226
    BORDER_FORCE = 959500686746345542
    PCSO = 1132949441389797397
    SERVER_BOOSTER = 959650957325635707
    POLITICS_BAN = 1265295557115510868
    BALL_INSPECTOR = 1197712388493934692
    VOICE_CHAT_WARDEN = 1334593677686870166
    EMBED_PERMS = 1339021325032751215
    DONT_DM_WHEN_MESSAGE_BLOCKED = 1345805710000128000

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
    MEMBER = 1142491622563643442

    BRITISH = 1220038224051830834
    ENGLISH = 1220038335226052697
    SCOTTISH = 1220038357204074607
    WELSH = 1220038385058582568
    NORTHERN_IRISH = 1220038413101568171
    COMMONWEALTH = 1295105020928462949
    VISITOR = 1132285964094558288


class CHANNELS:
    COMMONS = 959501347571531776
    BOT_SPAM = 968502541107228734
    POLITICS = 1141097424849481799
    PORT_OF_DOVER = 1131633452022767698
    LOGS = 959723562892144690
    POLICE_STATION = 1132970233502650388
    BOT_USAGE_LOG = 1197572903294730270
    MEMBER_UPDATES = 1279873633602244668  # on secret server
    DATA_BACKUP = 1281734214756335757  # on secret server
    IMAGE_CACHE = 1271188365244497971  # on secret server


class USERS:
    OGGERS = 404634271861571584
    COUNTRYBALL_BOT = 999736048596816014


class CATEGORIES:
    PERM_VC = 959493057076666379
    TICKETS = 1139976595336069161

POLITICS_WHITELISTED_USER_IDS = load_whitelist()

command_usage_tracker = defaultdict(lambda: {"count": 0, "last_used": None})

SUMMARISE_DAILY_LIMIT = 10

VC_LOCKDOWN_FILE = "vc_lockdown_status.txt"
VC_LOCKDOWN_WHITELIST = [
    ROLES.DUKE,
    ROLES.MARQUESS,
    ROLES.EARL,
    ROLES.VISCOUNT,
    ROLES.BARON,
    ROLES.KNIGHT,
    ROLES.LORD,
    ROLES.ESQUIRE,
    ROLES.GENTLEMAN,
    ROLES.YEOMAN,
    ROLES.COMMONER,
    ROLES.FREEMAN,
    ROLES.PEASANT,
    ROLES.SERF,
]


CUSTOM_RANK_BACKGROUNDS = {
    # "USER_ID_STRING": "custom_background_filename.png",
}


FLAG_LANGUAGE_MAPPINGS = {
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿": "British English",  # England
    "🏴󠁧󠁢󠁷󠁬󠁳󠁿": "Welsh",  # Wales
    # "🏴󠁧󠁢󠁳󠁣󠁴󠁿": "Scottish Gaelic",       # Scotland
    "🇦🇨": "British English",  # Ascension Island
    "🇦🇩": "Catalan",  # Andorra
    "🇦🇪": "Arabic",  # United Arab Emirates
    "🇦🇫": "Pashto",  # Afghanistan
    "🇦🇬": "British English",  # Antigua and Barbuda
    "🇦🇮": "British English",  # Anguilla
    "🇦🇱": "Albanian",  # Albania
    "🇦🇲": "Armenian",  # Armenia
    "🇦🇴": "Portuguese",  # Angola
    "🇦🇷": "Spanish",  # Argentina
    "🇦🇸": "Samoan",  # American Samoa
    "🇦🇹": "German",  # Austria
    "🇦🇺": "British English",  # Australia
    "🇦🇼": "Papiamento",  # Aruba
    "🇦🇽": "Swedish",  # Åland Islands
    "🇦🇿": "Azerbaijani",  # Azerbaijan
    "🇧🇦": "Bosnian",  # Bosnia and Herzegovina
    "🇧🇧": "British English",  # Barbados
    "🇧🇩": "Bengali",  # Bangladesh
    "🇧🇪": "Dutch",  # Belgium
    "🇧🇫": "French",  # Burkina Faso
    "🇧🇬": "Bulgarian",  # Bulgaria
    "🇧🇭": "Arabic",  # Bahrain
    "🇧🇮": "Kirundi",  # Burundi
    "🇧🇯": "French",  # Benin
    "🇧🇱": "French",  # Saint Barthélemy
    "🇧🇲": "British English",  # Bermuda
    "🇧🇳": "Malay",  # Brunei
    "🇧🇴": "Spanish",  # Bolivia
    "🇧🇶": "Dutch",  # Caribbean Netherlands
    "🇧🇷": "Portuguese",  # Brazil
    "🇧🇸": "British English",  # Bahamas
    "🇧🇹": "Dzongkha",  # Bhutan
    "🇧🇻": "Norwegian",  # Bouvet Island
    "🇧🇼": "British English",  # Botswana
    "🇧🇾": "Belarusian",  # Belarus
    "🇧🇿": "British English",  # Belize
    "🇨🇦": "British English",  # Canada
    "🇨🇨": "British English",  # Cocos (Keeling) Islands
    "🇨🇩": "French",  # Democratic Republic of the Congo
    "🇨🇫": "French",  # Central African Republic
    "🇨🇬": "French",  # Republic of the Congo
    "🇨🇭": "German",  # Switzerland
    "🇨🇮": "French",  # Côte d'Ivoire
    "🇨🇰": "British English",  # Cook Islands
    "🇨🇱": "Spanish",  # Chile
    "🇨🇲": "French",  # Cameroon
    "🇨🇳": "Mandarin Chinese",  # China
    "🇨🇴": "Spanish",  # Colombia
    "🇨🇵": "British English",  # Clipperton Island
    "🇨🇷": "Spanish",  # Costa Rica
    "🇨🇺": "Spanish",  # Cuba
    "🇨🇻": "Portuguese",  # Cape Verde
    "🇨🇼": "Papiamento",  # Curaçao
    "🇨🇽": "British English",  # Christmas Island
    "🇨🇾": "Greek",  # Cyprus
    "🇨🇿": "Czech",  # Czech Republic
    "🇩🇪": "German",  # Germany
    "🇩🇬": "British English",  # Diego Garcia
    "🇩🇯": "French",  # Djibouti
    "🇩🇰": "Danish",  # Denmark
    "🇩🇲": "British English",  # Dominica
    "🇩🇴": "Spanish",  # Dominican Republic
    "🇩🇿": "Arabic",  # Algeria
    "🇪🇨": "Spanish",  # Ecuador
    "🇪🇪": "Estonian",  # Estonia
    "🇪🇬": "Arabic",  # Egypt
    "🇪🇷": "Tigrinya",  # Eritrea
    "🇪🇸": "Spanish",  # Spain
    "🇪🇹": "Amharic",  # Ethiopia
    "🇫🇮": "Finnish",  # Finland
    "🇫🇯": "British English",  # Fiji
    "🇫🇰": "British English",  # Falkland Islands
    "🇫🇲": "British English",  # Micronesia
    "🇫🇴": "Faroese",  # Faroe Islands
    "🇫🇷": "French",  # France
    "🇬🇦": "French",  # Gabon
    "🇬🇧": "British English",  # United Kingdom
    "🇬🇩": "British English",  # Grenada
    "🇬🇪": "Georgian",  # Georgia
    "🇬🇫": "French",  # French Guiana
    "🇬🇬": "British English",  # Guernsey
    "🇬🇭": "British English",  # Ghana
    "🇬🇮": "British English",  # Gibraltar
    "🇬🇱": "Greenlandic",  # Greenland
    "🇬🇲": "British English",  # Gambia
    "🇬🇳": "French",  # Guinea
    "🇬🇵": "French",  # Guadeloupe
    "🇬🇶": "Spanish",  # Equatorial Guinea
    "🇬🇷": "Greek",  # Greece
    "🇬🇹": "Spanish",  # Guatemala
    "🇬🇺": "British English",  # Guam
    "🇬🇼": "Portuguese",  # Guinea-Bissau
    "🇬🇾": "British English",  # Guyana
    "🇭🇰": "Chinese",  # Hong Kong
    "🇭🇳": "Spanish",  # Honduras
    "🇭🇷": "Croatian",  # Croatia
    "🇭🇹": "Haitian Creole",  # Haiti
    "🇭🇺": "Hungarian",  # Hungary
    "🇮🇩": "Indonesian",  # Indonesia
    "🇮🇪": "British English",  # Ireland
    "🇮🇱": "Hebrew",  # Israel
    "🇮🇲": "British English",  # Isle of Man
    "🇮🇳": "Hindi",  # India
    "🇮🇶": "Arabic",  # Iraq
    "🇮🇷": "Persian",  # Iran
    "🇮🇸": "Icelandic",  # Iceland
    "🇮🇹": "Italian",  # Italy
    "🇯🇪": "British English",  # Jersey
    "🇯🇲": "Jamaican Patois",  # Jamaica
    "🇯🇴": "Arabic",  # Jordan
    "🇯🇵": "Japanese",  # Japan
    "🇰🇪": "Swahili",  # Kenya
    "🇰🇬": "Kyrgyz",  # Kyrgyzstan
    "🇰🇭": "Khmer",  # Cambodia
    "🇰🇮": "British English",  # Kiribati
    "🇰🇲": "Comorian",  # Comoros
    "🇰🇳": "British English",  # Saint Kitts and Nevis
    "🇰🇵": "Korean",  # North Korea
    "🇰🇷": "Korean",  # South Korea
    "🇰🇼": "Arabic",  # Kuwait
    "🇰🇾": "British English",  # Cayman Islands
    "🇰🇿": "Kazakh",  # Kazakhstan
    "🇱🇦": "Lao",  # Laos
    "🇱🇧": "Arabic",  # Lebanon
    "🇱🇨": "British English",  # Saint Lucia
    "🇱🇮": "German",  # Liechtenstein
    "🇱🇰": "Sinhala",  # Sri Lanka
    "🇱🇷": "British English",  # Liberia
    "🇱🇸": "Sesotho",  # Lesotho
    "🇱🇹": "Lithuanian",  # Lithuania
    "🇱🇺": "Luxembourgish",  # Luxembourg
    "🇱🇻": "Latvian",  # Latvia
    "🇱🇾": "Arabic",  # Libya
    "🇲🇦": "Arabic",  # Morocco
    "🇲🇨": "French",  # Monaco
    "🇲🇩": "Romanian",  # Moldova
    "🇲🇪": "Montenegrin",  # Montenegro
    "🇲🇫": "French",  # Saint Martin
    "🇲🇬": "Malagasy",  # Madagascar
    "🇲🇭": "Marshallese",  # Marshall Islands
    "🇲🇰": "Macedonian",  # North Macedonia
    "🇲🇱": "French",  # Mali
    "🇲🇲": "Burmese",  # Myanmar
    "🇲🇳": "Mongolian",  # Mongolia
    "🇲🇴": "Chinese",  # Macau
    "🇲🇵": "British English",  # Northern Mariana Islands
    "🇲🇶": "French",  # Martinique
    "🇲🇷": "Arabic",  # Mauritania
    "🇲🇸": "British English",  # Montserrat
    "🇲🇹": "Maltese",  # Malta
    "🇲🇺": "British English",  # Mauritius
    "🇲🇻": "Dhivehi",  # Maldives
    "🇲🇼": "British English",  # Malawi
    "🇲🇽": "Spanish",  # Mexico
    "🇲🇾": "Malay",  # Malaysia
    "🇲🇿": "Portuguese",  # Mozambique
    "🇳🇦": "British English",  # Namibia
    "🇳🇨": "French",  # New Caledonia
    "🇳🇪": "French",  # Niger
    "🇳🇫": "British English",  # Norfolk Island
    "🇳🇬": "British English",  # Nigeria
    "🇳🇮": "Spanish",  # Nicaragua
    "🇳🇱": "Dutch",  # Netherlands
    "🇳🇴": "Norwegian",  # Norway
    "🇳🇵": "Nepali",  # Nepal
    "🇳🇷": "Nauruan",  # Nauru
    "🇳🇺": "British English",  # Niue
    "🇳🇿": "British English",  # New Zealand
    "🇴🇲": "Arabic",  # Oman
    "🇵🇦": "Spanish",  # Panama
    "🇵🇪": "Spanish",  # Peru
    "🇵🇫": "French",  # French Polynesia
    "🇵🇬": "British English",  # Papua New Guinea
    "🇵🇭": "Filipino",  # Philippines
    "🇵🇰": "Urdu",  # Pakistan
    "🇵🇱": "Polish",  # Poland
    "🇵🇲": "French",  # Saint Pierre and Miquelon
    "🇵🇳": "British English",  # Pitcairn Islands
    "🇵🇷": "Spanish",  # Puerto Rico
    "🇵🇸": "Arabic",  # Palestine
    "🇵🇹": "Portuguese",  # Portugal
    "🇵🇼": "Palauan",  # Palau
    "🇵🇾": "Spanish",  # Paraguay
    "🇶🇦": "Arabic",  # Qatar
    "🇷🇪": "French",  # Réunion
    "🇷🇴": "Romanian",  # Romania
    "🇷🇸": "Serbian",  # Serbia
    "🇷🇺": "Russian",  # Russia
    "🇷🇼": "Kinyarwanda",  # Rwanda
    "🇸🇦": "Arabic",  # Saudi Arabia
    "🇸🇧": "British English",  # Solomon Islands
    "🇸🇨": "Seselwa",  # Seychelles
    "🇸🇩": "Arabic",  # Sudan
    "🇸🇪": "Swedish",  # Sweden
    "🇸🇬": "British English",  # Singapore
    "🇸🇭": "British English",  # Saint Helena
    "🇸🇮": "Slovene",  # Slovenia
    "🇸🇯": "Norwegian",  # Svalbard and Jan Mayen
    "🇸🇰": "Slovak",  # Slovakia
    "🇸🇱": "British English",  # Sierra Leone
    "🇸🇲": "Italian",  # San Marino
    "🇸🇳": "French",  # Senegal
    "🇸🇴": "Somali",  # Somalia
    "🇸🇷": "Dutch",  # Suriname
    "🇸🇸": "British English",  # South Sudan
    "🇸🇹": "Portuguese",  # São Tomé and Príncipe
    "🇸🇻": "Spanish",  # El Salvador
    "🇸🇽": "Dutch",  # Sint Maarten
    "🇸🇾": "Arabic",  # Syria
    "🇸🇿": "Swazi",  # Eswatini
    "🇹🇦": "British English",  # Tristan da Cunha
    "🇹🇨": "British English",  # Turks and Caicos Islands
    "🇹🇩": "French",  # Chad
    "🇹🇫": "French",  # French Southern Territories
    "🇹🇬": "French",  # Togo
    "🇹🇭": "Thai",  # Thailand
    "🇹🇯": "Tajik",  # Tajikistan
    "🇹🇰": "Tokelauan",  # Tokelau
    "🇹🇱": "Tetum",  # Timor-Leste
    "🇹🇲": "Turkmen",  # Turkmenistan
    "🇹🇳": "Arabic",  # Tunisia
    "🇹🇴": "Tongan",  # Tonga
    "🇹🇷": "Turkish",  # Turkey
    "🇹🇹": "British English",  # Trinidad and Tobago
    "🇹🇻": "Tuvaluan",  # Tuvalu
    "🇹🇼": "Mandarin Chinese",  # Taiwan
    "🇹🇿": "Swahili",  # Tanzania
    "🇺🇦": "Ukrainian",  # Ukraine
    "🇺🇬": "Swahili",  # Uganda
    "🇺🇲": "British English",  # U.S. Minor Outlying Islands
    "🇺🇸": "Over the top american yank speak",  # United States
    "🇺🇾": "Spanish",  # Uruguay
    "🇺🇿": "Uzbek",  # Uzbekistan
    "🇻🇦": "Italian",  # Vatican City
    "🇻🇨": "British English",  # Saint Vincent and the Grenadines
    "🇻🇪": "Spanish",  # Venezuela
    "🇻🇬": "British English",  # British Virgin Islands
    "🇻🇮": "British English",  # U.S. Virgin Islands
    "🇻🇳": "Vietnamese",  # Vietnam
    "🇻🇺": "Bislama",  # Vanuatu
    "🇼🇫": "French",  # Wallis and Futuna
    "🇼🇸": "Samoan",  # Samoa
    "🇽🇰": "Albanian",  # Kosovo
    "🇾🇪": "Arabic",  # Yemen
    "🇾🇹": "French",  # Mayotte
    "🇿🇦": "Zulu",  # South Africa
    "🇿🇲": "British English",  # Zambia
    "🇿🇼": "Shona",  # Zimbabwe
    "🏴‍☠️": "Pirate Speak",
    "🤓": "Nerd Speak",
    "🥷": "Over the top 'roadman' speak",
    "🎩": "British 'rp'/posh talk - 'the queens english'",
    "🏰": "Medieval/Olde English - Early Modern English or Elizabethan English commonly associated with the works of Shakespeare and the King James Bible",
}
