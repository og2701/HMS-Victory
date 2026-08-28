import re

# Dictionary mapping Americanisms to British English
# Based on the user's uploaded image and common Americanisms
#
# Every word the AutoMod rule blocks needs an entry here. The rule deletes the message and
# this is what rewrites it, so a word blocked with nothing to rewrite it to just loses the
# member's message: on_automod_action returns without posting when the correction comes
# back unchanged. analyze, favor, organize, realize and rumor were all in that state.
AMERICAN_TO_BRITISH = {
    "aging": "ageing",
    "analyze": "analyse",
    "analyzed": "analysed",
    "analyzes": "analyses",
    "analyzing": "analysing",
    "aluminum": "aluminium",
    "armor": "armour",
    "artifact": "artefact",
    "artifacts": "artefacts",
    "bruzz": "brother",
    # Brainrot, blocked by the rule and previously left to vanish. Same joke as bruzz:
    # rewritten into plain English rather than the message being eaten.
    "skibidi": "nonsense",
    "skibibi": "nonsense",
    "catalog": "catalogue",
    "center": "centre",
    "centers": "centres",
    # Almost certainly a fragment left over from guarding obfuscated soccer, and it only
    # ever matches the bare token "cer". Mapped anyway so a message containing it is
    # rewritten rather than swallowed; deleting it from the rule would be tidier.
    "cer": "football",
    "cilantro": "coriander",
    "color": "colour",
    "colors": "colours",
    "colorful": "colourful",
    "defense": "defence",
    "dialog": "dialogue",
    "diarrhea": "diarrhoea",
    "eggplant": "aubergine",
    "elevator": "lift",
    "favor": "favour",
    "favors": "favours",
    "favorite": "favourite",
    "favorites": "favourites",
    "flavor": "flavour",
    "flavors": "flavours",
    "gray": "grey",
    "harbor": "harbour",
    "honor": "honour",
    "honors": "honours",
    "humor": "humour",
    "humors": "humours",
    "labor": "labour",
    # A capital I passing as a lowercase l - the rule blocks the lookalike, so it needs a
    # correction like any other blocked word
    "iabor": "labour",
    "likable": "likeable",
    "livable": "liveable",
    "lovable": "loveable",
    "math": "maths",
    "neighbor": "neighbour",
    "neighbors": "neighbours",
    "neighborhood": "neighbourhood",
    "neighborhoods": "neighbourhoods",
    "organize": "organise",
    "organized": "organised",
    "organizes": "organises",
    "organizing": "organising",
    "realize": "realise",
    "realized": "realised",
    "realizes": "realises",
    "realizing": "realising",
    "rumor": "rumour",
    "rumors": "rumours",
    "shopping cart": "trolley",
    "sidewalk": "pavement",
    "soccer": "football",
    "takeout": "takeaway",
    "theater": "theatre",
    "theaters": "theatres",
    "unshakable": "unshakeable",
    "y all": "you all",
    "ya'll": "you all",
    "yall": "you all",
    "y'all": "you all",
    # Unicode apostrophe variants (curly \u2019, modifier \u02bc, left-quote \u2018, grave, etc.)
    "y\u2019all": "you all",
    "y\u02bcall": "you all",
    "y\u2018all": "you all",
    "y`all": "you all",
    "ya\u2019ll": "you all",
    "ya\u02bcll": "you all",
    "ya\u2018ll": "you all",
    "ya`ll": "you all",
    "yogurt": "yoghurt",
    "yogurts": "yoghurts",
    "zucchini": "courgette",
}


