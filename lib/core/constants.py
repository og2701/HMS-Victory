from config import ROLES

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
