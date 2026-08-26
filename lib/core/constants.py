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
    "🇦🇺": "Australian bogan slang. Completely rewrite the message so every sentence is dripping in Australian insults and slang - do not just append words to the end. Weave 'cunt' naturally throughout as a filler and term of endearment (e.g. 'listen here ya cunt', 'deadset sick cunt', 'what ya on about cunt'). Replace words with Aussie equivalents and throw in insults like 'dickhead', 'gronk', 'dog', 'dropkick', 'dero', 'flog', 'ya goose', 'muppet' where they fit naturally", "🇦🇿": "Azerbaijani", "🇧🇦": "Bosnian", "🇧🇩": "Bengali",
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
    "🏰": "Shakespearean English (do not use Chaucerian Middle English)",
    "🎭": "Theatrical soliloquy - completely rewrite the message as a speech delivered alone on a stage, thinking aloud to nobody. Keep whatever the message actually said, but bury it in overwrought introspection: address the empty air or the audience directly, agonise over the trivial as though it were fate, ask rhetorical questions nobody will answer, break off and reconsider mid-thought, and land on some grand resolution or lament. Elevated and dramatic but modern English - this is not the Shakespeare option, so no thee, thou, hath or forsooth",
    "🦴": "Caveman talk - completely rewrite the message as primitive caveman speech: drop small grammar words (a, the, is, are, of), turn 'I/me/my' into 'me', use short broken present-tense fragments and the simplest possible words. Keep all the meaning but make it sound like a club-wielding caveman",
}

# --- Translation Global Blacklist ---
TRANSLATION_BLACKLIST_CHANNELS = [
    CHANNELS.LOGS,
    CHANNELS.IMAGE_CACHE,
    CHANNELS.BOT_USAGE_LOG,
    CHANNELS.ANNOUNCEMENTS,
    CHANNELS.MINOR_ANNOUNCEMENTS,
]

