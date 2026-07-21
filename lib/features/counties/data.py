"""County Balls roster - the 92 historic counties of the UK (plus London).

Rarity follows popularity: the famous, populous places everyone wants are the
rarest; small obscure counties are common. Art assets live at
data/counties/<key>.webp (see data/counties/ART_PROMPTS.md for the generation
brief). Keys are stable identifiers - they name the asset files and the rows in
county_instances, so never rename one.
"""

import hashlib
from collections import namedtuple

County = namedtuple("County", ["name", "nation", "tier", "aliases"])

COUNTIES = {
    # --- England (40) ---
    "london": County("London", "England", "legendary", ["greater london", "city of london", "the big smoke"]),
    "yorkshire": County("Yorkshire", "England", "legendary", ["yorks", "gods own county", "god's own county"]),
    "devon": County("Devon", "England", "rare", ["devonshire"]),
    "essex": County("Essex", "England", "rare", []),
    "hampshire": County("Hampshire", "England", "rare", ["hants"]),
    "kent": County("Kent", "England", "rare", []),
    "lancashire": County("Lancashire", "England", "legendary", ["lancs"]),
    "middlesex": County("Middlesex", "England", "rare", ["middx"]),
    "surrey": County("Surrey", "England", "rare", []),
    "bedfordshire": County("Bedfordshire", "England", "common", ["beds"]),
    "berkshire": County("Berkshire", "England", "uncommon", ["berks"]),
    "buckinghamshire": County("Buckinghamshire", "England", "uncommon", ["bucks"]),
    "cambridgeshire": County("Cambridgeshire", "England", "uncommon", ["cambs"]),
    "cheshire": County("Cheshire", "England", "rare", []),
    "cornwall": County("Cornwall", "England", "legendary", ["kernow"]),
    "derbyshire": County("Derbyshire", "England", "uncommon", []),
    "dorset": County("Dorset", "England", "uncommon", ["dorsetshire"]),
    "durham": County("Durham", "England", "uncommon", ["county durham"]),
    "gloucestershire": County("Gloucestershire", "England", "uncommon", ["glos", "gloucs"]),
    "hertfordshire": County("Hertfordshire", "England", "uncommon", ["herts"]),
    "leicestershire": County("Leicestershire", "England", "uncommon", ["leics"]),
    "lincolnshire": County("Lincolnshire", "England", "uncommon", ["lincs"]),
    "norfolk": County("Norfolk", "England", "uncommon", []),
    "northamptonshire": County("Northamptonshire", "England", "uncommon", ["northants"]),
    "northumberland": County("Northumberland", "England", "uncommon", []),
    "nottinghamshire": County("Nottinghamshire", "England", "uncommon", ["notts"]),
    "oxfordshire": County("Oxfordshire", "England", "uncommon", ["oxon"]),
    "somerset": County("Somerset", "England", "rare", ["somersetshire"]),
    "staffordshire": County("Staffordshire", "England", "uncommon", ["staffs"]),
    "suffolk": County("Suffolk", "England", "uncommon", []),
    "sussex": County("Sussex", "England", "rare", []),
    "warwickshire": County("Warwickshire", "England", "uncommon", ["warks"]),
    "wiltshire": County("Wiltshire", "England", "uncommon", ["wilts"]),
    "worcestershire": County("Worcestershire", "England", "uncommon", ["worcs"]),
    "cumberland": County("Cumberland", "England", "common", []),
    "herefordshire": County("Herefordshire", "England", "common", []),
    "huntingdonshire": County("Huntingdonshire", "England", "common", ["hunts"]),
    "rutland": County("Rutland", "England", "common", ["rutlandshire"]),
    "shropshire": County("Shropshire", "England", "common", ["salop"]),
    "westmorland": County("Westmorland", "England", "common", ["westmoreland"]),

    # --- Wales (13) ---
    "glamorgan": County("Glamorgan", "Wales", "rare", ["glamorganshire", "morgannwg"]),
    "carmarthenshire": County("Carmarthenshire", "Wales", "uncommon", ["carms", "sir gar"]),
    "denbighshire": County("Denbighshire", "Wales", "uncommon", []),
    "flintshire": County("Flintshire", "Wales", "uncommon", ["flints"]),
    "monmouthshire": County("Monmouthshire", "Wales", "uncommon", ["sir fynwy"]),
    "pembrokeshire": County("Pembrokeshire", "Wales", "uncommon", ["pembs", "sir benfro"]),
    "anglesey": County("Anglesey", "Wales", "common", ["ynys mon", "sir fon"]),
    "brecknockshire": County("Brecknockshire", "Wales", "common", ["breconshire", "brecon"]),
    "caernarfonshire": County("Caernarfonshire", "Wales", "common", ["caernarvonshire", "carnarvonshire"]),
    "cardiganshire": County("Cardiganshire", "Wales", "common", ["ceredigion"]),
    "merionethshire": County("Merionethshire", "Wales", "common", ["merioneth", "meirionnydd"]),
    "montgomeryshire": County("Montgomeryshire", "Wales", "common", ["sir drefaldwyn"]),
    "radnorshire": County("Radnorshire", "Wales", "common", []),

    # --- Scotland (33) ---
    "lanarkshire": County("Lanarkshire", "Scotland", "rare", ["lanark"]),
    "midlothian": County("Midlothian", "Scotland", "rare", ["edinburghshire"]),
    "aberdeenshire": County("Aberdeenshire", "Scotland", "uncommon", []),
    "ayrshire": County("Ayrshire", "Scotland", "uncommon", []),
    "fife": County("Fife", "Scotland", "rare", ["fifeshire", "kingdom of fife"]),
    "inverness_shire": County("Inverness-shire", "Scotland", "uncommon", ["inverness"]),
    "perthshire": County("Perthshire", "Scotland", "uncommon", []),
    "renfrewshire": County("Renfrewshire", "Scotland", "uncommon", []),
    "stirlingshire": County("Stirlingshire", "Scotland", "uncommon", []),
    "angus": County("Angus", "Scotland", "common", ["forfarshire"]),
    "argyllshire": County("Argyllshire", "Scotland", "common", ["argyll"]),
    "banffshire": County("Banffshire", "Scotland", "common", []),
    "berwickshire": County("Berwickshire", "Scotland", "common", []),
    "buteshire": County("Buteshire", "Scotland", "common", ["bute"]),
    "caithness": County("Caithness", "Scotland", "common", []),
    "clackmannanshire": County("Clackmannanshire", "Scotland", "common", ["clacks"]),
    "dumfriesshire": County("Dumfriesshire", "Scotland", "common", ["dumfries"]),
    "dunbartonshire": County("Dunbartonshire", "Scotland", "common", ["dumbartonshire"]),
    "east_lothian": County("East Lothian", "Scotland", "common", ["haddingtonshire"]),
    "kincardineshire": County("Kincardineshire", "Scotland", "common", ["the mearns"]),
    "kinross_shire": County("Kinross-shire", "Scotland", "common", ["kinross"]),
    "kirkcudbrightshire": County("Kirkcudbrightshire", "Scotland", "common", []),
    "moray": County("Moray", "Scotland", "common", ["morayshire", "elginshire"]),
    "nairnshire": County("Nairnshire", "Scotland", "common", ["nairn"]),
    "orkney": County("Orkney", "Scotland", "uncommon", ["orkney islands"]),
    "peeblesshire": County("Peeblesshire", "Scotland", "common", []),
    "ross_and_cromarty": County("Ross and Cromarty", "Scotland", "common", ["ross shire", "ross"]),
    "roxburghshire": County("Roxburghshire", "Scotland", "common", []),
    "selkirkshire": County("Selkirkshire", "Scotland", "common", []),
    "shetland": County("Shetland", "Scotland", "uncommon", ["shetland islands", "zetland"]),
    "sutherland": County("Sutherland", "Scotland", "common", []),
    "west_lothian": County("West Lothian", "Scotland", "common", ["linlithgowshire"]),
    "wigtownshire": County("Wigtownshire", "Scotland", "common", ["wigtown"]),

    # --- Northern Ireland (6) ---
    "antrim": County("Antrim", "Northern Ireland", "rare", []),
    "armagh": County("Armagh", "Northern Ireland", "uncommon", []),
    "down": County("Down", "Northern Ireland", "rare", []),
    "londonderry": County("Londonderry", "Northern Ireland", "uncommon", ["derry"]),
    "tyrone": County("Tyrone", "Northern Ireland", "uncommon", []),
    "fermanagh": County("Fermanagh", "Northern Ireland", "common", []),
}

