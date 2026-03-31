from config import ROLES, CHANNELS

# --- XP & Rank System ---
CHAT_LEVEL_ROLE_THRESHOLDS = [
    (1000, ROLES.SERF), (2500, ROLES.PEASANT), (5000, ROLES.FREEMAN),
    (10000, ROLES.COMMONER), (15000, ROLES.YEOMAN), (20000, ROLES.GENTLEMAN),
    (25000, ROLES.ESQUIRE), (35000, ROLES.LORD), (50000, ROLES.KNIGHT),
    (70000, ROLES.BARON), (100000, ROLES.VISCOUNT), (150000, ROLES.EARL),
    (200000, ROLES.MARQUESS), (250000, ROLES.DUKE),
    (350000, ROLES.VICEROY), (500000, ROLES.LORD_HIGH_CHANCELLOR), (750000, ROLES.LORD_HIGH_STEWARD),
    (1100000, ROLES.GRAND_DUKE), (1750000, ROLES.ARCHDUKE), (3000000, ROLES.ROYAL_DUKE),
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
    "🇦🇺": "Australian slang - use 'cunt' constantly as a term of endearment and general filler (e.g. 'ya mad cunt', 'sick cunt', 'ya fuckin cunt'), pepper in insults like 'dickhead', 'gronk', 'dog', 'dropkick', 'dero', 'flog', 'ya goose', 'muppet'. After translating, convert the ENTIRE output to upside-down Unicode flip text using this exact mapping — a→ɐ b→q c→ɔ d→p e→ǝ f→ɟ g→ƃ h→ɥ i→ᴉ j→ɾ k→ʞ l→l m→ɯ n→u o→o p→d q→b r→ɹ s→s t→ʇ u→n v→ʌ w→ʍ x→x y→ʎ z→z A→∀ B→B C→Ɔ D→D E→Ǝ F→Ⅎ G→G H→H I→I J→ɾ K→K L→J M→W N→N O→O P→P Q→Q R→R S→S T→T U→∩ V→Λ W→M X→X Y→Y Z→Z .→˙ ,→' ?→¿ !→¡ — apply this to every single character so the whole message appears upside down", "🇦🇿": "Azerbaijani", "🇧🇦": "Bosnian", "🇧🇩": "Bengali",
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

# --- Translation Global Blacklist ---
TRANSLATION_BLACKLIST_CHANNELS = [
    CHANNELS.LOGS,
    CHANNELS.IMAGE_CACHE,
    CHANNELS.BOT_USAGE_LOG,
    CHANNELS.ANNOUNCEMENTS,
    CHANNELS.MINOR_ANNOUNCEMENTS,
]