# --- the patterned families ---------------------------------------------------------
# The list above grew a word at a time and left whole families out: three -ize verbs out
# of dozens, a handful of -our nouns, no -ll doubling at all. Inflections are spelt out
# because the matcher is word-bounded, so "apologize" does not catch "apologized".
#
# Deliberately absent, and worth leaving absent: words that are ordinary British English
# in another sense. The filter rewrites a member's message and reposts it under their
# name, so a wrong correction puts words in their mouth. program (a computer program is
# correct here), check, draft, story, tire, curb, meter (a gas meter is not a gas metre),
# rigor (mortis), practice and license (the noun/verb split), and the vocabulary traps
# pants, vest, jelly, chips, biscuit, rubber, fall, gas, mad, trunk, hood, highway,
# subway, period and pharmacy.
AMERICAN_TO_BRITISH.update({
    # -ize / -ise
    "apologize": "apologise", "apologized": "apologised", "apologizes": "apologises",
    "apologizing": "apologising",
    "authorize": "authorise", "authorized": "authorised", "authorizing": "authorising",
    "authorization": "authorisation",
    "capitalize": "capitalise", "capitalized": "capitalised",
    "categorize": "categorise", "categorized": "categorised",
    "characterize": "characterise", "characterized": "characterised",
    "civilization": "civilisation", "civilizations": "civilisations",
    "colonize": "colonise", "colonized": "colonised",
    "criticize": "criticise", "criticized": "criticised", "criticizing": "criticising",
    "customize": "customise", "customized": "customised", "customizing": "customising",
    "emphasize": "emphasise", "emphasized": "emphasised", "emphasizing": "emphasising",
    "familiarize": "familiarise", "familiarized": "familiarised",
    "finalize": "finalise", "finalized": "finalised", "finalizing": "finalising",
    "generalize": "generalise", "generalized": "generalised",
    "hypnotize": "hypnotise", "hypnotized": "hypnotised",
    "idolize": "idolise", "idolized": "idolised",
    "immunize": "immunise", "immunized": "immunised",
    "legalize": "legalise", "legalized": "legalised",
    "maximize": "maximise", "maximized": "maximised", "maximizing": "maximising",
    "memorize": "memorise", "memorized": "memorised", "memorizing": "memorising",
    "minimize": "minimise", "minimized": "minimised", "minimizing": "minimising",
    "modernize": "modernise", "modernized": "modernised",
    "monetize": "monetise", "monetized": "monetised",
    "normalize": "normalise", "normalized": "normalised",
    "optimize": "optimise", "optimized": "optimised", "optimizing": "optimising",
    "organization": "organisation", "organizations": "organisations",
    "personalize": "personalise", "personalized": "personalised",
    "prioritize": "prioritise", "prioritized": "prioritised", "prioritizing": "prioritising",
    "publicize": "publicise", "publicized": "publicised",
    "randomize": "randomise", "randomized": "randomised",
    "realization": "realisation",
    "recognize": "recognise", "recognized": "recognised", "recognizes": "recognises",
    "recognizing": "recognising",
    "revolutionize": "revolutionise", "revolutionized": "revolutionised",
    "scrutinize": "scrutinise", "scrutinized": "scrutinised",
    "socialize": "socialise", "socialized": "socialised", "socializing": "socialising",
    "specialize": "specialise", "specialized": "specialised",
    "specialization": "specialisation",
    "stabilize": "stabilise", "stabilized": "stabilised",
    "standardize": "standardise", "standardized": "standardised",
    "sterilize": "sterilise", "sterilized": "sterilised",
    "subsidize": "subsidise", "subsidized": "subsidised",
    "summarize": "summarise", "summarized": "summarised", "summarizing": "summarising",
    "symbolize": "symbolise", "symbolized": "symbolised",
    "sympathize": "sympathise", "sympathized": "sympathised",
    "theorize": "theorise", "theorized": "theorised",
    "utilize": "utilise", "utilized": "utilised", "utilizing": "utilising",
    "vandalize": "vandalise", "vandalized": "vandalised",
    "visualize": "visualise", "visualized": "visualised",
    # -yze
    "paralyze": "paralyse", "paralyzed": "paralysed", "paralyzing": "paralysing",
    "catalyze": "catalyse", "catalyzed": "catalysed",

    # -or / -our
    "arbor": "arbour",
    "armored": "armoured",
    "behavior": "behaviour", "behaviors": "behaviours", "behavioral": "behavioural",
    "candor": "candour",
    "clamor": "clamour",
    "demeanor": "demeanour",
    "endeavor": "endeavour", "endeavors": "endeavours",
    "fervor": "fervour",
    "odor": "odour", "odors": "odours",
    "parlor": "parlour",
    "rancor": "rancour",
    "savor": "savour", "savory": "savoury",
    "splendor": "splendour",
    "succor": "succour",
    "tumor": "tumour", "tumors": "tumours",
    "valor": "valour",
    "vigor": "vigour",

    # -er / -re
    "caliber": "calibre",
    "fiber": "fibre", "fibers": "fibres",
    "liter": "litre", "liters": "litres",
    "luster": "lustre",
    "meager": "meagre",
    "saber": "sabre",
    "somber": "sombre",
    "specter": "spectre",

    # doubled l
    "canceled": "cancelled", "canceling": "cancelling", "cancelation": "cancellation",
    "counselor": "counsellor", "counselors": "counsellors",
    "distill": "distil",
    "enroll": "enrol", "enrollment": "enrolment",
    "fueled": "fuelled", "fueling": "fuelling",
    "fulfill": "fulfil", "fulfillment": "fulfilment",
    "instill": "instil",
    "jeweler": "jeweller", "jewelry": "jewellery",
    "labeled": "labelled", "labeling": "labelling",
    "marvelous": "marvellous",
    "modeled": "modelled", "modeling": "modelling",
    "signaled": "signalled",
    "skillful": "skilful",
    "totaled": "totalled",
    "traveled": "travelled", "traveler": "traveller", "travelers": "travellers",
    "traveling": "travelling",
    "willful": "wilful",

    # ae / oe
    "anemia": "anaemia", "anemic": "anaemic",
    "anesthesia": "anaesthesia", "anesthetic": "anaesthetic",
    "archeology": "archaeology",
    "celiac": "coeliac",
    "edema": "oedema",
    "esophagus": "oesophagus",
    "estrogen": "oestrogen",
    "fetal": "foetal", "fetus": "foetus",
    "gynecology": "gynaecology",
    "hemoglobin": "haemoglobin", "hemorrhage": "haemorrhage",
    "leukemia": "leukaemia", "leukemias": "leukaemias", "leukemic": "leukaemic",
    "maneuver": "manoeuvre", "maneuvers": "manoeuvres",
    "orthopedic": "orthopaedic",
    "pediatric": "paediatric", "pediatrician": "paediatrician",

    # odds and ends
    "airplane": "aeroplane", "airplanes": "aeroplanes",
    "ax": "axe",
    "cozy": "cosy",
    "donut": "doughnut", "donuts": "doughnuts",
    "mold": "mould", "molded": "moulded", "moldy": "mouldy",
    "molt": "moult",
    "mustache": "moustache",
    "pajamas": "pyjamas",
    "plow": "plough",
    "smolder": "smoulder",
    "sulfur": "sulphur",

    # words for things
    "apartment": "flat", "apartments": "flats",
    "arugula": "rocket",
    "candy": "sweets",
    "cell phone": "mobile",
    "closet": "wardrobe",
    "cookie": "biscuit", "cookies": "biscuits",
    "cotton candy": "candy floss",
    "crib": "cot",
    "diaper": "nappy", "diapers": "nappies",
    "dumpster": "skip",
    "eraser": "rubber",
    "faucet": "tap", "faucets": "taps",
    "flashlight": "torch", "flashlights": "torches",
    "freeway": "motorway",
    "french fries": "chips",
    "garbage": "rubbish", "garbage can": "bin",
    "gasoline": "petrol",
    "gotten": "got",
    "ground beef": "mince",
    "license plate": "number plate",
    "mailbox": "postbox", "mailman": "postman",
    "mom": "mum", "moms": "mums",
    "movie": "film", "movies": "films",
    "oatmeal": "porridge",
    "pacifier": "dummy",
    "pantyhose": "tights",
    "parking lot": "car park",
    "popsicle": "ice lolly",
    "restroom": "toilet",
    "scallion": "spring onion", "scallions": "spring onions",
    "shrimp": "prawn", "shrimps": "prawns",
    "skillet": "frying pan",
    "sneakers": "trainers",
    "soda": "fizzy drink",
    "station wagon": "estate car",
    "sweater": "jumper", "sweaters": "jumpers",
    "tic-tac-toe": "noughts and crosses",
    "trash": "rubbish", "trash can": "bin", "trashcan": "bin",
    "turtleneck": "polo neck",
    "undershirt": "vest",
    "vacation": "holiday", "vacations": "holidays",
    "windshield": "windscreen",
    "wrench": "spanner",
    "zip code": "postcode",
})

# Pre-compile the pattern at module level for performance
_SORTED_KEYS = sorted(AMERICAN_TO_BRITISH.keys(), key=len, reverse=True)
_PATTERN = re.compile(r'\b(' + '|'.join(map(re.escape, _SORTED_KEYS)) + r')\b', re.IGNORECASE)

def correct_americanisms(text: str) -> str:
    """
    Corrects Americanisms in the given text to British English while preserving case.
    """
    def replace(match):
        word = match.group(0)
        lower_word = word.lower()
        replacement = AMERICAN_TO_BRITISH.get(lower_word)
        
        if not replacement:
            return word
        
        # Preserve casing
        if word.isupper():
            return replacement.upper()
        if word[0].isupper():
            return replacement.capitalize()
        return replacement

    return _PATTERN.sub(replace, text)
