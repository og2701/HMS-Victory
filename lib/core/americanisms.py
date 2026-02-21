import re

# Dictionary mapping Americanisms to British English
# Based on the user's uploaded image and common Americanisms
AMERICAN_TO_BRITISH = {
    "aging": "ageing",
    "aluminum": "aluminium",
    "armor": "armour",
    "artifact": "artefact",
    "artifacts": "artefacts",
    "bruzz": "brother",
    "catalog": "catalogue",
    "center": "centre",
    "centers": "centres",
    "cilantro": "coriander",
    "color": "colour",
    "colors": "colours",
    "colorful": "colourful",
    "defense": "defence",
    "dialog": "dialogue",
    "diarrhea": "diarrhoea",
    "eggplant": "aubergine",
    "elevator": "lift",
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
    "likable": "likeable",
    "livable": "liveable",
    "lovable": "loveable",
    "math": "maths",
    "neighbor": "neighbour",
    "neighbors": "neighbours",
    "neighborhood": "neighbourhood",
    "neighborhoods": "neighbourhoods",
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
    "zucchini": "courgette",
}

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