NATIONS = ["England", "Wales", "Scotland", "Northern Ireland"]

# --- Stats (BallsDex-style) ---
# Every county has base Clout (attack) and Grit (health); each caught instance
# additionally rolls a +/-20% bonus on both. Bases come from the tier plus a
# stable per-county jitter (hash of the key), so higher tiers are stronger on
# average without hand-tuning 92 stat lines, and a county's bases never change.
TIER_BASE_STATS = {"common": 600, "uncommon": 700, "rare": 800, "legendary": 920}


def base_stats(key: str) -> tuple:
    """(base_clout, base_grit) for a county - deterministic across restarts."""
    h = hashlib.md5(key.encode()).digest()
    base = TIER_BASE_STATS[COUNTIES[key].tier]
    return base + h[0] % 181 - 90, base + h[1] % 181 - 90


def normalise(text: str) -> str:
    """Lower-case, strip punctuation variants, and collapse whitespace so
    'Kinross-shire', 'kinross shire' and 'KINROSS  SHIRE' all compare equal."""
    s = text.lower().strip()
    for a, b in (("’", "'"), ("‘", "'"), (".", ""), ("-", " "), ("_", " ")):
        s = s.replace(a, b)
    return " ".join(s.split())


# normalised guess -> county key, built once at import
_LOOKUP: dict = {}
for _key, _c in COUNTIES.items():
    _LOOKUP[normalise(_c.name)] = _key
    for _alias in _c.aliases:
        _LOOKUP[normalise(_alias)] = _key


def match_county(guess: str) -> str | None:
    """Resolve a user's guess (or a command argument) to a county key."""
    norm = normalise(guess)
    if norm in _LOOKUP:
        return _LOOKUP[norm]
    # "county durham", "county antrim" style guesses
    if norm.startswith("county "):
        return _LOOKUP.get(norm[7:])
    return None
