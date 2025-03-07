from lib.utils import load_whitelist
from collections import defaultdict
from lib.constants import *

GUILD_ID = 959493056242008184

POLITICS_WHITELISTED_USER_IDS = load_whitelist()

command_usage_tracker = defaultdict(lambda: {"count": 0, "last_used": None})

SUMMARISE_DAILY_LIMIT = 10

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
