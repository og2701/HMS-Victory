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
    # The rule blocks the curly apostrophe too, which is what phones type
    "y\u2019all": "you all",
    "yogurt": "yoghurt",
    "yogurts": "yoghurts",
    "zucchini": "courgette",
}

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
